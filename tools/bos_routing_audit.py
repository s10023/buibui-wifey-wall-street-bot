"""bos Routing Audit — T2a probe (from docs/system-overview.md §6).

Re-segments the existing `backtest_trades` history for the `bos` strategy
across the full proposed SignalCandidate axes:

    timeframe × regime × session × volume_state × htf_alignment × direction

Goal: answer "is there ANY (n>=30) cell where `bos` is net-positive?"

This is a full-sample probe (not WFO). If positive cells emerge, the next
step is WFO-validated confirmation. If none emerge, treat as strong
demote/cut signal for `bos`.

Reuses production helpers so cell labels match the live gate semantics:
- regime via analytics.regime.classify_series (trade-TF candle)
- volume_state via analytics.backtest.gates._is_low_volume / _is_volume_spike
- htf_alignment via 4h EMA-50 slope (matches [bias.htf_ema] default anchor)

Usage:
    PYTHONPATH=. poetry run python tools/bos_routing_audit.py
    PYTHONPATH=. poetry run python tools/bos_routing_audit.py --db PATH --out PATH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.backtest.gates import _is_low_volume, _is_volume_spike
from analytics.regime import classify_series
from analytics.store import DEFAULT_DB_PATH

_MIN_N = 30  # cells below this n are reported but flagged low-confidence
_POSITIVE_BAR = 0.03  # avg_r threshold for "positive cell" (Gemini's bar)
_HTF_TF = "4h"  # F8 default anchor
_HTF_PERIOD = 50
_HTF_SLOPE_LOOKBACK = 10
_HTF_DEADBAND = 0.003  # matches [bias.htf_ema].deadband_pct

_SESSION_BUCKETS: list[tuple[int, int, str]] = [
    (0, 8, "asia"),
    (8, 13, "london"),
    (13, 22, "ny"),
    (22, 24, "off"),
]


def _session_bucket(ts_ms: int) -> str:
    hour = (ts_ms // 3_600_000) % 24
    for start, end, label in _SESSION_BUCKETS:
        if start <= hour < end:
            return label
    return "off"


def _load_bos_trades(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute("""
        SELECT
            t.symbol,
            t.timeframe,
            t.signal_time,
            t.direction,
            t.outcome,
            t.pnl_r
        FROM backtest_trades t
        WHERE t.strategy = 'bos'
          AND t.outcome != 'open'
          AND t.pnl_r IS NOT NULL
    """).df()


def _load_ohlcv(conn: duckdb.DuckDBPyConnection, symbol: str, tf: str) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT open_time, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ?
        ORDER BY open_time
        """,
        [symbol, tf],
    ).df()


def _compute_htf_slope_series(
    closes: pd.Series,
    period: int = _HTF_PERIOD,
    slope_lookback: int = _HTF_SLOPE_LOOKBACK,
) -> pd.Series:
    """Per-candle 4h EMA-50 slope. Matches compute_htf_ema_slope() but rolling."""
    ema = closes.ewm(span=period, adjust=False).mean()
    ema_lagged = ema.shift(slope_lookback)
    slope = (ema - ema_lagged) / ema_lagged
    return slope


def _classify_htf_alignment(slope: float | None, direction: str) -> str:
    if slope is None or pd.isna(slope):
        return "unknown"
    if abs(slope) < _HTF_DEADBAND:
        return "neutral"
    slope_dir = "long" if slope > 0 else "short"
    return "aligned" if slope_dir == direction else "counter"


