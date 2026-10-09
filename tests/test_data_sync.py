"""Tests for analytics/data_sync.py."""

import inspect
from datetime import date
from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import BARS_MAX_LIMIT, OHLCV_COLUMNS
from analytics.data_quality import _SPLIT_FACTORS, SessionGapReport, check_ohlcv
from analytics.data_store import (
    get_latest_open_time,
    init_schema,
    upsert_ohlcv,
)
from analytics.data_sync import (
    ADJUSTMENT_BASIS_TOL,
    backfill,
    basis_changed,
    sync,
)

_EMPTY = pd.DataFrame(columns=OHLCV_COLUMNS)


def _make_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _make_df(
    open_times: list[int], symbol: str = "AAPL", timeframe: str = "1h"
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": t,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
            }
            for t in open_times
        ],
        columns=OHLCV_COLUMNS,
    )


def test_data_sync_has_no_funding_or_oi_imports() -> None:
    """Phase-A guard: data_sync.py must not reference funding rates or open interest."""
    import analytics.data_sync as m

    src = inspect.getsource(m)
    assert "funding" not in src.lower()
    assert "open_interest" not in src.lower()


class TestBackfill:
    def test_fetches_and_stores_single_batch(self) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000])
        with patch("analytics.data_sync.fetch_bars", return_value=df):
            total = backfill(conn, "AAPL", "1h", 0)
        assert total == 3
        assert get_latest_open_time(conn, "AAPL", "1h") == 3_000

    def test_returns_zero_on_empty_response(self) -> None:
        conn = _make_conn()
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        with patch("analytics.data_sync.fetch_bars", return_value=empty_df):
            total = backfill(conn, "AAPL", "1h", 0)
        assert total == 0

    def test_passes_through_start_ms_to_fetch_bars(self) -> None:
        conn = _make_conn()
        captured: list[Any] = []

        def capture(*args: Any, **kwargs: Any) -> pd.DataFrame:
            captured.append((args, kwargs))
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        with patch("analytics.data_sync.fetch_bars", side_effect=capture):
            backfill(conn, "AAPL", "1h", 1_700_000_000_000)
        assert captured, "fetch_bars was not called"
        args, _ = captured[0]
        # fetch_bars(symbol, interval, start_ms, limit=...)
        assert args[0] == "AAPL"
        assert args[1] == "1h"
        assert args[2] == 1_700_000_000_000


class TestBackfillPaging:
    """fetch_bars truncates each call at BARS_MAX_LIMIT — backfill must page.

    Positive control: every test here asserts fetch_bars was called MORE than
    once and that the second call's start_ms advanced past the first page's
    last bar. A single-call implementation fails both assertions rather than
    passing vacuously.
    """

    def test_pages_until_a_short_page_arrives(self, monkeypatch: Any) -> None:
        conn = _make_conn()
        monkeypatch.setattr("analytics.data_sync.BARS_MAX_LIMIT", 3)
        pages = [
            _make_df([1_000, 2_000, 3_000], timeframe="1d"),  # full page
            _make_df([4_000, 5_000], timeframe="1d"),  # short page -> stop
        ]
        starts: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            starts.append(start)
            return pages[len(starts) - 1] if len(starts) <= len(pages) else _EMPTY

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            total = backfill(conn, "AAPL", "1d", 0)

        assert starts == [0, 3_001], "second page must resume past the last bar"
        assert total == 5
        assert get_latest_open_time(conn, "AAPL", "1d") == 5_000

    def test_full_page_at_the_real_limit_triggers_another_fetch(self) -> None:
        """Pins the loop to the real constant, not to a patched stand-in."""
        conn = _make_conn()
        full = _make_df(list(range(1, BARS_MAX_LIMIT + 1)), timeframe="1d")
        calls: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            calls.append(start)
            return full if len(calls) == 1 else _EMPTY

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            total = backfill(conn, "AAPL", "1d", 0)

        assert len(calls) == 2, "a page at exactly the cap may hide more history"
        assert calls[1] == BARS_MAX_LIMIT + 1
        assert total == BARS_MAX_LIMIT

    def test_short_first_page_makes_exactly_one_call(self) -> None:
        conn = _make_conn()
        calls: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            calls.append(start)
            return _make_df([1_000, 2_000], timeframe="1d")

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            total = backfill(conn, "AAPL", "1d", 0)

        assert calls == [0]
        assert total == 2


