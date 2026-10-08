"""The survival core's daily read-out (#423) against the replay's own rules."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from analytics.overlay.live import (
    VM_SIGMA_TARGET,
    completed_closes,
    core_state,
    format_core,
)
from analytics.overlay.rules import MA_WINDOW, ma_signal, vol_weight


def _closes(values: np.ndarray, start: str = "2025-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, name="close")


def _walk(n: int = 400, seed: int = 7, vol: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    return _closes(5000.0 * np.exp(np.cumsum(rng.normal(0.0003, vol, n))))


class TestOneRuleNoCopy:
    """The read-out must equal what the replay's rules say at the same close."""

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_ma_leg_matches_ma_signal(self, seed: int) -> None:
        close = _walk(seed=seed)
        assert core_state(close).ma_in == (ma_signal(close).iloc[-1] == 1.0)

    @pytest.mark.parametrize("vol", [0.002, 0.007, 0.02])
    def test_vm_leg_matches_vol_weight(self, vol: float) -> None:
        close = _walk(vol=vol)
        # lag=0 reads the weight set at the last close: the one held next session.
        expected = vol_weight(close.pct_change(), VM_SIGMA_TARGET, lag=0).iloc[-1]
        assert core_state(close).vm_weight == pytest.approx(expected)

    def test_both_vm_regimes_are_reached(self) -> None:
        """Positive control: the parametrised vols straddle the cap."""
        assert core_state(_walk(vol=0.002)).vm_weight == 1.0
        assert core_state(_walk(vol=0.02)).vm_weight < 0.5


class TestFlipLevel:
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_a_close_past_the_level_flips_the_ma_leg(self, seed: int) -> None:
        close = _walk(seed=seed)
        state = core_state(close)
        nxt = close.index[-1] + pd.offsets.BDay(1)
        beyond = state.flip_level * (0.999 if state.ma_in else 1.001)
        short = state.flip_level * (1.001 if state.ma_in else 0.999)
        flipped = ma_signal(pd.concat([close, pd.Series([beyond], index=[nxt])]))
        held = ma_signal(pd.concat([close, pd.Series([short], index=[nxt])]))
        assert (flipped.iloc[-1] == 1.0) != state.ma_in
        assert (held.iloc[-1] == 1.0) == state.ma_in


class TestState:
    def test_sessions_in_state_counts_since_the_last_switch(self) -> None:
        up = np.linspace(100.0, 200.0, MA_WINDOW + 50)
        down = np.linspace(199.0, 120.0, 80)
        close = _closes(np.concatenate([up, down]))
        state = core_state(close)
        sig = ma_signal(close).dropna()
        last_in = sig[sig == 1.0].index[-1]
        assert not state.ma_in
        assert state.sessions_in_state == int((sig.index > last_in).sum())

    def test_out_means_zero_exposure_whatever_the_weight(self) -> None:
        close = _closes(np.linspace(300.0, 100.0, MA_WINDOW + 30))
        state = core_state(close)
        assert not state.ma_in
        assert state.vm_weight > 0.0
        assert state.exposure == 0.0

    def test_too_few_closes_raise_rather_than_report_warm_up(self) -> None:
        with pytest.raises(ValueError, match="need 200 closes"):
            core_state(_walk(n=MA_WINDOW - 1))

    def test_format_names_both_legs_and_the_date(self) -> None:
        line = format_core(core_state(_walk()))
        for token in ("Core OV-1×VM", "exposure", "SMA200", "flips", "σ̂20", "as of"):
            assert token in line


class TestCompletedCloses:
    def _today_bar(self) -> pd.Series:
        return _closes(np.array([1.0, 2.0, 3.0]), start="2026-10-06")

    def test_drops_a_bar_dated_today_before_the_close(self) -> None:
        now = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
        assert completed_closes(self._today_bar(), now).index[-1].day == 7

    def test_keeps_it_after_the_close(self) -> None:
        now = datetime(2026, 10, 8, 21, 0, tzinfo=UTC)
        assert completed_closes(self._today_bar(), now).index[-1].day == 8
