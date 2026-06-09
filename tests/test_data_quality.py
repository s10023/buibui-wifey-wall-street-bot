"""Tests for analytics/data_quality.py."""

import pandas as pd

from analytics.data_quality import DataQualityReport, check_ohlcv

_COLS = ["symbol", "timeframe", "open_time", "open", "high", "low", "close", "volume"]


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Build an OHLCV frame; each row dict overrides the clean defaults."""
    base = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }
    out = []
    for i, r in enumerate(rows):
        row = {**base, "open_time": 1_000 + i * 86_400_000}
        row.update(r)
        out.append(row)
    return pd.DataFrame(out, columns=_COLS)


def test_clean_frame_is_clean() -> None:
    rep = check_ohlcv(_frame([{}, {}, {}]))
    assert isinstance(rep, DataQualityReport)
    assert rep.n_rows == 3
    assert rep.quarantine_idx == ()
    assert rep.is_clean is True


def test_nan_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"close": float("nan")}, {}]))
    assert rep.nan_idx == (1,)
    assert 1 in rep.quarantine_idx
    assert rep.is_clean is False


def test_nonpositive_price_is_quarantined() -> None:
    rep = check_ohlcv(_frame([{}, {"low": 0.0}, {"open": -5.0}]))
    assert rep.nonpositive_price_idx == (1, 2)
    assert set(rep.quarantine_idx) == {1, 2}


def test_broken_bar_geometry_is_quarantined() -> None:
    # high below low; and high below open
    rep = check_ohlcv(_frame([{"high": 98.0}, {"high": 100.5, "open": 101.0}]))
    assert set(rep.bad_bar_idx) == {0, 1}
    assert set(rep.quarantine_idx) == {0, 1}


def test_empty_frame() -> None:
    rep = check_ohlcv(_frame([]))
    assert rep.n_rows == 0
    assert rep.quarantine_idx == ()
    assert rep.is_clean is False
