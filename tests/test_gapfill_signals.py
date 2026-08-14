"""Gap-fill magnet signal tests (edge-hunt #5).

Three things these have to prove, because each is a defect class this repo has
already shipped once:

* the SIGN is right in BOTH directions (a down gap must go long, an up gap
  short) — a sign error is what made the pundit ledger manufacture fake wins;
* the causality assertion is NOT VACUOUS — it carries a positive control on the
  channel the shift guards, per the #181 audit of two vacuous sleeve guards;
* the materiality floor actually excludes something — a floor that admits every
  bar is not a floor, and "unfilled gap" would then mean "every session".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol
from analytics.gapfill.signals import (
    GAP_MAX_AGE,
    MAX_DIST_SIGMA,
    cross_sectional_long_score,
    gap_fill_stats,
    magnet_score,
    nearest_unfilled_level,
    range_regime_mask,
    reversal_score,
)


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(rows), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        },
        index=idx,
    )


def _flat_vol(bars: pd.DataFrame, value: float = 0.01) -> pd.Series:
    """Constant trailing vol, so the materiality floor is a fixed % threshold."""
    return pd.Series(value, index=bars.index)


class TestGapDetection:
    def test_up_gap_leaves_the_prior_close_as_an_unfilled_level_below(self) -> None:
        # day1 closes 100; day2 opens 105 (a +5% gap, 5 sigma at vol=0.01) and
        # never trades back down to 100 -> the level stays live at 100.
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (105.0, 106.0, 104.0, 105.0),
                (105.0, 106.0, 104.5, 105.5),
            ]
        )
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert np.isnan(lvl.iloc[0])
        assert lvl.iloc[1] == 100.0
        assert lvl.iloc[2] == 100.0

    def test_down_gap_leaves_the_prior_close_as_an_unfilled_level_above(self) -> None:
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (95.0, 96.0, 94.0, 95.0),
                (95.0, 96.0, 94.5, 95.5),
            ]
        )
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert lvl.iloc[1] == 100.0
        assert lvl.iloc[2] == 100.0

    def test_a_gap_filled_on_its_own_session_never_becomes_a_level(self) -> None:
        # opens at 105 but the session's low reaches back through 100 -> filled.
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (105.0, 106.0, 99.0, 104.0),
                (104.0, 105.0, 103.0, 104.5),
            ]
        )
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert lvl.isna().all()

    def test_a_later_session_trading_through_the_edge_clears_it(self) -> None:
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (105.0, 106.0, 104.0, 105.0),
                (105.0, 105.5, 99.5, 100.5),  # low pierces 100 -> filled here
                (101.0, 102.0, 100.8, 101.5),
            ]
        )
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert lvl.iloc[1] == 100.0
        assert np.isnan(lvl.iloc[2])
        assert np.isnan(lvl.iloc[3])

    def test_materiality_floor_excludes_a_small_gap(self) -> None:
        # a +0.2% gap against 1% vol is 0.2 sigma, below the 0.5 floor.
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (100.2, 101.0, 100.1, 100.5),
                (100.5, 101.0, 100.4, 100.8),
            ]
        )
        assert nearest_unfilled_level(bars, _flat_vol(bars)).isna().all()
        # ...and the SAME bars clear a floor low enough to admit them, which is
        # what proves the exclusion came from the floor and not from the walk.
        admitted = nearest_unfilled_level(bars, _flat_vol(bars), min_gap_sigma=0.1)
        assert admitted.iloc[1] == 100.0

    def test_an_unfilled_gap_expires_after_max_age(self) -> None:
        rows = [(100.0, 100.0, 100.0, 100.0), (105.0, 106.0, 104.0, 105.0)]
        rows += [(105.0, 105.5, 104.5, 105.0)] * (GAP_MAX_AGE + 2)
        bars = _bars(rows)
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert lvl.iloc[1] == 100.0
        assert lvl.iloc[GAP_MAX_AGE] == 100.0  # created at i=1, age = 59 < 60
        assert np.isnan(lvl.iloc[GAP_MAX_AGE + 1])  # age hits 60 -> expired

    def test_nearest_wins_when_two_gaps_are_open(self) -> None:
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (105.0, 106.0, 104.0, 105.0),  # gap edge 100 (far)
                (110.0, 111.0, 109.0, 110.0),  # gap edge 105 (near)
                (110.0, 111.0, 109.5, 110.5),
            ]
        )
        lvl = nearest_unfilled_level(bars, _flat_vol(bars))
        assert lvl.iloc[3] == 105.0


class TestMagnetSign:
    """The direction of the trade. Both arms asserted, because one alone passes
    under a sign flip."""

    @staticmethod
    def _score_last(rows: list[tuple[float, float, float, float]]) -> float:
        """Warm prologue + the supplied gap rows; returns the final score.

        Two fixture properties are load-bearing and neither is cosmetic:

        * the prologue is a seeded random WALK, not a flat line — `ew_return_vol`
          of a constant series is 0, which makes the materiality floor
          unclearable and the distance divide by zero, so a flat fixture returns
          NaN everywhere and every sign assertion below is unfalsifiable;
        * each prologue bar OPENS AT THE PRIOR CLOSE, so the prologue itself
          contains no gaps. With `open == close` (the obvious way to write it)
          every ~1σ daily move is itself a material gap, and the rows under test
          are then scored against some leftover prologue level instead.

        It ends at exactly 100.0 so the gap sizes in `rows` are exact.
        """
        cfg = ForecastConfig()
        rng = np.random.default_rng(7)
        walk = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.01, 80))
        walk[-1] = 100.0
        pre: list[tuple[float, float, float, float]] = []
        for i, c in enumerate(walk):
            o = walk[i - 1] if i else c
            pre.append((o, max(o, c) * 1.002, min(o, c) * 0.998, c))
        full = _bars(pre + rows)
        return float(magnet_score({"X": full}, cfg)["X"].iloc[-1])

    # Gaps are sized ~1σ (material, floor is 0.5σ) and price stays within ~1σ of
    # the edge, INSIDE the 2σ magnet range. A 5% gap against 1% vol would be
    # material and score exactly 0.0 — correctly, since the edge is then 5σ away
    # and the thesis only claims a pull "when it trades close".

    def test_down_gap_above_price_scores_LONG(self) -> None:
        # opens 1% below the 100.0 prior close -> edge 100.0 sits ABOVE price,
        # never touched again -> expect price to rise toward it -> positive.
        rows = [(99.0, 99.4, 98.8, 99.0), (99.1, 99.5, 98.9, 99.2)]
        assert self._score_last(rows) > 0.0

    def test_up_gap_below_price_scores_SHORT(self) -> None:
        rows = [(101.0, 101.4, 100.5, 101.0), (101.0, 101.3, 100.4, 100.8)]
        assert self._score_last(rows) < 0.0

    def test_no_live_gap_scores_nan(self) -> None:
        # each row opens exactly at the prior close -> no gap is ever created.
        rows = [(100.0, 100.5, 99.5, 100.0), (100.0, 100.5, 99.5, 100.0)]
        assert np.isnan(self._score_last(rows))


class TestCausality:
    def test_magnet_score_is_causal_with_a_positive_control(self) -> None:
        cfg = ForecastConfig()
        rng = np.random.default_rng(11)
        n = 300
        idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
        frames: dict[str, pd.DataFrame] = {}
        for sym in ("A", "B", "C"):
            c = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.015, n))
            frames[sym] = pd.DataFrame(
                {
                    "open": c * (1 + rng.normal(0.0, 0.004, n)),
                    "high": c * 1.012,
                    "low": c * 0.988,
                    "close": c,
                },
                index=idx,
            )

        base = magnet_score(frames, cfg)
        k = 200
        bumped = {s: f.copy() for s, f in frames.items()}
        col = bumped["A"]["close"].to_numpy(dtype=np.float64, copy=True)
        col[k] *= 1.10
        bumped["A"]["close"] = col

        after = magnet_score(bumped, cfg)
        pd.testing.assert_frame_equal(
            base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
        )

        # POSITIVE CONTROL on the guarded channel. The `.shift(1)` under test is
        # what stops close[k] reaching the leverage row for day k; assert the
        # UNSHIFTED score at k really did move, otherwise the equality above is
        # satisfied by the perturbation never arriving and would pass with the
        # shift deleted.
        moved = float(abs(after["A"].iloc[k + 1] - base["A"].iloc[k + 1]))
        assert np.isfinite(moved) and moved > 1e-12, (
            "the bumped close never moved the score at k+1 — the causality "
            "assertion is vacuous and would pass with the shift removed"
        )

    def test_range_regime_mask_is_causal_and_boolean(self) -> None:
        rng = np.random.default_rng(3)
        idx = pd.date_range("2020-01-01", periods=200, freq="D", tz="UTC")
        mkt = pd.Series(rng.normal(0.0, 0.01, 200), index=idx)
        mask = range_regime_mask(mkt)
        assert mask.dtype == bool
        bumped = mkt.copy()
        k = 150
        bumped.iloc[k] += 0.5
        after = range_regime_mask(bumped)
        pd.testing.assert_series_equal(mask.iloc[: k + 1], after.iloc[: k + 1])
        assert (mask.iloc[k + 1 :] != after.iloc[k + 1 :]).any(), (
            "the bump never changed the regime mask — this control is vacuous"
        )


class TestCrossSectionalOrientation:
    def test_high_metric_maps_to_positive_long_score(self) -> None:
        m = pd.DataFrame({"A": [1.0], "B": [-1.0], "C": [0.0]})
        z = cross_sectional_long_score(m)
        assert z["A"].iloc[0] > 0.0
        assert z["B"].iloc[0] < 0.0

    def test_opposite_orientation_to_the_lowvol_sibling(self) -> None:
        from analytics.lowvol.signals import cross_sectional_score

        m = pd.DataFrame({"A": [1.0], "B": [-1.0], "C": [0.0]})
        assert (
            cross_sectional_long_score(m)["A"].iloc[0]
            == -(cross_sectional_score(m)["A"].iloc[0])
        )

    def test_degenerate_row_is_all_nan(self) -> None:
        m = pd.DataFrame({"A": [2.0], "B": [2.0]})
        assert cross_sectional_long_score(m).iloc[0].isna().all()


class TestReversalControl:
    def test_reversal_is_the_negated_prior_return(self) -> None:
        cfg = ForecastConfig()
        n = 120
        idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
        rng = np.random.default_rng(5)
        c = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
        frame = pd.DataFrame(
            {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c}, index=idx
        )
        sc = reversal_score({"X": frame}, cfg)
        ret = pd.Series(c, index=idx).pct_change()
        # score at row d is the negated return of d-1 (one shift), so a positive
        # prior-day return must give a negative score.
        k = 100
        assert np.sign(sc["X"].iloc[k]) == -np.sign(ret.iloc[k - 1])

    def test_reversal_carries_no_gap_logic(self) -> None:
        """Perturbing OPEN alone moves the magnet but must not move reversal."""
        cfg = ForecastConfig()
        n = 120
        idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
        c = np.full(n, 100.0)
        frame = pd.DataFrame(
            {"open": c.copy(), "high": c * 1.02, "low": c * 0.98, "close": c},
            index=idx,
        )
        bumped = frame.copy()
        opens = bumped["open"].to_numpy(dtype=np.float64, copy=True)
        opens[100] = 130.0
        bumped["open"] = opens
        before = reversal_score({"X": frame}, cfg)["X"]
        after = reversal_score({"X": bumped}, cfg)["X"]
        pd.testing.assert_series_equal(before, after)


class TestGapFillStats:
    def test_counts_and_fill_rate(self) -> None:
        bars = _bars(
            [
                (100.0, 100.0, 100.0, 100.0),
                (105.0, 106.0, 104.0, 105.0),  # up gap, edge 100
                (105.0, 105.5, 99.0, 100.0),  # filled on session 2 of its life
                (100.0, 101.0, 99.5, 100.5),
            ]
        )
        s = gap_fill_stats(bars, _flat_vol(bars))
        assert s["n_gaps"] == 1.0
        assert s["up_share"] == 1.0
        assert s["fill_1"] == 0.0  # not filled on its creation session
        assert s["fill_5"] == 1.0

    def test_empty_population_reports_nan_not_zero(self) -> None:
        bars = _bars([(100.0, 100.5, 99.5, 100.0)] * 5)
        s = gap_fill_stats(bars, _flat_vol(bars))
        assert s["n_gaps"] == 0.0
        assert np.isnan(s["fill_5"])


def test_max_dist_sigma_zeroes_a_distant_magnet() -> None:
    """A live-but-distant gap must score exactly 0, not merely small.

    Asserting `(x == 0) | x.isna()` would be satisfied by an all-NaN column, so
    this asserts the level is genuinely LIVE and the score is genuinely 0.0 —
    both, on the same rows. An earlier draft used a flat close series, which made
    vol 0, which made every row NaN, which passed while measuring nothing.
    """
    cfg = ForecastConfig()
    rng = np.random.default_rng(19)
    n = 160
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    walk = 100.0 * np.cumprod(1 + rng.normal(0.0, 0.01, n))
    # One big DOWN gap at k: the session OPENS 6% under the prior close, leaving
    # the edge far ABOVE, and price keeps walking down there without returning.
    # The tail must keep walking, not go flat — a constant tail decays
    # `ew_return_vol` toward 0, and a 0 vol makes the distance inf -> NaN, which
    # would fail the `== 0.0` assertion for the wrong reason.
    k = 100
    walk[k:] = walk[k - 1] * 0.94 * np.cumprod(1 + rng.normal(0.0, 0.004, n - k))
    o = np.concatenate([[walk[0]], walk[:-1]])  # gapless...
    o[k] = walk[k]  # ...except the gap under test
    frame = pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, walk) * 1.002,
            "low": np.minimum(o, walk) * 0.998,
            "close": walk,
        },
        index=idx,
    )
    vol = ew_return_vol(frame["close"], cfg.vol_span).shift(1)
    level = nearest_unfilled_level(frame, vol)
    tail = slice(k + 5, n)
    assert level.iloc[tail].notna().all(), "the gap is not live — test measures nothing"
    sc = magnet_score({"X": frame}, cfg)["X"]
    assert (sc.iloc[tail] == 0.0).all(), (
        f"a live magnet beyond {MAX_DIST_SIGMA} sigma did not zero out"
    )
