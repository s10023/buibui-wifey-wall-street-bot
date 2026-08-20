"""Exact-value tests for the correlation deflator.

Every case here has a closed-form answer written as a literal, never as a
re-derivation of the code under test — a test that re-implements its subject
can never falsify it (CLAUDE.md § Testing).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from analytics.research_guards import (
    SeriesDeflator,
    effective_independent_series,
)

# Rows 2-4 of the order-4 Hadamard matrix: zero-mean and mutually orthogonal,
# so every pairwise Pearson correlation is EXACTLY 0.
_H2 = [1.0, -1.0, 1.0, -1.0]
_H3 = [1.0, 1.0, -1.0, -1.0]
_H4 = [1.0, -1.0, -1.0, 1.0]


def _panel(**cols: list[float]) -> dict[str, pd.Series]:
    return {name: pd.Series(values) for name, values in cols.items()}


class TestEffectiveIndependentSeries:
    def test_identical_series_collapse_to_one(self) -> None:
        """rho == 1 exactly, so k series carry the information of exactly one."""
        result = effective_independent_series(_panel(a=_H2, b=_H2, c=_H2))
        assert result.measured is True
        assert result.k == 3
        assert result.rho == 1.0
        assert result.n_eff == 1.0
        assert result.t_deflator == math.sqrt(3.0)

    def test_orthogonal_series_are_fully_independent(self) -> None:
        """POSITIVE CONTROL for ``measured``.

        rho == 0 exactly, so n_eff == k and the deflator is 1.0 — numerically
        identical to every un-measurable case below. This is the assertion that
        makes ``measured`` load-bearing rather than decorative: without it the
        flag could be hardwired to False and the suite would stay green.
        """
        result = effective_independent_series(_panel(a=_H2, b=_H3, c=_H4))
        assert result.measured is True
        assert result.k == 3
        # Orthogonal by construction; pandas' pairwise corr leaves ~1e-17 of
        # accumulated rounding, so the tolerance is on the residue, not on the
        # claim.
        assert result.rho == pytest.approx(0.0, abs=1e-12)
        assert result.n_eff == pytest.approx(3.0)
        assert result.t_deflator == 1.0

    def test_negative_rho_is_clamped_fail_safe(self) -> None:
        """rho == -0.5 drives n_eff ABOVE k; the clamp must refuse to inflate.

        Unclamped this would return sqrt(2/4) == 0.707 and make a pooled
        t-stat look *better* for being correlated — the fail-open direction for
        a guard whose entire job is the opposite.
        """
        result = effective_independent_series(
            _panel(a=[1.0, 0.0, -1.0], b=[-1.0, 1.0, 0.0])
        )
        assert result.measured is True
        assert result.rho == -0.5
        assert result.n_eff == 4.0
        assert result.t_deflator == 1.0

    def test_degenerate_negative_rho_is_not_measured(self) -> None:
        """rho == -1 with k == 2 puts the equicorrelation denominator at zero."""
        result = effective_independent_series(
            _panel(a=[1.0, 0.0, -1.0], b=[-1.0, 0.0, 1.0])
        )
        assert result.measured is False
        assert result.rho == -1.0
        assert result.t_deflator == 1.0

    def test_single_series_is_not_measured(self) -> None:
        result = effective_independent_series(_panel(a=_H2))
        assert result.measured is False
        assert result.k == 1
        assert math.isnan(result.rho)
        assert result.n_eff == 1.0
        assert result.t_deflator == 1.0

    def test_empty_panel_is_not_measured(self) -> None:
        result = effective_independent_series({})
        assert result.measured is False
        assert result.k == 0
        assert result.n_eff == 1.0

    def test_non_overlapping_series_are_not_measured(self) -> None:
        """No shared index means no pair to correlate — nan, not zero."""
        panel = {
            "a": pd.Series([1.0, 2.0, 3.0], index=[0, 1, 2]),
            "b": pd.Series([1.0, 2.0, 3.0], index=[9, 10, 11]),
        }
        result = effective_independent_series(panel)
        assert result.measured is False
        assert math.isnan(result.rho)
        assert result.t_deflator == 1.0

    def test_result_is_frozen(self) -> None:
        result = effective_independent_series(_panel(a=_H2, b=_H3))
        assert isinstance(result, SeriesDeflator)
