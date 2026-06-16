from unittest.mock import MagicMock, patch

from analytics import analytics_runner


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
