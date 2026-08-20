"""Correlation deflator — how many INDEPENDENT series a pooled panel carries.

Ported 2026-08-20 from the parent's ``analytics/forecast/attribution.py``
(``effective_independent_series``), where it deflates the EWMAC regime
attribution's t-stats. The numeric path is unchanged. Four things diverge, and
each is a decision rather than drift:

* **Home.** Upstream hosts it inside a sleeve-attribution module. Here it is a
  research guard — its whole job is to stop a pooled cell reading as
  significant when it is not, which is this package's remit — and it sits
  beside :mod:`analytics.research_guards.power`, whose consumer
  ``tools/distil_power.py`` is this function's consumer too.
* **Return shape.** Upstream returns a bare ``(n_eff, t_deflator)``. This
  returns :class:`SeriesDeflator`, carrying ``k`` and ``rho`` as well, because
  a deflator quoted without its ``rho`` cannot be checked by the next reader.
  Matches ``BootstrapCI`` / ``HaircutResult`` / ``PBOResult`` next door.
* **``measured``.** An addition, not a change: see the flag's rationale on
  :func:`effective_independent_series`.
* **Domain.** The docstrings speak about equities rather than perps.

⚠ **The parent's measured ``n_eff`` 2.92 does NOT transfer.** That is 25 crypto
perps at mean pairwise ``rho`` 0.315, and it moves on its own panel (14 series
→ 1.97, three → 1.42). Measure the panel in front of you. A deflator quoted for
one panel and reused on another is the same defect as ``regime.py``'s crypto
bar counts crossing the fork — a figure that looks portable and silently
changes meaning.

Why this exists here at all: ``tools/distil_power.py`` has always **accepted**
``--n-series`` / ``--n-eff`` and deflated by them, while nothing in this repo
could **measure** the second. ``distil_power.effective_n`` returns ``n_obs``
undeflated when both are omitted, so the deflator failed open on the very tool
that priced H-004. (H-001/H-002 used a two-sample MDE instead, which this module
bears on equally: a pooled ``sd`` is subject to the same correlation.)

The approximation is equicorrelation: one constant pairwise correlation, no
autocorrelation term. It is deliberately crude and still decision-changing —
on a 505-name equity panel the market factor alone drives ``n_eff`` into the
single digits, so a naive pooled t-stat is inflated by an order of magnitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["SeriesDeflator", "effective_independent_series"]


@dataclass(frozen=True)
class SeriesDeflator:
    """Effective independent series behind a pooled cross-section.

    ``rho`` is ``nan`` when no pair could be correlated at all. It is reported
    even when ``measured`` is ``False`` and the model degenerated, so the
    caller can see *why* rather than only *that*.
    """

    k: int
    rho: float
    n_eff: float
    t_deflator: float
    measured: bool


def effective_independent_series(
    per_instrument_net: dict[str, pd.Series],
) -> SeriesDeflator:
    """Return the correlation deflator for ``k`` correlated return streams.

    Pooling symbol-days treats ``k`` instruments as ``k`` independent bets.
    They are not: under an equicorrelation approximation with mean pairwise
    correlation ``rho``, ``k`` series carry the noise reduction of only
    ``n_eff = k / (1 + (k-1) * rho)`` independent ones, so a naive t-stat over
    pooled symbol-days is inflated by ``sqrt(k / n_eff)``.

    ``measured`` is ``False`` whenever the deflator could not be computed —
    fewer than two series, no overlapping pair to correlate, or a mean
    correlation so negative that the equicorrelation model degenerates. Every
    one of those returns ``t_deflator == 1.0``, which is *numerically
    identical* to a genuinely uncorrelated panel; collapsing the two states
    would print the confident one. **A caller that gates on the deflator must
    read ``measured`` first** — that is the whole reason the flag exists, and
    ``tools/n_eff.py`` refuses to emit ``distil_power`` flags without it.
    """
    k = len(per_instrument_net)
    if k < 2:
        return SeriesDeflator(
            k=k, rho=math.nan, n_eff=float(max(k, 1)), t_deflator=1.0, measured=False
        )
    corr = pd.DataFrame(per_instrument_net).corr().to_numpy()
    off = corr[~np.eye(k, dtype=bool)]
    off = off[~np.isnan(off)]
    if len(off) == 0:
        return SeriesDeflator(
            k=k, rho=math.nan, n_eff=float(k), t_deflator=1.0, measured=False
        )
    rho = float(np.mean(off))
    denom = 1.0 + (k - 1) * rho
    if denom <= 0:
        return SeriesDeflator(
            k=k, rho=rho, n_eff=float(k), t_deflator=1.0, measured=False
        )
    n_eff = k / denom
    if n_eff <= 0:
        return SeriesDeflator(
            k=k, rho=rho, n_eff=float(k), t_deflator=1.0, measured=False
        )
    # Clamped at 1.0 on purpose (upstream's reasoning, carried verbatim). A
    # negative mean correlation would make n_eff > k and the "deflator" would
    # INFLATE the t-stat — the fail-open direction for something whose whole
    # job is to stop a cell reading as significant when it is not. It does not
    # bind on a long-only equity panel (rho > 0), but a guard should be
    # fail-safe by construction, not by luck.
    return SeriesDeflator(
        k=k,
        rho=rho,
        n_eff=n_eff,
        t_deflator=max(1.0, math.sqrt(k / n_eff)),
        measured=True,
    )
