"""Tests for analytics/warning_audit.py (H9 warning-value audit lib)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.signal.types import SignalEvent
from analytics.warning_audit import (
    WARNING_KEYS,
    WarningVerdict,
    compute_warning_flags,
    evaluate_warning_cells,
    tag_trades,
    two_sample_lift_ci,
)
from signals.alert_formatter import _build_candle_warnings


def _mk_df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """OHLCV frame from (open, high, low, close) tuples.

    Bar spacing is uniform but arbitrary — the helpers under test read bar
    *order*, never bar duration, and the ``tf`` label is only a dict key into
    ``ohlcv_by_key``. Entry timestamps below are derived from this constant, so
    change both together or not at all.
    """
    return pd.DataFrame(
        {
            "open_time": [3_600_000 * i for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [100.0] * len(rows),
        }
    )


# (name, rows, {direction: {key: expected}}) — unlisted keys expected False.
_CASES: list[
    tuple[str, list[tuple[float, float, float, float]], dict[str, dict[str, bool]]]
] = [
    (
        "plain",
        [(100.0, 101.0, 99.0, 100.5), (100.5, 103.0, 100.0, 102.0)],
        {"long": {}, "short": {}},
    ),
    (
        "marubozu",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.5, 99.8, 110.0)],
        {"long": {"w1_marubozu": True}, "short": {"w1_marubozu": True}},
    ),
    (
        # doji with a huge upper wick: raw wick-rejection is True for long but
        # MUST be suppressed because the candle is a doji (live precedence).
        "doji_suppresses_w5",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.0, 99.5, 100.4)],
        {"long": {"w7_doji": True}, "short": {"w7_doji": True}},
    ),
    (
        "inside_bar",
        [(100.0, 110.0, 90.0, 105.0), (104.0, 106.0, 95.0, 96.0)],
        {"long": {"w8_inside_bar": True}, "short": {"w8_inside_bar": True}},
    ),
    (
        "wick_reject_long_only",
        [(100.0, 101.0, 99.0, 100.5), (100.0, 110.0, 99.5, 102.0)],
        {"long": {"w5_wick_rejection": True}, "short": {}},
    ),
    (
        "equal_lows_long_only",
        [
            (100.0, 101.0, 95.0, 100.2),
            (100.2, 100.6, 95.1, 100.4),
            (100.4, 100.8, 99.6, 100.0),
        ],
        {"long": {"w2_equal_levels": True}, "short": {}},
    ),
    (
        "three_greens_long_only",
        [
            (100.0, 101.5, 99.0, 101.0),
            (101.0, 102.5, 100.0, 102.0),
            (102.0, 103.5, 101.2, 103.0),
        ],
        {"long": {"w6_consecutive": True}, "short": {}},
    ),
]

# Note substrings in _build_candle_warnings output → flag key.
_NOTE_MARKERS = {
    "w1_marubozu": "Wickless candle",
    "w2_equal_levels": "liquidity",
    "w5_wick_rejection": "wick rejection",
    "w6_consecutive": "in a row",
    "w7_doji": "Doji signal candle",
    "w8_inside_bar": "inside prior range",
}


class TestComputeWarningFlags:
    def test_returns_none_below_two_rows(self) -> None:
        df = _mk_df([(100.0, 101.0, 99.0, 100.5)])
        assert compute_warning_flags(df, "long") is None

    def test_all_keys_present(self) -> None:
        df = _mk_df(_CASES[0][1])
        flags = compute_warning_flags(df, "long")
        assert flags is not None
        assert set(flags) == set(WARNING_KEYS)

    def test_expected_flags_per_case(self) -> None:
        for name, rows, per_dir in _CASES:
            df = _mk_df(rows)
            for direction, expected in per_dir.items():
                flags = compute_warning_flags(df, direction)
                assert flags is not None, name
                for key in WARNING_KEYS:
                    want = expected.get(key, False)
                    assert flags[key] is want, f"{name}/{direction}/{key}"


class TestFlagsAgreeWithTheAlertBuilder:
    """``compute_warning_flags`` must agree with ``_build_candle_warnings`` exactly.

    This is the load-bearing test of the whole port: the lib re-derives warnings
    the live path never persisted, so its only claim to correctness is that it
    reproduces the live builder case-for-case.

    Named away from "LiveParity" deliberately — that phrase means the gate-stack
    replay in this repo, and `make check-orphan-tests` matched the class's
    inferred subject against `run_param_sweep`'s `live_parity` **kwarg**, which
    is the parameter-name false positive that tool documents. A recurring
    dismissible hit trains the reader to ignore an advisory check.
    """

    def test_parity_across_case_battery(self) -> None:
        for name, rows, per_dir in _CASES:
            df = _mk_df(rows)
            for direction in per_dir:
                event = SignalEvent(
                    symbol="SPY",
                    timeframe="4h",
                    strategy="wick_fills",
                    direction=direction,
                    reason="parity-test",
                    open_time=int(df.iloc[-1]["open_time"]),
                    price=float(df.iloc[-1]["close"]),
                )
                notes = _build_candle_warnings([event], df)
                flags = compute_warning_flags(df, direction)
                assert flags is not None, name
                for key, marker in _NOTE_MARKERS.items():
                    fired_live = any(marker in n for n in notes)
                    assert flags[key] is fired_live, f"{name}/{direction}/{key}"


class TestTwoSampleLiftCi:
    def test_separated_cohorts_ci_excludes_zero(self) -> None:
        rng = np.random.default_rng(7)
        warned = rng.normal(1.0, 0.1, 50)
        clean = rng.normal(0.0, 0.1, 50)
        lo, hi = two_sample_lift_ci(warned, clean, n_boot=500)
        assert lo > 0.5
        assert hi > lo

    def test_tiny_cohort_returns_nan(self) -> None:
        lo, hi = two_sample_lift_ci([1.0], [0.0, 0.1], n_boot=100)
        assert np.isnan(lo) and np.isnan(hi)


class TestTagTrades:
    def _entries(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["SPY"] * 3,
                "tf": ["4h"] * 3,
                "strategy": ["wick_fills"] * 3,
                "direction": ["long"] * 3,
                # candle 3 (plenty of history), candle 0 (< 2-bar window),
                # and a timestamp absent from the OHLCV frame.
                "ts_ms": [3 * 3_600_000, 0, 999_999_999],
                "r": [0.5, -1.0, 1.0],
            }
        )

    def test_tags_matching_and_drops_unmatched(self) -> None:
        ohlcv = _mk_df(
            [
                (100.0, 101.0, 99.0, 100.5),
                (100.5, 102.0, 100.0, 101.5),
                (101.5, 103.0, 101.0, 102.5),
                (102.5, 104.0, 102.0, 103.5),  # 3 greens incl. this one
            ]
        )
        tagged, dropped = tag_trades(self._entries(), {("SPY", "4h"): ohlcv})
        assert dropped == 2
        assert len(tagged) == 1
        assert bool(tagged.iloc[0]["w6_consecutive"]) is True
        assert set(WARNING_KEYS) <= set(tagged.columns)

    def test_missing_symbol_frame_drops_all(self) -> None:
        tagged, dropped = tag_trades(self._entries(), {})
        assert dropped == 3
        assert tagged.empty
        assert set(WARNING_KEYS) <= set(tagged.columns)


def _block(
    n: int,
    r_mean: float,
    flag: str | None,
    rng: np.random.Generator,
    sd: float = 0.3,
) -> pd.DataFrame:
    """``sd`` is exposed because a COSMETIC assertion needs the cell's CI to
    fit INSIDE the ±0.05R bar, and the 0.3 default cannot at these n (at n=80
    its CI half-width is ~0.066, i.e. wider than the bar it is tested against).
    """
    df = pd.DataFrame({"direction": ["long"] * n, "r": rng.normal(r_mean, sd, n)})
    for key in WARNING_KEYS:
        df[key] = key == flag
    return df


class TestEvaluateWarningCells:
    def test_taxonomy_mapping(self) -> None:
        rng = np.random.default_rng(7)
        tagged = pd.concat(
            [
                _block(80, -0.8, "w7_doji", rng),  # reliable loser
                _block(80, 0.8, "w1_marubozu", rng),  # reliable winner
                # Powered null: sd tightened so the CI genuinely excludes an
                # effect at the bar. At the 0.3 default it does NOT, and this
                # line asserted COSMETIC anyway until 2026-08-13.
                _block(80, 0.0, "w6_consecutive", rng, sd=0.1),
                _block(5, -1.0, "w2_equal_levels", rng),  # under-powered
                _block(300, 0.05, None, rng),  # clean bulk
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        assert len(verdicts) == 12
        by: dict[tuple[str, str], WarningVerdict] = {
            (v.warning, v.direction): v for v in verdicts
        }
        assert by[("w7_doji", "long")].verdict == "SUPPRESS-CANDIDATE"
        assert by[("w1_marubozu", "long")].verdict == "REVERSE"
        assert by[("w6_consecutive", "long")].verdict == "COSMETIC"
        assert by[("w2_equal_levels", "long")].verdict == "INSUFFICIENT"
        assert by[("w7_doji", "short")].verdict == "INSUFFICIENT"
        # lift stamp populated on a tested cell
        assert by[("w7_doji", "long")].lift_hi < 0.0

    def test_concentrate_maps_to_cosmetic_with_raw_kept(self) -> None:
        rng = np.random.default_rng(11)
        tagged = pd.concat(
            [
                _block(80, 0.3, "w8_inside_bar", rng),
                _block(300, 0.9, None, rng),
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        by = {(v.warning, v.direction): v for v in verdicts}
        v = by[("w8_inside_bar", "long")]
        assert v.raw_decision == "CONCENTRATE"
        assert v.verdict == "COSMETIC"

    def test_wide_ci_is_insufficient_not_cosmetic(self) -> None:
        """COSMETIC claims a warning carries no information. That needs the CI
        to RULE OUT an effect at the bar, not merely both cohorts clearing a
        sample-size floor.

        Both cohorts here are far past ``min_n`` (so the old ``n >= min_n``
        rule called this COSMETIC) while the noise is wide enough that an
        effect the size of the bar is entirely consistent with the data.
        """
        rng = np.random.default_rng(23)
        # sd=3.0 at n=60: CI half-width ~0.76, i.e. ~15x the 0.05R bar.
        noisy = pd.DataFrame(
            {"direction": ["long"] * 60, "r": rng.normal(0.0, 3.0, 60)}
        )
        for key in WARNING_KEYS:
            noisy[key] = key == "w7_doji"
        tagged = pd.concat([noisy, _block(300, 0.0, None, rng)]).reset_index(drop=True)

        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        v = {(x.warning, x.direction): x for x in verdicts}[("w7_doji", "long")]

        assert v.n_warned >= 30 and v.n_clean >= 30  # a size rule cannot help
        # positive control: the CI really is wide
        assert v.ci_lo is not None and v.ci_hi is not None
        assert v.ci_hi - v.ci_lo > 0.10
        assert v.verdict == "INSUFFICIENT"

    def test_single_holm_family(self) -> None:
        rng = np.random.default_rng(3)
        tagged = pd.concat(
            [
                _block(80, -0.8, "w7_doji", rng),
                _block(80, 0.8, "w1_marubozu", rng),
                _block(300, 0.05, None, rng),
            ]
        ).reset_index(drop=True)
        verdicts = evaluate_warning_cells(tagged, n_boot=500)
        tested = [v for v in verdicts if v.adj_pvalue is not None]
        # every tested cell reports the same family size
        assert len({v.n_tests for v in tested}) == 1
