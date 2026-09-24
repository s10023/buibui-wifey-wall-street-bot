"""Calendar-time value-weighted insider book — H-024 phase 3, pure.

Turns labelled Form 4 transactions into a daily weight matrix and books that
matrix against daily closes, net of the shared-base cost model. Nothing here
touches the DB or the network: :mod:`analytics.insider.replay` is the front
door, so every branch below is fixture-testable.

The pre-registration this implements is frozen in
``docs/superpowers/specs/2026-08-29-h024-insider-routine-opportunistic-design.md``:
monthly calendar-time formation from the **filing** month, value weights at
formation, a 1- or 3-month hold with 3-month holds run as overlapping thirds
(Jegadeesh-Titman), and costs from the shared base.

Three operationalisations the frozen text left unsourced, resolved here and
recorded as Amendment 4 rather than left to whoever reads the output:

1. **"Value-weighted by market cap" is booked on trailing dollar ADV.** There is
   no market-cap series in this repo — not in the DB, not in
   ``config/universe.json`` — so a literal reading is unimplementable without a
   new provider. ADV is computed from our own bars, is point-in-time by
   construction, and is the same quantity the cost model already buckets, so
   the weight and the spread it pays are read off one number. It is a
   liquidity weight, not a size weight: ADV/market-cap is turnover, which
   varies several-fold cross-sectionally, so the book tilts toward
   high-turnover names. This was decided before any return was computed, so
   the choice cannot be reverse-engineered from the result.
2. **The cost model is applied in RETURN space, not in R.**
   ``CostModel.cost_breakdown`` is trade-shaped (entry/stop prices) and a weight
   book has neither, so the *parameters* are used directly: half-spread by ADV
   bucket and sqrt-law impact charged on ``|dw|``, borrow accrued daily on short
   weights. Same constants, same buckets, different denominator.
3. **Formation reads ``filing_date``, falling back to ``acceptance_ts``.** The
   frozen line says "filed during that month"; a row carrying neither is dropped
   rather than dated from its trade, because dating a filing by its trade date
   is precisely the lookahead the filing clock exists to avoid.

This book carries no volatility governor: the pre-registration is a VW
cash book, so ``pre_governor_return`` equals ``portfolio_return`` and
``governor`` is all-ones — those fields exist to satisfy :class:`XSBookResult`,
which the shared evaluation path consumes, and are not a disabled feature.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.backtest.cost_model import CostModel
from analytics.insider.classify import OPPORTUNISTIC, ROUTINE
from analytics.xsmom.book import XSBookResult

#: The pre-registered panel start. Form 4 history reaches back to 2015 so that
#: classification at the start of 2018 has its three preceding years.
STUDY_START = "2018-01-02"

#: Open-market purchase / sale codes. The long leg is P, the short leg is S.
BUY_CODE = "P"
SELL_CODE = "S"

#: Trading days per year, for the daily borrow accrual.
DAYS_PER_YEAR = 252.0

#: The frozen trial family — exactly four cells, fixed in advance. Changing this
#: table re-opens the trial count and therefore the DSR deflation.
#: key -> (label, long_only, hold_months)
TRIALS: dict[str, tuple[str, bool, int]] = {
    "T1": (OPPORTUNISTIC, False, 1),
    "T2": (OPPORTUNISTIC, True, 1),
    "T3": (OPPORTUNISTIC, False, 3),
    "T4": (OPPORTUNISTIC, True, 3),
}

#: The routine-arm mirror of every trial. A control, never a trial: these do not
#: enter the DSR family, and the reversal observable is read on the paired
#: difference between a trial and its placebo.
PLACEBOS: dict[str, tuple[str, bool, int]] = {
    f"P{key[1:]}": (ROUTINE, long_only, hold)
    for key, (_, long_only, hold) in TRIALS.items()
}


@dataclass(frozen=True)
class InsiderInputs:
    """Day-indexed price and liquidity panels, aligned on one union index."""

    closes: pd.DataFrame
    dollar_volume: pd.DataFrame

    @property
    def symbols(self) -> list[str]:
        return [str(c) for c in self.closes.columns]


def trailing_adv(dollar_volume: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Trailing mean daily dollar volume — the VW proxy AND the cost bucket key.

    Causal by construction: row ``t`` reads bars up to and including ``t``, and
    the book only ever consults it on a formation date whose weights first earn
    on ``t+1``.
    """
    return dollar_volume.rolling(window=window_days, min_periods=1).mean()


