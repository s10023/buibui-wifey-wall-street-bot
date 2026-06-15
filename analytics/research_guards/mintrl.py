"""Minimum Track Record Length (López de Prado).

The number of observations a strategy needs before its PSR against
``target_sr`` reaches ``confidence``. Pure math; round-trips with ``psr.py``.
"""

from statistics import NormalDist

_NORM = NormalDist()


def min_track_record_length(
    sr: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    target_sr: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """Fractional number of observations needed for PSR to reach ``confidence``.

    ``kurtosis`` is non-excess (normal = 3.0). Returns ``inf`` when ``sr`` does
    not exceed ``target_sr`` (the confidence bound is unreachable). The result
    is fractional — callers typically ceil it.
    """
    if sr <= target_sr:
        return float("inf")
    variance = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    z = _NORM.inv_cdf(confidence)
    return 1.0 + variance * (z / (sr - target_sr)) ** 2