class TestFrozenTailIsPageScoped:
    """Only the page that ENDS the paging loop may have its tail quarantined.

    ``check_ohlcv`` judges a frozen tail from the frame's last row, but
    ``backfill`` hands it up to ``BARS_MAX_LIMIT`` bars at a time — so an
    intermediate page's last row is a paging boundary, not a tape that stopped.
    Passing the flag unconditionally would re-admit through that boundary the
    mid-history false positives the rule exists to exclude (measured DB-wide:
    1,368 rows matching the row shape against 9 matching the run).
    """

    def test_only_the_final_page_asserts_the_series_end(self, monkeypatch: Any) -> None:
        monkeypatch.setattr("analytics.data_sync.BARS_MAX_LIMIT", 3)
        conn = _make_conn()
        pages = [
            _make_df([1_000, 2_000, 3_000], timeframe="1d"),  # full -> boundary
            _make_df([4_000, 5_000], timeframe="1d"),  # short -> series end
        ]
        seen: list[bool] = []
        real = check_ohlcv

        def spy(df: pd.DataFrame, **kw: Any) -> Any:
            seen.append(bool(kw.get("series_ends_here", False)))
            return real(df, **kw)

        calls: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            calls.append(start)
            return pages[len(calls) - 1] if len(calls) <= len(pages) else _EMPTY

        with (
            patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch),
            patch("analytics.data_sync.check_ohlcv", side_effect=spy),
        ):
            backfill(conn, "AAPL", "1d", 0)

        assert seen == [False, True], (
            "the full page is a paging boundary; only the short page ends the tape"
        )

    def test_a_frozen_tail_reaching_the_end_is_not_stored(self) -> None:
        """End to end: the dead-tape bars never reach the table."""
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        # bars 2 and 3 forward-fill bar 1's close at zero volume
        for i in (1, 2):
            for col in ("open", "high", "low", "close"):
                df.loc[i, col] = 101.0
            df.loc[i, "volume"] = 0.0

        with patch("analytics.data_sync.fetch_bars", side_effect=[df, _EMPTY]):
            total = backfill(conn, "AAPL", "1d", 0)

        assert total == 1, "only the last traded bar survives"
        assert get_latest_open_time(conn, "AAPL", "1d") == 1_000


class TestSync:
    def test_fetches_from_latest_open_time(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        captured: list[int] = []

        def capture(c: Any, sym: Any, tf: Any, start: int, **kwargs: Any) -> int:
            captured.append(start)
            return 0

        with patch("analytics.data_sync.backfill", side_effect=capture):
            sync(conn, "AAPL", "1h")
        assert captured == [1_000_000]

    def test_raises_when_no_existing_data(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="Run backfill first"):
            sync(conn, "AAPL", "1h")

    def test_returns_zero_when_no_new_data(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        with patch("analytics.data_sync.fetch_bars", return_value=empty_df):
            total = sync(conn, "AAPL", "1h")
        assert total == 0


def test_backfill_quarantines_bad_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="AAPL", timeframe="1d")
    df.loc[1, "close"] = float("nan")  # one corrupt row
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "AAPL", "1d", 0)
    assert stored == 2  # corrupt row dropped
    row = conn.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE symbol = 'AAPL' AND timeframe = '1d'"
    ).fetchone()
    assert row is not None
    assert row[0] == 2


def test_backfill_clean_data_stores_all_rows() -> None:
    conn = _make_conn()
    df = _make_df([1_000, 2_000, 3_000], symbol="MSFT", timeframe="1d")
    with patch("analytics.data_sync.fetch_bars", return_value=df):
        stored = backfill(conn, "MSFT", "1d", 0)
    assert stored == 3  # unchanged behaviour on clean data


class TestBackfillSessionGapWarning:
    def test_backfill_warns_on_session_gap(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        gappy = SessionGapReport("1d", "session", 2, 3, (date(2024, 1, 3),))
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=gappy),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert any("session gap" in r.message.lower() for r in caplog.records)

    def test_backfill_silent_when_no_gaps(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        clean = SessionGapReport("1d", "session", 3, 3, ())
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=clean),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert not any("session gap" in r.message.lower() for r in caplog.records)


def _bars_at(
    open_times: list[int],
    level: float,
    symbol: str = "AAPL",
    timeframe: str = "1d",
) -> pd.DataFrame:
    """Coherent OHLCV bars whose whole geometry sits at ``level``.

    Scaling open/high/low/close together matters: a frame with a close outside
    its own high is quarantined as bad geometry, which would make a split test
    pass for the wrong reason.
    """
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": t,
                "open": level,
                "high": level * 1.01,
                "low": level * 0.99,
                "close": level,
                "volume": 1_000_000.0,
            }
            for t in open_times
        ],
        columns=OHLCV_COLUMNS,
    )


def _stored_closes(
    conn: duckdb.DuckDBPyConnection, symbol: str = "AAPL"
) -> list[float]:
    return [
        float(r[0])
        for r in conn.execute(
            "SELECT close FROM ohlcv WHERE symbol = ? ORDER BY open_time", [symbol]
        ).fetchall()
    ]


