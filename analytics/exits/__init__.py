"""Exit-policy research package (exit spec 2026-06-05).

`mfe_mae.py` is the §2 excursion diagnostic. The §3–§5 policy / replay / A/B
layer: `policies.py` + `replay.py` are the pluggable engine
(ported verbatim — they carry no portfolio dependency), and `audit.py` is the
verdict layer, which substitutes a per-trade R Sharpe plus a paired bootstrap
for upstream's portfolio-book headline. Read `audit.py`'s docstring before
changing the metric.
"""

from analytics.exits.audit import (
    POLICY_KINDS,
    TIME_STOP_FLOOR_BY_TF,
    ExitAbRow,
    PolicyResult,
    ReplayedTrade,
    baseline_agreement,
    resolve_ledger_under_policy,
    run_exit_ab,
)
from analytics.exits.mfe_mae import (
    EXCURSION_COLUMNS,
    aggregate_cohorts,
    compute_excursions,
)
from analytics.exits.policies import ExitPolicyConfig, composite, fixed
from analytics.exits.replay import ExitOutcome, replay_exits

__all__ = [
    "EXCURSION_COLUMNS",
    "POLICY_KINDS",
    "TIME_STOP_FLOOR_BY_TF",
    "ExitAbRow",
    "ExitOutcome",
    "ExitPolicyConfig",
    "PolicyResult",
    "ReplayedTrade",
    "aggregate_cohorts",
    "baseline_agreement",
    "composite",
    "compute_excursions",
    "fixed",
    "replay_exits",
    "resolve_ledger_under_policy",
    "run_exit_ab",
]
