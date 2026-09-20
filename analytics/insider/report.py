"""Assemble the H-024 verdict: per-cell guards, β attribution, placebo pairing.

Reuses ``evaluate_xs`` (DSR / PBO / boot-CI) and ``beta_attribution`` from the
xsmom sleeve, and reads ``passes_sleeve_gate`` on the pre-committed ``T1`` cell
only. Two things this report does that no earlier sleeve's did, both because the
pre-registration asked for them by name:

``paired``      The reversal observable. Each trial is differenced against its
                routine-arm placebo day-for-day and bootstrapped: **a CI
                containing zero means the routine and opportunistic arms are
                indistinguishable on this panel, so the classification carries no
                information here and the row closes.** This is a control, not a
                trial — a placebo never enters the DSR family.

``long_only``   T2/T4 are not market-neutral, so the velocity lesson binds
                (``long_only`` +0.649 gross hedged to +0.004, alpha t +0.01): a
                long-only pass counts as signal only when the beta-hedged alpha
                t-stat is positive. Declared in the spec before any data.

⚠ **The DSR family is the FOUR trials, not the eight books.** The pre-registered
family size is what ``distil_power`` priced the 0.3047 bar at; deflating against
eight would silently raise the bar the sleeve was registered to clear, and
inflating it by counting controls as trials is the same error in the other
direction.

⚠ **"Session-day cluster keys" collapses to a no-op at this unit and that is
why no cluster key is passed.** ``audit_guard``'s clustering exists because many
trades share one session day; a calendar-time book already emits exactly one
observation per session day, so the deflator would be 1.0 by construction. The
serial dependence that remains is absorbed by the stationary block bootstrap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.insider.book import PLACEBOS, TRIALS
from analytics.research_guards import GATE_SHARPE, passes_sleeve_gate
from analytics.research_guards.bootstrap import block_bootstrap_ci
from analytics.xsmom.book import XSBookResult
from analytics.xsmom.diagnostics import BetaAttribution, beta_attribution
from analytics.xsmom.report import XSReport, evaluate_xs

#: The trial whose gate is the sleeve's verdict. Frozen.
PRIMARY_KEY = "T1"

#: Trials that are long-only, and therefore carry the beta-hedged alpha
#: condition instead of the realized-beta guardrail.
LONG_ONLY_KEYS = tuple(key for key, (_, long_only, _) in TRIALS.items() if long_only)


@dataclass(frozen=True)
class PairedDifference:
    """One trial minus its routine placebo, bootstrapped on the paired series.

    ⚠ **Three states, not two.** "The arms are indistinguishable" and "the pair
    could not be measured" are different findings and collapsing them prints the
    confident one — the ``audit_guard`` lesson (an ``INSUFFICIENT`` cell is not a
    powered null). ``measurable`` is False when neither book was ever funded, and
    a book of all zeros is an empty panel rather than a book that agrees with its
    mirror.
    """

    trial: str
    placebo: str
    mean_diff_daily: float
    lo: float
    hi: float
    measurable: bool

    @property
    def indistinguishable(self) -> bool:
        """True when the pair IS measurable and its CI contains zero.

        This is the spec's reversal observable. An unmeasurable pair returns
        False here and must be read off :attr:`measurable`, never as evidence
        that the two arms behaved the same way.
        """
        return bool(self.measurable and self.lo <= 0.0 <= self.hi)


@dataclass(frozen=True)
class InsiderReport:
    """The four trials, the four placebos, and the pre-registered verdict legs."""

    cells: dict[str, XSReport]
    attribution: dict[str, BetaAttribution]
    paired: dict[str, PairedDifference]
    primary_key: str
    passed: bool

    @property
    def realized_beta(self) -> float:
        """Realized β of the primary L/S cell — the construction's guardrail."""
        return self.attribution[self.primary_key].beta

    @property
    def long_only_signal(self) -> dict[str, bool]:
        """Per long-only trial: does a pass count as signal at all?

        A long-only book is market exposure plus whatever else it has, so the
        spec pre-committed to reading the beta-hedged alpha t-stat rather than
        the raw Sharpe. False here means a headline Sharpe is beta, not edge.
        """
        return {
            key: bool(self.attribution[key].alpha_tstat > 0.0)
            for key in LONG_ONLY_KEYS
            if key in self.attribution
        }

    @property
    def reversal_fires(self) -> bool:
        """True when the PRIMARY trial is indistinguishable from its placebo."""
        pair = self.paired.get(self.primary_key)
        return bool(pair.indistinguishable) if pair is not None else False


