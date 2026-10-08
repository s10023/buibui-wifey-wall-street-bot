"""Multi-symbol pooled-trades WFO sweep — covers T-A (combined) and Task A
(directional) follow-ups to T14.

Runs `run_param_sweep` (tp_r-only grid) per symbol for a fixed cohort, then pools
OOS Trade objects across symbols and picks the tp_r maximizing pooled OOS avg_r.

Two modes:
  --direction combined  (default)  — pool BacktestResult.closed_trades (both
                                     directions). Original T-A use case.
  --direction long|short           — pool BacktestResult.long_closed_trades /
                                     short_closed_trades. Task A use case:
                                     directional tp_r_long / tp_r_short overrides.
  --direction both                 — print combined + long + short tables per
                                     cell. Useful when comparing.

Cell lists are hardcoded:
  --cells t-a        Original T-A T14-pending markers.
  --cells task-a     (default) Task A directional cells — 4 strategies × 3 TFs ×
                     2 day_filters, including 1wk per Task C finding.
  --cells inside-bar inside_bar audit — full 6-cell directional sweep (3 TFs ×
                     2 day_filters) on the 13-sym cohort. Retires the crypto-era
                     tp_r_long=4.0 / tp_r_short=2.0 leak in strategy_params.toml.
  --cells pin-bar    pin_bar audit — full 6-cell directional sweep (3 TFs ×
                     2 day_filters) on the 13-sym cohort. Retires the crypto-era
                     tp_r_long=5.0 / tp_r_short=3.0 leak in strategy_params.toml.
  --cells candle-resweep
                     tp_r re-sweep at the new ATR multipliers committed in PR
                     #33 (Task C-followup). Phase 1: 3 candle patterns
                     (pin_bar, inside_bar, engulfing) × 3 TFs × 2 day_filters =
                     18 cells. Most-likely-to-shift first — candle patterns
                     are where ATR floor actually bites vs structural SL.
                     Pair with --fixed-atr to pin each cell's
                     atr_sl_multiplier_<tf> + atr_sl_floor from the
                     committed signal_watch TOML (closes the ATR loop).
  --cells phase2-resweep
                     tp_r re-sweep Phase 2 — the remaining 9 strategies with
                     `atr_sl_floor = true` whose tp_r values were calibrated
                     before PR #33: hammer_hanging_man, doji, morning_evening_star,
                     bos, trend_day, orb (4h-only), eqh_eql, ema, order_block.
                     8 strategies × 3 TFs × 2 day_filters + orb × 4h × 2
                     day_filters = 50 cells. Closes the ATR loop completely.
                     Pair with --fixed-atr.

Flags:
  --fixed-atr        Load each cell's atr_sl_multiplier + atr_sl_floor from
                     the cell's signal_watch TOML (config_label column) via
                     `analytics.signal_config.load_signal_config`. Required
                     for the `candle-resweep` cell list since the headline
                     finding (tp_r winners under ATR floor) is meaningless
                     without ATR floor pinned at sweep time. Off by default
                     for back-compat with t-a / task-a / inside-bar / pin-bar
                     (none of which were ATR-aware at run time).

Usage:
    PYTHONPATH=. poetry run python tools/multi_symbol_wfo.py [--direction long|short|both] [--cells task-a|t-a|inside-bar|pin-bar|candle-resweep|phase2-resweep] [--fixed-atr] [--db PATH]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from analytics.backtest_lib import BacktestResult, Trade
from analytics.backtest_runner import (
    _build_htf_slope_series_by_symbol,
    _build_regime_series_by_symbol,
)
from analytics.data_store import DEFAULT_DB_PATH
from analytics.param_sweep import ParamRange, _float_range, run_param_sweep
from analytics.signal_config import SignalWatchConfig, load_signal_config

if TYPE_CHECKING:
    import pandas as pd

    from analytics.backtest.live_parity_config import LiveParityConfig
    from analytics.signal_config import BiasConfig
    from analytics.signal_config import StrategyOverride as LiveStrategyOverride

CONFIG_PATHS: dict[str, Path] = {
    "signal_watch.toml": Path("config/signal_watch.toml"),
    "signal_watch_weekdays.toml": Path("config/signal_watch_weekdays.toml"),
}

SYMBOLS = (
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "ORCL",
    "ADBE",
    "NVDA",
    "AMD",
    "TSLA",
    "MSTR",
    "SPY",
    "QQQ",
)

# Per-TF history anchors: matches T14 (data start = earliest yfinance candle
# the watchlist covers uniformly). 1wk uses the 1d anchor — it just gives us
# fewer bars, which the MIN_POOLED_N gate filters out naturally.
SINCE_4H = "2024-05-16"
SINCE_1D = "2023-01-03"
SINCE_1WK = "2023-01-03"

# Cohort-pooled minimum trade count gate. Kept at 10 across cohort sizes so
# the decision rule stays comparable to T-A (4-sym) and Task A (4-sym): we
# want enough samples to trust the pooled avg_r but not so many that thin-edge
# strategies get filtered out. Applied PER DIRECTION when in directional mode
# (so a 20-trade combined sample with 10 long + 10 short clears, but a
# 12-trade sample with 11 long + 1 short does not for short).
MIN_POOLED_N = 10

TP_R_RANGE = ParamRange("tp_r", _float_range(1.0, 5.0, 0.5))

FEE_PCT = 0.0
WFO_SPLIT = 0.7

# (strategy, tf, day_filter, current_tp_r, config_label)
# config_label is the TOML the cell lives in (for the printed report only).
# T-A cell list (combined-direction WFO that retired the T14 "multi-symbol
# pending" markers). Kept for re-runnability / regression.
CELLS_T_A: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu)
    ("pin_bar", "4h", "tue_thu", 4.5, "signal_watch.toml"),
    ("pin_bar", "1d", "tue_thu", 4.0, "signal_watch.toml"),
    ("hammer_hanging_man", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("inside_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("bos", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("bos", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("orb", "4h", "tue_thu", 5.0, "signal_watch.toml"),
    # signal_watch_weekdays.toml
    ("pin_bar", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("hammer_hanging_man", "4h", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    ("bos", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("bos", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("trend_day", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("orb", "4h", "weekdays", 5.0, "signal_watch_weekdays.toml"),
    ("eqh_eql", "1d", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    ("morning_evening_star", "1d", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    ("ema", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
]

# Task A cell list — 4 strategies T14 flagged for directional gaps × 3 TFs ×
# 2 day_filters. 1wk included per Task C finding (every WFO since T13 had
# skipped 1wk; production signals routed through uncalibrated R-multiples).
# current_tp_r shown for context only — directional commit decision uses the
# new tp_r_long_<tf> / tp_r_short_<tf> keys (architecture extension in this PR).
CELLS_TASK_A: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu) — uses strategy-wide tp_r=3.0 on all 4 strategies.
    ("ema", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "4h", "tue_thu", 3.5, "signal_watch.toml"),  # tp_r_4h=3.5 (T14)
    ("trend_day", "1d", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0
    ("trend_day", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("morning_evening_star", "4h", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0
    ("morning_evening_star", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("morning_evening_star", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    # signal_watch_weekdays.toml — mix of strategy-wide and per-TF keys (see T14/T-A).
    ("ema", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("ema", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("ema", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("engulfing", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1d", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1wk", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    (
        "trend_day",
        "4h",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=4.0 (T-A)
    ("trend_day", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("trend_day", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    (
        "morning_evening_star",
        "4h",
        "weekdays",
        2.5,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=2.5 (T14)
    (
        "morning_evening_star",
        "1d",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_1d=4.0
    (
        "morning_evening_star",
        "1wk",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # inherits tp_r=4.0 fallback
]

# inside_bar audit — full 6-cell directional sweep (3 TFs × 2 day_filters) on
# the 13-sym cohort. Retires the stale crypto-era directional override
# (tp_r_long=4.0 / tp_r_short=2.0 in strategy_params.toml — derived from BTC 1h
# / SOL 1h, 2026-05-14 fork era) that leaks into 3 of 6 production
# cells (1d tue_thu, 1wk tue_thu, 1wk weekdays — no per-TF combined commit yet).
# Also re-validates the prior single/4-sym commits on the 13-sym cohort:
#   - tp_r_4h=3.5 (both configs) was T-A 4-sym "no_edge confirmed" at n=68
#   - tp_r_1d=2.5 (weekdays only) was T14 AAPL-only at n=17 (thin sample)
# current_tp_r reflects the *combined* per-TF effective value (per-TF commit
# where present; fallback tp_r=3.0 otherwise).
CELLS_INSIDE_BAR: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu)
    ("inside_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),  # tp_r_4h=3.5 (T-A)
    ("inside_bar", "1d", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0 fallback
    ("inside_bar", "1wk", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0 fallback
    # signal_watch_weekdays.toml
    (
        "inside_bar",
        "4h",
        "weekdays",
        3.5,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=3.5 (T14 AAPL)
    (
        "inside_bar",
        "1d",
        "weekdays",
        2.5,
        "signal_watch_weekdays.toml",
    ),  # tp_r_1d=2.5 (T14 AAPL, n=17)
    (
        "inside_bar",
        "1wk",
        "weekdays",
        3.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r=3.0 fallback
]

# pin_bar audit — full 6-cell directional sweep (3 TFs × 2 day_filters) on the
# 13-sym cohort. Retires the stale crypto-era directional override
# (tp_r_long=5.0 / tp_r_short=3.0 in strategy_params.toml — ETH 1h+15m / SOL 1h
# derived, 2026-05-14 fork era) that leaks into 2 of 6 production
# cells (1wk tue_thu, 1wk weekdays — no per-TF combined commit). The 4 of 6
# cells with per-TF combined (`tp_r_4h` / `tp_r_1d` on both signal_watch
# TOMLs) already shadow the stale override, but those per-TF values are
# themselves single-name (T14 AAPL) or 4-sym pre-Task E commits that warrant
# 13-sym re-validation in the same sweep.
# current_tp_r reflects the *combined* per-TF effective value (per-TF commit
# where present; fallback tp_r=3.0 / tp_r=3.5 otherwise).
CELLS_PIN_BAR: list[tuple[str, str, str, float, str]] = [
    # signal_watch.toml (tue_thu)
    ("pin_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),  # tp_r_4h=3.5 (Task E)
    ("pin_bar", "1d", "tue_thu", 3.5, "signal_watch.toml"),  # tp_r_1d=3.5 (T-A 4-sym)
    ("pin_bar", "1wk", "tue_thu", 3.0, "signal_watch.toml"),  # tp_r=3.0 fallback
    # signal_watch_weekdays.toml
    (
        "pin_bar",
        "4h",
        "weekdays",
        5.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_4h=5.0 (T-A 4-sym thin margin +0.010R n=99)
    (
        "pin_bar",
        "1d",
        "weekdays",
        4.0,
        "signal_watch_weekdays.toml",
    ),  # tp_r_1d=4.0 (T14 AAPL)
    (
        "pin_bar",
        "1wk",
        "weekdays",
        3.5,
        "signal_watch_weekdays.toml",
    ),  # tp_r=3.5 fallback
]

# candle-resweep — tp_r re-sweep at the committed ATR multipliers (PR #33,
# Task C-followup, 2026-05-20). Phase 1 scope = 3 candle patterns × 3 TFs ×
# 2 day_filters = 18 cells. Run with --fixed-atr so each cell's
# atr_sl_multiplier_<tf> + atr_sl_floor flow into `run_param_sweep` from the
# canonical TOML (config_label column); without that, the sweep replays the
# pre-PR-33 SL geometry and the resulting tp_r winners are the stale numbers
# the widened SLs of Task C-followup invalidate. current_tp_r
# reflects the *combined* per-TF effective value in each config (per-TF
# commit where present; fallback tp_r=3.0/3.5 otherwise).
CELLS_CANDLE_RESWEEP: list[tuple[str, str, str, float, str]] = [
    # pin_bar (PR #32 baseline — full directional commits already on both configs)
    ("pin_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("pin_bar", "1d", "tue_thu", 3.5, "signal_watch.toml"),
    ("pin_bar", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("pin_bar", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("pin_bar", "1d", "weekdays", 2.5, "signal_watch_weekdays.toml"),
    ("pin_bar", "1wk", "weekdays", 4.5, "signal_watch_weekdays.toml"),
    # inside_bar (PR #31 baseline — full directional commits already on both configs)
    ("inside_bar", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("inside_bar", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("inside_bar", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("inside_bar", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("inside_bar", "1d", "weekdays", 2.5, "signal_watch_weekdays.toml"),
    ("inside_bar", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    # engulfing (Task A baseline — per-TF combined commits already on both configs)
    ("engulfing", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("engulfing", "4h", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1d", "weekdays", 3.5, "signal_watch_weekdays.toml"),
    ("engulfing", "1wk", "weekdays", 3.5, "signal_watch_weekdays.toml"),
]

# phase2-resweep — Phase 2 of the candle-resweep follow-up (covers the
# remaining 9 atr_sl_floor=true strategies that PR #39 left unswept). 8 strategies × 3 TFs ×
# 2 day_filters + orb × 4h × 2 day_filters = 50 cells. Run with --fixed-atr.
# current_tp_r reflects the *combined* per-TF effective value in each config
# (per-TF commit where present; strategy-wide tp_r fallback; global 2.0 when
# strategy has no tp_r line at all — eqh_eql / order_block 4h/1wk / trend_day
# 1d/1wk / ema 1d/1wk fall into this last bucket).
CELLS_PHASE2_RESWEEP: list[tuple[str, str, str, float, str]] = [
    # hammer_hanging_man
    ("hammer_hanging_man", "4h", "tue_thu", 4.0, "signal_watch.toml"),
    ("hammer_hanging_man", "1d", "tue_thu", 2.5, "signal_watch.toml"),
    ("hammer_hanging_man", "1wk", "tue_thu", 4.0, "signal_watch.toml"),
    ("hammer_hanging_man", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("hammer_hanging_man", "1d", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    ("hammer_hanging_man", "1wk", "weekdays", 4.0, "signal_watch_weekdays.toml"),
    # doji
    ("doji", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("doji", "1d", "tue_thu", 2.5, "signal_watch.toml"),
    ("doji", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("doji", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("doji", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("doji", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    # morning_evening_star
    ("morning_evening_star", "4h", "tue_thu", 2.5, "signal_watch.toml"),
    ("morning_evening_star", "1d", "tue_thu", 2.5, "signal_watch.toml"),
    ("morning_evening_star", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("morning_evening_star", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("morning_evening_star", "1d", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    ("morning_evening_star", "1wk", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    # bos
    ("bos", "4h", "tue_thu", 3.0, "signal_watch.toml"),
    ("bos", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("bos", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("bos", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("bos", "1d", "weekdays", 2.5, "signal_watch_weekdays.toml"),
    ("bos", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    # trend_day
    ("trend_day", "4h", "tue_thu", 4.5, "signal_watch.toml"),
    ("trend_day", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("trend_day", "4h", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("trend_day", "1d", "weekdays", 5.0, "signal_watch_weekdays.toml"),
    ("trend_day", "1wk", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    # orb (4h-only by mechanic)
    ("orb", "4h", "tue_thu", 3.5, "signal_watch.toml"),
    ("orb", "4h", "weekdays", 2.5, "signal_watch_weekdays.toml"),
    # eqh_eql
    ("eqh_eql", "4h", "tue_thu", 2.0, "signal_watch.toml"),
    ("eqh_eql", "1d", "tue_thu", 2.0, "signal_watch.toml"),
    ("eqh_eql", "1wk", "tue_thu", 2.0, "signal_watch.toml"),
    ("eqh_eql", "4h", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    ("eqh_eql", "1d", "weekdays", 5.0, "signal_watch_weekdays.toml"),
    ("eqh_eql", "1wk", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    # ema
    ("ema", "4h", "tue_thu", 2.5, "signal_watch.toml"),
    ("ema", "1d", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "1wk", "tue_thu", 3.0, "signal_watch.toml"),
    ("ema", "4h", "weekdays", 2.5, "signal_watch_weekdays.toml"),
    ("ema", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("ema", "1wk", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    # order_block
    ("order_block", "4h", "tue_thu", 2.0, "signal_watch.toml"),
    ("order_block", "1d", "tue_thu", 5.0, "signal_watch.toml"),
    ("order_block", "1wk", "tue_thu", 2.0, "signal_watch.toml"),
    ("order_block", "4h", "weekdays", 2.0, "signal_watch_weekdays.toml"),
    ("order_block", "1d", "weekdays", 3.0, "signal_watch_weekdays.toml"),
    ("order_block", "1wk", "weekdays", 2.0, "signal_watch_weekdays.toml"),
]


def _date_to_ms(d: str) -> int:
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _since_for_tf(tf: str) -> int:
    if tf == "4h":
        return _date_to_ms(SINCE_4H)
    if tf == "1wk":
        return _date_to_ms(SINCE_1WK)
    return _date_to_ms(SINCE_1D)


def _min_trades_for_tf(tf: str) -> int:
    # Mirrors the `_tf_defaults` in param_sweep.main. 1wk gets the same floor
    # as 1d (2) — 1wk samples are intrinsically thinnest and the WFO min_trades
    # gate filters per-symbol; the real edge gate is MIN_POOLED_N downstream.
    return {"4h": 5, "1d": 2, "1wk": 2}.get(tf, 5)


def _select_trades(res: BacktestResult, direction: str) -> list[Trade]:
    """Filter BacktestResult trades by direction.

    direction = "combined" → res.closed_trades (both directions)
              = "long"     → res.long_closed_trades
              = "short"    → res.short_closed_trades
    """
    if direction == "long":
        return res.long_closed_trades
    if direction == "short":
        return res.short_closed_trades
    return res.closed_trades


@dataclass
class PooledRow:
    tp_r: float
    pooled_n: int
    pooled_avg_r: float | None
    pooled_win_rate: float | None
    per_symbol_n: dict[str, int]


def _pool_oos_by_tp_r(
    per_symbol_results: dict[str, list],
    direction: str = "combined",
) -> list[PooledRow]:
    """Group SweepRow lists by tp_r across symbols and compute pooled OOS metrics.

    direction filters which trades are pooled (see _select_trades).
    """
    by_tp: dict[float, dict[str, BacktestResult]] = defaultdict(dict)
    for sym, rows in per_symbol_results.items():
        for row in rows:
            tp = float(row.params["tp_r"])
            by_tp[tp][sym] = row.oos_result

    pooled: list[PooledRow] = []
    for tp in sorted(by_tp.keys()):
        per_sym_n: dict[str, int] = {}
        r_values: list[float] = []
        win_count = 0
        for sym in SYMBOLS:
            res = by_tp[tp].get(sym)
            if res is None:
                per_sym_n[sym] = 0
                continue
            trades = _select_trades(res, direction)
            per_sym_n[sym] = len(trades)
            for t in trades:
                pnl = t.pnl_r
                if pnl is None:
                    continue
                r_values.append(pnl)
                if t.outcome == "win":
                    win_count += 1
        n = len(r_values)
        avg_r = sum(r_values) / n if n > 0 else None
        wr = win_count / n if n > 0 else None
        pooled.append(
            PooledRow(
                tp_r=tp,
                pooled_n=n,
                pooled_avg_r=avg_r,
                pooled_win_rate=wr,
                per_symbol_n=per_sym_n,
            )
        )
    return pooled


def _pick_winner(pooled: list[PooledRow]) -> PooledRow | None:
    """Pick the tp_r with the highest pooled OOS avg_r, subject to MIN_POOLED_N."""
    eligible = [
        p for p in pooled if p.pooled_n >= MIN_POOLED_N and p.pooled_avg_r is not None
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.pooled_avg_r or float("-inf"))


def _fmt_r(v: float | None) -> str:
    if v is None:
        return "  —  "
    return f"{v:+.3f}R"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "  —  "
    return f"{v * 100:.1f}%"


def _print_pooled_grid(
    pooled: list[PooledRow],
    direction_label: str,
    current_tp_r: float,
    tf: str,
) -> None:
    print(
        f"\n  [{direction_label}]"
        f"  {'tp_r':>5}  {'pooled n':>9}  {'pooled avg_r':>13}  {'pooled wr':>10}  "
        f"per-symbol n ({', '.join(SYMBOLS)})"
    )
    print("  " + "─" * 110)
    for p in pooled:
        per_sym = ", ".join(str(p.per_symbol_n.get(s, 0)) for s in SYMBOLS)
        print(
            f"  {'':10}  {p.tp_r:>5.1f}  {p.pooled_n:>9}  {_fmt_r(p.pooled_avg_r):>13}  "
            f"{_fmt_pct(p.pooled_win_rate):>10}  ({per_sym})"
        )

    winner = _pick_winner(pooled)
    if winner is None:
        print(
            f"\n  ✗ [{direction_label}] No eligible winner "
            f"(no tp_r reaches pooled_n ≥ {MIN_POOLED_N}). Keep current."
        )
        return

    if (winner.pooled_avg_r or 0.0) <= 0:
        print(
            f"\n  ✗ [{direction_label}] Best pooled OOS avg_r is non-positive "
            f"({_fmt_r(winner.pooled_avg_r)} at tp_r={winner.tp_r}, n={winner.pooled_n}). "
            "No directional edge — keep current."
        )
        return

    delta = winner.tp_r - current_tp_r
    arrow = "→" if delta != 0.0 else "="
    # Suggest TOML key based on direction.
    if direction_label == "long":
        key_hint = f"tp_r_long_{tf} = {winner.tp_r}"
    elif direction_label == "short":
        key_hint = f"tp_r_short_{tf} = {winner.tp_r}"
    else:
        key_hint = f"tp_r_{tf} = {winner.tp_r}"
    print(
        f"\n  ✓ [{direction_label}] Winner: tp_r {current_tp_r} {arrow} {winner.tp_r}  "
        f"(pooled OOS avg_r={_fmt_r(winner.pooled_avg_r)}, "
        f"n={winner.pooled_n}, wr={_fmt_pct(winner.pooled_win_rate)})"
        f"\n     TOML: {key_hint}"
    )


def _load_atr_lookup() -> dict[str, SignalWatchConfig]:
    """Load both signal_watch TOMLs, keyed by config_label (filename)."""
    return {label: load_signal_config(path) for label, path in CONFIG_PATHS.items()}


def _resolve_atr(
    cfg: SignalWatchConfig | None,
    strategy: str,
    tf: str,
) -> tuple[float | None, bool]:
    """Return (atr_sl_multiplier, atr_sl_floor) resolved from cfg, or (None, False)
    when cfg is None (i.e., --fixed-atr was not requested).

    Per-symbol overrides are absent for the candle-resweep targets — passing
    SYMBOLS[0] is harmless and resolves to the TF-level commit.
    """
    if cfg is None:
        return (None, False)
    mult = cfg.effective_atr_sl_multiplier(strategy, SYMBOLS[0], tf)
    floor = cfg.effective_atr_sl_floor(strategy, SYMBOLS[0], tf)
    return (mult, floor)


@dataclass
class _LiveParityCtx:
    """Pre-built live-parity inputs for one config (signal_watch TOML).

    `--live-parity` replays the live gate stack inside each cell's
    `run_param_sweep` so the reported `n` reflects the live-filtered population
    rather than raw detector signals. The five per-strategy gates apply
    (regime / direction_filter / F8 HTF-EMA / ADR bias / cooldown); the
    cross-strategy `conflict_resolver` is *not* part of this — the WFO tool
    sweeps one strategy at a time, so there is no cross-strategy event pool.
    The regime / HTF-slope series are pre-computed once per config over the
    full cohort window and indexed by HTF open_time so every cell reuses them.
    """

    live_parity: LiveParityConfig
    bias_cfg: BiasConfig | None
    strategy_params: dict[str, LiveStrategyOverride] | None
    regime_by_sym: dict[str, pd.Series] | None
    htf_by_sym: dict[str, dict[tuple[str, int, int], pd.Series]] | None


def _build_live_parity_ctx(
    conn: duckdb.DuckDBPyConnection,
    config_label: str,
    start_ms: int,
    end_ms: int,
) -> _LiveParityCtx:
    """Load a config and force-enable the live-parity gates for WFO replay.

    Reuses `load_backtest_config` (the canonical loader that wires `[bias]`,
    `[strategy_params]`, and `[backtest.live_parity]`) then overrides the
    `LiveParityConfig` to turn on every per-strategy gate regardless of what the
    TOML toggled — the point of `--live-parity` is to replay the live stack.
    `conflict_resolver` stays off (cross-strategy, not applicable here). The
    regime / HTF-slope pre-compute reuses the backtest_runner builders so the
    series are byte-for-byte what the production sweep would feed the engine.
    """
    from analytics.backtest_config import load_backtest_config

    cfg = load_backtest_config(CONFIG_PATHS[config_label])
    forced = replace(
        cfg.live_parity,
        enabled=True,
        regime=True,
        direction_filter=True,
        f8_htf_ema=True,
        adr_bias=True,
        conflict_resolver=False,
        cooldown=True,
    )
    cfg.live_parity = forced
    regime_by_sym = _build_regime_series_by_symbol(
        conn, cfg, list(SYMBOLS), start_ms, end_ms
    )
    htf_by_sym = _build_htf_slope_series_by_symbol(
        conn, cfg, list(SYMBOLS), start_ms, end_ms
    )
    return _LiveParityCtx(
        live_parity=forced,
        bias_cfg=cfg.bias,
        strategy_params=cfg.live_strategy_params,
        regime_by_sym=regime_by_sym,
        htf_by_sym=htf_by_sym,
    )


def _sweep_cell(
    conn: duckdb.DuckDBPyConnection,
    strategy: str,
    tf: str,
    day_filter: str,
    current_tp_r: float,
    config_label: str,
    directions: tuple[str, ...],
    atr_cfg: SignalWatchConfig | None = None,
    lp_ctx: _LiveParityCtx | None = None,
) -> None:
    since_ms = _since_for_tf(tf)
    min_trades = _min_trades_for_tf(tf)
    atr_mult, atr_floor = _resolve_atr(atr_cfg, strategy, tf)

    atr_label = (
        f"  ATR floor: {atr_floor}, multiplier={atr_mult}"
        if atr_cfg is not None
        else ""
    )
    lp_label = "  live-parity: ON" if lp_ctx is not None else ""
    header = (
        f"\n{'=' * 110}\n"
        f"  Cell: {strategy} / {tf} / day_filter={day_filter}  "
        f"(currently tp_r={current_tp_r} in {config_label})"
        f"{atr_label}{lp_label}\n"
        f"{'=' * 110}"
    )
    print(header)

    per_symbol_results: dict[str, list] = {}
    for sym in SYMBOLS:
        rows = run_param_sweep(
            conn=conn,
            strategy=strategy,
            symbol=sym,
            timeframe=tf,
            days=0,  # ignored when since_ms set
            param_ranges=[TP_R_RANGE],
            wfo_split=WFO_SPLIT,
            min_trades=min_trades,
            fee_pct=FEE_PCT,
            top_n=99,
            since_ms=since_ms,
            day_filter=day_filter,
            atr_sl_multiplier=atr_mult,
            atr_sl_floor=atr_floor,
            live_parity=lp_ctx.live_parity if lp_ctx is not None else None,
            bias_cfg=lp_ctx.bias_cfg if lp_ctx is not None else None,
            regime_series=(
                lp_ctx.regime_by_sym.get(sym)
                if lp_ctx is not None and lp_ctx.regime_by_sym is not None
                else None
            ),
            strategy_params=lp_ctx.strategy_params if lp_ctx is not None else None,
            htf_slope_series_by_anchor=(
                lp_ctx.htf_by_sym.get(sym)
                if lp_ctx is not None and lp_ctx.htf_by_sym is not None
                else None
            ),
        ).rows
        per_symbol_results[sym] = rows

    any_grid = False
    for direction in directions:
        pooled = _pool_oos_by_tp_r(per_symbol_results, direction=direction)
        if not pooled:
            print(f"  [{direction}] No grid results (detection or data error).")
            continue
        any_grid = True
        _print_pooled_grid(pooled, direction, current_tp_r, tf)

    if not any_grid:
        print("  Skipping cell — no grid data for any direction.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="4-symbol pooled WFO sweep (T-A combined + Task A directional)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database (default: analytics.db)",
    )
    parser.add_argument(
        "--cells",
        choices=(
            "task-a",
            "t-a",
            "inside-bar",
            "pin-bar",
            "candle-resweep",
            "phase2-resweep",
        ),
        default="task-a",
        help="Cell list to sweep. task-a (default) = 4 directional-gap cells "
        "× 3 TFs × 2 day_filters. t-a = original T-A combined-direction cells. "
        "inside-bar = inside_bar audit, 6-cell directional sweep retiring the "
        "stale crypto tp_r_long/tp_r_short override. pin-bar = pin_bar audit, "
        "same 6-cell shape, retiring the crypto tp_r_long=5.0/tp_r_short=3.0 "
        "override. candle-resweep = tp_r re-sweep at PR #33 ATR multipliers, "
        "Phase 1 (3 candle patterns × 3 TFs × 2 day_filters = 18 cells); pair "
        "with --fixed-atr. phase2-resweep = Phase 2 (remaining 9 strategies "
        "with atr_sl_floor=true = 50 cells); also pair with --fixed-atr.",
    )
    parser.add_argument(
        "--fixed-atr",
        action="store_true",
        help="Pin each cell's atr_sl_multiplier_<tf> + atr_sl_floor from its "
        "signal_watch TOML (config_label column) at sweep time. Required for "
        "--cells candle-resweep — without it, the sweep replays pre-PR-#33 "
        "SL geometry and the tp_r winners are stale. Off by default.",
    )
    parser.add_argument(
        "--direction",
        choices=("long", "short", "both", "combined"),
        default="both",
        help="Pool by direction. long / short = single-direction pooling "
        "(Task A). combined = both directions in one bucket (T-A). "
        "both (default) = print all three tables per cell.",
    )
    parser.add_argument(
        "--cell",
        type=str,
        default=None,
        help='Filter to a single cell, format "strategy/tf/day_filter". '
        'Example: "ema/4h/tue_thu". Omit to run all cells in the chosen list.',
    )
    parser.add_argument(
        "--live-parity",
        action="store_true",
        dest="live_parity",
        help="Replay the live gate stack inside each cell's sweep so the "
        "reported n/avg_r reflect the live-filtered population (regime, "
        "direction_filter, F8 HTF-EMA, ADR bias, cooldown) instead of raw "
        "detector signals. The cross-strategy conflict_resolver is not applied "
        "(this tool sweeps one strategy at a time). Off by default — without it "
        "the sweep replays raw signals (the historical filter-divergence path).",
    )
    args = parser.parse_args()

    if args.cells == "task-a":
        all_cells = CELLS_TASK_A
    elif args.cells == "t-a":
        all_cells = CELLS_T_A
    elif args.cells == "inside-bar":
        all_cells = CELLS_INSIDE_BAR
    elif args.cells == "pin-bar":
        all_cells = CELLS_PIN_BAR
    elif args.cells == "candle-resweep":
        all_cells = CELLS_CANDLE_RESWEEP
    else:
        all_cells = CELLS_PHASE2_RESWEEP

    if args.cell:
        parts = args.cell.split("/")
        if len(parts) != 3:
            raise SystemExit(
                f"--cell must be strategy/tf/day_filter, got: {args.cell!r}"
            )
        wanted = tuple(parts)
        cells = [c for c in all_cells if (c[0], c[1], c[2]) == wanted]
        if not cells:
            raise SystemExit(f"No cell matches {args.cell!r}")
    else:
        cells = list(all_cells)

    if args.direction == "both":
        directions: tuple[str, ...] = ("combined", "long", "short")
    else:
        directions = (args.direction,)

    atr_lookup: dict[str, SignalWatchConfig] | None = (
        _load_atr_lookup() if args.fixed_atr else None
    )

    atr_status = (
        "ATR floor: ON (pinned from TOML)"
        if atr_lookup is not None
        else "ATR floor: off"
    )
    lp_status = (
        "live-parity: ON (regime+direction_filter+F8+ADR+cooldown)"
        if args.live_parity
        else "live-parity: off (raw signals)"
    )
    print(
        f"Multi-symbol pooled WFO sweep — cohort: {', '.join(SYMBOLS)}\n"
        f"  Cells: {len(cells)} ({args.cells})   "
        f"tp_r grid: {len(TP_R_RANGE.values)} values "
        f"({TP_R_RANGE.values[0]}..{TP_R_RANGE.values[-1]} step 0.5)\n"
        f"  Directions: {', '.join(directions)}   {atr_status}   {lp_status}\n"
        f"  Anchors: 4h since {SINCE_4H}, 1d since {SINCE_1D}, "
        f"1wk since {SINCE_1WK}   fee_pct={FEE_PCT}   "
        f"wfo_split={WFO_SPLIT}   min_pooled_n={MIN_POOLED_N} (per direction)"
    )

    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        lp_ctx_by_label: dict[str, _LiveParityCtx] = {}
        if args.live_parity:
            # Build regime / HTF-slope series once per config over the widest
            # cohort window (earliest 1d/1wk anchor); cells reusing the same
            # config share the pre-computed series. 4h-only cells simply index
            # into a series that starts later — binary search handles it.
            lp_start_ms = _date_to_ms(SINCE_1D)
            lp_end_ms = int(datetime.now(UTC).timestamp() * 1000)
            for _label in {c[4] for c in cells}:
                lp_ctx_by_label[_label] = _build_live_parity_ctx(
                    conn, _label, lp_start_ms, lp_end_ms
                )

        for strategy, tf, day_filter, current_tp_r, config_label in cells:
            atr_cfg = atr_lookup.get(config_label) if atr_lookup else None
            _sweep_cell(
                conn,
                strategy,
                tf,
                day_filter,
                current_tp_r,
                config_label,
                directions,
                atr_cfg=atr_cfg,
                lp_ctx=lp_ctx_by_label.get(config_label),
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
