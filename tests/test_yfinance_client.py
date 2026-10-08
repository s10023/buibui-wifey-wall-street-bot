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


def test_fetch_history_never_auto_adjusts_prices() -> None:
    """As-of adjustment guard: auto_adjust must stay False so the close is the raw
    print. (Split factors are still back-applied by yfinance — a bounded as-of
    violation documented in the module docstring and docs/redesign/phase0-lookahead-audit.md.)
    """
    from utils.yfinance_client import fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]},
        index=pd.DatetimeIndex(["2026-01-02"], tz="America/New_York"),
    )
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        fetch_history("AAPL", interval="1d")
    kwargs = mock_ticker.history.call_args.kwargs
    assert kwargs["auto_adjust"] is False
    assert kwargs["actions"] is False


def test_fetch_total_return_close_is_the_one_auto_adjusted_fetch() -> None:
    """The total-return fetch adjusts for dividends; fetch_history must not."""
    from utils.yfinance_client import fetch_total_return_close

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame(
        {"Open": [1.0, 1.1], "Close": [1.5, 1.6]},
        index=pd.DatetimeIndex(["2026-01-02", "2026-01-05"], tz="America/New_York"),
    )
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        s = fetch_total_return_close("SPY")
    kwargs = mock_ticker.history.call_args.kwargs
    assert kwargs["auto_adjust"] is True
    assert kwargs["interval"] == "1d"
    assert s.tolist() == [1.5, 1.6]
    assert pd.DatetimeIndex(s.index).tz is None
    assert s.index[0] == pd.Timestamp("2026-01-02 05:00")


def test_fetch_total_return_close_empty_on_no_data() -> None:
    from utils.yfinance_client import fetch_total_return_close

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        assert fetch_total_return_close("SPY").empty
