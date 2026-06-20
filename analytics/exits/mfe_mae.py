"""Per-alert MFE/MAE excursion study over the live outcome ledger (exit spec §2).

For every resolved `signal_alert_outcomes` row (win / loss / expired), walk
the OHLCV bars the trade actually held — strictly after the signal candle
(`candle_ts_ms`) up to and including the exit bar (`outcome_filled_at_ms`,
as resolved by `analytics/signal/outcome_backfill.py`) — and record, in R
units (÷ |entry − sl|):

  - mfe_r: max favorable excursion (best unrealized R reached, floored at 0)
  - mae_r: max adverse excursion (worst unrealized R, positive magnitude,
    floored at 0)

Conservative intrabar conventions (anti-bias, exit spec §4):

  - loss exit bar: its favorable extreme does NOT count toward MFE — no way
    to know the favorable wick printed before the stop touch (adverse-first,
    mirrors `_scan_forward`'s same-bar tie rule).
  - win exit bar: MFE clamps to max(prior-bar MFE, rr_ratio) — post-TP
    overshoot is not credited; the exit bar's adverse extreme DOES count
    toward MAE (assume it printed before TP).
  - expired: both extremes of every in-window bar count.

Excursions are GROSS of costs — price-path geometry for exit design; net
realized PnL (fee/slippage) already lives in `outcome_r`.

Pure functions over a DuckDB conn / DataFrames; no clock or network I/O.
"""

import duckdb
import numpy as np
import pandas as pd

from analytics.store.market_data import get_ohlcv

EXCURSION_COLUMNS = [
    "signal_id",
    "symbol",
    "tf",
    "strategy",
    "direction",
    "outcome",
    "outcome_r",
    "rr_ratio",
    "mfe_r",
    "mae_r",
    "bars_held",
]


def _excursion_for_row(
    window: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    rr_ratio: float,
    outcome: str,
) -> tuple[float, float] | None:
    """(mfe_r, mae_r) for one resolved alert over its held window.

    `window` holds the bars strictly after the signal candle up to and
    including the exit bar, in time order. Returns None when the window is
    empty or risk is zero (excursions in R are undefined).
    """
    if window.empty:
        return None
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return None
    high = window["high"].to_numpy(dtype=np.float64)
    low = window["low"].to_numpy(dtype=np.float64)
    if direction == "long":
        fav = (high - entry) / risk
        adv = (entry - low) / risk
    else:
        fav = (entry - low) / risk
        adv = (high - entry) / risk

    prior_fav = float(fav[:-1].max()) if len(fav) > 1 else 0.0
    if outcome == "loss":
        mfe = prior_fav
    elif outcome == "win":
        mfe = max(prior_fav, float(rr_ratio))
    else:  # expired — no intrabar exit event; every extreme was reachable
        mfe = float(fav.max())
    mae = float(adv.max())
    return max(mfe, 0.0), max(mae, 0.0)


def compute_excursions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-alert MFE/MAE rows for every resolved ledger row (EXCURSION_COLUMNS).

    Groups rows by (symbol, tf) so each group's OHLCV is fetched once — the
    same batching shape as `backfill_outcomes`. Rows with zero risk, an empty
    held window, or missing OHLCV are dropped; the caller can diff
    len(result) against the resolved count for coverage.
    """
    rows = conn.execute(
        "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
        "entry_price, sl_price, rr_ratio, outcome, outcome_r, "
        "outcome_filled_at_ms "
        "FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired') "
        "AND candle_ts_ms IS NOT NULL "
        "AND entry_price IS NOT NULL "
        "AND sl_price IS NOT NULL "
        "AND rr_ratio IS NOT NULL "
        "AND outcome_filled_at_ms IS NOT NULL"
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=EXCURSION_COLUMNS)

    by_group: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_group.setdefault((str(r[1]), str(r[2])), []).append(r)

    out: list[dict[str, object]] = []
    for (symbol, tf), grp in by_group.items():
        start = min(int(r[5]) for r in grp)
        end = max(int(r[11]) for r in grp)
        bars = get_ohlcv(conn, symbol, tf, start, end)
        if bars.empty:
            continue
        open_time = bars["open_time"].to_numpy(dtype=np.int64)
        for (
            signal_id,
            _sym,
            _tf,
            strategy,
            direction,
            candle_ts_ms,
            entry_price,
            sl_price,
            rr_ratio,
            outcome,
            outcome_r,
            filled_at_ms,
        ) in grp:
            lo_i = int(np.searchsorted(open_time, int(candle_ts_ms), side="right"))
            hi_i = int(np.searchsorted(open_time, int(filled_at_ms), side="right"))
            exc = _excursion_for_row(
                bars.iloc[lo_i:hi_i],
                direction=str(direction),
                entry=float(entry_price),
                sl_price=float(sl_price),
                rr_ratio=float(rr_ratio),
                outcome=str(outcome),
            )
            if exc is None:
                continue
            mfe_r, mae_r = exc
            out.append(
                {
                    "signal_id": str(signal_id),
                    "symbol": symbol,
                    "tf": tf,
                    "strategy": str(strategy),
                    "direction": str(direction),
                    "outcome": str(outcome),
                    "outcome_r": float(outcome_r)
                    if outcome_r is not None
                    else float("nan"),
                    "rr_ratio": float(rr_ratio),
                    "mfe_r": mfe_r,
                    "mae_r": mae_r,
                    "bars_held": hi_i - lo_i,
                }
            )
    return pd.DataFrame(out, columns=EXCURSION_COLUMNS)


def aggregate_cohorts(
    excursions: pd.DataFrame,
    *,
    by: tuple[str, ...] = ("strategy", "tf", "direction"),
    min_n: int = 30,
) -> pd.DataFrame:
    """Cohort-level MFE/MAE aggregation — the exit spec §2 table.

    Groups by (outcome, *by); pass by=() for the overall per-cohort roll-up.
    Columns map onto the spec's 4-pattern verdict grid: reach_05 / reach_10
    are the share of the cohort whose MFE hit ≥0.5R / ≥1.0R, and tp_r_p50 is
    the target those trades were asked to reach. Cells below min_n are
    dropped (diagnostic n-floor).
    """
    if excursions.empty:
        return pd.DataFrame()
    keys = ["outcome", *by]
    enriched = excursions.assign(
        reach_05=(excursions["mfe_r"] >= 0.5).astype(float),
        reach_10=(excursions["mfe_r"] >= 1.0).astype(float),
    )
    agg = (
        enriched.groupby(keys)
        .agg(
            n=("mfe_r", "size"),
            mfe_mean=("mfe_r", "mean"),
            mfe_p50=("mfe_r", "median"),
            mae_mean=("mae_r", "mean"),
            mae_p50=("mae_r", "median"),
            reach_05=("reach_05", "mean"),
            reach_10=("reach_10", "mean"),
            tp_r_p50=("rr_ratio", "median"),
            bars_held_p50=("bars_held", "median"),
            outcome_r_mean=("outcome_r", "mean"),
        )
        .reset_index()
    )
    agg = agg[agg["n"] >= min_n]
    return agg.sort_values(keys).reset_index(drop=True)
