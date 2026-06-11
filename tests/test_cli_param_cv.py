"""Tests for cli/param.py --cv-* flags (Phase 0.3c purged+embargoed CV)."""

from __future__ import annotations

import argparse

from analytics.backtest.cv_splits import CvConfig
from cli.param import _cv_from_args, add_param_sweep_subparser

_REQUIRED = ["--strategy", "bos", "--symbol", "AAPL", "--timeframe", "1d"]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    add_param_sweep_subparser(subparsers)
    return parser.parse_args(["param-sweep", *argv])


class TestCvFlags:
    def test_defaults_to_contiguous_legacy_path(self) -> None:
        args = _parse(_REQUIRED)
        assert args.cv_mode == "contiguous"
        assert args.cv_folds == 5
        assert args.cv_purge_bars == 0
        assert args.cv_embargo_bars is None
        assert _cv_from_args(args) is None

    def test_purged_builds_cv_config(self) -> None:
        args = _parse(
            [
                *_REQUIRED,
                "--cv-mode",
                "purged",
                "--cv-folds",
                "8",
                "--cv-purge-bars",
                "3",
                "--cv-embargo-bars",
                "10",
            ]
        )
        assert _cv_from_args(args) == CvConfig(
            mode="purged", n_folds=8, purge_bars=3, embargo_bars=10
        )

    def test_purged_defaults(self) -> None:
        args = _parse([*_REQUIRED, "--cv-mode", "purged"])
        assert _cv_from_args(args) == CvConfig(
            mode="purged", n_folds=5, purge_bars=0, embargo_bars=None
        )
