"""Tests for analytics/backtest/cv_splits.py — Phase 0.3c purged+embargoed K-fold CV."""

from __future__ import annotations

import pytest

from analytics.backtest.cv_splits import CvConfig, fold_bounds


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
