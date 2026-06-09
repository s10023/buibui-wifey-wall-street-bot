"""Tests for analytics/data_quality.py."""

import pandas as pd

from analytics.data_quality import DataQualityReport, check_ohlcv, quarantine

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


def test_duplicate_timestamp_quarantines_later_row() -> None:
    df = _frame([{}, {}, {}])
    df.loc[2, "open_time"] = df.loc[1, "open_time"]  # row 2 duplicates row 1
    rep = check_ohlcv(df)
    assert rep.duplicate_time_idx == (2,)
    assert 2 in rep.quarantine_idx


def test_nonmonotonic_timestamp_is_warned_not_dropped() -> None:
    df = _frame([{}, {}, {}])
    # row 0's open_time is 1_000 (see _frame); 0 is earlier than every row -> backwards
    df.loc[2, "open_time"] = 0
    rep = check_ohlcv(df)
    assert rep.nonmonotonic_idx == (2,)
    assert 2 not in rep.quarantine_idx  # warn-only


def test_zero_volume_is_warned_not_dropped() -> None:
    rep = check_ohlcv(_frame([{}, {"volume": 0.0}, {}]))
    assert rep.zero_volume_idx == (1,)
    assert 1 not in rep.quarantine_idx


def test_return_outlier_flagged() -> None:
    # row 1 close jumps +80% vs row 0 (101 -> 181.8): outlier, not split-like
    rep = check_ohlcv(
        _frame([{}, {"open": 180.0, "high": 182.0, "low": 179.0, "close": 181.8}, {}])
    )
    assert 1 in rep.return_outlier_idx
    assert 1 not in rep.suspected_split_idx


def test_suspected_split_flagged() -> None:
    # row 1 close halves vs row 0 (101 -> ~50.5): ratio ~0.5 == 2:1 split
    rep = check_ohlcv(
        _frame([{}, {"open": 50.0, "high": 51.0, "low": 49.5, "close": 50.5}, {}])
    )
    assert 1 in rep.suspected_split_idx


def test_normal_move_not_flagged() -> None:
    # +3% day is neither outlier nor split
    rep = check_ohlcv(
        _frame([{}, {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0}, {}])
    )
    assert rep.return_outlier_idx == ()
    assert rep.suspected_split_idx == ()


def test_quarantine_drops_only_bad_rows() -> None:
    df = _frame([{}, {"close": float("nan")}, {}, {"low": 0.0}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 2
    assert len(dropped) == 2
    assert clean["close"].tolist() == [101.0, 101.0]
    # input frame is untouched
    assert len(df) == 4


def test_quarantine_clean_frame_is_passthrough() -> None:
    df = _frame([{}, {}, {}])
    rep = check_ohlcv(df)
    clean, dropped = quarantine(df, rep)
    assert len(clean) == 3
    assert dropped.empty
    assert list(clean.columns) == list(df.columns)