def _annotate(trades: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Add regime, session, volume_state, htf_alignment columns."""
    out_parts: list[pd.DataFrame] = []

    # Cache 4h OHLCV + slope per symbol (reused across timeframes).
    htf_slope_cache: dict[str, pd.DataFrame] = {}
    for symbol in trades["symbol"].unique():
        htf = _load_ohlcv(conn, str(symbol), _HTF_TF)
        if htf.empty:
            continue
        htf = htf.copy()
        htf["slope"] = _compute_htf_slope_series(htf["close"])
        htf_slope_cache[str(symbol)] = htf[["open_time", "slope"]].sort_values(
            "open_time"
        )

    for (symbol, tf), group in trades.groupby(["symbol", "timeframe"], sort=False):
        symbol_s, tf_s = str(symbol), str(tf)
        ohlcv = _load_ohlcv(conn, symbol_s, tf_s)
        if ohlcv.empty:
            g = group.copy()
            g["regime"] = "unknown"
            g["volume_state"] = "unknown"
            g["htf_alignment"] = "unknown"
            g["session"] = g["signal_time"].apply(_session_bucket)
            out_parts.append(g)
            continue

        regime_series = classify_series(ohlcv, tf_s)
        regime_lookup = pd.Series(regime_series.values, index=ohlcv["open_time"].values)
        idx_lookup = pd.Series(range(len(ohlcv)), index=ohlcv["open_time"].values)

        g = group.copy()
        g["regime"] = g["signal_time"].map(regime_lookup).fillna("unknown")
        g["session"] = g["signal_time"].apply(_session_bucket)

        # Volume state — uses the production classifier on each trade's host candle.
        def _vol_state(
            ts: int,
            _ohlcv: pd.DataFrame = ohlcv,
            _idx_lookup: pd.Series = idx_lookup,
        ) -> str:
            idx_val = _idx_lookup.get(ts)
            if idx_val is None or pd.isna(idx_val):
                return "unknown"
            idx_i = int(idx_val)
            if _is_volume_spike(_ohlcv, idx_i):
                return "spike"
            if _is_low_volume(_ohlcv, idx_i):
                return "low"
            return "normal"

        g["volume_state"] = g["signal_time"].apply(_vol_state)

        # HTF alignment — 4h EMA-50 slope at the latest closed 4h candle before signal_time.
        htf_cached: pd.DataFrame | None = htf_slope_cache.get(symbol_s)
        if htf_cached is None or htf_cached.empty:
            g["htf_alignment"] = "unknown"
        else:
            htf_times = htf_cached["open_time"].to_numpy()
            htf_slopes = htf_cached["slope"].to_numpy()

            def _htf(
                ts: int,
                dir_: str,
                _times: np.ndarray = htf_times,
                _slopes: np.ndarray = htf_slopes,
            ) -> str:
                pos = int(_times.searchsorted(ts, side="right")) - 1
                if pos < 0:
                    return "unknown"
                slope = float(_slopes[pos])
                return _classify_htf_alignment(slope, dir_)

            g["htf_alignment"] = [
                _htf(ts, d)
                for ts, d in zip(g["signal_time"], g["direction"], strict=False)
            ]

        out_parts.append(g)

    return pd.concat(out_parts, ignore_index=True)


def _aggregate(trades: pd.DataFrame) -> pd.DataFrame:
    grouped = trades.groupby(
        [
            "timeframe",
            "regime",
            "session",
            "volume_state",
            "htf_alignment",
            "direction",
        ],
        sort=False,
        observed=True,
    )
    agg = grouped.agg(
        n_trades=("pnl_r", "size"),
        avg_r=("pnl_r", "mean"),
        median_r=("pnl_r", "median"),
        win_rate=("outcome", lambda s: (s == "win").mean()),
        total_r=("pnl_r", "sum"),
    ).reset_index()
    agg["expectancy_R"] = agg["avg_r"] * agg["win_rate"]
    agg["positive_cell"] = (agg["avg_r"] > _POSITIVE_BAR) & (agg["n_trades"] >= _MIN_N)
    agg["low_confidence"] = agg["n_trades"] < _MIN_N
    return agg.sort_values("avg_r", ascending=False).reset_index(drop=True)


def _print_summary(agg: pd.DataFrame) -> None:
    total_trades = int(agg["n_trades"].sum())
    overall_avg_r = (
        float((agg["avg_r"] * agg["n_trades"]).sum() / total_trades)
        if total_trades
        else 0.0
    )
    print()
    print("=" * 80)
    print(f"bos routing audit — {total_trades:,} trades")
    print(f"Overall weighted avg_r: {overall_avg_r:+.4f}R")
    print("=" * 80)

    positives = agg[agg["positive_cell"]].copy()
    print(
        f"\n>>> POSITIVE CELLS (n >= {_MIN_N}, avg_r > +{_POSITIVE_BAR:.3f}): "
        f"{len(positives)} of {len(agg)} total cells"
    )
    if positives.empty:
        print(
            "    NONE. bos shows no net-positive cell across the routing axes tested."
        )
    else:
        cols = [
            "timeframe",
            "regime",
            "session",
            "volume_state",
            "htf_alignment",
            "direction",
            "n_trades",
            "avg_r",
            "win_rate",
            "total_r",
        ]
        with pd.option_context("display.max_rows", None, "display.width", 200):
            print(
                positives[cols].to_string(
                    index=False, float_format=lambda x: f"{x:+.4f}"
                )
            )

    high_conf = agg[~agg["low_confidence"]].copy()
    print(f"\n>>> TOP 15 HIGH-CONFIDENCE CELLS (n >= {_MIN_N}), by avg_r:")
    cols = [
        "timeframe",
        "regime",
        "session",
        "volume_state",
        "htf_alignment",
        "direction",
        "n_trades",
        "avg_r",
        "win_rate",
    ]
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(
            high_conf.head(15)[cols].to_string(
                index=False, float_format=lambda x: f"{x:+.4f}"
            )
        )

    print(f"\n>>> BOTTOM 10 HIGH-CONFIDENCE CELLS (n >= {_MIN_N}), by avg_r:")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(
            high_conf.tail(10)[cols].to_string(
                index=False, float_format=lambda x: f"{x:+.4f}"
            )
        )

    # Per-axis aggregates — useful for "is the issue regime, session, vol, or htf?"
    print(
        "\n>>> PER-AXIS WEIGHTED AVG_R (sanity check — which axis discriminates most?)"
    )
    for axis in (
        "regime",
        "session",
        "volume_state",
        "htf_alignment",
        "direction",
        "timeframe",
    ):
        rows: list[dict[str, object]] = []
        for bucket, d in agg.groupby(axis):
            total_n = int(d["n_trades"].sum())
            weighted = (
                float((d["avg_r"] * d["n_trades"]).sum() / total_n) if total_n else 0.0
            )
            rows.append({"bucket": str(bucket), "n": total_n, "avg_r": weighted})
        per = (
            pd.DataFrame(rows).set_index("bucket").sort_values("avg_r", ascending=False)
        )
        print(f"\n  By {axis}:")
        print(
            per.to_string(
                float_format=lambda x: (
                    f"{x:+.4f}" if isinstance(x, float) else f"{int(x):,}"
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path, default=Path("/tmp/bos_routing_audit.csv"))
    args = p.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: db not found at {args.db}", file=sys.stderr)
        return 1

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        trades = _load_bos_trades(conn)
        if trades.empty:
            print("No bos trades found in backtest_trades.", file=sys.stderr)
            return 1
        print(f"Loaded {len(trades):,} bos trades.", file=sys.stderr)
        annotated = _annotate(trades, conn)
        agg = _aggregate(annotated)
        agg.to_csv(args.out, index=False)
        print(f"Wrote {len(agg)} cells to {args.out}", file=sys.stderr)
        _print_summary(agg)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
