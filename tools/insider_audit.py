"""H-024 phase 3 — routine vs opportunistic insider sleeve audit (read-only).

Runs the frozen four-trial family and its four routine-arm placebos over the
research universe (1d), prints the cohort split, each cell's headline plus
DSR / PBO / boot-CI / MinTRL and realized equity-β to SPY, the paired
trial-minus-placebo reversal test, and the PASS/FAIL on the pre-committed ``T1``
cell. Books are priced gross and net side by side, because a sleeve that is
negative before costs is a signal failure rather than a cost failure and the two
verdicts read differently. No writes.

**The first look at a return in this row happens here.** Everything upstream —
ingestion, the coverage observable, the classifier, the cohort shape — is built
and reported without one, which is what makes this output a test rather than a
search.

Usage::

    PYTHONPATH=. poetry run python tools/insider_audit.py
    PYTHONPATH=. poetry run python tools/insider_audit.py --symbols AAPL,MSFT
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest.cost_model import CostModel
from analytics.forecast.config import ForecastConfig
from analytics.insider.book import PLACEBOS, TRIALS, InsiderInputs
from analytics.insider.classify import OPPORTUNISTIC, ROUTINE, cohort_shape
from analytics.insider.replay import (
    insider_market_return,
    labelled_transactions,
    load_insider_inputs,
    replay_insider_trials,
    shared_base_cost_model,
)
from analytics.insider.report import (
    InsiderReport,
    annualized_mean_bps,
    evaluate_insider_trials,
)
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe


def zero_cost(model: CostModel) -> CostModel:
    """The same model with every charge set to zero — the gross read.

    Built from the live model rather than from ``CostModel()`` so the ADV
    thresholds and window still match: only the PRICES are removed, which keeps
    the gross and net books identical in every other respect.
    """
    return dataclasses.replace(
        model,
        half_spread_bps=tuple(0.0 for _ in model.half_spread_bps),
        impact_coef=0.0,
        borrow_rate_annual=0.0,
        commission_bps=0.0,
    )


def cell_frame(rep: InsiderReport) -> pd.DataFrame:
    """One row per book: the headline, the guards and the β attribution."""
    rows = []
    for key, cell in rep.cells.items():
        attr = rep.attribution[key]
        label, long_only, hold = {**TRIALS, **PLACEBOS}[key]
        rows.append(
            {
                "cell": key,
                "arm": label[:4],
                "form": "long" if long_only else "ls",
                "hold_m": hold,
                "days": cell.n_obs,
                "sharpe": cell.sharpe_annual,
                "dsr": cell.dsr,
                "pbo": cell.pbo,
                "boot_lo": cell.boot_lo,
                "beta": attr.beta,
                "alpha_t": attr.alpha_tstat,
                "hedged_sr": attr.beta_hedged_sharpe,
            }
        )
    return pd.DataFrame(rows)


def paired_frame(rep: InsiderReport) -> pd.DataFrame:
    """The reversal observable: trial minus its routine placebo, in bps/month."""
    rows = []
    for pair in rep.paired.values():
        rows.append(
            {
                "trial": pair.trial,
                "placebo": pair.placebo,
                "diff_bps_mo": annualized_mean_bps(pair.mean_diff_daily),
                "ci_lo_bps": annualized_mean_bps(pair.lo),
                "ci_hi_bps": annualized_mean_bps(pair.hi),
                # Three states: an unmeasurable pair is not a pair that agreed.
                "measurable": pair.measurable,
                "indistinct": pair.indistinguishable,
            }
        )
    return pd.DataFrame(rows)


def population_line(labelled: pd.DataFrame) -> str:
    """The cohort split on the panel actually booked — Amendment 3's question."""
    shape = cohort_shape(labelled)
    classified = shape.classified_rows
    share = shape.routine_row_share
    return (
        f"population: {len(labelled)} P/S rows · "
        f"{labelled['symbol'].nunique()} symbols · "
        f"{labelled['owner_cik'].nunique()} insiders | "
        f"classified {classified} rows "
        f"({100.0 * classified / len(labelled):.1f}% of population) | "
        f"routine {shape.rows[ROUTINE]} / opportunistic {shape.rows[OPPORTUNISTIC]} "
        f"= {100.0 * share:.1f}% routine (WP: ~55%)"
    )


