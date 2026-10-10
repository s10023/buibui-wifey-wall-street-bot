import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pandas as pd
import pytest
from yfinance.exceptions import (  # type: ignore[import-untyped]
    YFException,
    YFRateLimitError,
)

from analytics import analytics_runner
from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.data_store import init_schema, upsert_ohlcv
from utils.config_validation import load_pundit_ledger_symbols


class TestResolveSymbols:
    def test_explicit_symbols_win(self) -> None:
        assert analytics_runner._resolve_symbols(["AAPL"], use_universe=True) == [
            "AAPL"
        ]

    @patch("analytics.analytics_runner.load_stocks_config")
    def test_default_uses_stocks_watchlist(self, mock_stocks: MagicMock) -> None:
        mock_stocks.return_value = {"AAPL": {}, "MSFT": {}}
        with patch("analytics.analytics_runner.load_universe_policy"):
            assert analytics_runner._resolve_symbols(None, use_universe=False) == [
                "AAPL",
                "MSFT",
            ]

    @patch("analytics.analytics_runner.load_research_universe")
    def test_universe_flag_uses_research_universe(self, mock_uni: MagicMock) -> None:
        uni = MagicMock()
        uni.active_symbols.return_value = ["AAPL", "SPY", "JPM"]
        uni.describe.return_value = "Research universe: ..."
        mock_uni.return_value = uni
        assert analytics_runner._resolve_symbols(None, use_universe=True) == [
            "AAPL",
            "SPY",
            "JPM",
        ]

    @patch("analytics.analytics_runner.load_pundit_ledger_symbols")
    def test_pundit_flag_uses_ledger(self, mock_ledger: MagicMock) -> None:
        mock_ledger.return_value = ["^GSPC", "GC=F", "NVDA"]
        assert analytics_runner._resolve_symbols(None, use_pundit=True) == [
            "^GSPC",
            "GC=F",
            "NVDA",
        ]

    @patch("analytics.analytics_runner.load_stocks_config")
    @patch("analytics.analytics_runner.load_pundit_ledger_symbols")
    def test_pundit_flag_does_not_fall_back_to_watchlist(
        self, mock_ledger: MagicMock, mock_stocks: MagicMock
    ) -> None:
        """An empty ledger must EXIT, never silently sync the watchlist instead.

        This is the defect the flag exists to fix, one level down: a fallback here
        would resync the same 13 stocks.json names that `go-live` already covers and
        report success, leaving the ledger's index/futures underlyings untouched —
        indistinguishable from the flag working.
        """
        mock_ledger.return_value = []
        with pytest.raises(SystemExit):
            analytics_runner._resolve_symbols(None, use_pundit=True)
        mock_stocks.assert_not_called()


class TestCoreFlag:
    @patch("analytics.analytics_runner.load_stocks_config")
    def test_core_resolves_to_the_caret_symbol_without_argv(
        self, mock_stocks: MagicMock
    ) -> None:
        assert analytics_runner._resolve_symbols(None, use_core=True) == ["^GSPC"]
        mock_stocks.assert_not_called()

    def test_core_is_exclusive_with_the_other_sources(self) -> None:
        from cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["analytics", "sync", "--core"])
        assert args.core and not args.pundit and not args.universe
        with pytest.raises(SystemExit):
            parser.parse_args(["analytics", "sync", "--core", "--pundit"])


_STORED_MS = int(pd.Timestamp("2024-01-16 05:00", tz="UTC").timestamp() * 1000)


def _provider_frame(stamps: list[str]) -> pd.DataFrame:
    """What ``yf.Ticker(...).history`` returns: capitalised, tz-aware columns."""
    idx = pd.DatetimeIndex(stamps, tz="UTC")
    n = len(stamps)
    return pd.DataFrame(
        {
            "Open": [100.0] * n,
            "High": [102.0] * n,
            "Low": [99.0] * n,
            "Close": [101.0] * n,
            "Volume": [1_000_000.0] * n,
        },
        index=idx,
    )