def _mean(x: npt.NDArray[np.float64]) -> float:
    return float(np.mean(x)) if len(x) else float("nan")


def paired_difference(
    trial: XSBookResult, placebo: XSBookResult, *, trial_key: str, placebo_key: str
) -> PairedDifference:
    """Bootstrap the daily difference between a trial and its routine mirror.

    Both books are produced on one index by ``replay_insider_trials``, so the
    subtraction is paired by construction; the common-tail slice below is a
    guard against a caller that built them separately, not a repair for a date
    misalignment it could not detect.
    """
    a, b = trial.portfolio_return, placebo.portfolio_return
    n = min(len(a), len(b))
    diff = np.asarray(a[-n:] - b[-n:], dtype=np.float64)
    # Measurability is a property of the BOOKS, not of their difference: an
    # all-zero difference between two funded books is a real null, while two
    # never-funded books produce the same zeros and establish nothing.
    funded = bool(np.any(a[-n:] != 0.0) or np.any(b[-n:] != 0.0))
    if n < 2 or not funded:
        return PairedDifference(
            trial=trial_key,
            placebo=placebo_key,
            mean_diff_daily=_mean(diff),
            lo=float("nan"),
            hi=float("nan"),
            measurable=False,
        )
    ci = block_bootstrap_ci(diff, stat_fn=_mean, seed=7)
    return PairedDifference(
        trial=trial_key,
        placebo=placebo_key,
        mean_diff_daily=_mean(diff),
        lo=ci.lo,
        hi=ci.hi,
        measurable=True,
    )


def evaluate_insider_trials(
    books: dict[str, XSBookResult],
    cfg: ForecastConfig,
    market_ret: pd.Series,
    *,
    primary_key: str = PRIMARY_KEY,
) -> InsiderReport:
    """Score every book, gate the primary cell, and pair each trial's placebo.

    ``market_ret`` is the SPY daily return over the same index. For the L/S cells
    a large realized β means the dollar-neutral construction FAILED, so that cell
    is not evidence about the premise — the ``lowvol`` / ``pead`` precedent, where
    a fired guardrail invalidated the construction rather than the claim.

    H-024 has no trend-sleeve comparison, so an empty trend array is passed, as
    the BAB, gapfill and velocity reports all do.
    """
    family = {key: books[key].portfolio_return for key in TRIALS if key in books}
    empty_trend = np.asarray([], dtype=np.float64)

    cells: dict[str, XSReport] = {}
    attribution: dict[str, BetaAttribution] = {}
    for key, book in books.items():
        cells[key] = evaluate_xs(
            book, cfg, trial_returns=family, trend_returns=empty_trend
        )
        r = book.portfolio_return
        m: npt.NDArray[np.float64] = market_ret.reindex(book.daily_index).to_numpy(
            dtype=np.float64
        )
        live = r != 0.0  # warm-up and unfunded days are 0.0 by construction
        attribution[key] = beta_attribution(r[live], m[live], cfg.annualization_days)

    paired: dict[str, PairedDifference] = {}
    for trial_key, placebo_key in zip(TRIALS, PLACEBOS, strict=True):
        if trial_key in books and placebo_key in books:
            paired[trial_key] = paired_difference(
                books[trial_key],
                books[placebo_key],
                trial_key=trial_key,
                placebo_key=placebo_key,
            )

    primary = cells[primary_key]
    passed = passes_sleeve_gate(
        dsr=primary.dsr,
        pbo=primary.pbo,
        boot_lo=primary.boot_lo,
        sharpe_annual=primary.sharpe_annual,
        gate_sharpe=GATE_SHARPE,
    )
    return InsiderReport(
        cells=cells,
        attribution=attribution,
        paired=paired,
        primary_key=primary_key,
        passed=passed,
    )


def annualized_mean_bps(daily: float) -> float:
    """Daily mean return as monthly basis points — the WP's reporting unit.

    The paper quotes 82 bps/mo; a daily mean is not comparable to it by eye, and
    every earlier sleeve that mixed the two units published a number nobody could
    check against its source.
    """
    return daily * (252.0 / 12.0) * 10_000.0 if math.isfinite(daily) else float("nan")
