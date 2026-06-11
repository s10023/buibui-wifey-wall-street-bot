"""Tests for analytics/backtest/cv_splits.py — Phase 0.3c purged+embargoed K-fold CV."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.backtest.cv_splits import (
    CvConfig,
    fold_bounds,
    purged_kfold_split,
)

_TF_MS = 3_600_000  # 1h bars


def _make_ohlcv(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _TF_MS for i in range(n)],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [1_000.0] * n,
        }
    )


def _make_signals(rows: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [r * _TF_MS for r in rows],
            "direction": ["long"] * len(rows),
            "reason": ["test"] * len(rows),
        }
    )


class TestFoldBounds:
    def test_partitions_evenly_divisible_rows(self) -> None:
        assert fold_bounds(100, 5) == [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]

    def test_distributes_remainder_to_earliest_folds(self) -> None:
        assert fold_bounds(102, 5) == [(0, 21), (21, 42), (42, 62), (62, 82), (82, 102)]

    def test_bounds_partition_every_row_exactly_once(self) -> None:
        bounds = fold_bounds(97, 4)
        covered = [i for start, end in bounds for i in range(start, end)]
        assert covered == list(range(97))

    def test_rejects_single_fold(self) -> None:
        with pytest.raises(ValueError, match="n_folds"):
            fold_bounds(100, 1)

    def test_rejects_fewer_rows_than_folds(self) -> None:
        with pytest.raises(ValueError, match="n_rows"):
            fold_bounds(3, 5)


class TestCvConfig:
    def test_defaults(self) -> None:
        cfg = CvConfig()
        assert cfg.mode == "contiguous"
        assert cfg.n_folds == 5
        assert cfg.purge_bars == 0
        assert cfg.embargo_bars is None

    def test_rejects_bad_n_folds(self) -> None:
        with pytest.raises(ValueError, match="n_folds"):
            CvConfig(n_folds=1)

    def test_rejects_negative_purge(self) -> None:
        with pytest.raises(ValueError, match="purge_bars"):
            CvConfig(purge_bars=-1)

    def test_rejects_negative_embargo(self) -> None:
        with pytest.raises(ValueError, match="embargo_bars"):
            CvConfig(embargo_bars=-1)

    def test_resolve_embargo_explicit_passthrough(self) -> None:
        assert CvConfig(embargo_bars=7).resolve_embargo_bars(1000) == 7

    def test_resolve_embargo_defaults_to_one_percent(self) -> None:
        assert CvConfig().resolve_embargo_bars(1000) == 10

    def test_resolve_embargo_floors_at_one_bar(self) -> None:
        assert CvConfig().resolve_embargo_bars(50) == 1


class TestPurgedKfoldSplit:
    def test_produces_n_folds_with_disjoint_full_coverage_tests(self) -> None:
        ohlcv = _make_ohlcv(100)
        cfg = CvConfig(mode="purged", n_folds=5, embargo_bars=2)
        folds = purged_kfold_split(ohlcv, _make_signals([]), cfg)
        assert len(folds) == 5
        assert [f.fold for f in folds] == [0, 1, 2, 3, 4]
        recombined = pd.concat([f.test_ohlcv for f in folds], ignore_index=True)
        pd.testing.assert_frame_equal(recombined, ohlcv)

    def test_first_fold_has_no_pre_train_segment(self) -> None:
        cfg = CvConfig(mode="purged", embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 0 test = rows [0, 20) → only the post-test segment survives
        assert len(folds[0].train_segments) == 1
        post_ohlcv, _ = folds[0].train_segments[0]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 22 * _TF_MS  # 20 + embargo 2

    def test_last_fold_has_no_post_train_segment(self) -> None:
        cfg = CvConfig(mode="purged", embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # last fold test = rows [80, 100) → only the pre-test segment survives
        assert len(folds[-1].train_segments) == 1
        pre_ohlcv, _ = folds[-1].train_segments[0]
        assert int(pre_ohlcv.iloc[-1]["open_time"]) == 79 * _TF_MS

    def test_purge_trims_pre_test_segment_tail(self) -> None:
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 2 test = [40, 60) → pre segment = [0, 37) after purging 3 bars
        pre_ohlcv, _ = folds[2].train_segments[0]
        assert int(pre_ohlcv.iloc[-1]["open_time"]) == 36 * _TF_MS

    def test_embargo_trims_post_test_segment_head(self) -> None:
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), _make_signals([]), cfg)
        # fold 2 test = [40, 60) → post segment starts at row 62
        post_ohlcv, _ = folds[2].train_segments[1]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 62 * _TF_MS

    def test_signals_assigned_to_test_and_train_windows(self) -> None:
        signals = _make_signals([10, 38, 41, 61, 79])
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        fold2 = folds[2]  # test [40, 60); pre [0, 37); post [62, 100)
        assert list(fold2.test_signals["open_time"]) == [41 * _TF_MS]
        (_, pre_sigs), (_, post_sigs) = fold2.train_segments
        assert list(pre_sigs["open_time"]) == [10 * _TF_MS]
        assert list(post_sigs["open_time"]) == [79 * _TF_MS]

    def test_signals_in_purge_and_embargo_zones_dropped_from_fold(self) -> None:
        # row 38 sits in fold 2's purge zone [37, 40); row 61 in its embargo zone [60, 62)
        signals = _make_signals([38, 61])
        cfg = CvConfig(mode="purged", purge_bars=3, embargo_bars=2)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        fold2 = folds[2]
        in_fold = [
            *fold2.test_signals["open_time"],
            *(ot for _, sigs in fold2.train_segments for ot in sigs["open_time"]),
        ]
        assert in_fold == []

    def test_each_signal_lands_in_exactly_one_test_fold(self) -> None:
        signals = _make_signals([0, 19, 20, 55, 99])
        cfg = CvConfig(mode="purged", embargo_bars=1)
        folds = purged_kfold_split(_make_ohlcv(100), signals, cfg)
        pooled = sorted(ot for f in folds for ot in f.test_signals["open_time"])
        assert pooled == sorted(signals["open_time"])

    def test_empty_signals_produce_empty_signal_frames(self) -> None:
        cfg = CvConfig(mode="purged", n_folds=2, embargo_bars=1)
        folds = purged_kfold_split(_make_ohlcv(50), _make_signals([]), cfg)
        assert all(f.test_signals.empty for f in folds)
        assert all(sigs.empty for f in folds for _, sigs in f.train_segments)

    def test_default_embargo_uses_one_percent_of_rows(self) -> None:
        cfg = CvConfig(mode="purged")
        folds = purged_kfold_split(_make_ohlcv(500), _make_signals([]), cfg)
        # 1% of 500 = 5 bars: fold 0 test = [0, 100) → post segment starts at row 105
        post_ohlcv, _ = folds[0].train_segments[0]
        assert int(post_ohlcv.iloc[0]["open_time"]) == 105 * _TF_MS
