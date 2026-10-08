"""TOM (#422): the window rule, its causality, the frame and the hedged gate."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from analytics.overlay.replay import arm_returns
from analytics.research_guards.bootstrap import BootstrapCI
from analytics.tom import (
    complete_months,
    evaluate_tom,
    hedged_returns,
    premise_reading,
    tom_frame,
    tom_position,
    tom_verdict,
    window_spread,
)
from analytics.trading_calendar import nyse_sessions


def _xnys(start: str, end: str) -> pd.DatetimeIndex:
    days = nyse_sessions(date.fromisoformat(start), date.fromisoformat(end))
    return pd.DatetimeIndex(pd.to_datetime(days))


def _in_window(pos: pd.Series) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pos.index[pos == 1.0]]


class TestWindow:
    def test_known_months(self) -> None:
        pos = tom_position(_xnys("2024-01-01", "2024-03-31"))
        assert _in_window(pos) == [
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-31",
            "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-29",
            "2024-03-01", "2024-03-04", "2024-03-05", "2024-03-28",
        ]  # fmt: skip

    def test_a_holiday_moves_the_window_with_the_calendar(self) -> None:
        # Labor Day (Sep 2) pushes the first three sessions of September out by one.
        pos = tom_position(_xnys("2024-08-01", "2024-09-30"))
        assert _in_window(pos)[3:7] == [
            "2024-08-30", "2024-09-03", "2024-09-04", "2024-09-05",
        ]  # fmt: skip

    def test_four_sessions_a_month(self) -> None:
        pos = tom_position(_xnys("2015-01-01", "2024-12-31"))
        per_month = pos.groupby(pd.DatetimeIndex(pos.index).to_period("M")).sum()
        assert (per_month == 4.0).all()


class TestCausality:
    """Truncating the calendar changes no label before the cut (spec § Amendment 2)."""

    CUTS = (
        "2010-03-15",
        "2012-10-17",
        "2015-06-10",
        "2018-11-14",
        "2021-02-17",
        "2024-05-15",
    )

    @pytest.mark.parametrize("cut", CUTS)
    def test_no_label_before_the_cut_moves(self, cut: str) -> None:
        full = tom_position(_xnys("2008-01-01", "2025-12-31"))
        short = tom_position(
            pd.DatetimeIndex(full.index[full.index <= pd.Timestamp(cut)])
        )
        before = short.index[:-1]
        pd.testing.assert_series_equal(short.loc[before], full.loc[before])

    @pytest.mark.parametrize("cut", CUTS)
    def test_positive_control_the_cut_session_is_mislabelled(self, cut: str) -> None:
        """Each cut is mid-month, so the truncated calendar must call it a month end."""
        full = tom_position(_xnys("2008-01-01", "2025-12-31"))
        short = tom_position(
            pd.DatetimeIndex(full.index[full.index <= pd.Timestamp(cut)])
        )
        assert short.iloc[-1] == 1.0
        assert full.loc[pd.Timestamp(cut)] == 0.0

    def test_the_rule_reads_no_return(self) -> None:
        dates = _xnys("2020-01-01", "2020-12-31")
        rng = np.random.default_rng(1)
        a = pd.DataFrame(
            {"mkt_rf": rng.normal(0, 0.01, len(dates)), "rf": 0.0001}, index=dates
        )
        b = a.assign(mkt_rf=-a["mkt_rf"] * 3)
        fa = tom_frame(a, start=dates[0], sessions_fn=nyse_sessions)
        fb = tom_frame(b, start=dates[0], sessions_fn=nyse_sessions)
        assert not np.allclose(fa["mkt"], fb["mkt"])  # the perturbation arrived
        pd.testing.assert_series_equal(fa["pos"], fb["pos"])


class TestFrame:
    def _ff(self, dates: pd.DatetimeIndex) -> pd.DataFrame:
        return pd.DataFrame({"mkt_rf": 0.001, "rf": 0.0001}, index=dates)

    def test_an_unfinished_month_is_cut(self) -> None:
        dates = _xnys("2024-01-01", "2024-03-15")
        kept = complete_months(dates, nyse_sessions)
        assert kept[-1] == pd.Timestamp("2024-02-29")

    def test_a_finished_month_is_kept(self) -> None:
        dates = _xnys("2024-01-01", "2024-03-31")
        assert complete_months(dates, nyse_sessions)[-1] == pd.Timestamp("2024-03-28")

    def test_the_panel_opens_labelled(self) -> None:
        f = tom_frame(self._ff(_xnys("2023-11-01", "2024-03-31")),
                      start=pd.Timestamp("2024-01-01"), sessions_fn=nyse_sessions)  # fmt: skip
        assert f.index[0] == pd.Timestamp("2024-01-02")
        assert f["pos"].iloc[0] == 1.0
        assert f["mkt"].iloc[0] == pytest.approx(0.0011)

    def test_lag_shifts_the_position_one_session_later(self) -> None:
        ff = self._ff(_xnys("2023-11-01", "2024-03-31"))
        base = tom_frame(
            ff, start=pd.Timestamp("2024-01-01"), sessions_fn=nyse_sessions
        )
        late = tom_frame(
            ff, start=pd.Timestamp("2024-01-01"), sessions_fn=nyse_sessions, lag=1
        )
        assert late.loc["2024-01-05", "pos"] == 1.0
        assert base.loc["2024-01-05", "pos"] == 0.0
        assert late.loc["2024-01-31", "pos"] == 0.0


def _arms(
    alpha_daily: float, beta: float, n: int = 6000, seed: int = 3
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2000-01-03", periods=n)
    rf = pd.Series(0.0001, index=idx)
    mkt = rf + rng.normal(0.0003, 0.01, n)
    noise = rng.normal(0.0, 0.002, n)
    arm = rf + beta * (mkt - rf) + alpha_daily + noise
    return pd.DataFrame({"bh": mkt, "tom": arm, "rf": rf})


class TestGate:
    def test_pure_beta_hedges_to_noise(self) -> None:
        arms = _arms(0.0, 0.3)
        h, beta = hedged_returns(arms)
        assert beta == pytest.approx(0.3, abs=0.01)
        assert abs(h.mean()) < 1e-4

    def test_a_planted_alpha_is_found(self) -> None:
        gate = evaluate_tom(_arms(0.0004, 0.2), n_boot=300)
        assert gate.hedged_sharpe > 2.0
        assert gate.verdict == "FOUND"
        assert gate.premise == "positive"

    def test_no_alpha_is_not_found(self) -> None:
        gate = evaluate_tom(_arms(0.0, 0.2), n_boot=300)
        assert gate.verdict != "FOUND"

    @pytest.mark.parametrize(
        ("sharpe", "dsr", "lo", "hi", "expected"),
        [
            (0.9, 0.97, 0.10, 1.6, "FOUND"),
            (0.9, 0.90, 0.10, 1.6, "BOUNDED"),
            (0.5, 0.97, 0.10, 1.2, "BOUNDED"),
            (0.2, 0.40, -0.30, 0.69, "EXCLUDED"),
            (0.3, 0.50, -0.30, 0.90, "INSUFFICIENT"),
        ],
    )
    def test_verdict_order(
        self, sharpe: float, dsr: float, lo: float, hi: float, expected: str
    ) -> None:
        ci = BootstrapCI(point=sharpe, lo=lo, hi=hi, alpha=0.05, n_valid=100)
        assert tom_verdict(sharpe, dsr, ci) == expected

    @pytest.mark.parametrize(
        ("lo", "hi", "expected"),
        [(-0.5, -0.01, "refuted"), (-0.5, 0.6, "not refuted"), (0.1, 0.6, "positive")],
    )
    def test_premise_is_read_apart_from_the_verdict(
        self, lo: float, hi: float, expected: str
    ) -> None:
        ci = BootstrapCI(point=0.0, lo=lo, hi=hi, alpha=0.05, n_valid=100)
        assert premise_reading(ci) == expected


class TestWindowSpread:
    def test_inside_and_outside_means(self) -> None:
        dates = _xnys("2000-01-01", "2019-12-31")
        pos = tom_position(dates)
        ex = np.where(pos == 1.0, 0.002, -0.0001)
        frame = pd.DataFrame(
            {"mkt": ex + 0.0001, "rf": 0.0001, "pos": pos}, index=dates
        )
        ws = window_spread(frame, n_boot=200)
        assert ws.inside == pytest.approx(0.002)
        assert ws.outside == pytest.approx(-0.0001)
        assert ws.diff.lo > 0.0

    def test_arm_returns_charges_both_sides(self) -> None:
        dates = _xnys("2024-01-01", "2024-12-31")
        frame = pd.DataFrame(
            {"mkt": 0.0, "rf": 0.0, "pos": tom_position(dates)}, index=dates
        )
        arms = arm_returns(frame, bps=2.0, positions={"tom": "pos"})
        # 12 windows, each entered and left once; the first session is free.
        sides = (frame["pos"].diff().abs().fillna(0.0)).sum()
        assert arms["tom"].sum() == pytest.approx(-sides * 2e-4)
        assert sides in (23.0, 24.0)
