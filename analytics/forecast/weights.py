"""Candidate forecast-weight schemes for the P2 weight study.

Pure: weight vectors are derived from the speed *structure* (count + slow
spans), never from realized performance. Each scheme is flagged ``a_priori``
(no look-ahead, defensible to ship) or not (data-snooped, motivated by the
observed per-speed Sharpes — must be haircut as such).

The a-priori set is ``{equal, inverse_cost}``: no clean a-priori non-equal
handcraft toward the *fast* speeds exists without correlation estimation on
this data (which would reintroduce look-ahead), so any fast tilt is, by
construction, data-snooped here.
"""

from __future__ import annotations

from typing import NamedTuple

from analytics.forecast.config import ForecastConfig


class WeightScheme(NamedTuple):
    weights: tuple[float, ...]
    a_priori: bool


def _norm(w: tuple[float, ...]) -> tuple[float, ...]:
    s = sum(w)
    if s <= 0.0:
        raise ValueError("weight scheme must have positive sum")
    return tuple(x / s for x in w)


def candidate_schemes(cfg: ForecastConfig) -> dict[str, WeightScheme]:
    """Labelled weight-scheme family for the study (speeds ordered fast->slow)."""
    n = len(cfg.speeds)
    slows = [float(slow) for _, slow, _ in cfg.speeds]

    equal = _norm(tuple(1.0 for _ in range(n)))
    # slower legs trade less -> cheaper: a-priori weight proportional to slow span
    inverse_cost = _norm(tuple(slows))

    fast_tilt_linear = _norm(tuple(float(n - i) for i in range(n)))
    rho = 0.5
    fast_tilt_geom = _norm(tuple(rho**i for i in range(n)))
    half = (n + 1) // 2
    drop_two_slowest = _norm(tuple(1.0 if i < half else 0.0 for i in range(n)))
    fast_only = _norm(tuple(1.0 if i == 0 else 0.0 for i in range(n)))

    return {
        "equal": WeightScheme(equal, True),
        "inverse_cost": WeightScheme(inverse_cost, True),
        "fast_tilt_linear": WeightScheme(fast_tilt_linear, False),
        "fast_tilt_geom": WeightScheme(fast_tilt_geom, False),
        "drop_two_slowest": WeightScheme(drop_two_slowest, False),
        "fast_only": WeightScheme(fast_only, False),
    }
