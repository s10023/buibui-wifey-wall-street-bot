"""Research guardrails: overfitting & multiple-testing controls.

Pure-math statistics (no DB / IO / network) used to gate strategy selection
against in-sample mirages: Probabilistic & Deflated Sharpe, PBO/CSCV, the
multiple-testing Sharpe haircut, Minimum Track Record Length, block /
stationary bootstrap confidence intervals, and survival metrics (drawdown,
ulcer index, drawdown-breach odds).

Eager re-exports so callers can do
``from analytics.research_guards import deflated_sharpe_ratio, cscv_pbo``.
"""

from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.cluster import (
    ClusterStats,
    cluster_bootstrap_ci,
    cluster_stats,
)
from analytics.research_guards.correlation import (
    SeriesDeflator,
    effective_independent_series,
)
from analytics.research_guards.dsr import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe,
)
from analytics.research_guards.gate import (
    DEPLOY_SHARPE,
    GATE_DSR,
    GATE_PBO,
    GATE_SHARPE,
    passes_gate,
    passes_sleeve_gate,
)
from analytics.research_guards.haircut import HaircutResult, haircut_sharpe
from analytics.research_guards.mintrl import min_track_record_length
from analytics.research_guards.pbo import PBOResult, cscv_pbo
from analytics.research_guards.power import required_sharpe
from analytics.research_guards.psr import probabilistic_sharpe_ratio
from analytics.research_guards.sharpe import ann_sharpe, per_period_sharpe
from analytics.research_guards.survival import (
    drawdown_breach_curve,
    drawdown_series,
    max_drawdown,
    time_under_water,
    ulcer_index,
)

__all__ = [
    "DEPLOY_SHARPE",
    "EULER_MASCHERONI",
    "GATE_DSR",
    "GATE_PBO",
    "GATE_SHARPE",
    "BootstrapCI",
    "ClusterStats",
    "HaircutResult",
    "PBOResult",
    "SeriesDeflator",
    "ann_sharpe",
    "block_bootstrap_ci",
    "cluster_bootstrap_ci",
    "cluster_stats",
    "cscv_pbo",
    "deflated_sharpe_ratio",
    "drawdown_breach_curve",
    "drawdown_series",
    "effective_independent_series",
    "expected_max_sharpe",
    "haircut_sharpe",
    "max_drawdown",
    "min_track_record_length",
    "passes_gate",
    "passes_sleeve_gate",
    "per_period_sharpe",
    "probabilistic_sharpe_ratio",
    "required_sharpe",
    "time_under_water",
    "ulcer_index",
]
