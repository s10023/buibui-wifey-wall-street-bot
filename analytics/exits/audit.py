"""Re-resolve the live ledger under exit policies and A/B them (exit spec §4–§5).

For each resolved alert this walks the forward OHLCV window (entry → entry +
`max_hold_bars`) under a policy via `replay_exits`, and scores the resulting
per-trade R series. Every arm sees identical entries and stops — only exit
management differs, and the runner targets the alert's own EFFECTIVE R target
(see `effective_tp_r`) — so the comparison is apples-to-apples. With that target
the `fixed` arm reproduces the live resolver's label on **267 of 267** rows.

THE METRIC SUBSTITUTION (the reason this port sat backlogged; parent PR #437)
---------------------------------------------------------------------------
Upstream judged exits on **portfolio Sharpe** from its P1 `PaperBook` — a daily
equity curve under fixed-fractional sizing and concurrency caps. This fork has
no `portfolio/` package, so that headline is unavailable. The substitution:

  * **Headline = per-trade R Sharpe**, `stats_overfit.sharpe_ratio` over the
    realized-R series — the same statistic the four equity sleeves use. It is
    **deliberately not annualized**: the ledger mixes `4h` and `1d` alerts at
    irregular spacing with no equity curve, so any `sqrt(N)` factor would be
    invented rather than measured.
  * **The decisive leg is a PAIRED bootstrap CI on the per-alert R uplift** —
    `research_guards.block_bootstrap_ci` with `stat_fn=mean` over
    `d_i = r_policy_i − r_fixed_i`. Every arm re-resolves *the same* alerts, so
    pairing removes alert-level variance; a Sharpe delta alone is a point
    estimate, and a point estimate is a coin flip with extra steps (#152).
  * **DSR is a reported STAMP, not a leg** — `research_guards.dsr` with the arms
    themselves as `trial_srs`, matching the four sleeves' call shape. It inherits
    their normal higher-moment defaults, which a per-trade R series (point mass
    at exactly −1R, long right tail) violates; that is tolerable for a stamp and
    would not be for a leg, which is the other half of why it is not one.
  * **`passes_sleeve_gate` is deliberately NOT called**, for two reasons that
    are both live footguns here. Its `sharpe_annual` leg expects an annualized
    sleeve Sharpe; feeding it a per-trade Sharpe would quote `GATE_SHARPE` as
    "our bar" while measuring a different quantity — a gate is its full leg set,
    not the constant with a name (#165). And its `pbo` leg needs a CSCV trial
    matrix; with a handful of named arms CSCV returns NaN, which `passes_gate`
    turns into an automatic fail, i.e. a guard that can never pass (#166's
    mirror). The arms below are named hypotheses, not a swept grid.

Upstream's portfolio headline was dominated by its caps (~90% of alerts were
cap-skipped), which is why it could not see the population-level exit effect it
measured as real. With no book, this fork measures **exactly that population
effect** — the substitution is not a degraded copy of the parent's question, it
is the half of it the parent could not reach.

Per-tf `max_hold_bars` is `DEFAULT_MAX_HOLD_BARS` — the live resolver's own
window, reused rather than redeclared. The time-stop floors are RE-DERIVED for
equities (`docs/audits/2026-08-14-mfe-timing.md`); upstream's are crypto.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np

from analytics.backtest.stats_overfit import sharpe_ratio
from analytics.exits.policies import ExitPolicyConfig, composite, fixed
from analytics.exits.replay import replay_exits
from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.dsr import deflated_sharpe_ratio
from analytics.signal.outcome_backfill import DEFAULT_MAX_HOLD_BARS, effective_tp_r
from analytics.store.market_data import get_latest_open_time, get_ohlcv

TIME_STOP_FLOOR_BY_TF: dict[str, int] = {
    # Winner bars-to-first-+1R p90 — the level below which a time-stop clips
    # real winners. Re-derived 2026-08-14 on 45 wins (4h n=33 p90=4.0, max=16;
    # 1d n=12 p90=2.9, max=4); see docs/audits/2026-08-14-mfe-timing.md.
    # Upstream's crypto floors were 15m 65 / 1h 17 / 4h 7 / 1d 3.
    "4h": 4,
    "1d": 3,
}

POLICY_KINDS: tuple[str, ...] = ("fixed", "time_only", "be_partial", "composite")
"""Baseline first, then the two levers in isolation, then both together.