class TestBasisChangedPredicate:
    """The pure half of the guard, at its boundary."""

    def test_a_split_sized_restatement_is_a_change(self) -> None:
        assert basis_changed(400.0, 100.0) is True  # 4:1
        assert basis_changed(48.0, 144.0) is True  # 1:3 reverse

    def test_noise_below_the_tolerance_is_not(self) -> None:
        assert basis_changed(400.0, 402.0) is False  # 0.5%
        assert basis_changed(400.0, 400.0) is False

    def test_the_tolerance_separates_noise_from_every_split(self) -> None:
        """What matters is the MARGIN, not the boundary.

        The exact boundary is not representable in binary floating point
        (``100.0 * 1.01 / 100.0 - 1.0`` is 1.0000000000000009e-2), so a test
        pinning behaviour AT the tolerance would pin a rounding artifact. No
        real decision sits there: the smallest canonical split factor is 25%
        away from parity and a settled candle moves by well under 1%.
        """
        assert ADJUSTMENT_BASIS_TOL < 0.05, "must not swallow a small split"
        for factor in _SPLIT_FACTORS:
            assert basis_changed(100.0, 100.0 * factor) is (factor != 1.0), (
                f"factor {factor} must be classified by the same rule"
            )
        assert basis_changed(100.0, 100.0 * (1.0 + ADJUSTMENT_BASIS_TOL * 2)) is True

    def test_an_unmeasurable_basis_answers_false(self) -> None:
        """Keep-on-doubt: absent or non-positive reads as 'append', never 'restated'."""
        assert basis_changed(None, 100.0) is False
        assert basis_changed(100.0, None) is False
        assert basis_changed(0.0, 100.0) is False


class TestAdjustmentBasisGuard:
    """A split restates history; the overlap bar is where that is observable.

    ``sync`` re-fetches the newest stored bar, so a provider that moved the
    whole series onto a post-split basis hands back a different close for a bar
    that cannot legitimately have changed. Each "does not re-sync" assertion
    below is paired with the first test, which asserts the same channel MOVES —
    otherwise a guard wired to nothing would pass every one of them.
    """

    def test_a_split_restates_the_whole_stored_series(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _bars_at([1_000, 2_000, 3_000], 400.0))
        starts: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            starts.append(start)
            if start == 3_000:  # the tail, already on the post-split basis
                return _bars_at([3_000, 4_000], 100.0)
            return _bars_at([1_000, 2_000, 3_000, 4_000], 100.0)

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            sync(conn, "AAPL", "1d")

        assert starts == [3_000, 1_000], (
            "a restatement must re-sync from the earliest bar"
        )
        assert _stored_closes(conn) == [100.0, 100.0, 100.0, 100.0], (
            "every bar must end on ONE basis — a surviving 400.0 is the seam"
        )

    def test_an_unchanged_overlap_bar_appends_only(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _bars_at([1_000, 2_000, 3_000], 400.0))
        starts: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            starts.append(start)
            return _bars_at([3_000, 4_000], 400.0)

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            sync(conn, "AAPL", "1d")

        assert starts == [3_000], "no restatement means no re-sync"
        assert _stored_closes(conn) == [400.0, 400.0, 400.0, 400.0]

    def test_a_forming_bar_finalising_does_not_re_sync(self) -> None:
        """The overlap bar moves on every sync by design — only a SPLIT-sized move counts."""
        conn = _make_conn()
        upsert_ohlcv(conn, _bars_at([1_000, 2_000, 3_000], 400.0))
        starts: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            starts.append(start)
            return _bars_at([3_000], 402.0)  # +0.5%, a candle that closed

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            sync(conn, "AAPL", "1d")

        assert starts == [3_000], (
            "finalising a forming candle must not re-fetch history"
        )

    def test_a_single_bar_series_cannot_re_sync_itself(self) -> None:
        """earliest == latest, so the re-sync would re-fetch the same page forever."""
        conn = _make_conn()
        upsert_ohlcv(conn, _bars_at([1_000], 400.0))
        starts: list[int] = []

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            starts.append(start)
            return _bars_at([1_000], 100.0)

        with patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch):
            sync(conn, "AAPL", "1d")

        assert starts == [1_000], "nothing behind the overlap bar to repair"

    def test_the_warning_names_the_symbol_and_the_ratio(self, caplog: Any) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _bars_at([1_000, 2_000], 400.0))

        def fake_fetch(
            sym: str, tf: str, start: int, *args: Any, **kwargs: Any
        ) -> pd.DataFrame:
            return (
                _bars_at([2_000], 100.0)
                if start == 2_000
                else _bars_at([1_000, 2_000], 100.0)
            )

        with (
            caplog.at_level("WARNING"),
            patch("analytics.data_sync.fetch_bars", side_effect=fake_fetch),
        ):
            sync(conn, "AAPL", "1d")

        assert "adjustment basis changed AAPL 1d" in caplog.text
