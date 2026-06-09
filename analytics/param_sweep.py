"""Parameter sweep with Walk-Forward Optimization (WFO).

Usage:
    poetry run python -m analytics.param_sweep \\
        --strategy bos --symbol BTCUSDT --tf 1h \\
        [--param tp_r=1.0:5.0:0.5] [--param sl_pct=0.005:0.03:0.005] \\
        [--wfo-split 0.7] [--min-trades 5] [--top-n 10] [--days 180] \\
        [--fee-pct 0.0005] [--db PATH]

Default param ranges (when --param not specified) come from STRATEGY_REGISTRY.ParamSpec
plus the two universal sweep axes: tp_r (1.0–5.0 step 0.5) and sl_pct (0.5%–3% step 0.25%).

Output: ranked table showing IS score, OOS score, OOS/IS decay ratio, and proposed TOML diff.
Configs with OOS avg_r < 0 or decay < 0.4 are flagged as overfit.
"""

from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pandas as pd

from analytics.backtest.stats_overfit import (
    OverfitStats,
    build_performance_matrix,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from analytics.backtest.stats_overfit import _moments as _r_moments
from analytics.backtest_lib import BacktestResult, run_backtest
from analytics.backtest_runner import detect_signals_for_strategy
from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv
from analytics.perf_timer import timed
from analytics.strategies import KNOWN_STRATEGIES, STRATEGY_REGISTRY

# Timeline buckets fed to CSCV when computing sweep-level PBO.
_PBO_MATRIX_ROWS = 100

if TYPE_CHECKING:
    from collections.abc import Mapping

    from analytics.backtest.live_parity_config import LiveParityConfig
    from analytics.signal_config import BiasConfig, StrategyOverride

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score(result: BacktestResult, min_trades: int) -> float:
    """Composite score: avg_r × win_rate × sqrt(closed_trades).

    Returns 0.0 when trade count is below min_trades (statistically unreliable).
    """
    closed = len(result.closed_trades)
    if closed < min_trades:
        return 0.0
    avg_r = result.avg_r
    if avg_r is None or avg_r <= 0:
        return 0.0
    return avg_r * result.win_rate * sqrt(closed)


# ---------------------------------------------------------------------------
# Param grid
# ---------------------------------------------------------------------------


@dataclass
class ParamRange:
    name: str
    values: list[float | int]


def _float_range(start: float, stop: float, step: float) -> list[float]:
    """Inclusive float range with rounding to avoid float drift."""
    result = []
    v = start
    while v <= stop + 1e-9:
        result.append(round(v, 8))
        v = round(v + step, 8)
    return result


def _parse_param_spec(spec: str) -> ParamRange:
    """Parse 'name=min:max:step' into a ParamRange."""
    if "=" not in spec:
        raise ValueError(f"Expected 'name=min:max:step', got: {spec!r}")
    name, rest = spec.split("=", 1)
    parts = rest.split(":")
    if len(parts) != 3:
        raise ValueError(f"Expected 'min:max:step' after '=', got: {rest!r}")
    mn, mx, st = float(parts[0]), float(parts[1]), float(parts[2])
    values = _float_range(mn, mx, st)
    if not values:
        raise ValueError(f"Empty range for param {name!r}: {spec!r}")
    return ParamRange(name=name, values=values)


def _default_param_ranges(strategy: str) -> list[ParamRange]:
    """Build default param ranges: tp_r × sl_pct only (the two highest-impact axes).

    Strategy-specific params (swing_n, lookback, etc.) explode the grid size and should
    be added explicitly via --param when you want to sweep them. Default keeps it fast:
    9 × 11 = 99 combos, typically finishes in seconds.
    """
    _ = strategy  # reserved for future strategy-specific defaults
    return [
        ParamRange("tp_r", _float_range(1.0, 5.0, 0.5)),
        ParamRange("sl_pct", _float_range(0.005, 0.03, 0.0025)),
    ]


def _strategy_param_ranges(strategy: str) -> list[ParamRange]:
    """Build ranges for strategy-specific params only (excludes tp_r/sl_pct).

    Use with --param-mode=full or append to default ranges manually.
    """
    ranges: list[ParamRange] = []
    spec = STRATEGY_REGISTRY.get(strategy)
    if spec is None:
        return ranges
    for ps in spec.params:
        if ps.name in ("tp_r", "sl_pct"):
            continue
        mn, mx = float(ps.min_val), float(ps.max_val)
        if ps.param_type == "int":
            int_step = max(1, int((mx - mn) / 8))
            values: list[float | int] = list(range(int(mn), int(mx) + 1, int_step))
        else:
            float_step = (mx - mn) / 8
            values = _float_range(mn, mx, float_step)
        if len(values) > 1:
            ranges.append(ParamRange(name=ps.name, values=values))
    return ranges


def _build_grid(ranges: list[ParamRange]) -> list[dict[str, Any]]:
    """Cartesian product of all param ranges → list of param dicts."""
    keys = [r.name for r in ranges]
    value_lists = [r.values for r in ranges]
    return [
        dict(zip(keys, combo, strict=False))
        for combo in itertools.product(*value_lists)
    ]


# ---------------------------------------------------------------------------
# WFO split
# ---------------------------------------------------------------------------


def _split_ohlcv(
    ohlcv: pd.DataFrame, split: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split OHLCV into in-sample and out-of-sample by row index."""
    n = len(ohlcv)
    split_idx = int(n * split)
    return ohlcv.iloc[:split_idx].copy(), ohlcv.iloc[split_idx:].copy()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SweepRow:
    params: dict[str, Any]
    is_result: BacktestResult
    oos_result: BacktestResult
    is_score: float
    oos_score: float
    decay: float  # oos_score / is_score; NaN when is_score == 0
    overfit: bool  # True when decay < 0.4 or OOS avg_r < 0
    overfit_stats: OverfitStats | None = None  # DSR/PBO (Phase 0.3a/b)

    @property
    def is_avg_r(self) -> float | None:
        return self.is_result.avg_r

    @property
    def oos_avg_r(self) -> float | None:
        return self.oos_result.avg_r

    @property
    def is_trades(self) -> int:
        return len(self.is_result.closed_trades)

    @property
    def oos_trades(self) -> int:
        return len(self.oos_result.closed_trades)

    @property
    def is_win_rate(self) -> float:
        return self.is_result.win_rate

    @property
    def oos_win_rate(self) -> float:
        return self.oos_result.win_rate

    # Gate 3 — directional OOS metrics (derived from oos_result, no extra compute)
    @property
    def long_oos_avg_r(self) -> float | None:
        return self.oos_result.long_avg_r

    @property
    def short_oos_avg_r(self) -> float | None:
        return self.oos_result.short_avg_r

    @property
    def long_oos_n(self) -> int:
        return len(self.oos_result.long_closed_trades)

    @property
    def short_oos_n(self) -> int:
        return len(self.oos_result.short_closed_trades)


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------


def _sweep_grid_worker(
    params: dict[str, Any],
    ohlcv_is: pd.DataFrame,
    signals_is: pd.DataFrame,
    ohlcv_oos: pd.DataFrame,
    signals_oos: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    fee_pct: float,
    is_min: int,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    *,
    live_parity: LiveParityConfig | None = None,
    bias_cfg: BiasConfig | None = None,
    regime_series: pd.Series | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
) -> SweepRow:
    """Single grid-combo backtest worker — module-level so ProcessPoolExecutor can pickle it.

    The live-parity inputs (``live_parity`` + ``bias_cfg`` + the pre-computed
    ``regime_series`` / ``htf_slope_series_by_anchor`` / ``strategy_params``) are
    forwarded verbatim to both the IS and OOS ``run_backtest`` calls so a WFO
    cell replays the live gate stack. They default to ``None`` (no-op), keeping
    legacy sweep behaviour byte-identical. The IS and OOS windows each apply the
    gates over their own OHLCV slice — regime / direction_filter / HTF-EMA
    resolve per-signal so they are unaffected by the split, while ADR (rolling
    14-day) and cooldown (per-call state) reset at the boundary, which is the
    intended WFO semantics (IS and OOS are independent samples).
    """
    tp_r = float(params.get("tp_r", 2.0))
    sl_pct = float(params.get("sl_pct", 0.02))
    bt_is = run_backtest(
        ohlcv_is,
        signals_is,
        symbol,
        timeframe,
        strategy,
        sl_pct=sl_pct,
        tp_r=tp_r,
        fee_pct=fee_pct,
        atr_sl_multiplier=atr_sl_multiplier,
        atr_sl_floor=atr_sl_floor,
        live_parity=live_parity,
        bias_cfg=bias_cfg,
        regime_series=regime_series,
        strategy_params=strategy_params,
        htf_slope_series_by_anchor=htf_slope_series_by_anchor,
    )
    bt_oos = run_backtest(
        ohlcv_oos,
        signals_oos,
        symbol,
        timeframe,
        strategy,
        sl_pct=sl_pct,
        tp_r=tp_r,
        fee_pct=fee_pct,
        atr_sl_multiplier=atr_sl_multiplier,
        atr_sl_floor=atr_sl_floor,
        live_parity=live_parity,
        bias_cfg=bias_cfg,
        regime_series=regime_series,
        strategy_params=strategy_params,
        htf_slope_series_by_anchor=htf_slope_series_by_anchor,
    )
    is_s = _score(bt_is, is_min)
    oos_s = _score(bt_oos, 1)
    decay = (oos_s / is_s) if is_s > 0 else float("nan")
    oos_avg_r = bt_oos.avg_r
    overfit = (oos_avg_r is None or oos_avg_r <= 0) or (
        not math.isnan(decay) and decay < 0.4
    )
    return SweepRow(
        params=params,
        is_result=bt_is,
        oos_result=bt_oos,
        is_score=is_s,
        oos_score=oos_s,
        decay=decay,
        overfit=overfit,
    )


def _closed_r(result: BacktestResult) -> list[float]:
    return [t.pnl_r for t in result.closed_trades if t.pnl_r is not None]


def _attach_overfit_stats(rows: list[SweepRow]) -> None:
    """Compute DSR per config + a sweep-level PBO over the full grid, in place.

    Per-config: IS/OOS per-trade Sharpe and a Deflated Sharpe that haircuts the
    IS Sharpe by the grid's trial count and cross-trial Sharpe dispersion.
    Sweep-level: one CSCV PBO over a time-bucketed full-history performance
    matrix (NaN when too few configs/trades to compute). Mutates each row's
    ``overfit_stats``.
    """
    if not rows:
        return
    n_trials = len(rows)
    is_sharpes = [sharpe_ratio(_closed_r(r.is_result)) for r in rows]
    # Build the PBO matrix in a deterministic column order (rows arrive in
    # ProcessPoolExecutor completion order, and configs with identical trades —
    # e.g. an inert tp_r under a structural SL — create exact CSCV argmax ties
    # whose tie-break is column-index sensitive). Sort by params so the reported
    # PBO is reproducible run-to-run.
    ordered = sorted(rows, key=lambda r: tuple(sorted(r.params.items())))
    trade_points = [
        [
            (t.entry_time, t.pnl_r)
            for t in (*r.is_result.closed_trades, *r.oos_result.closed_trades)
            if t.pnl_r is not None
        ]
        for r in ordered
    ]
    pbo = probability_of_backtest_overfitting(
        build_performance_matrix(trade_points, _PBO_MATRIX_ROWS)
    ).pbo
    for r, is_sr in zip(rows, is_sharpes, strict=True):
        is_r = _closed_r(r.is_result)
        _, _, skew, kurt = _r_moments(is_r)
        dsr = deflated_sharpe_ratio(is_sr, is_sharpes, len(is_r), skew, kurt)
        r.overfit_stats = OverfitStats(
            is_sharpe=is_sr,
            oos_sharpe=sharpe_ratio(_closed_r(r.oos_result)),
            deflated_sharpe=dsr,
            n_trials=n_trials,
            pbo=pbo,
        )


def run_param_sweep(
    conn: duckdb.DuckDBPyConnection,
    strategy: str,
    symbol: str,
    timeframe: str,
    days: int,
    param_ranges: list[ParamRange],
    wfo_split: float,
    min_trades: int,
    fee_pct: float,
    top_n: int,
    adr_suppress_threshold: float | None = None,
    since_ms: int | None = None,
    day_filter: str = "off",
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    *,
    live_parity: LiveParityConfig | None = None,
    bias_cfg: BiasConfig | None = None,
    regime_series: pd.Series | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
) -> list[SweepRow]:
    """Run WFO grid sweep. Returns rows sorted by IS score (descending).

    The live-parity inputs are threaded into every grid ``run_backtest`` call so
    each cell's trade counts reflect the live-filtered population (regime,
    direction_filter, F8 HTF-EMA, ADR bias, cooldown) instead of raw detector
    signals. They default to ``None`` — when omitted the sweep is byte-identical
    to the legacy raw-signal path. The cross-strategy ``conflict_resolver`` gate
    is *not* replayed here: this sweep is single-strategy, so there is no
    cross-strategy event pool for it to act on.

    ``regime_series`` / ``htf_slope_series_by_anchor`` are the pre-computed HTF
    series for ``symbol`` indexed by HTF ``open_time`` (built once by the caller
    via the backtest_runner helpers); the engine binary-searches them per signal.
    """
    end_ms = int(time.time() * 1000)
    start_ms = since_ms if since_ms is not None else end_ms - days * 24 * 3_600 * 1_000

    ohlcv_full = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
    if ohlcv_full.empty:
        print(
            f"  ERROR: No OHLCV data for {symbol}/{timeframe}. "
            "Run 'wifey analytics backfill' first.",
            file=sys.stderr,
        )
        return []

    ohlcv_is, ohlcv_oos = _split_ohlcv(ohlcv_full, wfo_split)

    # Detect signals once on full history, then split by candle timestamp.
    # This avoids running detection on a truncated window (which would miss
    # pivot points that need future candles for confirmation).
    signals_full = detect_signals_for_strategy(
        conn,
        ohlcv_full,
        symbol,
        timeframe,
        strategy,
        start_ms,
        end_ms,
    )
    if signals_full is None:
        print(
            f"  ERROR: Could not detect signals for {strategy} "
            f"(missing funding or secondary data).",
            file=sys.stderr,
        )
        return []

    if adr_suppress_threshold is not None and not signals_full.empty:
        from analytics.signal_lib import _filter_signals_by_adr

        signals_full = _filter_signals_by_adr(
            ohlcv_full, signals_full, adr_suppress_threshold
        )

    if day_filter != "off" and not signals_full.empty:
        from analytics.backtest_lib import filter_signals_by_day
        from analytics.signal_config import _day_filter_to_weekdays

        allowed_days = _day_filter_to_weekdays(day_filter)
        if allowed_days is not None:
            signals_full = filter_signals_by_day(signals_full, allowed_days)

    # Split signals at the same timestamp boundary as OHLCV.
    if not ohlcv_is.empty and not ohlcv_oos.empty:
        split_ts = int(ohlcv_oos.iloc[0]["open_time"])
        signals_is = signals_full[signals_full["open_time"] < split_ts].copy()
        signals_oos = signals_full[signals_full["open_time"] >= split_ts].copy()
    else:
        signals_is = signals_full.copy()
        signals_oos = pd.DataFrame()

    # Drop sl_pct from param_ranges if the strategy uses structural SL prices.
    # When sl_price is non-zero in signals, run_backtest ignores sl_pct entirely —
    # sweeping it wastes grid space and produces duplicate rows.
    uses_structural_sl = (
        "sl_price" in signals_full.columns and signals_full["sl_price"].ne(0).any()
    )
    if uses_structural_sl:
        param_ranges = [r for r in param_ranges if r.name != "sl_pct"]

    grid = _build_grid(param_ranges)
    n = len(grid)
    is_min = max(1, min_trades // 2)  # relax min_trades for IS scoring (more history)

    sl_note = (
        " (sl_pct dropped — strategy uses structural SLs)" if uses_structural_sl else ""
    )
    workers = max(1, min((os.cpu_count() or 2) - 1, n))
    print(f"\n  Sweep: {strategy} / {symbol} / {timeframe}{sl_note}")
    print(
        f"  Grid size: {n} combos | IS candles: {len(ohlcv_is)} | OOS candles: {len(ohlcv_oos)}"
        f" | workers: {workers}"
    )

    rows: list[SweepRow] = []
    with timed(f"grid ({n} combos)"):
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _sweep_grid_worker,
                    p,
                    ohlcv_is,
                    signals_is,
                    ohlcv_oos,
                    signals_oos,
                    symbol,
                    timeframe,
                    strategy,
                    fee_pct,
                    is_min,
                    atr_sl_multiplier,
                    atr_sl_floor,
                    live_parity=live_parity,
                    bias_cfg=bias_cfg,
                    regime_series=regime_series,
                    strategy_params=strategy_params,
                    htf_slope_series_by_anchor=htf_slope_series_by_anchor,
                ): p
                for p in grid
            }
            print("  Running...", end="", flush=True)
            for done, fut in enumerate(as_completed(futures), start=1):
                rows.append(fut.result())
                if done % max(1, n // 20) == 0:
                    print(".", end="", flush=True)
        print(" done")

    # --- Overfitting controls (Phase 0.3a/b) over the full grid -------------
    # Computed before the top_n truncation so the cross-trial Sharpe variance
    # and the PBO matrix see every trial, not just the survivors.
    _attach_overfit_stats(rows)

    # Primary sort: IS score (composite). Fallback: when all scores are 0 (every config
    # has negative avg_r), sort by IS avg_r directly so the "least bad" configs surface
    # rather than arbitrary grid-order entries.
    all_zero = all(r.is_score == 0.0 for r in rows)
    if all_zero:
        rows.sort(key=lambda r: r.is_avg_r or float("-inf"), reverse=True)
    else:
        rows.sort(key=lambda r: r.is_score, reverse=True)

    return rows[:top_n]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_SEP = "─"


def _fmt_r(v: float | None) -> str:
    if v is None:
        return "  —  "
    return f"{v:+.3f}R"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_decay(v: float) -> str:
    if math.isnan(v):
        return "  —  "
    return f"{v:.2f}"


def _directional_split_hint(row: SweepRow) -> str:
    """Return a recommendation line if long/short OOS avg_r diverge enough to warrant a split.

    Threshold: both directions have ≥ 3 OOS trades AND |long - short| ≥ 0.1R.
    A difference ≥ 0.5R between optimal tp_r per direction suggests using
    tp_r_long / tp_r_short in TOML instead of a single combined tp_r.
    """
    l_r = row.long_oos_avg_r
    s_r = row.short_oos_avg_r
    if l_r is None or s_r is None:
        return ""
    if row.long_oos_n < 3 or row.short_oos_n < 3:
        return ""
    delta = abs(l_r - s_r)
    if delta < 0.1:
        return ""
    worse = "long" if l_r < s_r else "short"
    return (
        f"  ↕ Directional gap: ↑{_fmt_r(l_r)} (n={row.long_oos_n})  "
        f"↓{_fmt_r(s_r)} (n={row.short_oos_n})  Δ={delta:.3f}R"
        f"  — consider tp_r_{worse} override"
    )


def format_sweep_results(
    rows: list[SweepRow],
    strategy: str,
    symbol: str,
    timeframe: str,
    current_toml: dict[str, Any] | None = None,
) -> str:
    if not rows:
        return "  No results."

    lines: list[str] = []
    lines.append(f"\n{'WFO Param Sweep':^100}")
    lines.append(f"{'Strategy':>12}: {strategy}   Symbol: {symbol}   TF: {timeframe}")
    lines.append(_SEP * 100)

    # Header — extended with directional OOS columns
    param_names = list(rows[0].params.keys())
    param_cols = "  ".join(f"{p[:8]:>8}" for p in param_names)
    lines.append(
        f"  {'#':>3}  {param_cols}  "
        f"{'IS avg_r':>8}  {'IS wr%':>6}  {'IS n':>4}  "
        f"{'OOS avg_r':>9}  {'↑OOS':>7}  {'↓OOS':>7}  {'OOS n':>5}  "
        f"{'decay':>6}  {'flag':>6}"
    )
    lines.append(_SEP * 100)

    for i, row in enumerate(rows, 1):
        param_vals = "  ".join(f"{row.params[p]:>8.4g}" for p in param_names)
        flag = "⚠ OVERFIT" if row.overfit else "  ok"
        lines.append(
            f"  {i:>3}  {param_vals}  "
            f"{_fmt_r(row.is_avg_r):>8}  {_fmt_pct(row.is_win_rate):>6}  {row.is_trades:>4}  "
            f"{_fmt_r(row.oos_avg_r):>9}  {_fmt_r(row.long_oos_avg_r):>7}  "
            f"{_fmt_r(row.short_oos_avg_r):>7}  {row.oos_trades:>5}  "
            f"{_fmt_decay(row.decay):>6}  {flag}"
        )

    lines.append(_SEP * 100)

    # Recommend top non-overfit config
    clean = [r for r in rows if not r.overfit]
    if clean:
        best = clean[0]
        lines.append("\n  Recommended config (best non-overfit):")
        for k, v in best.params.items():
            old = (current_toml or {}).get(k)
            arrow = f"  ← was {old}" if old is not None and old != v else ""
            lines.append(f"    {k} = {v}{arrow}")
        lines.append(
            f"\n  OOS: avg_r={_fmt_r(best.oos_avg_r)}  "
            f"win_rate={_fmt_pct(best.oos_win_rate)}  "
            f"trades={best.oos_trades}  "
            f"decay={_fmt_decay(best.decay)}"
        )
        stats = best.overfit_stats
        if stats is not None:
            # DSR is a probability (PSR vs the multiple-testing-deflated null),
            # not an R-multiple — render Sharpe as a plain ratio, DSR/PBO as %.
            pbo_str = "n/a" if math.isnan(stats.pbo) else _fmt_pct(stats.pbo)
            lines.append(
                f"  Overfit controls: trials N={stats.n_trials}  PBO={pbo_str}"
            )
            lines.append(
                f"  Recommended config: per-trade Sharpe={stats.is_sharpe:+.2f}  "
                f"Deflated Sharpe={_fmt_pct(stats.deflated_sharpe)} "
                "(P[edge real]; ≥95% = robust)"
            )
        hint = _directional_split_hint(best)
        if hint:
            lines.append(hint)
    else:
        all_negative = all((r.is_avg_r or 0.0) < 0 for r in rows)
        if all_negative:
            best_r = rows[0].is_avg_r
            lines.append(
                f"\n  ✗  Strategy has no positive edge on this symbol/TF "
                f"(best IS avg_r: {_fmt_r(best_r)}). "
                "Param tuning cannot fix a broken signal — fix the detector logic first."
            )
        else:
            lines.append(
                "\n  ⚠  All top configs failed OOS validation (decay < 0.4 or OOS avg_r < 0). "
                "Consider more history (--days) or narrower param ranges."
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch strategy audit
# ---------------------------------------------------------------------------

_AUDIT_TP_RANGE = ParamRange("tp_r", _float_range(1.0, 5.0, 0.5))  # 9 combos, fast


@dataclass
class AuditRow:
    strategy: str
    best_is_avg_r: float | None
    best_oos_avg_r: float | None
    best_tp_r: float
    oos_trades: int
    is_trades: int
    verdict: str  # "good" | "marginal" | "no_data" | "no_edge" | "skipped"
    skip_reason: str = ""
    # Gate 3 — directional OOS metrics at the best combined tp_r
    best_long_oos_avg_r: float | None = None
    best_short_oos_avg_r: float | None = None
    long_oos_n: int = 0
    short_oos_n: int = 0


def _audit_strategy_worker(
    strat: str,
    signals_is: pd.DataFrame,
    signals_oos: pd.DataFrame,
    ohlcv_is: pd.DataFrame,
    ohlcv_oos: pd.DataFrame,
    symbol: str,
    timeframe: str,
    tp_values: list[float | int],
    is_min: int,
    fee_pct: float,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
) -> AuditRow:
    """Per-strategy backtest grid worker — module-level so ProcessPoolExecutor can pickle it.

    Runs the 9-tp_r × IS+OOS grid for one strategy. All data passed explicitly
    (no closures) since child processes get a fresh interpreter with no shared state.
    """
    best_is: float | None = None
    best_oos: float | None = None
    best_tp = float(tp_values[0])
    best_oos_trades = 0
    best_is_trades = 0
    best_long_oos: float | None = None
    best_short_oos: float | None = None
    best_long_oos_n = 0
    best_short_oos_n = 0

    for tp_r in tp_values:
        tp = float(tp_r)
        bt_is = run_backtest(
            ohlcv_is,
            signals_is,
            symbol,
            timeframe,
            strat,
            sl_pct=0.02,
            tp_r=tp,
            fee_pct=fee_pct,
            atr_sl_multiplier=atr_sl_multiplier,
            atr_sl_floor=atr_sl_floor,
        )
        bt_oos = run_backtest(
            ohlcv_oos,
            signals_oos,
            symbol,
            timeframe,
            strat,
            sl_pct=0.02,
            tp_r=tp,
            fee_pct=fee_pct,
            atr_sl_multiplier=atr_sl_multiplier,
            atr_sl_floor=atr_sl_floor,
        )
        is_n = len(bt_is.closed_trades)
        is_r = bt_is.avg_r
        if is_n >= is_min and is_r is not None and (best_is is None or is_r > best_is):
            best_is = is_r
            best_tp = tp
            best_is_trades = is_n
            best_oos = bt_oos.avg_r
            best_oos_trades = len(bt_oos.closed_trades)
            best_long_oos = bt_oos.long_avg_r
            best_short_oos = bt_oos.short_avg_r
            best_long_oos_n = len(bt_oos.long_closed_trades)
            best_short_oos_n = len(bt_oos.short_closed_trades)

    if best_is is None:
        verdict = "no_data"
    elif best_is < 0:
        verdict = "no_edge"
    elif best_oos is None or best_oos_trades < 3:
        verdict = "no_data"
    elif best_oos > 0.2:
        verdict = "good"
    elif best_oos > 0:
        verdict = "marginal"
    else:
        verdict = "no_edge"

    return AuditRow(
        strategy=strat,
        best_is_avg_r=best_is,
        best_oos_avg_r=best_oos,
        best_tp_r=best_tp,
        oos_trades=best_oos_trades,
        is_trades=best_is_trades,
        verdict=verdict,
        best_long_oos_avg_r=best_long_oos,
        best_short_oos_avg_r=best_short_oos,
        long_oos_n=best_long_oos_n,
        short_oos_n=best_short_oos_n,
    )


def run_strategy_audit(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    days: int,
    strategies: list[str],
    wfo_split: float,
    min_trades: int,
    fee_pct: float,
    adr_suppress_threshold: float | None = None,
    since_ms: int | None = None,
    day_filter: str = "off",
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
) -> list[AuditRow]:
    """Quick tp_r sweep across all strategies — produces one verdict row per strategy."""
    end_ms = int(time.time() * 1000)
    start_ms = since_ms if since_ms is not None else end_ms - days * 24 * 3_600 * 1_000

    ohlcv_full = get_ohlcv(conn, symbol, timeframe, start_ms, end_ms)
    if ohlcv_full.empty:
        print(
            f"  ERROR: No OHLCV data for {symbol}/{timeframe}. "
            "Run 'wifey analytics backfill' first.",
            file=sys.stderr,
        )
        return []

    ohlcv_is, ohlcv_oos = _split_ohlcv(ohlcv_full, wfo_split)
    split_ts = int(ohlcv_oos.iloc[0]["open_time"]) if not ohlcv_oos.empty else 0

    is_min = max(1, min_trades // 2)
    tp_values = _AUDIT_TP_RANGE.values

    # Phase 1 — detect signals for every strategy sequentially (needs shared DB conn;
    # detection is fast for most strategies — pure pandas, no DB access).
    active_strategies = [s for s in strategies if s != "seasonality"]
    detected: list[tuple[str, pd.DataFrame | None]] = []
    print(f"  Phase 1: detecting signals for {len(active_strategies)} strategies...")

    if adr_suppress_threshold is not None:
        from analytics.signal_lib import _filter_signals_by_adr
    if day_filter != "off":
        from analytics.backtest_lib import filter_signals_by_day
        from analytics.signal_config import _day_filter_to_weekdays

        allowed_days = _day_filter_to_weekdays(day_filter)
    else:
        allowed_days = None

    with timed("signal detection"):
        for strategy in active_strategies:
            sigs = detect_signals_for_strategy(
                conn, ohlcv_full, symbol, timeframe, strategy, start_ms, end_ms
            )
            if (
                sigs is not None
                and adr_suppress_threshold is not None
                and not sigs.empty
            ):
                sigs = _filter_signals_by_adr(ohlcv_full, sigs, adr_suppress_threshold)
            if sigs is not None and allowed_days is not None and not sigs.empty:
                sigs = filter_signals_by_day(sigs, allowed_days)
            detected.append((strategy, sigs))

    # Phase 2 — run tp_r grid per strategy in parallel processes.
    # run_backtest is pure Python loops (Trade iteration) — GIL is never released,
    # so ThreadPoolExecutor gives no speedup. ProcessPoolExecutor spawns real OS
    # processes, each with its own GIL, giving true parallelism.
    # DataFrames (ohlcv_is/oos, signals_is/oos) are pickled once per strategy
    # submission — acceptable overhead given the backtest work saved.
    workers = max(1, min((os.cpu_count() or 2) - 1, len(active_strategies)))
    print(
        f"  Phase 2: backtesting {len(active_strategies)} strategies × {len(tp_values)} tp_r values | workers: {workers}"
    )

    # Build skipped rows immediately (no subprocess needed); queue real work for pool.
    rows: list[AuditRow] = []
    to_submit: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    for strat, sigs in detected:
        if sigs is None:
            rows.append(
                AuditRow(
                    strategy=strat,
                    best_is_avg_r=None,
                    best_oos_avg_r=None,
                    best_tp_r=0.0,
                    oos_trades=0,
                    is_trades=0,
                    verdict="skipped",
                    skip_reason="missing funding or secondary data",
                )
            )
        else:
            sigs_is = sigs[sigs["open_time"] < split_ts].copy()
            sigs_oos = sigs[sigs["open_time"] >= split_ts].copy()
            to_submit.append((strat, sigs_is, sigs_oos))

    tp_values_list = [float(v) for v in tp_values]
    with timed("backtest grid"), ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _audit_strategy_worker,
                strat,
                sigs_is,
                sigs_oos,
                ohlcv_is,
                ohlcv_oos,
                symbol,
                timeframe,
                tp_values_list,
                is_min,
                fee_pct,
                atr_sl_multiplier,
                atr_sl_floor,
            ): strat
            for strat, sigs_is, sigs_oos in to_submit
        }
        for fut in as_completed(futures):
            strat = futures[fut]
            row = fut.result()
            rows.append(row)
            verdict_label = (
                row.verdict
                if row.verdict != "skipped"
                else f"skipped ({row.skip_reason})"
            )
            print(f"  {strat}: {verdict_label}")

    # Sort: good → marginal → no_data → no_edge → skipped; within tier by OOS avg_r
    _order = {"good": 0, "marginal": 1, "no_data": 2, "no_edge": 3, "skipped": 4}
    rows.sort(
        key=lambda r: (_order.get(r.verdict, 5), -(r.best_oos_avg_r or float("-inf")))
    )
    return rows


def format_audit_results(
    rows: list[AuditRow],
    symbol: str,
    timeframe: str,
    days: int,
    window: str | None = None,
) -> str:
    if not rows:
        return "  No results."

    _VERDICT_LABEL = {
        "good": "✅ good",
        "marginal": "⚠  marginal",
        "no_data": "⚡ no data",
        "no_edge": "✗  no edge",
        "skipped": "—  skipped",
    }

    lines: list[str] = []
    lines.append(f"\n{'Strategy Audit':^92}")
    _win = window if window is not None else f"{days}d"
    lines.append(f"  Symbol: {symbol}   TF: {timeframe}   Window: {_win}")
    lines.append(_SEP * 92)
    lines.append(
        f"  {'Strategy':<24}  {'Best IS':>8}  {'IS n':>4}  "
        f"{'Best OOS':>9}  {'↑OOS':>7}  {'↓OOS':>7}  {'OOS n':>5}  {'tp_r':>5}  Verdict"
    )
    lines.append(_SEP * 92)

    for row in rows:
        label = _VERDICT_LABEL.get(row.verdict, row.verdict)
        skip = f"  ({row.skip_reason})" if row.skip_reason else ""
        lines.append(
            f"  {row.strategy:<24}  {_fmt_r(row.best_is_avg_r):>8}  {row.is_trades:>4}  "
            f"{_fmt_r(row.best_oos_avg_r):>9}  {_fmt_r(row.best_long_oos_avg_r):>7}  "
            f"{_fmt_r(row.best_short_oos_avg_r):>7}  {row.oos_trades:>5}  "
            f"{row.best_tp_r:>5.1f}  {label}{skip}"
        )

    lines.append(_SEP * 92)

    good = [r for r in rows if r.verdict == "good"]
    marginal = [r for r in rows if r.verdict == "marginal"]
    no_edge = [r for r in rows if r.verdict == "no_edge"]

    lines.append(
        f"\n  Summary: {len(good)} good  {len(marginal)} marginal  "
        f"{len(no_edge)} no-edge  "
        f"{sum(1 for r in rows if r.verdict in ('no_data', 'skipped'))} insufficient-data"
    )

    # Directional split candidates — strategies where long/short diverge enough
    split_candidates = [
        r
        for r in rows
        if r.best_long_oos_avg_r is not None
        and r.best_short_oos_avg_r is not None
        and r.long_oos_n >= 3
        and r.short_oos_n >= 3
        and abs(r.best_long_oos_avg_r - r.best_short_oos_avg_r) >= 0.1
        and r.verdict in ("good", "marginal")
    ]
    if split_candidates:
        lines.append(
            "\n  ↕ Directional split candidates (|↑OOS − ↓OOS| ≥ 0.1R, n ≥ 3 each):"
        )
        for r in split_candidates:
            delta = abs((r.best_long_oos_avg_r or 0) - (r.best_short_oos_avg_r or 0))
            lines.append(
                f"    {r.strategy:<24}  ↑{_fmt_r(r.best_long_oos_avg_r)} (n={r.long_oos_n})"
                f"  ↓{_fmt_r(r.best_short_oos_avg_r)} (n={r.short_oos_n})"
                f"  Δ={delta:.3f}R → deep-sweep for tp_r_long / tp_r_short"
            )

    if good or marginal:
        lines.append(
            "\n  Next step: deep-sweep winners with "
            "`wifey param-sweep --strategy <name> --days 365`"
        )
    if no_edge:
        names = ", ".join(r.strategy for r in no_edge)
        lines.append(f"  Fix detector logic before re-sweeping: {names}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="WFO parameter sweep for a single strategy × symbol × TF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--strategy", required=True, help="Strategy name (e.g. bos)")
    p.add_argument("--symbol", required=True, help="Symbol (e.g. BTCUSDT)")
    p.add_argument("--tf", required=True, dest="timeframe", help="Timeframe (e.g. 1h)")
    p.add_argument(
        "--param",
        action="append",
        dest="params",
        metavar="NAME=MIN:MAX:STEP",
        help="Param range override. Repeatable. E.g. --param tp_r=1.0:5.0:0.5",
    )
    p.add_argument(
        "--wfo-split",
        type=float,
        default=0.7,
        help="Fraction of candles used as in-sample (default: 0.7)",
    )
    p.add_argument(
        "--min-trades",
        type=int,
        default=0,
        help="Min closed trades in IS window to score a config (default: auto)",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top configs to display (default: 10)",
    )
    p.add_argument(
        "--days", type=int, default=180, help="Days of history to load (default: 180)"
    )
    p.add_argument(
        "--fee-pct",
        type=float,
        default=0.0005,
        help="Taker fee fraction (default: 0.0005 = 0.05%%)",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database (default: analytics.db)",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.strategy not in KNOWN_STRATEGIES:
        print(
            f"Unknown strategy {args.strategy!r}. "
            f"Choose from: {', '.join(KNOWN_STRATEGIES)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build param ranges
    if args.params:
        try:
            param_ranges = [_parse_param_spec(s) for s in args.params]
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        param_ranges = _default_param_ranges(args.strategy)

    grid_size = 1
    for r in param_ranges:
        grid_size *= len(r.values)

    # Auto min_trades based on TF if not specified
    min_trades = args.min_trades
    if min_trades == 0:
        _tf_defaults = {"15m": 20, "1h": 12, "4h": 5, "1d": 2}
        min_trades = _tf_defaults.get(args.timeframe, 8)

    print(f"\nParam sweep  {args.strategy} / {args.symbol} / {args.timeframe}")
    print(
        f"Days: {args.days}  WFO split: {args.wfo_split:.0%} IS / {1 - args.wfo_split:.0%} OOS"
    )
    print(f"Grid: {grid_size} combos  Min trades: {min_trades}  Top-N: {args.top_n}")
    print(f"Params: {', '.join(r.name for r in param_ranges)}")

    if grid_size > 5000:
        print(f"\n  WARNING: Grid has {grid_size} combos — this may take a while.")

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(args.db), read_only=True)
    try:
        rows = run_param_sweep(
            conn=conn,
            strategy=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            param_ranges=param_ranges,
            wfo_split=args.wfo_split,
            min_trades=min_trades,
            fee_pct=args.fee_pct,
            top_n=args.top_n,
        )
    finally:
        conn.close()

    print(format_sweep_results(rows, args.strategy, args.symbol, args.timeframe))


if __name__ == "__main__":
    main()