The two middle arms are a LEVER DECOMPOSITION, not a swept grid: without them
`composite` reports one number and cannot say which half earned it — and on this
ledger the two halves target different cohorts (a time-stop bites the expired
cohort, which is 9.0% here vs upstream's 39%, while breakeven/partial bites the
44% of `4h` losses that touched +1R first).
"""

_LEDGER_SQL = (
    "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
    "       entry_price, sl_price, rr_ratio, outcome, tp_price "
    "FROM signal_alert_outcomes "
    "WHERE outcome IN ('win', 'loss', 'expired') AND candle_ts_ms IS NOT NULL "
    "  AND entry_price IS NOT NULL AND sl_price IS NOT NULL "
    "  AND rr_ratio IS NOT NULL "
    "ORDER BY candle_ts_ms"
)


# `effective_tp_r` now lives in `analytics.signal.outcome_backfill` — its single
# definition — and is imported above. It moved there when the live resolver was
# fixed to credit the target it walks: the replay derived the effective R
# read-side while the ledger still recorded the declared one, and two copies of
# that conversion is exactly the divergence #165 warns about. The import keeps
# `analytics.exits.audit.effective_tp_r` resolving for existing callers.


def _policy_for(
    kind: str,
    *,
    tf: str,
    rr: float,
    max_hold_by_tf: dict[str, int],
    time_stop_by_tf: dict[str, int],
) -> ExitPolicyConfig | None:
    """Build the per-alert policy (tp_r = the alert's own `rr_ratio`).

    Returns None for a timeframe with no declared hold window — the same refusal
    `_resolve_max_hold` makes, rather than guessing a cap.
    """
    mh = max_hold_by_tf.get(tf)
    if mh is None:
        return None
    if kind == "fixed":
        return fixed(tp_r=rr, max_hold_bars=mh)
    if kind == "time_only":
        # Time-stop alone: no breakeven, no partial. `ExitPolicyConfig` treats
        # `breakeven_arm_r=None` + `partial_frac=0.0` as disabled, so this is
        # `fixed` with an earlier expiry — the other half of the decomposition.
        ts = min(time_stop_by_tf.get(tf, mh), mh)
        return ExitPolicyConfig(
            name="time_only", tp_r=rr, max_hold_bars=mh, time_stop_bars=ts
        )
    if kind == "be_partial":
        # Breakeven + partial only: the time-stop is pinned at the full window,
        # so this arm isolates the lock levers from the time lever.
        return composite(tp_r=rr, max_hold_bars=mh, time_stop_bars=mh)
    if kind == "composite":
        ts = min(time_stop_by_tf.get(tf, mh), mh)
        return composite(tp_r=rr, max_hold_bars=mh, time_stop_bars=ts)
    raise ValueError(f"unknown policy kind {kind!r}")


@dataclass(frozen=True)
class ReplayedTrade:
    """One alert re-resolved under a policy.

    Stands in for upstream's `portfolio.book.LedgerTrade`; carries only what the
    R-space verdict needs, since there is no book to size into.
    """

    signal_id: str
    symbol: str
    tf: str
    strategy: str
    direction: str
    entry_ts_ms: int
    exit_ts_ms: int
    entry_price: float
    sl_price: float
    outcome: str
    realized_r: float
    bars_held: int
    recorded_outcome: str


@dataclass(frozen=True)
class PolicyResult:
    """Re-resolved trades + per-trade cohort stats under one policy."""

    name: str
    trades: list[ReplayedTrade]
    n: int
    expiry_rate: float
    win_rate: float
    avg_hold_bars: float
    avg_r: float


def resolve_ledger_under_policy(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    *,
    max_hold_by_tf: dict[str, int] | None = None,
    time_stop_by_tf: dict[str, int] | None = None,
) -> PolicyResult:
    """Re-resolve every scoreable alert under `kind`."""
    max_hold = max_hold_by_tf if max_hold_by_tf is not None else DEFAULT_MAX_HOLD_BARS
    time_stop = (
        time_stop_by_tf if time_stop_by_tf is not None else TIME_STOP_FLOOR_BY_TF
    )

    rows = conn.execute(_LEDGER_SQL).fetchall()
    by_group: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_group.setdefault((str(r[1]), str(r[2])), []).append(r)

    trades: list[ReplayedTrade] = []
    holds: list[int] = []
    rs: list[float] = []
    n_exp = 0
    n_win = 0

    for (sym, tf), grp in by_group.items():
        mh = max_hold.get(tf)
        if mh is None:
            continue
        start = min(int(g[5]) for g in grp)
        # Fetch to the END OF THE SERIES rather than deriving a calendar span
        # from a bar count. Upstream used `max(candle_ts) + (mh + 2) * tf_ms`,
        # which is exact on a 24/7 crypto tape and covers **0.0%** of real
        # equity windows here: RTH gaps mean 30 `4h` bars span ~132 `4h` units
        # of wall-clock (p95 150), and 14 `1d` bars span ~20 (p95 22). The
        # truncation silently marked would-be winners to market at the last
        # fetched bar, and it biased the A/B — a short window cannot touch a
        # policy whose time-stop fires at bar 3–4, only the long-held baseline.
        # Removing the trap by construction beats re-tuning the literal.
        end = get_latest_open_time(conn, sym, tf)
        if end is None:
            continue
        bars = get_ohlcv(conn, sym, tf, start, end)
        if bars.empty:
            continue
        ot = bars["open_time"].to_numpy(dtype=np.int64)
        hi = bars["high"].to_numpy(dtype=np.float64)
        lo = bars["low"].to_numpy(dtype=np.float64)
        cl = bars["close"].to_numpy(dtype=np.float64)

        for sid, _s, _t, strat, direction, cts, entry, sl, rr, rec, tp in grp:
            pol = _policy_for(
                kind,
                tf=tf,
                rr=effective_tp_r(
                    direction=str(direction),
                    entry=float(entry),
                    sl_price=float(sl),
                    rr_ratio=float(rr),
                    tp_price=None if tp is None else float(tp),
                ),
                max_hold_by_tf=max_hold,
                time_stop_by_tf=time_stop,
            )
            if pol is None:
                continue
            a = int(np.searchsorted(ot, int(cts), side="right"))
            fwd_hi = hi[a : a + mh]
            fwd_lo = lo[a : a + mh]
            fwd_cl = cl[a : a + mh]
            fwd_ot = ot[a : a + mh]
            if len(fwd_hi) == 0 or abs(float(entry) - float(sl)) <= 0.0:
                continue
            eo = replay_exits(
                fwd_hi,
                fwd_lo,
                fwd_cl,
                direction=str(direction),
                entry=float(entry),
                sl_price=float(sl),
                policy=pol,
            )
            trades.append(
                ReplayedTrade(
                    signal_id=str(sid),
                    symbol=sym,
                    tf=tf,
                    strategy=str(strat),
                    direction=str(direction),
                    entry_ts_ms=int(cts),
                    exit_ts_ms=int(fwd_ot[eo.exit_bar]),
                    entry_price=float(entry),
                    sl_price=float(sl),
                    outcome=eo.outcome,
                    realized_r=eo.realized_r,
                    bars_held=eo.exit_bar + 1,
                    recorded_outcome=str(rec),
                )
            )
            holds.append(eo.exit_bar + 1)
            rs.append(eo.realized_r)
            n_exp += eo.outcome == "expired"
            n_win += eo.outcome == "win"

    n = len(trades)
    return PolicyResult(
        name=kind,
        trades=trades,
        n=n,
        expiry_rate=n_exp / n if n else 0.0,
        win_rate=n_win / n if n else 0.0,
        avg_hold_bars=float(np.mean(holds)) if holds else 0.0,
        avg_r=float(np.mean(rs)) if rs else 0.0,
    )


@dataclass(frozen=True)
class ExitAbRow:
    """One policy's line in the A/B table.

    `uplift*` and `d_sharpe` are measured against the `fixed` baseline over the
    PAIRED subset (alerts both arms resolved); they are 0.0 / None on the
    baseline row itself.
    """

    name: str
    n: int
    sharpe: float
    avg_r: float
    expiry_rate: float
    win_rate: float
    avg_hold_bars: float
    n_paired: int
    d_sharpe: float
    uplift: float
    uplift_ci: BootstrapCI | None
    dsr: float


def _paired_r(
    baseline: PolicyResult, arm: PolicyResult
) -> tuple[list[float], list[float]]:
    """Realized-R series for the alerts BOTH arms resolved, in a stable order."""
    base_by_id = {t.signal_id: t.realized_r for t in baseline.trades}
    arm_by_id = {t.signal_id: t.realized_r for t in arm.trades}
    shared = [t.signal_id for t in baseline.trades if t.signal_id in arm_by_id]
    return [base_by_id[s] for s in shared], [arm_by_id[s] for s in shared]


def run_exit_ab(
    conn: duckdb.DuckDBPyConnection,
    *,
    kinds: tuple[str, ...] = POLICY_KINDS,
    max_hold_by_tf: dict[str, int] | None = None,
    time_stop_by_tf: dict[str, int] | None = None,
    n_boot: int = 10_000,
    seed: int | None = 7,
) -> list[ExitAbRow]:
    """A/B each policy in R space against the first `kinds` entry as baseline."""
    if not kinds:
        return []
    results = {
        k: resolve_ledger_under_policy(
            conn,
            k,
            max_hold_by_tf=max_hold_by_tf,
            time_stop_by_tf=time_stop_by_tf,
        )
        for k in kinds
    }
    baseline = results[kinds[0]]
    # The ARMS are the trials: multiplicity here is "how many policies did we
    # look at", not a swept grid. Same call shape the four sleeves use.
    trial_srs = [
        sharpe_ratio([t.realized_r for t in results[k].trades])
        for k in kinds
        if len(results[k].trades) >= 2
    ]

    out: list[ExitAbRow] = []
    for kind in kinds:
        pr = results[kind]
        rs = [t.realized_r for t in pr.trades]
        sharpe = sharpe_ratio(rs) if len(rs) >= 2 else 0.0
        dsr = (
            deflated_sharpe_ratio(sharpe, len(rs), trial_srs=trial_srs)
            if len(rs) >= 2
            else float("nan")
        )

        base_r, arm_r = _paired_r(baseline, pr)
        n_paired = len(arm_r)
        ci: BootstrapCI | None = None
        d_sharpe = 0.0
        uplift = 0.0
        if kind != kinds[0] and n_paired >= 2:
            diffs = np.asarray(arm_r, dtype=np.float64) - np.asarray(
                base_r, dtype=np.float64
            )
            uplift = float(diffs.mean())
            d_sharpe = sharpe_ratio(arm_r) - sharpe_ratio(base_r)
            ci = block_bootstrap_ci(
                diffs,
                lambda a: float(np.mean(a)),
                n_boot=n_boot,
                seed=seed,
            )

        out.append(
            ExitAbRow(
                name=kind,
                n=pr.n,
                sharpe=sharpe,
                avg_r=pr.avg_r,
                expiry_rate=pr.expiry_rate,
                win_rate=pr.win_rate,
                avg_hold_bars=pr.avg_hold_bars,
                n_paired=n_paired,
                d_sharpe=d_sharpe,
                uplift=uplift,
                uplift_ci=ci,
                dsr=dsr,
            )
        )
    return out


def baseline_agreement(result: PolicyResult) -> tuple[int, int]:
    """(agreements, n) between a replayed outcome and the RECORDED ledger label.

    A positive control on the ported engine: the `fixed` arm re-implements what
    the live resolver already did, so a low agreement rate means the replay is
    not reproducing production rather than that exits are interesting. It is
    reported, never asserted — the conventions differ deliberately (adverse-first
    same-bar ties, mark-to-market at the time-stop), so exact equality is the
    wrong bar.
    """
    agree = sum(t.outcome == t.recorded_outcome for t in result.trades)
    return agree, len(result.trades)
