"""backtest_cache table accessors and BacktestSnapshot duck-type.

`BacktestSnapshot` lives here (not in confidence.py per the original plan) because
it is the return type of `get_backtest_cache` — confidence_ratings has no
dependency on it, while this module is its sole producer.
"""

import hashlib
import time
from dataclasses import dataclass
from typing import Any

import duckdb


@dataclass
class BacktestSnapshot:
    """Pre-computed aggregate stats cached in backtest_cache table.

    Duck-type compatible with BacktestResult for signal filtering.
    closed_trades / long_closed_trades / short_closed_trades return dummy
    lists of the correct length — only len() and truthiness are used by callers.
    """

    symbol: str
    timeframe: str
    strategy: str
    fee_pct: float = 0.0
    n_closed: int = 0
    n_long: int = 0
    n_short: int = 0
    n_win: int = 0
    n_loss: int = 0
    r_win_rate: float = 0.0
    r_avg: float = 0.0
    r_total: float = 0.0
    n_long_win: int = 0
    r_long_win_rate: float | None = None
    r_long_avg: float | None = None
    r_long_total: float = 0.0
    n_short_win: int = 0
    r_short_win_rate: float | None = None
    r_short_avg: float | None = None
    r_short_total: float = 0.0
    h_median: float | None = None
    h_long_median: float | None = None
    h_short_median: float | None = None
    # Per-direction sd of pnl_r (2026-08-07). NULL on rows cached before the
    # column existed, and on cells with fewer than 2 directional trades — both
    # mean "no dispersion estimate", which the EV gate treats as abstain. The
    # cache is keyed per candle, so pre-migration rows age out within a bar.
    r_long_sd: float | None = None
    r_short_sd: float | None = None

    @property
    def closed_trades(self) -> list[None]:
        return [None] * self.n_closed

    @property
    def long_pnl_sd(self) -> float | None:
        """Mirrors `BacktestResult.long_pnl_sd` so the EV gate reads one interface."""
        return self.r_long_sd

    @property
    def short_pnl_sd(self) -> float | None:
        """Mirrors `BacktestResult.short_pnl_sd` so the EV gate reads one interface."""
        return self.r_short_sd

    @property
    def long_closed_trades(self) -> list[None]:
        return [None] * self.n_long

    @property
    def short_closed_trades(self) -> list[None]:
        return [None] * self.n_short

    @property
    def win_count(self) -> int:
        return self.n_win

    @property
    def loss_count(self) -> int:
        return self.n_loss

    @property
    def win_rate(self) -> float:
        return self.r_win_rate

    @property
    def avg_r(self) -> float:
        return self.r_avg

    @property
    def total_r(self) -> float:
        return self.r_total

    @property
    def long_win_count(self) -> int:
        return self.n_long_win

    @property
    def long_win_rate(self) -> float | None:
        return self.r_long_win_rate

    @property
    def long_avg_r(self) -> float | None:
        return self.r_long_avg

    @property
    def long_total_r(self) -> float:
        return self.r_long_total

    @property
    def short_win_count(self) -> int:
        return self.n_short_win

    @property
    def short_win_rate(self) -> float | None:
        return self.r_short_win_rate

    @property
    def short_avg_r(self) -> float | None:
        return self.r_short_avg

    @property
    def short_total_r(self) -> float:
        return self.r_short_total

    @property
    def median_duration_h(self) -> float | None:
        return self.h_median

    @property
    def long_median_duration_h(self) -> float | None:
        return self.h_long_median

    @property
    def short_median_duration_h(self) -> float | None:
        return self.h_short_median


def _make_bt_cache_key(run_id: str, last_candle_ts: int) -> str:
    """24-char hex key combining run params hash and last closed candle timestamp."""
    return hashlib.sha256(f"{run_id}|{last_candle_ts}".encode()).hexdigest()[:24]


def get_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
) -> BacktestSnapshot | None:
    """Return cached BacktestSnapshot for cache_key, or None on miss."""
    row = conn.execute(
        "SELECT symbol, timeframe, strategy, fee_pct, "
        "n_closed, n_long, n_short, n_win, n_loss, "
        "r_win_rate, r_avg, r_total, "
        "n_long_win, r_long_win_rate, r_long_avg, r_long_total, "
        "n_short_win, r_short_win_rate, r_short_avg, r_short_total, "
        "h_median, h_long_median, h_short_median, "
        "r_long_sd, r_short_sd "
        "FROM backtest_cache WHERE cache_key = ?",
        [cache_key],
    ).fetchone()
    if row is None:
        return None
    return BacktestSnapshot(
        symbol=str(row[0]),
        timeframe=str(row[1]),
        strategy=str(row[2]),
        fee_pct=float(row[3]),
        n_closed=int(row[4]),
        n_long=int(row[5]),
        n_short=int(row[6]),
        n_win=int(row[7]),
        n_loss=int(row[8]),
        r_win_rate=float(row[9]),
        r_avg=float(row[10]),
        r_total=float(row[11]),
        n_long_win=int(row[12]),
        r_long_win_rate=float(row[13]) if row[13] is not None else None,
        r_long_avg=float(row[14]) if row[14] is not None else None,
        r_long_total=float(row[15]) if row[15] is not None else 0.0,
        n_short_win=int(row[16]),
        r_short_win_rate=float(row[17]) if row[17] is not None else None,
        r_short_avg=float(row[18]) if row[18] is not None else None,
        r_short_total=float(row[19]) if row[19] is not None else 0.0,
        h_median=float(row[20]) if row[20] is not None else None,
        h_long_median=float(row[21]) if row[21] is not None else None,
        h_short_median=float(row[22]) if row[22] is not None else None,
        r_long_sd=float(row[23]) if row[23] is not None else None,
        r_short_sd=float(row[24]) if row[24] is not None else None,
    )


def put_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    cache_key: str,
    run_id: str,
    last_candle_ts: int,
    result: Any,
) -> None:
    """Persist a BacktestResult's aggregate stats to backtest_cache.

    result must be a BacktestResult instance. Trades are not stored.
    Uses parameterised INSERT OR REPLACE — no DataFrame scan, no try/finally needed.
    """
    from analytics.backtest_lib import BacktestResult

    assert isinstance(result, BacktestResult)
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT OR REPLACE INTO backtest_cache VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            cache_key,
            run_id,
            last_candle_ts,
            result.symbol,
            result.timeframe,
            result.strategy,
            result.fee_pct,
            len(result.closed_trades),
            len(result.long_closed_trades),
            len(result.short_closed_trades),
            result.win_count,
            result.loss_count,
            result.win_rate,
            result.avg_r,
            result.total_r,
            result.long_win_count,
            result.long_win_rate,
            result.long_avg_r,
            result.long_total_r,
            result.short_win_count,
            result.short_win_rate,
            result.short_avg_r,
            result.short_total_r,
            result.median_duration_h,
            result.long_median_duration_h,
            result.short_median_duration_h,
            now_ms,
            # Must stay last, matching the DDL/migration column order.
            result.long_pnl_sd,
            result.short_pnl_sd,
        ],
    )


def prune_backtest_cache(
    conn: duckdb.DuckDBPyConnection,
    keep_days: int = 30,
) -> None:
    """Delete backtest_cache rows older than keep_days."""
    cutoff_ms = int(time.time() * 1000) - keep_days * 24 * 3600 * 1000
    conn.execute("DELETE FROM backtest_cache WHERE cached_at_ms < ?", [cutoff_ms])