class TestRunSyncTotalFailure:
    """A sync where every fetch comes back empty must exit non-zero (#444).

    On 2026-10-03 a DNS outage made yfinance log ``Cookie/crumb fetch failed``
    and return an empty frame for all 400 calls; every series reported
    ``0 new rows`` and the task exited 0, so ``job.sh`` never notified. These
    tests drive the real ``sync`` → ``fetch_bars`` → ``fetch_history`` chain and
    mock only ``yf.Ticker``, the network boundary.
    """

    SYMBOLS = ["AAPL", "MSFT"]

    def _run(self, client: MagicMock, symbols: list[str] | None = None) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        for symbol in self.SYMBOLS:
            upsert_ohlcv(
                conn,
                pd.DataFrame(
                    [[symbol, "1d", _STORED_MS, 100.0, 102.0, 99.0, 101.0, 1e6]],
                    columns=OHLCV_COLUMNS,
                ),
            )

        @contextmanager
        def session(_: Path) -> Iterator[duckdb.DuckDBPyConnection]:
            yield conn

        with (
            patch.object(analytics_runner, "_open_session", session),
            patch("utils.yfinance_client.yf.Ticker", client),
        ):
            analytics_runner.run_sync(symbols or self.SYMBOLS, ["1d"])

    def test_every_fetch_empty_exits_non_zero(self) -> None:
        client = MagicMock()
        client.return_value.history.return_value = pd.DataFrame()
        with pytest.raises(SystemExit) as exc:
            self._run(client)
        assert exc.value.code == 1
        # Positive control: the exit came from fetches that ran, not a skip.
        assert client.return_value.history.call_count == len(self.SYMBOLS)

    @pytest.mark.parametrize(
        "stamps",
        [
            pytest.param(["2024-01-15 05:00", "2024-01-16 05:00"], id="overlap-only"),
            pytest.param(["2024-01-12 05:00", "2024-01-15 05:00"], id="older-only"),
        ],
    )
    def test_answered_with_no_new_bars_exits_zero(
        self, stamps: list[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """``older-only`` yields literally ``0 new rows`` and must still pass."""
        client = MagicMock()
        client.return_value.history.return_value = _provider_frame(stamps)
        with caplog.at_level(logging.INFO):
            self._run(client)
        assert client.return_value.history.call_count == len(self.SYMBOLS)
        assert "Sync summary: 0 of 2 fetches returned no data" in caplog.text

    def test_partial_failure_exits_zero(self) -> None:
        client = MagicMock()
        client.return_value.history.side_effect = [
            pd.DataFrame(),
            _provider_frame(["2024-01-16 05:00"]),
        ]
        self._run(client)
        assert client.return_value.history.call_count == 2

    def test_one_tickers_provider_error_does_not_strand_the_rest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """#474: AVB `1wk` raised a Dividends out-of-range YFException on
        2026-10-10 and aborted the run, leaving every later member unsynced."""
        client = MagicMock()
        client.return_value.history.side_effect = [
            YFException("The following 'Dividends' events are out-of-range"),
            _provider_frame(["2024-01-16 05:00"]),
        ]
        with caplog.at_level(logging.INFO):
            self._run(client)  # exits 0: one of two fetches failed
        assert client.return_value.history.call_count == 2
        assert "AAPL 1d: YFException" in caplog.text
        assert "Sync complete: MSFT 1d — 1 new rows" in caplog.text
        assert "Sync summary: 1 of 2 fetches returned no data" in caplog.text

    def test_provider_errors_on_every_fetch_still_exit_non_zero(self) -> None:
        client = MagicMock()
        client.return_value.history.side_effect = YFException("bad payload")
        with pytest.raises(SystemExit) as exc:
            self._run(client)
        assert exc.value.code == 1
        assert client.return_value.history.call_count == len(self.SYMBOLS)

    def test_a_rate_limit_still_aborts_the_run(self) -> None:
        """YFRateLimitError subclasses YFException, yet every later call would
        hit the same limit, so it must not be skipped like a bad payload."""
        client = MagicMock()
        client.return_value.history.side_effect = YFRateLimitError()
        with pytest.raises(YFRateLimitError):
            self._run(client)
        assert client.return_value.history.call_count == 1

    def test_unbackfilled_series_are_not_fetch_failures(self) -> None:
        """A series with no stored bars is skipped before any fetch."""
        client = MagicMock()
        client.return_value.history.return_value = pd.DataFrame()
        self._run(client, symbols=["NVDA"])
        client.return_value.history.assert_not_called()


class TestLoadPunditLedgerSymbols:
    def _write(self, tmp_path: Path, rows: list[object]) -> Path:
        p = tmp_path / "pundit-calls.jsonl"
        p.write_text(
            "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows),
            encoding="utf-8",
        )
        return p

    def test_returns_sorted_distinct_symbols(self, tmp_path: Path) -> None:
        p = self._write(
            tmp_path,
            [
                {"symbol": "NVDA"},
                {"symbol": "^GSPC"},
                {"symbol": "NVDA"},
                {"symbol": "GC=F"},
            ],
        )
        assert load_pundit_ledger_symbols(p) == ["GC=F", "NVDA", "^GSPC"]

    @pytest.mark.parametrize(
        "bad", [None, "", "  ", "none", "NULL", "n/a", "unspecified", "tbd"]
    )
    def test_unresolved_symbols_are_dropped(self, tmp_path: Path, bad: object) -> None:
        """A JSON null must not become the string 'None' and get fetched."""
        p = self._write(tmp_path, [{"symbol": bad}, {"symbol": "NVDA"}])
        assert load_pundit_ledger_symbols(p) == ["NVDA"]

    def test_malformed_lines_do_not_block_the_rest(self, tmp_path: Path) -> None:
        """One bad row must not stop the refresh of every other symbol."""
        p = self._write(
            tmp_path,
            ["{not json", "[1,2,3]", "", {"symbol": "TLT"}, {"no_symbol_key": 1}],
        )
        assert load_pundit_ledger_symbols(p) == ["TLT"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_pundit_ledger_symbols(tmp_path / "nope.jsonl")

    def test_shares_one_invalid_symbol_definition_with_the_scorer(self) -> None:
        """The sync path must reject exactly what the scorer rejects.

        If these diverge, `--pundit` fetches a symbol `pundit_score` discards (waste)
        or skips one it scores (the STALE-forever bug, reintroduced).
        """
        from tools import pundit_score
        from utils.config_validation import INVALID_LEDGER_SYMBOLS

        assert pundit_score._INVALID_SYMBOLS is INVALID_LEDGER_SYMBOLS
