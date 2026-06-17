import numpy as np

from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)


def test_guard_signatures_match_parent_report_calls() -> None:
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, size=400)

    # block_bootstrap_ci(r, stat_fn=, seed=) -> obj with .lo/.hi
    boot = block_bootstrap_ci(r, stat_fn=lambda x: float(np.mean(x)), seed=7)
    assert hasattr(boot, "lo") and hasattr(boot, "hi")

    # cscv_pbo(mat) -> obj with .pbo
    mat = np.column_stack([r, rng.normal(0.0, 0.01, size=400)])
    assert hasattr(cscv_pbo(mat), "pbo")

    # deflated_sharpe_ratio(sr, n_obs, trial_srs=) -> float
    dsr = deflated_sharpe_ratio(0.05, len(r), trial_srs=[0.05, 0.02])
    assert isinstance(dsr, float)

    # min_track_record_length(sr, target_sr=, confidence=) -> float
    mtrl = min_track_record_length(0.05, target_sr=0.01, confidence=0.95)
    assert isinstance(mtrl, float)
