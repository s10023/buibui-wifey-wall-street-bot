"""Unit tests for utils.yfinance_client.

yfinance is mocked end-to-end — these tests must never touch the network.
"""

from unittest.mock import MagicMock, patch

import pandas as pd


def test_fetch_history_calls_yfinance_with_canonical_args() -> None:
    from utils.yfinance_client import fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-01-02"], tz="America/New_York"),
    )
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker) as mock_cls:
        df = fetch_history("AAPL", interval="1d", period="6mo")
        mock_cls.assert_called_once_with("AAPL")
        mock_ticker.history.assert_called_once_with(
            period="6mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is None


def test_fetch_history_returns_empty_df_on_no_data() -> None:
    from utils.yfinance_client import fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        df = fetch_history("INVALID", interval="1d", period="6mo")
        assert df.empty
