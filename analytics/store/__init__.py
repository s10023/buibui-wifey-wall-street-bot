"""Store package — full split landed in store-2.

Re-exports the public API formerly defined in `analytics/data_store.py`.
The legacy module remains as a thin shim for the 30+ existing import sites.
"""

from analytics.store._common import DEFAULT_DB_PATH, _upsert
from analytics.store.backtest_cache import (
    BacktestSnapshot,
    _make_bt_cache_key,
    get_backtest_cache,
    prune_backtest_cache,
    put_backtest_cache,
)
from analytics.store.backtest_runs import (
    _backtest_run_id,
    get_win_rate_by_strategy,
    list_backtest_runs,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.store.combos import (
    get_combo_lookup,
    get_cross_tf_combo_lookup,
    list_combo_runs,
    list_cross_tf_combo_runs,
    upsert_combo_run,
    upsert_cross_tf_combo_run,
)
from analytics.store.confidence import (
    get_confidence_ratings,
    get_directional_confidence_ratings,
    prune_undeclared_confidence_ratings,
    upsert_confidence_ratings,
)
from analytics.store.earnings import (
    get_earnings_facts,
    upsert_earnings_facts,
)
from analytics.store.market_data import (
    get_latest_open_time,
    get_ohlcv,
    upsert_ohlcv,
)
from analytics.store.schema import init_schema
from analytics.store.signals import (
    _OUTCOME_COLUMNS,
    get_signals_history,
    upsert_signal_outcome,
    upsert_signals,
)
from analytics.store.stats_cache import (
    get_stats_cache,
    upsert_stats_cache,
)

__all__ = [
    "BacktestSnapshot",
    "DEFAULT_DB_PATH",
    "_OUTCOME_COLUMNS",
    "_backtest_run_id",
    "_make_bt_cache_key",
    "_upsert",
    "get_backtest_cache",
    "get_combo_lookup",
    "get_confidence_ratings",
    "get_cross_tf_combo_lookup",
    "get_directional_confidence_ratings",
    "get_earnings_facts",
    "get_latest_open_time",
    "get_ohlcv",
    "get_signals_history",
    "get_stats_cache",
    "get_win_rate_by_strategy",
    "init_schema",
    "list_backtest_runs",
    "list_combo_runs",
    "list_cross_tf_combo_runs",
    "prune_backtest_cache",
    "prune_undeclared_confidence_ratings",
    "put_backtest_cache",
    "upsert_backtest_run",
    "upsert_backtest_trades",
    "upsert_combo_run",
    "upsert_confidence_ratings",
    "upsert_cross_tf_combo_run",
    "upsert_earnings_facts",
    "upsert_ohlcv",
    "upsert_signal_outcome",
    "upsert_signals",
    "upsert_stats_cache",
]
