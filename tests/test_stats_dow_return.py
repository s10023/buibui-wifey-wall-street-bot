"""DOW median return + the standard error the Stats tab dims on (parent #598 port).

Why the error bar exists at all: at the n this card runs (~48-52 weekdays in a 1y
window), SE measured **0.08-0.32%** across SPY/QQQ/NVDA/AAPL/MSFT at 365d against
cell means of 0.02-0.52% — comparable to or larger than the value it qualifies. The
column was rendering that in green and red with the same visual weight as the range
column, which is real signal.

**On equity data no cell survives the bar.** All **25 of 25** fall inside it and the
largest |t| anywhere is **1.76** (SPY Tue); mean and median disagree on SIGN in
**5 of 25**. Script: `docs/plans/scripts/dow_return_noise.py`, which calls
`compute_dow_patterns` rather than reading 1d bars — the two sources disagree, so
quote the function.

**The constant is 2.576, not the parent's 2.69.** Bonferroni over the **5** weekdays
this card shows at once (0.05/5 = 0.01 two-sided); crypto trades weekends and gets 7.
Nothing gates on it — it is a display threshold, not a significance test.

The fixture is skewed on purpose. A symmetric one makes mean == median and would
pass against an implementation that computed the mean twice.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from analytics.data_store import init_schema
from analytics.stats.dow import DOWRow, compute_dow_patterns
from analytics.store.market_data import upsert_ohlcv

SYM = "SPY"
# Every Monday is +1% except one -20% gap: mean goes negative, median stays +1%.
QUIET_RET = 0.01
CRASH_RET = -0.20
N_MONDAYS = 8

# The value the Stats tab dims on. Kept in step with `DOW_NOISE_SE` in
# web/ui/src/pages/Stats.svelte — if one moves without the other, the UI and this
# test disagree about what "noise" means.
DOW_NOISE_SE = 2.576


def _monday_rows(returns: list[float]) -> pd.DataFrame:
    """One 1h bar per Monday at 12:00 UTC, each with the given close/open return.

    12:00 UTC keeps the bar on the same calendar DATE under both UTC and this
    machine's UTC+8 local zone, which is what DuckDB renders in.
    """
    now = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monday = now - timedelta(days=(now.weekday()) % 7)
    rows = []
    for i, ret in enumerate(returns):
        ts = int((monday - timedelta(weeks=i)).timestamp() * 1000)
        close = 100.0 * (1.0 + ret)
        rows.append(
            {
                "symbol": SYM,
                "timeframe": "1h",
                "open_time": ts,
                "open": 100.0,
                "high": max(100.0, close),
                "low": min(100.0, close),
                "close": close,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    return pd.DataFrame(rows)


def _mondays(returns: list[float]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_ohlcv(conn, _monday_rows(returns))
    return conn


def _mon(conn: duckdb.DuckDBPyConnection) -> DOWRow:
    return next(r for r in compute_dow_patterns(conn, SYM, 3650).rows if r.dow == "Mon")


def test_median_return_ignores_the_crash_the_mean_cannot() -> None:
    rets = [CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)
    row = _mon(_mondays(rets))
    assert row.avg_return_pct == pytest.approx(statistics.mean(rets))
    assert row.median_return_pct == pytest.approx(QUIET_RET)
    # The whole point: one drags negative, the other does not.
    assert row.avg_return_pct < 0 < row.median_return_pct


def test_stderr_matches_stddev_over_sqrt_n() -> None:
    rets = [CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)
    row = _mon(_mondays(rets))
    expected = statistics.stdev(rets) / (len(rets) ** 0.5)
    assert row.return_stderr_pct == pytest.approx(expected)


def test_this_cell_is_noise_by_its_own_error_bar() -> None:
    """The dimming rule the UI applies: |mean| < 2.576 SE means 'no direction'."""
    row = _mon(_mondays([CRASH_RET] + [QUIET_RET] * (N_MONDAYS - 1)))
    assert row.return_stderr_pct is not None
    assert abs(row.avg_return_pct) < DOW_NOISE_SE * row.return_stderr_pct


def test_a_real_direction_is_not_dimmed() -> None:
    """Non-vacuity: the rule must also let a genuine signal through.

    Without this, a rule that dimmed EVERYTHING would pass the test above and the
    column would go uniformly grey — the mirror failure of rendering noise in
    colour, and just as misleading. That matters more here than upstream, because
    on real equity data every cell IS currently dimmed (25 of 25), so this is the
    only place the not-dimmed branch is exercised at all.
    """
    row = _mon(_mondays([QUIET_RET] * N_MONDAYS))  # every Monday identical
    assert row.return_stderr_pct == pytest.approx(0.0)
    assert abs(row.avg_return_pct) > DOW_NOISE_SE * (row.return_stderr_pct or 0.0)


def test_stderr_is_none_at_n_equals_one() -> None:
    """Sample stddev is undefined at n=1.

    Must stay None rather than coerce to 0.0 — a zero-width error bar would make a
    single observation look infinitely significant, which is the exact inversion of
    what this field is for.
    """
    row = _mon(_mondays([QUIET_RET]))
    assert row.sample_days == 1
    assert row.return_stderr_pct is None


def _weekday_only_conn(weeks: int = 4) -> duckdb.DuckDBPyConnection:
    """Seed Mon-Fri bars only — the shape real RTH ingest produces."""
    now = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monday = now - timedelta(days=(now.weekday()) % 7)
    rows = []
    for back in range(weeks * 7):
        day = monday - timedelta(days=back)
        if day.weekday() >= 5:  # Sat/Sun: no US equity session
            continue
        rows.append(
            {
                "symbol": SYM,
                "timeframe": "1h",
                "open_time": int(day.timestamp() * 1000),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    upsert_ohlcv(conn, pd.DataFrame(rows))
    return conn


def test_equity_dow_table_is_exactly_five_rows() -> None:
    """The precondition behind the 2.576 constant.

    `DOW_NOISE_SE` is Bonferroni over the number of cells read at once, so the row
    count is load-bearing: inheriting the parent's 2.69 (k=7) would under-correct.

    `compute_dow_patterns` does NOT filter weekends — it groups whatever bars it is
    given. The 5 comes from the DATA, which is why this seeds Mon-Fri only, the
    shape RTH ingest actually produces. Asserting equality rather than a superset
    on purpose: a `>=` check passes against any row count and would have been
    green even if weekend rows appeared.
    """
    rows = compute_dow_patterns(_weekday_only_conn(), SYM, 3650).rows
    assert [r.dow for r in rows] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert len(rows) == 5


def test_every_weekday_row_carries_an_error_bar() -> None:
    """No cell may reach the UI with a None stderr at a real sample size.

    The UI dims `None` as "cannot claim a direction", which is right for n<2 but
    would silently grey the whole column if the field failed to populate — an
    always-dimmed table looks identical to a correctly-dimmed one.
    """
    rows = compute_dow_patterns(_weekday_only_conn(), SYM, 3650).rows
    assert all(r.sample_days >= 2 for r in rows)
    assert all(r.return_stderr_pct is not None for r in rows)
