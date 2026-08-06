"""Signal package — full split landed in signal-3.

signal-1 landed the lightweight type dataclasses moved from signals/alert_formatter.py.
signal-2 extracted the scanner leaves (`_common`, `gates`, `resolvers`,
`bt_cache`, `stats_context`, `cofire`).
signal-3 (this PR) moves `scan_symbol` / `run_scan_cycle` into `scanner.py` and
reduces `signal_lib.py` to a re-export shim.

The package re-exports every name `analytics/signal_lib.py` previously exposed
in its `__all__` so `from analytics.signal_lib import X` keeps working
zero-edits at all 9 external import sites.
"""

from analytics.backtest_lib import (
    BacktestResult,
    _is_low_volume,
    _is_volume_spike,
)
from analytics.data_store import (
    BacktestSnapshot,
    _backtest_run_id,
    _make_bt_cache_key,
    get_backtest_cache,
    get_ohlcv,
    put_backtest_cache,
    upsert_backtest_run,
    upsert_signal_outcome,
    upsert_signals,
)
from analytics.overnight_gap_lib import gap_fill_warning, get_overnight_gap
from analytics.signal._common import (
    _CANDLE_CLOSE_BUFFER_SECS,
    _SCAN_WINDOW,
    _bt_mem_cache,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    secs_until_next_boundary,
)
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import (
    _find_cross_tf_cofire,
    _find_live_cofire,
    _parse_htf_ltf_pairs,
)
from analytics.signal.gates import (
    _filter_signals_by_adr,
    _is_adr_exempt,
    adr_gate_applies,
)
from analytics.signal.resolvers import (
    _resolve_atr_sl_floor,
    _resolve_atr_sl_multiplier,
    _resolve_sl_pct,
    _resolve_tp_r,
    _resolve_volume_spike_boost,
    _resolve_volume_spike_boost_long,
    _resolve_volume_spike_boost_short,
    _resolve_volume_suppress,
    _resolve_volume_suppress_long,
    _resolve_volume_suppress_short,
)
from analytics.signal.scanner import run_scan_cycle, scan_symbol
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import ConfluenceData, SignalEvent, StatsContext
from analytics.signal_config import (
    BacktestFilterConfig,
    BiasConfig,
    StrategyOverride,
    _day_filter_to_weekdays,
)
from analytics.strategies import STRATEGY_REGISTRY
from signals.cooldown_store import CooldownStore
from signals.registry import SIGNAL_REGISTRY

__all__ = [
    "BacktestFilterConfig",
    "BacktestResult",
    "BacktestSnapshot",
    "BiasConfig",
    "ConfluenceData",
    "CooldownStore",
    "SIGNAL_REGISTRY",
    "STRATEGY_REGISTRY",
    "SignalEvent",
    "StatsContext",
    "StrategyOverride",
    "_CANDLE_CLOSE_BUFFER_SECS",
    "_SCAN_WINDOW",
    "_backtest_run_id",
    "_backtest_summary",
    "_bt_mem_cache",
    "_compute_backtest",
    "_compute_stats_context",
    "_day_filter_to_weekdays",
    "_filter_signals_by_adr",
    "_find_cross_tf_cofire",
    "_find_live_cofire",
    "_fmt_hold",
    "_is_adr_exempt",
    "_is_low_volume",
    "_is_volume_spike",
    "_make_bt_cache_key",
    "_parse_htf_ltf_pairs",
    "_reset_bt_cache",
    "_resolve_atr_sl_floor",
    "_resolve_atr_sl_multiplier",
    "_resolve_sl_pct",
    "_resolve_tp_r",
    "_resolve_volume_spike_boost",
    "_resolve_volume_spike_boost_long",
    "_resolve_volume_spike_boost_short",
    "_resolve_volume_suppress",
    "_resolve_volume_suppress_long",
    "_resolve_volume_suppress_short",
    "adr_gate_applies",
    "gap_fill_warning",
    "get_backtest_cache",
    "get_ohlcv",
    "get_overnight_gap",
    "parse_timeframe_secs",
    "put_backtest_cache",
    "run_scan_cycle",
    "scan_symbol",
    "secs_until_next_boundary",
    "upsert_backtest_run",
    "upsert_signal_outcome",
    "upsert_signals",
]
