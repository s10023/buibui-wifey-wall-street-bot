"""Unit tests for the velocity-alternation sleeve's signal construction.

Three things these assert that a green suite would otherwise not prove:

* ``decline_metrics`` returns the arithmetic it documents, on a hand-built series
  where depth / duration / velocity are known exactly rather than re-derived by
  the test (a test that re-implements its subject can never falsify it — #150).
* THE THESIS SIGN. ``velocity_score`` must rank a SLOW decliner above a FAST one
  at identical depth. That single assertion is the hypothesis; invert it and the
  sleeve measures the opposite claim while every other test still passes.
* the causality guard is NOT VACUOUS — it carries a positive control on the
  channel the `.shift(1)` protects, so it fails if the shift is deleted (#181).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.velocity.signals import (
    LOOKBACK,
    MIN_DEPTH,
    MIN_DURATION,
    decline_metrics,
    depth_score,
    duration_score,
    eligible_count,
    velocity_score,
)

_N = 100


def _idx(n: int = _N) -> pd.DatetimeIndex:
    return pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")


def _slow_decline() -> pd.Series:
    """Unique peak 100 at bar 59, then -0.75/bar to 85.0 at bar 79, then flat.

    At bar 79: depth = (100-85)/100 = 0.15, duration = 20, velocity = 0.0075.
    """
    c = np.full(_N, 90.0)
    c[59] = 100.0
    for i in range(60, 80):
        c[i] = 100.0 - 0.75 * (i - 59)
    c[80:] = 85.0
    return pd.Series(c, index=_idx())


def _fast_decline() -> pd.Series:
    """Unique peak 100 at bar 74, then -3.0/bar to 85.0 at bar 79, then flat.

    At bar 79: depth = 0.15 (identical to slow), duration = 5, velocity = 0.03.
    """
    c = np.full(_N, 90.0)
    c[74] = 100.0
    for i in range(75, 80):
        c[i] = 100.0 - 3.0 * (i - 74)
    c[80:] = 85.0
    return pd.Series(c, index=_idx())


def _frames(**series: pd.Series) -> dict[str, pd.DataFrame]:
    return {
        sym: pd.DataFrame(
            {"open": s, "high": s, "low": s, "close": s},
            index=pd.DatetimeIndex(s.index),
        )
        for sym, s in series.items()
    }


class TestDeclineMetrics:
    def test_depth_duration_velocity_are_the_documented_arithmetic(self) -> None:
        m = decline_metrics(_slow_decline())
        row = m.iloc[79]
        assert row["depth"] == float(np.float64(15.0) / np.float64(100.0))
        assert row["duration"] == 20.0
        assert np.isclose(row["velocity"], 0.15 / 20.0)

    def test_velocity_is_exactly_depth_over_duration_wherever_eligible(self) -> None:
        m = decline_metrics(_slow_decline()).dropna()
        assert not m.empty
        assert np.allclose(m["velocity"], m["depth"] / m["duration"])

    def test_warmup_rows_are_nan(self) -> None:
        m = decline_metrics(_slow_decline())
        assert m.iloc[: LOOKBACK - 1].isna().all().all()

    def test_shallow_decline_is_ineligible(self) -> None:
        # Bar 62 is only 2.25% off the peak — below MIN_DEPTH.
        m = decline_metrics(_slow_decline())
        assert (100.0 - _slow_decline().iloc[62]) / 100.0 < MIN_DEPTH
        assert m.iloc[62].isna().all()

    def test_too_brief_a_decline_is_ineligible(self) -> None:
        # Deep enough (12%) but it happened in ONE session — below MIN_DURATION.
        c = np.full(_N, 90.0)
        c[59] = 100.0
        c[60:] = 88.0
        m = decline_metrics(pd.Series(c, index=_idx()))
        assert MIN_DEPTH <= (100.0 - 88.0) / 100.0
        assert MIN_DURATION > 1
        assert m.iloc[60].isna().all()

    def test_a_tied_peak_resolves_to_the_earliest_bar(self) -> None:
        """Documented convention, not a derivation: earliest peak -> longer duration."""
        c = np.full(_N, 90.0)
        c[59] = 100.0
        c[65] = 100.0  # exact tie
        c[66:] = 85.0
        m = decline_metrics(pd.Series(c, index=_idx()))
        assert m.iloc[70]["duration"] == 70 - 59  # not 70 - 65


class TestThesisSign:
    def test_slow_decliner_outranks_fast_decliner_at_equal_depth(self) -> None:
        """THE HYPOTHESIS. H-007 longs the slow grind, shorts the sharp drop.

        Both names are 15% off their peak at bar 79; only the pace differs.
        Scores are `.shift(1)`-ed, so bar 79's metrics land on bar 80.
        """
        frames = _frames(SLOW=_slow_decline(), FAST=_fast_decline())
        slow_m = decline_metrics(frames["SLOW"]["close"]).iloc[79]
        fast_m = decline_metrics(frames["FAST"]["close"]).iloc[79]
        assert np.isclose(slow_m["depth"], fast_m["depth"])  # depth held constant
        assert slow_m["duration"] > fast_m["duration"]  # only the pace differs

        score = velocity_score(frames)
        assert score["SLOW"].iloc[80] > score["FAST"].iloc[80], (
            "velocity_score ranked the FAST decliner above the SLOW one — that is "
            "the inverse of H-007 and would book the opposite sleeve"
        )

    def test_depth_control_is_negatively_oriented(self) -> None:
        frames = _frames(SHALLOW=_slow_decline() * 1.0, DEEP=_slow_decline())
        deep = frames["DEEP"]["close"].copy()
        deep.iloc[80:] = 70.0  # 30% off peak vs 15%
        frames["DEEP"] = pd.DataFrame(
            {"open": deep, "high": deep, "low": deep, "close": deep}, index=deep.index
        )
        score = depth_score(frames)
        assert score["SHALLOW"].iloc[85] > score["DEEP"].iloc[85]

    def test_duration_control_is_positively_oriented(self) -> None:
        frames = _frames(SLOW=_slow_decline(), FAST=_fast_decline())
        score = duration_score(frames)
        assert score["SLOW"].iloc[80] > score["FAST"].iloc[80]


class TestSharedPopulation:
    def test_all_three_scores_share_one_eligibility_mask(self) -> None:
        """The controls must run on the ratio's OWN population.

        The thesis is that `depth/duration` beats its components; comparing it to
        a control drawn from a wider population would compare two different
        experiments and the correlation read in `report` would be meaningless.
        """
        rng = np.random.default_rng(7)
        frames = {}
        for sym in ("A", "B", "C", "D"):
            c = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.02, 400))
            s = pd.Series(c, index=_idx(400))
            frames[sym] = pd.DataFrame(
                {"open": s, "high": s, "low": s, "close": s}, index=s.index
            )
        v = velocity_score(frames).notna()
        d = depth_score(frames).notna()
        t = duration_score(frames).notna()
        assert v.sum().sum() > 0, "fixture produced no eligible name-days"
        pd.testing.assert_frame_equal(v, d)
        pd.testing.assert_frame_equal(v, t)

    def test_eligible_count_matches_the_score_frame(self) -> None:
        frames = _frames(SLOW=_slow_decline(), FAST=_fast_decline())
        counts = eligible_count(frames)
        pd.testing.assert_series_equal(
            counts, velocity_score(frames).notna().sum(axis=1), check_names=False
        )


class TestCausality:
    def test_velocity_score_is_causal_with_a_positive_control(self) -> None:
        rng = np.random.default_rng(11)
        n = 300
        idx = _idx(n)
        frames: dict[str, pd.DataFrame] = {}
        for sym in ("A", "B", "C"):
            c = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.02, n))
            s = pd.Series(c, index=idx)
            frames[sym] = pd.DataFrame(
                {"open": s, "high": s, "low": s, "close": s}, index=idx
            )

        base = velocity_score(frames)
        k = 200
        bumped = {s: f.copy() for s, f in frames.items()}
        col = bumped["A"]["close"].to_numpy(dtype=np.float64, copy=True)
        col[k] *= 0.80  # a 20% single-bar drop — moves depth AND duration
        bumped["A"]["close"] = col

        after = velocity_score(bumped)
        pd.testing.assert_frame_equal(
            base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
        )

        # POSITIVE CONTROL on the guarded channel. The `.shift(1)` under test is
        # what stops close[k] reaching day k's leverage row; assert the score at
        # k+1 really did move, otherwise the equality above is satisfied by the
        # perturbation never arriving and would pass with the shift deleted.
        moved = float(abs(after["A"].iloc[k + 1] - base["A"].iloc[k + 1]))
        assert np.isfinite(moved) and moved > 1e-12, (
            "the bumped close never moved the score at k+1 — the causality "
            "assertion is vacuous and would pass with the shift removed"
        )