def run(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str] | None,
    cfg: ForecastConfig,
    inputs: InsiderInputs,
    labelled: pd.DataFrame,
    cost: CostModel,
) -> InsiderReport:
    books = replay_insider_trials(
        conn, symbols=symbols, cost=cost, inputs=inputs, labelled=labelled
    )
    spy = insider_market_return(conn)
    return evaluate_insider_trials(books, cfg, spy)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--symbols",
        default=None,
        help="explicit comma-separated subset of the universe",
    )
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else None
    conn = duckdb.connect(str(args.db), read_only=True)
    cfg = ForecastConfig()
    model = shared_base_cost_model()

    universe = symbols or load_research_universe().stocks()
    print(f"DB: {args.db}")
    print(f"universe: {len(universe)} members")

    inputs = load_insider_inputs(conn, universe)
    labelled = labelled_transactions(conn, universe)
    if inputs.closes.empty or labelled.empty:
        print("no panel — run `make wifey-insider-backfill` first")
        return
    print(population_line(labelled))
    print(
        f"priced: half-spread {model.half_spread_bps} bps by ADV bucket "
        f"{model.adv_thresholds}, impact_coef {model.impact_coef:g}, "
        f"borrow {model.borrow_rate_annual:.2%}/yr, ADV window "
        f"{model.adv_window_days:g}d (shared base)"
    )

    for tag, priced in (("GROSS", zero_cost(model)), ("NET", model)):
        rep = run(
            conn,
            symbols=symbols,
            cfg=cfg,
            inputs=inputs,
            labelled=labelled,
            cost=priced,
        )
        print(f"\n=== H-024 trial family — {tag} ===")
        print(
            cell_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.3f}")
        )
        print(f"\n--- reversal observable ({tag}) ---")
        print(
            paired_frame(rep).to_string(index=False, float_format=lambda x: f"{x:+.1f}")
        )
        verdict = "PASS" if rep.passed else "FAIL"
        print(f"\nprimary cell {rep.primary_key} @ {tag}: {verdict}")
        print(f"  realized β (construction guardrail): {rep.realized_beta:+.3f}")
        for key, is_signal in rep.long_only_signal.items():
            reads = "signal" if is_signal else "MARKET BETA, not edge"
            print(
                f"  long-only {key}: hedged alpha t "
                f"{rep.attribution[key].alpha_tstat:+.2f} → {reads}"
            )
        primary_pair = rep.paired.get(rep.primary_key)
        if primary_pair is not None and not primary_pair.measurable:
            print(
                "  ⚠ reversal observable UNMEASURABLE: neither arm was ever "
                "funded — this is not evidence that the arms agree"
            )
        elif rep.reversal_fires:
            print(
                "  ⚠ reversal observable FIRES: the primary trial is "
                "indistinguishable from its routine placebo"
            )

    print(
        "\nGate (pre-registered, primary = T1 opportunistic L/S 1-month VW): "
        "DSR>=0.95 ∧ PBO<=0.5 ∧ boot_lo>0 ∧ Sharpe>=0.7, DSR deflated at the "
        "4-trial family. Placebos are CONTROLS and are not in that family. "
        "Weights are trailing-dollar-ADV (Amendment 4: no market-cap series "
        "exists here — a liquidity weight, not a size weight). Long-only cells "
        "are not market-neutral: a pass counts as signal only on a positive "
        "beta-hedged alpha t. Survivorship runs in OPPOSITE directions by leg — "
        "buys flattered, sells understated — so a long-only pass is an upper "
        "bound. Costs are MODELLED, not realised."
    )


if __name__ == "__main__":
    main()