def trailing_sigma(closes: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Trailing daily return stdev — the sqrt-law impact term's sigma."""
    return closes.pct_change().rolling(window=window_days, min_periods=3).std()


def month_end_dates(index: pd.DatetimeIndex) -> dict[pd.Period, pd.Timestamp]:
    """Last trading day of each calendar month present in ``index``.

    Formation happens on a trading day, never on a calendar month-end the tape
    may not have: a Sunday 30th would index nothing and silently drop that
    month's cohort.
    """
    if len(index) == 0:
        return {}
    stamps = pd.Series(index, index=index)
    return {
        pd.Period(period): pd.Timestamp(group.max())
        for period, group in stamps.groupby(index.to_period("M"))
    }


def filing_month(labelled: pd.DataFrame) -> pd.Series:
    """The month a row became public — ``filing_date``, else ``acceptance_ts``.

    Returns NaT for a row carrying neither, which :func:`cohort_weights` drops.
    Dating such a row from its transaction date would date a filing by
    information the market did not yet have.
    """
    if "filing_date" in labelled.columns:
        filed = pd.to_datetime(labelled["filing_date"], errors="coerce", utc=True)
    else:
        filed = pd.Series(pd.NaT, index=labelled.index, dtype="datetime64[ns, UTC]")
    if "acceptance_ts" in labelled.columns:
        accepted = pd.to_datetime(labelled["acceptance_ts"], errors="coerce", utc=True)
        filed = filed.fillna(accepted)
    return filed.dt.tz_localize(None).dt.to_period("M")


def cohort_weights(
    labelled: pd.DataFrame,
    adv: pd.DataFrame,
    *,
    label: str,
    code: str,
) -> dict[pd.Period, pd.Series]:
    """Value weights per filing month for one (label, side) leg.

    A name enters month ``M``'s cohort when at least one insider carrying
    ``label`` filed a ``code`` trade in it. Weights are that name's trailing ADV
    at the month's last trading day, normalised to sum to 1 across the cohort.
    A name whose ADV is missing or non-positive at formation is dropped rather
    than floored: an unpriceable name is not a position.
    """
    if labelled.empty or adv.empty:
        return {}

    frame = labelled
    if "label" in frame.columns:
        frame = frame[frame["label"].astype(str) == label]
    if "transaction_code" in frame.columns:
        frame = frame[frame["transaction_code"].astype(str) == code]
    if frame.empty:
        return {}

    months = filing_month(frame)
    ends = month_end_dates(pd.DatetimeIndex(adv.index))

    out: dict[pd.Period, pd.Series] = {}
    for period, group in frame.groupby(months):
        key = pd.Period(period)
        formation = ends.get(key)
        if formation is None:
            continue
        names = sorted({str(s) for s in group["symbol"] if str(s) in adv.columns})
        if not names:
            continue
        # reindex rather than .loc: a name absent from the panel comes back NaN
        # and is dropped below, where .loc would raise on it.
        row = adv.reindex(index=[formation], columns=names).to_numpy(dtype=float)[0]
        sizes = pd.Series(row, index=names, dtype=float)
        sizes = sizes[sizes.notna() & (sizes > 0.0)]
        total = float(sizes.sum())
        if total <= 0.0:
            continue
        out[key] = sizes / total
    return out


def daily_weights(
    cohorts: dict[pd.Period, pd.Series],
    index: pd.DatetimeIndex,
    columns: list[str],
    *,
    hold_months: int,
) -> pd.DataFrame:
    """Expand monthly cohorts into the daily weight matrix the book earns on.

    Each cohort carries ``1 / hold_months`` of the leg and is held over the
    ``hold_months`` months following its formation, so a 3-month hold runs as
    three overlapping thirds and the leg sums to 1 once it is fully ramped.

    The ramp is left honest: for the first ``hold_months - 1`` months only
    some of the sleeves are funded, so leg exposure starts below 1 rather than
    being renormalised up — normalising would lever the earliest cohorts, which
    is a position no investor could have held.

    Causality: a cohort formed on its month's last trading day first earns on
    the next trading day, so no weight is informed by the return it books.
    """
    weights = pd.DataFrame(0.0, index=index, columns=columns)
    if not cohorts or len(index) == 0:
        return weights

    ends = month_end_dates(index)
    share = 1.0 / float(hold_months)
    for period, cohort in cohorts.items():
        formation = ends.get(period)
        if formation is None:
            continue
        start = int(index.searchsorted(formation, side="right"))
        stop_stamp = ends.get(period + hold_months)
        stop = (
            len(index)
            if stop_stamp is None
            else int(index.searchsorted(stop_stamp, side="right"))
        )
        if start >= stop:
            continue
        held = [name for name in cohort.index if name in weights.columns]
        if not held:
            continue
        positions = weights.columns.get_indexer(pd.Index(held))
        current = weights.iloc[start:stop, positions].to_numpy(dtype=float)
        weights.iloc[start:stop, positions] = (
            current + cohort.loc[held].to_numpy(dtype=float) * share
        )
    return weights


def leg_weights(
    labelled: pd.DataFrame,
    adv: pd.DataFrame,
    index: pd.DatetimeIndex,
    columns: list[str],
    *,
    label: str,
    long_only: bool,
    hold_months: int,
) -> pd.DataFrame:
    """The signed daily weight matrix for one pre-registered cell.

    Long-only holds the buy leg alone and sums to +1; the long-short cell is
    ``buys - sells``, each leg normalised to 1, so it is dollar-neutral at gross
    2. A name both bought and sold in one filing month appears in both legs and
    nets down, which is the honest reading of two cohorts formed from the same
    month's filings.
    """
    buys = daily_weights(
        cohort_weights(labelled, adv, label=label, code=BUY_CODE),
        index,
        columns,
        hold_months=hold_months,
    )
    if long_only:
        return buys
    sells = daily_weights(
        cohort_weights(labelled, adv, label=label, code=SELL_CODE),
        index,
        columns,
        hold_months=hold_months,
    )
    return buys - sells


def book_weights(
    weights: pd.DataFrame,
    closes: pd.DataFrame,
    adv: pd.DataFrame,
    sigma: pd.DataFrame,
    cost: CostModel,
) -> XSBookResult:
    """Book a daily weight matrix net of the shared-base cost model.

    Per name: ``gross = w * return``; ``spread`` and sqrt-law ``impact`` are
    charged on ``|dw|`` at that name's own ADV bucket, and ``borrow`` accrues
    daily on short weights. The portfolio is the sum of legs, matching
    ``run_xs_backtest`` so both books' Sharpes are read on one convention.
    """
    idx = pd.DatetimeIndex(weights.index)
    rets = closes.reindex(index=idx, columns=weights.columns).pct_change()
    w = weights.fillna(0.0)

    gross = w * rets.fillna(0.0)
    turnover = w.diff()
    turnover.iloc[0] = w.iloc[0]
    turnover = turnover.abs()

    adv_a = adv.reindex(index=idx, columns=w.columns).to_numpy(dtype=float)
    sigma_a = sigma.reindex(index=idx, columns=w.columns).to_numpy(dtype=float)

    edges = np.asarray(cost.adv_thresholds, dtype=float)
    spreads = np.asarray(cost.half_spread_bps, dtype=float) / 10_000.0
    priced = np.isfinite(adv_a) & (adv_a > 0.0)
    # Unknown ADV lands in bucket 0 — the widest spread, per the cost model.
    bucket = np.searchsorted(edges, np.where(priced, adv_a, 0.0), side="right")
    spread_frac = spreads[bucket]

    impact_frac = np.zeros_like(adv_a)
    usable = priced & np.isfinite(sigma_a) & (sigma_a > 0.0)
    impact_frac[usable] = (
        cost.impact_coef * sigma_a[usable] * np.sqrt(cost.notional_usd / adv_a[usable])
    )

    trade_cost = turnover * (spread_frac + impact_frac)
    borrow = (-w).clip(lower=0.0) * (cost.borrow_rate_annual / DAYS_PER_YEAR)

    net = gross - trade_cost - borrow
    portfolio: npt.NDArray[np.float64] = net.sum(axis=1).to_numpy(dtype=np.float64)
    active: npt.NDArray[np.float64] = w.ne(0.0).sum(axis=1).to_numpy(dtype=np.float64)

    return XSBookResult(
        daily_index=idx,
        portfolio_return=portfolio,
        pre_governor_return=portfolio.copy(),
        governor=np.ones(len(idx), dtype=np.float64),
        active_count=active,
        per_instrument_net={str(col): net[col] for col in net.columns},
    )
