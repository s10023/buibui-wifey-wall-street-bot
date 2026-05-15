"""Tests for monitor/price_lib.py — pure price monitor logic."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rich.text import Text
from utils.binance_client import create_client, get_wallet_target, sync_binance_time

from monitor.price_lib import (
    batch_get_asia_open,
    batch_get_klines,
    clear_screen,
    format_pct,
    format_pct_rich,
    format_pct_simple,
    get_klines,
    get_price_changes,
    sort_table,
    sort_table_raw,
)
from tests.conftest import strip_ansi


class TestFormatPct:
    """Tests for format_pct()."""

    def test_positive_value(self) -> None:
        assert strip_ansi(str(format_pct(2.5))) == "+2.50%"

    def test_negative_value(self) -> None:
        assert strip_ansi(str(format_pct(-1.3))) == "-1.30%"

    def test_zero_value(self) -> None:
        assert strip_ansi(str(format_pct(0))) == "+0.00%"

    def test_string_number(self) -> None:
        assert strip_ansi(str(format_pct("3.14"))) == "+3.14%"

    def test_non_numeric_returns_input(self) -> None:
        assert format_pct("N/A") == "N/A"

    def test_positive_has_color_codes(self) -> None:
        result = str(format_pct(1.0))
        assert "\033[" in result or "\x1b[" in result

    def test_negative_has_color_codes(self) -> None:
        result = str(format_pct(-1.0))
        assert "\033[" in result or "\x1b[" in result


class TestFormatPctSimple:
    """Tests for format_pct_simple()."""

    def test_positive(self) -> None:
        assert format_pct_simple(2.5) == "+2.50%"

    def test_negative(self) -> None:
        assert format_pct_simple(-1.3) == "-1.30%"

    def test_zero(self) -> None:
        assert format_pct_simple(0) == "+0.00%"

    def test_string_number(self) -> None:
        assert format_pct_simple("3.14") == "+3.14%"

    def test_non_numeric_returns_string(self) -> None:
        assert format_pct_simple("N/A") == "N/A"


class TestSortTable:
    """Tests for sort_table()."""

    def setup_method(self) -> None:
        self.headers = [
            "Symbol",
            "Last Price",
            "15m %",
            "1h %",
            "4h %",
            "Since Asia 8AM",
            "24h %",
        ]
        self.table = [
            ["BTCUSDT", "62457.10", "+0.53%", "+1.42%", "+1.80%", "+0.88%", "+2.31%"],
            ["ETHUSDT", "3408.50", "+0.22%", "+1.05%", "+1.30%", "+0.71%", "+1.74%"],
            ["SOLUSDT", "143.22", "-0.08%", "+0.34%", "+0.50%", "+0.11%", "+0.89%"],
        ]

    def test_sort_by_15m_descending(self) -> None:
        result = sort_table(self.table, self.headers, "change_15m", True)
        assert result[0][0] == "BTCUSDT"
        assert result[-1][0] == "SOLUSDT"

    def test_sort_by_15m_ascending(self) -> None:
        result = sort_table(self.table, self.headers, "change_15m", False)
        assert result[0][0] == "SOLUSDT"
        assert result[-1][0] == "BTCUSDT"

    def test_sort_by_1h(self) -> None:
        result = sort_table(self.table, self.headers, "change_1h", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_by_4h(self) -> None:
        result = sort_table(self.table, self.headers, "change_4h", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_by_24h(self) -> None:
        result = sort_table(self.table, self.headers, "change_24h", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_by_asia(self) -> None:
        result = sort_table(self.table, self.headers, "change_asia", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_preserves_row_integrity(self) -> None:
        result = sort_table(self.table, self.headers, "change_15m", True)
        btc_row = [r for r in result if r[0] == "BTCUSDT"][0]
        assert btc_row[1] == "62457.10"

    def test_sort_with_ansi_codes(self) -> None:
        """Sort should strip ANSI codes before comparing."""
        table_with_ansi = [
            [
                "BTCUSDT",
                "62457",
                "\033[32m+0.53%\033[0m",
                "+1.42%",
                "+1.80%",
                "+0.88%",
                "+2.31%",
            ],
            [
                "ETHUSDT",
                "3408",
                "\033[32m+0.22%\033[0m",
                "+1.05%",
                "+1.30%",
                "+0.71%",
                "+1.74%",
            ],
        ]
        result = sort_table(table_with_ansi, self.headers, "change_15m", True)
        assert result[0][0] == "BTCUSDT"

    def test_sort_invalid_column_raises(self) -> None:
        with pytest.raises(KeyError):
            sort_table(self.table, self.headers, "invalid_col", True)


class TestClearScreen:
    """Tests for clear_screen()."""

    @patch("monitor.price_lib.os.system")
    @patch("monitor.price_lib.os.name", "posix")
    def test_unix_clear(self, mock_system: Any) -> None:
        clear_screen()
        mock_system.assert_called_once_with("clear")

    @patch("monitor.price_lib.os.system")
    @patch("monitor.price_lib.os.name", "nt")
    def test_windows_clear(self, mock_system: Any) -> None:
        clear_screen()
        mock_system.assert_called_once_with("cls")


class TestSyncBinanceTime:
    """Tests for sync_binance_time()."""

    @patch("utils.binance_client.time.time", return_value=1700000000.0)
    def test_sets_time_offset(self, _mock_time: Any) -> None:
        mock_client = MagicMock()
        mock_client.get_server_time.return_value = {"serverTime": 1700000001000}
        sync_binance_time(mock_client)
        assert mock_client.TIME_OFFSET == 1000

    @patch("utils.binance_client.time.time", return_value=1700000001.0)
    def test_negative_offset(self, _mock_time: Any) -> None:
        mock_client = MagicMock()
        mock_client.get_server_time.return_value = {"serverTime": 1700000000000}
        sync_binance_time(mock_client)
        assert mock_client.TIME_OFFSET == -1000

    def test_raises_runtime_error_on_api_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.get_server_time.side_effect = Exception("network error")
        with pytest.raises(
            RuntimeError, match="Failed to sync time with Binance server"
        ):
            sync_binance_time(mock_client)


class TestCreateClient:
    """Tests for create_client() startup validation."""

    def test_raises_value_error_when_api_key_missing(self) -> None:
        with (
            patch("utils.binance_client.load_dotenv"),
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="BINANCE_API_KEY"),
        ):
            create_client()

    def test_raises_value_error_when_api_secret_missing(self) -> None:
        with (
            patch("utils.binance_client.load_dotenv"),
            patch.dict("os.environ", {"BINANCE_API_KEY": "key"}, clear=True),
            pytest.raises(ValueError, match="BINANCE_API_SECRET"),
        ):
            create_client()


class TestGetWalletTarget:
    """Tests for get_wallet_target() env parsing."""

    def test_returns_single_float(self) -> None:
        with patch.dict("os.environ", {"WALLET_TARGET": "2000"}):
            targets, invalid = get_wallet_target()
            assert targets == [2000.0]
            assert invalid == []

    def test_returns_multiple_floats(self) -> None:
        with patch.dict("os.environ", {"WALLET_TARGET": "500,1000,5000"}):
            targets, invalid = get_wallet_target()
            assert targets == [500.0, 1000.0, 5000.0]
            assert invalid == []

    def test_returns_empty_when_env_not_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            targets, invalid = get_wallet_target()
            assert targets == []
            assert invalid == []

    def test_skips_invalid_entries(self) -> None:
        with patch.dict("os.environ", {"WALLET_TARGET": "1000,bad,2000"}):
            targets, invalid = get_wallet_target()
            assert targets == [1000.0, 2000.0]
            assert invalid == ["bad"]

    def test_returns_all_invalid(self) -> None:
        with patch.dict("os.environ", {"WALLET_TARGET": "bad,also_bad"}):
            targets, invalid = get_wallet_target()
            assert targets == []
            assert invalid == ["bad", "also_bad"]


class TestGetKlines:
    """Tests for get_klines()."""

    def test_returns_last_kline(self) -> None:
        kline1 = ["first_kline"]
        kline2 = ["second_kline"]
        mock_client = MagicMock()
        mock_client.get_klines.return_value = [kline1, kline2]
        result = get_klines(mock_client, "BTCUSDT", "15m", 15)
        assert result == kline2

    def test_returns_none_on_error(self) -> None:
        mock_client = MagicMock()
        mock_client.get_klines.side_effect = Exception("API error")
        assert get_klines(mock_client, "BTCUSDT", "15m", 15) is None

    def test_returns_none_on_empty_klines(self) -> None:
        mock_client = MagicMock()
        mock_client.get_klines.return_value = []
        assert get_klines(mock_client, "BTCUSDT", "15m", 15) is None


class TestBatchGetKlines:
    """Tests for batch_get_klines() error handling."""

    def test_skips_failed_worker_futures(self) -> None:
        mock_client = MagicMock()
        # First call raises, second call succeeds
        mock_client.get_klines.side_effect = [Exception("fail"), [["ok_kline"]]]
        results = batch_get_klines(mock_client, ["BTCUSDT", "ETHUSDT"], [("15m", 15)])
        # At least one result should be None (failed), one should have data
        assert len(results) <= 2  # failed workers are skipped (continue), not added
        # The successful one should have the kline data
        successful = [v for v in results.values() if v is not None]
        assert len(successful) == 1


class TestBatchGetAsiaOpen:
    """Tests for batch_get_asia_open() error handling."""

    def test_skips_failed_worker_futures(self) -> None:
        mock_client = MagicMock()
        with patch(
            "monitor.price_lib.get_open_price_asia", side_effect=Exception("fail")
        ):
            # Worker raises — should not propagate; result just omitted
            results = batch_get_asia_open(mock_client, ["BTCUSDT"])
            assert results == {}


class TestGetPriceChanges:
    """Tests for get_price_changes()."""

    def test_returns_table_for_valid_symbols(
        self, mock_ticker_data: list[dict[str, Any]], mock_kline_data: list[Any]
    ) -> None:
        mock_client = MagicMock()
        mock_client.get_ticker.return_value = mock_ticker_data
        mock_client.get_klines.return_value = [mock_kline_data]
        with patch("monitor.price_lib.get_open_price_asia", return_value=62000.0):
            table, invalid = get_price_changes(mock_client, ["BTCUSDT"])
            assert len(table) == 1
            assert table[0][0] == "BTCUSDT"
            assert len(invalid) == 0

    def test_invalid_symbol_in_ticker(
        self, mock_ticker_data: list[dict[str, Any]]
    ) -> None:
        mock_client = MagicMock()
        mock_client.get_ticker.return_value = mock_ticker_data
        with (
            patch("monitor.price_lib.batch_get_klines", return_value={}),
            patch("monitor.price_lib.get_open_price_asia", return_value=None),
        ):
            table, invalid = get_price_changes(mock_client, ["XYZUSDT"])
            assert len(table) == 1
            assert table[0][1] == "Error"
            assert len(invalid) == 1

    def test_ticker_api_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.get_ticker.side_effect = Exception("API down")
        table, invalid = get_price_changes(mock_client, ["BTCUSDT"])
        assert table[0][1] == "Error"

    def test_telegram_mode_uses_simple_format(
        self, mock_ticker_data: list[dict[str, Any]], mock_kline_data: list[Any]
    ) -> None:
        mock_client = MagicMock()
        mock_client.get_ticker.return_value = mock_ticker_data
        mock_client.get_klines.return_value = [mock_kline_data]
        with patch("monitor.price_lib.get_open_price_asia", return_value=62000.0):
            table, _ = get_price_changes(mock_client, ["BTCUSDT"], telegram=True)
            for cell in table[0][2:]:
                assert "\033[" not in str(cell)


class TestFormatPctRich:
    def test_positive_returns_green_text(self) -> None:
        result = format_pct_rich(2.5)
        assert isinstance(result, Text)
        assert "+2.50%" in result.plain

    def test_negative_returns_red_text(self) -> None:
        result = format_pct_rich(-1.5)
        assert isinstance(result, Text)
        assert "-1.50%" in result.plain

    def test_zero_returns_yellow_text(self) -> None:
        result = format_pct_rich(0.0)
        assert isinstance(result, Text)
        assert "+0.00%" in result.plain

    def test_returns_text_instance_not_string(self) -> None:
        assert not isinstance(format_pct_rich(1.0), str)


class TestSortTableRaw:
    ROWS: list[list[Any]] = [
        ["C", "150", 2.0, None, None, None, None],
        ["A", "100", 1.0, None, None, None, None],
        ["B", "200", 3.0, None, None, None, None],
    ]

    def test_descending(self) -> None:
        result = sort_table_raw(self.ROWS, col_idx=2, reverse=True)
        assert [r[0] for r in result] == ["B", "C", "A"]

    def test_ascending(self) -> None:
        result = sort_table_raw(self.ROWS, col_idx=2, reverse=False)
        assert [r[0] for r in result] == ["A", "C", "B"]

    def test_none_sorts_last_in_descending(self) -> None:
        rows: list[list[Any]] = [
            ["A", "100", None, None, None, None, None],
            ["B", "200", 1.0, None, None, None, None],
        ]
        result = sort_table_raw(rows, col_idx=2, reverse=True)
        assert result[0][0] == "B"
        assert result[1][0] == "A"

    def test_none_sorts_last_in_ascending(self) -> None:
        rows: list[list[Any]] = [
            ["A", "100", None, None, None, None, None],
            ["B", "200", 1.0, None, None, None, None],
        ]
        result = sort_table_raw(rows, col_idx=2, reverse=False)
        assert result[0][0] == "B"
        assert result[1][0] == "A"
