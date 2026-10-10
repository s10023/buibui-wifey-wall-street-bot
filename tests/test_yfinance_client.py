"""Unit tests for utils.yfinance_client.

yfinance is mocked end-to-end — these tests must never touch the network.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from yfinance.exceptions import (  # type: ignore[import-untyped]
    YFException,
    YFRateLimitError,
)


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


def test_fetch_history_start_replaces_period() -> None:
    """A ``start`` is sent instead of ``period`` (#323: it sets the 1wk anchor)."""
    from utils.yfinance_client import fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        fetch_history("ABBV", interval="1wk", start="2013-01-07")
    mock_ticker.history.assert_called_once_with(
        start="2013-01-07",
        interval="1wk",
        auto_adjust=False,
        actions=False,
    )


def test_fetch_last_price_reads_the_quote() -> None:
    from utils.yfinance_client import fetch_last_price

    mock_ticker = MagicMock()
    mock_ticker.fast_info = {"lastPrice": 184.06}
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        assert fetch_last_price("AVB") == 184.06


def test_fetch_last_price_answers_none_on_any_failure() -> None:
    """The caller is an advisory cross-check that must never break a sync."""
    from utils.yfinance_client import fetch_last_price

    with patch("utils.yfinance_client.yf.Ticker", side_effect=RuntimeError("boom")):
        assert fetch_last_price("AVB") is None
    mock_ticker = MagicMock()
    mock_ticker.fast_info = {"lastPrice": 0.0}
    with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
        assert fetch_last_price("AVB") is None


def test_fetch_history_wraps_a_payload_error_as_provider_error() -> None:
    """#474: a per-ticker YFException becomes ProviderError, cause kept."""
    from utils.yfinance_client import ProviderError, fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = YFException("Dividends events are out-of-range")
    with (
        patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker),
        pytest.raises(ProviderError, match="AVB 1wk: YFException") as exc,
    ):
        fetch_history("AVB", interval="1wk")
    assert isinstance(exc.value.__cause__, YFException)


def test_fetch_history_lets_a_rate_limit_through_unwrapped() -> None:
    from utils.yfinance_client import ProviderError, fetch_history

    mock_ticker = MagicMock()
    mock_ticker.history.side_effect = YFRateLimitError()
    with (
        patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker),
        pytest.raises(YFRateLimitError) as exc,
    ):
        fetch_history("AAPL", interval="1d")
    assert not isinstance(exc.value, ProviderError)
