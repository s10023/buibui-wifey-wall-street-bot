"""Causal gap-fill "magnet" signal primitives (edge-hunt #5, equity-native).

Pure: numpy + pandas + the shared forecast vol primitive. No DB, no engine.

The thesis (forwarded from the parent's `/ingest-x`, @DaanCrypto re the S&P 500):
an **unfilled** overnight gap acts as a magnet — price tends to drift toward the
gap edge when it trades close to it, especially in a range regime. Equities gap
overnight and over weekends, so unlike the 24/7 crypto tape there is a dense gap
population to test.

**What the construction mechanically is — the a-priori claim, and what measuring
it actually returned.** The edge of an unfilled *up* gap sits BELOW the current
price and the edge of an unfilled *down* gap sits ABOVE it, so "trade toward the
nearest edge" is short-after-an-up-gap and long-after-a-down-gap. That reads like
a **gap-fade**, i.e. short-horizon reversal in a costume, which is why
`reversal_score` exists as a control — the `corr_to_trend` lesson from the xsmom
sleeve, where a +0.62 correlation meant the sleeve was not the diversifier it
looked like.

**That prediction was wrong, and the control is what showed it: measured
correlation −0.163** (2026-08-14, 501 names, 2,163 days). The nearest unfilled
edge is often days old and has nothing to do with yesterday's return, so the book
is very nearly uncorrelated with plain 1-session reversal. Keep the control arm:
it earned its place by falsifying the reasoning that motivated it, which is the
only way that reasoning could have been checked.

Every transform is causal. The score for the position held during day `d+1` is
built only from information observable at the close of day `d`, and the final
`.shift(1)` in `magnet_score` / `reversal_score` is what enforces it: the leverage
row multiplying the `d-1 -> d` return must be knowable at `d-1`.

Pre-registered constants — chosen a priori, NOT swept. Changing one is a new
trial and must be declared before results are read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol

MIN_GAP_SIGMA = 0.5
"""Materiality floor. A gap counts only if `|open/prev_close - 1|` is at least
this many trailing daily sigmas. US equities gap on nearly every session, so
without a floor "unfilled gap" is not a population — it is every bar."""

MAX_DIST_SIGMA = 2.0
"""The thesis' "when it trades close" clause. A gap edge further than this many
daily sigmas from the close scores 0 — it is not a magnet, it is scenery."""

GAP_MAX_AGE = 60
"""Sessions an unfilled gap stays a level. The claim is short-horizon; a gap from
three years ago is not what anyone means by a magnet, and an unbounded list would
also make the scan O(n^2)."""

RANGE_WINDOW = 20
"""Sessions in the market range-regime test."""

RANGE_SIGMA = 1.0
"""Sideways = the market drifted less than this many sigmas over `RANGE_WINDOW`."""

FILL_HORIZONS = (1, 5, 20, 60)
"""Descriptive fill-rate horizons, in sessions."""


def nearest_unfilled_level(
    bars: pd.DataFrame,
    vol_daily: pd.Series,
    *,
    min_gap_sigma: float = MIN_GAP_SIGMA,
    max_age: int = GAP_MAX_AGE,
) -> pd.Series:
    """Price level of the nearest still-unfilled gap edge, as of each day's close.

    `bars` needs `open`/`high`/`low`/`close` on a sorted daily index; `vol_daily`
    is the trailing daily return vol **already `.shift(1)`-ed**, so the
    materiality test at day `d` uses vol through `d-1` against a gap observable at
    `d`'s open.

    The magnet level is the gap's FAR edge — `close[d-1]`, the price the market
    must return to for the gap to be filled. An up gap (`open > prev_close`)
    leaves that edge below; a down gap leaves it above.

    Per day, in this order, each step using only day-`d` data:

    1. **create** day `d`'s gap if it clears the materiality floor;
    2. **expire** anything older than `max_age`, then **fill-check** every open
       gap against day `d`'s own high/low (so a gap can be created and filled on
       the same session, which is the common case);
    3. **snapshot** the surviving gap nearest to `close[d]`.

    Returns NaN on days with no live gap. Never looks forward: step 2 reads only
    `high[d]`/`low[d]`, and the result is stamped at `d`'s close.
    """
    o = bars["open"].to_numpy(dtype=np.float64)
    hi = bars["high"].to_numpy(dtype=np.float64)
    lo = bars["low"].to_numpy(dtype=np.float64)
    cl = bars["close"].to_numpy(dtype=np.float64)
    v = vol_daily.reindex(bars.index).to_numpy(dtype=np.float64)
    n = len(cl)
    out = np.full(n, np.nan, dtype=np.float64)

    # (level, is_up, created_index)
    open_gaps: list[tuple[float, bool, int]] = []
    for i in range(1, n):
        prev_close = cl[i - 1]
        sigma = v[i]
        if prev_close > 0.0 and np.isfinite(sigma) and sigma > 0.0:
            gap_pct = o[i] / prev_close - 1.0
            if np.isfinite(gap_pct) and abs(gap_pct) >= min_gap_sigma * sigma:
                open_gaps.append((prev_close, gap_pct > 0.0, i))

        alive: list[tuple[float, bool, int]] = []
        for level, is_up, created in open_gaps:
            if i - created >= max_age:
                continue
            filled = lo[i] <= level if is_up else hi[i] >= level
            if not filled:
                alive.append((level, is_up, created))
        open_gaps = alive

        if open_gaps:
            out[i] = min(open_gaps, key=lambda g: abs(g[0] - cl[i]))[0]

    return pd.Series(out, index=bars.index)


def gap_fill_stats(
    bars: pd.DataFrame,
    vol_daily: pd.Series,
    *,
    min_gap_sigma: float = MIN_GAP_SIGMA,
    horizons: tuple[int, ...] = FILL_HORIZONS,
) -> dict[str, float]:
    """Descriptive: how many material gaps, and what share fill within N sessions.

    **A fill rate is not an edge** and is reported as description only — a 70%
    fill rate with fat losing tails is still negative expectancy. The book in
    `magnet_score` is what carries the tradeable claim; this exists so the audit
    can state the population it is drawn from.

    Counts a gap as filled at horizon `h` if any of the `h` sessions from its
    creation day onward (inclusive) trades through the edge.
    """
    o = bars["open"].to_numpy(dtype=np.float64)
    hi = bars["high"].to_numpy(dtype=np.float64)
    lo = bars["low"].to_numpy(dtype=np.float64)
    cl = bars["close"].to_numpy(dtype=np.float64)
    v = vol_daily.reindex(bars.index).to_numpy(dtype=np.float64)
    n = len(cl)

    filled_by: list[int | None] = []
    n_up = 0
    for i in range(1, n):
        prev_close = cl[i - 1]
        sigma = v[i]
        if not (prev_close > 0.0 and np.isfinite(sigma) and sigma > 0.0):
            continue
        gap_pct = o[i] / prev_close - 1.0
        if not (np.isfinite(gap_pct) and abs(gap_pct) >= min_gap_sigma * sigma):
            continue
        is_up = gap_pct > 0.0
        n_up += int(is_up)
        hit: int | None = None
        for j in range(i, min(n, i + max(horizons))):
            if (lo[j] <= prev_close) if is_up else (hi[j] >= prev_close):
                hit = j - i + 1
                break
        filled_by.append(hit)

    total = len(filled_by)
    stats: dict[str, float] = {
        "n_gaps": float(total),
        "up_share": float(n_up / total) if total else float("nan"),
    }
    for h in horizons:
        stats[f"fill_{h}"] = (
            float(sum(1 for f in filled_by if f is not None and f <= h) / total)
            if total
            else float("nan")
        )
    return stats


def _raw_magnet(
    bars: pd.DataFrame, vol_daily: pd.Series, *, max_dist_sigma: float
) -> pd.Series:
    """Signed proximity to the nearest unfilled edge, in [-1, 1], at day `d`'s close.

    `+1` = the edge sits exactly at the close and above it (expect price up, go
    long); `-1` = at the close and below. Decays linearly to 0 at
    `max_dist_sigma` and is 0 beyond, which is the thesis' "trades close to
    them" clause expressed as a weight rather than a boolean cut.
    """
    close = bars["close"]
    level = nearest_unfilled_level(bars, vol_daily)
    dist = (level - close) / (close * vol_daily.reindex(bars.index))
    dist = dist.replace([np.inf, -np.inf], np.nan)
    weight = (1.0 - dist.abs() / max_dist_sigma).clip(lower=0.0)
    signed: pd.Series = np.sign(dist) * weight
    return signed


def magnet_score(
    bars_by_symbol: dict[str, pd.DataFrame],
    cfg: ForecastConfig,
    *,
    max_dist_sigma: float = MAX_DIST_SIGMA,
) -> pd.DataFrame:
    """Per-name gap-magnet score, `.shift(1)`-ed so it is usable as day-`d` leverage.

    Columns = symbols, index = sorted union daily index, warm-up NaN. The shift is
    the causality guard: the raw score at `d` reads `close[d]`, so the position it
    sizes cannot be held until `d+1`.
    """
    union = _union(bars_by_symbol)
    cols: dict[str, pd.Series] = {}
    for sym, bars in bars_by_symbol.items():
        vol = ew_return_vol(bars["close"], cfg.vol_span).shift(1)
        raw = _raw_magnet(bars, vol, max_dist_sigma=max_dist_sigma)
        cols[sym] = raw.shift(1).reindex(union)
    return pd.DataFrame(cols, index=union)


def reversal_score(
    bars_by_symbol: dict[str, pd.DataFrame], cfg: ForecastConfig
) -> pd.DataFrame:
    """CONTROL ARM: plain 1-session reversal, `.shift(1)`-ed identically.

    Negative previous-session return scaled by trailing vol — no gap logic at all.
    Because the magnet construction is mechanically a gap-fade (see the module
    docstring), a gap result that merely tracks this control is a reversal result
    wearing a gap costume. The report prints `corr_to_reversal` for exactly that
    reason; it is not a competing sleeve.
    """
    union = _union(bars_by_symbol)
    cols: dict[str, pd.Series] = {}
    for sym, bars in bars_by_symbol.items():
        close = bars["close"]
        vol = ew_return_vol(close, cfg.vol_span).shift(1)
        raw = -(close.pct_change() / vol)
        cols[sym] = raw.replace([np.inf, -np.inf], np.nan).shift(1).reindex(union)
    return pd.DataFrame(cols, index=union)


def cross_sectional_long_score(metric: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score, POSITIVE orientation: high metric -> long.

    Per row: `(x - row_mean) / row_std`. Rows with <2 finite values or zero
    dispersion -> all-NaN; inf -> NaN.

    **The sign is deliberately opposite to `lowvol.signals.cross_sectional_score`**,
    which is a *negative* z because its inputs (beta, realized vol) rank
    low-is-good. This sleeve's inputs are already signed and directional — a
    positive magnet score means "expect price to rise toward the edge" — so
    negating would invert every position. Two functions, one letter of difference
    in the formula, opposite meanings: read which one you are calling.
    """
    demeaned = metric.sub(metric.mean(axis=1), axis=0)
    z = demeaned.div(metric.std(axis=1), axis=0)
    return z.replace([np.inf, -np.inf], np.nan)


