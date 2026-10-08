import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from analytics import analytics_runner
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
