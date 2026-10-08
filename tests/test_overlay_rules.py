"""OV-1 rule, calendar mapping and frame: semantics and causality.

The causality check mirrors the H1 audit's: truncate the signal series at six
cuts and require every position that the truncated data can determine to match
the full-series answer. A "did not change" check is satisfied both when the
invariant holds and when the perturbation never arrives, so it ships two
positive controls that observe the same channel and must be flagged: a rule
that reads tomorrow's close, and the ``lag=0`` mapping that reads the close
ending the session being earned.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

from analytics.overlay.frame import build_frame
from analytics.overlay.rules import ma_signal, position_on

CUTS = 6


def _close(n: int = 900, seed: int = 11) -> pd.Series:
    """A weekday random-walk close; volatile enough that the SMA is crossed often."""
    idx = pd.bdate_range("1928-01-02", periods=n)
    steps = np.random.default_rng(seed).normal(0.0, 0.015, n)
    return pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)


def _return_calendar(close: pd.Series) -> pd.DatetimeIndex:
    """The close's sessions plus every Saturday in range, like the French file."""
    sats = pd.date_range(close.index[0], close.index[-1], freq="W-SAT")
    return pd.DatetimeIndex(close.index.union(sats))


class TestMaSignal:
    def test_warm_up_is_nan_not_flat(self) -> None:
        sig = ma_signal(_close(), window=200)
        assert sig.iloc[:199].isna().all()
        assert sig.iloc[199:].notna().all()

    def test_above_sma_is_long(self) -> None:
        close = pd.Series(
            [1.0, 1.0, 1.0, 2.0, 0.5], index=pd.bdate_range("2020-01-01", periods=5)
        )
        assert ma_signal(close, window=3).tolist()[2:] == [0.0, 1.0, 0.0]


class TestPositionOn:
    def _sig(self) -> pd.Series:
        # Thu, Fri, Mon.
        return pd.Series(
            [1.0, 0.0, 1.0],
            index=pd.to_datetime(["1930-01-02", "1930-01-03", "1930-01-06"]),
        )

    def test_lag_one_reads_the_prior_close(self) -> None:
        dates = pd.to_datetime(["1930-01-03", "1930-01-04", "1930-01-06", "1930-01-07"])
        pos = position_on(self._sig(), pd.DatetimeIndex(dates), lag=1)
        # Fri ← Thu; Sat ← Fri; Mon ← Fri (no Saturday close); Tue ← Mon.
        assert pos.tolist() == [1.0, 0.0, 0.0, 1.0]

    def test_lag_two_adds_one_session_of_delay(self) -> None:
        dates = pd.to_datetime(["1930-01-06", "1930-01-07"])
        pos = position_on(self._sig(), pd.DatetimeIndex(dates), lag=2)
        assert pos.tolist() == [1.0, 0.0]

    def test_no_prior_close_is_nan(self) -> None:
        pos = position_on(
            self._sig(), pd.DatetimeIndex(pd.to_datetime(["1930-01-02"])), lag=1
        )
        assert np.isnan(pos.iloc[0])

    def test_lag_zero_reads_the_same_session(self) -> None:
        pos = position_on(
            self._sig(), pd.DatetimeIndex(pd.to_datetime(["1930-01-03"])), lag=0
        )
        assert pos.tolist() == [0.0]

    def test_negative_lag_is_refused(self) -> None:
        with pytest.raises(ValueError):
            position_on(self._sig(), pd.DatetimeIndex([]), lag=-1)


def _disagreements(rule: Callable[[pd.Series], pd.Series], lag: int) -> int:
    """Cuts at which the truncated series changes a position it fully determines."""
    close = _close()
    dates = _return_calendar(close)
    full = position_on(rule(close), dates, lag=lag)
    flagged = 0
    for c in np.linspace(300, len(close) - 2, CUTS).astype(int):
        cut = close.index[c]
        nxt = close.index[c + 1]
        # Every return session up to the next close needs only closes <= cut.
        eligible = dates[dates <= nxt]
        trunc = position_on(rule(close.loc[:cut]), eligible, lag=lag)
        if not trunc.equals(full.loc[eligible]):
            flagged += 1
    return flagged


class TestCausality:
    def test_ov1_rule_is_causal_at_every_cut(self) -> None:
        assert _disagreements(lambda c: ma_signal(c, 200), lag=1) == 0

    def test_execution_lag_arm_is_causal(self) -> None:
        assert _disagreements(lambda c: ma_signal(c, 200), lag=2) == 0

    def test_positive_control_peeking_rule_is_flagged(self) -> None:
        def peek(c: pd.Series) -> pd.Series:
            return (c.shift(-1) > c).astype(float).where(c.shift(-1).notna())

        assert _disagreements(peek, lag=1) >= 1

    def test_positive_control_same_session_mapping_is_flagged(self) -> None:
        def daily_up(c: pd.Series) -> pd.Series:
            return (c > c.shift(1)).astype(float).where(c.shift(1).notna())

        assert _disagreements(daily_up, lag=0) >= 1
        assert _disagreements(daily_up, lag=1) == 0


class TestBuildFrame:
    def _inputs(self) -> tuple[pd.Series, pd.Series, pd.Series]:
        close = _close()
        dates = _return_calendar(close)
        mkt = pd.Series(0.0005, index=dates)
        rf = pd.Series(0.0001, index=dates)
        return close, mkt, rf

    def test_saturdays_are_kept(self) -> None:
        close, mkt, rf = self._inputs()
        frame = build_frame(close, mkt, rf, start=pd.Timestamp("1928-01-01"))
        assert (pd.DatetimeIndex(frame.index).dayofweek == 5).sum() > 0
        assert frame.index[-1] == mkt.index[-1]

    def test_panel_opens_at_first_defined_position_on_or_after_start(self) -> None:
        close, mkt, rf = self._inputs()
        frame = build_frame(close, mkt, rf, start=pd.Timestamp("1928-01-01"))
        # The 200th close completes the SMA; the next return session is first.
        first_sig = close.index[199]
        assert frame.index[0] == mkt.index[mkt.index > first_sig][0]
        later = build_frame(close, mkt, rf, start=pd.Timestamp("1929-06-01"))
        assert later.index[0] >= pd.Timestamp("1929-06-01")
        assert frame["pos"].notna().all()

    def test_market_past_the_last_close_is_refused(self) -> None:
        close, mkt, rf = self._inputs()
        extra = mkt.index[-1] + pd.Timedelta(days=3)
        mkt = pd.concat([mkt, pd.Series([0.0], index=[extra])])
        with pytest.raises(ValueError, match="sync"):
            build_frame(close, mkt, rf.reindex(mkt.index, fill_value=0.0))

    def test_missing_rf_is_refused(self) -> None:
        close, mkt, rf = self._inputs()
        with pytest.raises(ValueError, match="lack"):
            build_frame(close, mkt, rf.iloc[:-5], start=pd.Timestamp("1928-01-01"))
