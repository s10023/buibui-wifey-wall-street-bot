"""Research guardrails: overfitting & multiple-testing controls.

Pure-math statistics (no DB / IO / network) used to gate strategy selection
against in-sample mirages: Probabilistic & Deflated Sharpe, PBO/CSCV, the
multiple-testing Sharpe haircut, Minimum Track Record Length, and block /
stationary bootstrap confidence intervals.

Eager re-exports so callers can do
``from analytics.research_guards import deflated_sharpe_ratio, cscv_pbo``.
"""

from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.dsr import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe,
)
from analytics.research_guards.haircut import HaircutResult, haircut_sharpe
from analytics.research_guards.mintrl import min_track_record_length
from analytics.research_guards.pbo import PBOResult, cscv_pbo
from analytics.research_guards.psr import probabilistic_sharpe_ratio

__all__ = [
    "EULER_MASCHERONI",
    "BootstrapCI",
    "HaircutResult",
    "PBOResult",
    "block_bootstrap_ci",
    "cscv_pbo",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "haircut_sharpe",
    "min_track_record_length",
    "probabilistic_sharpe_ratio",
]