def range_regime_mask(
    market_ret: pd.Series,
    *,
    window: int = RANGE_WINDOW,
    sigma_mult: float = RANGE_SIGMA,
) -> pd.Series:
    """Causal boolean: was the MARKET sideways over the trailing `window`?

    Sideways = the cumulative market move over the window is smaller than
    `sigma_mult` times what its own realized vol implies for a window that long
    (`vol * sqrt(window)`). Both terms are trailing and the result is
    `.shift(1)`-ed, so day `d`'s mask is knowable at `d-1`.

    Defined at the MARKET level, not per name, because the thesis is a statement
    about the index chopping sideways. One pre-registered definition — the regime
    split is a single declared secondary cell, **not** a swept dimension; sweeping
    regime definitions is how a maximum gets mistaken for an effect.
    """
    cum = market_ret.rolling(window, min_periods=window).sum()
    vol = market_ret.rolling(window, min_periods=window).std() * np.sqrt(window)
    mask = cum.abs() < (sigma_mult * vol)
    out: pd.Series = mask.fillna(False).shift(1).fillna(False).astype(bool)
    return out


def _union(bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx: pd.DatetimeIndex | None = None
    for bars in bars_by_symbol.values():
        cur = pd.DatetimeIndex(bars.index)
        idx = cur if idx is None else idx.union(cur)
    if idx is None:
        return pd.DatetimeIndex([])
    return idx.sort_values()
