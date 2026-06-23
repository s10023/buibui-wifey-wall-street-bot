"""Causal PEAD signal + leverage primitives (edge-hunt #4, PEAD-lite).

Pure: numpy + pandas + the forecast vol primitive. No DB, no engine. The signal is
a *seasonal-random-walk* SUE (Foster-Olsen-Shevlin / Bernard-Thomas) — this
quarter's diluted EPS minus the same fiscal quarter a year ago, scaled by the
trailing std of that change — built only from reported EPS, so no paid analyst
consensus is needed.

Two causality guards make the book look-ahead-free:
  * the SUE std at announcement ``a`` uses only *prior* unexpected-earnings
    observations (``.shift(1).rolling(...)`` over announcement order);
  * a position enters on the **next NYSE session strictly after** the announcement
    date and is held flat for ``window`` sessions, so it never touches the
    announcement bar itself (the surprise jump is excluded; the drift is captured).
Spec: docs/superpowers/specs/2026-06-23-edge-hunt-4-pead-lite-design.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.vol import ew_return_vol

_SUE_WINDOW = 8  # trailing quarters for the UE std
_SUE_MIN = 4  # min prior UE observations before a SUE is defined (warm-up)
_DRIFT_WINDOW = 60  # pre-registered Bernard-Thomas drift horizon (trading sessions)


def _union(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for s in closes.values():
        idx = idx.union(pd.DatetimeIndex(s.index))
    return idx.sort_values()


def seasonal_sue(eps: pd.DataFrame) -> pd.DataFrame:
    """Add ``ue`` and ``sue`` columns to a long earnings panel.

    Input columns: ``symbol``, ``fy``, ``fp``, ``announce_date``, ``eps_diluted``.
    ``ue`` = EPS − EPS[same symbol, same fp, fy−1] (the seasonal random walk; NaN
    when the prior-year quarter is absent). ``sue`` = ``ue`` / trailing std of the
    prior ``ue`` observations (per symbol, by announcement order, ``min_periods``
    ``_SUE_MIN``) — causal, so no later announcement moves an earlier ``sue``.
    """
    df = eps.copy()
    df["announce_date"] = pd.to_datetime(df["announce_date"])
    lut = {
        (s, p, int(y)): v
        for s, p, y, v in zip(
            df["symbol"], df["fp"], df["fy"], df["eps_diluted"], strict=True
        )
    }
    prior = [
        lut.get((s, p, int(y) - 1))
        for s, p, y in zip(df["symbol"], df["fp"], df["fy"], strict=True)
    ]
    df["ue"] = df["eps_diluted"] - pd.Series(prior, index=df.index, dtype="float64")
    df = df.sort_values(["symbol", "announce_date"]).reset_index(drop=True)
    std = df.groupby("symbol")["ue"].transform(
        lambda s: s.shift(1).rolling(_SUE_WINDOW, min_periods=_SUE_MIN).std()
    )
    df["sue"] = (df["ue"] / std).replace([np.inf, -np.inf], np.nan)
    return df


def _active_sue(
    sue: pd.DataFrame,
    union: pd.DatetimeIndex,
    symbols: list[str],
    *,
    long_only: bool,
    window: int,
) -> pd.DataFrame:
    """Wide daily SUE held flat over each name's [announce+1, announce+window) cohort.

    Entry = first session strictly after the announcement (causal); later
    announcements overwrite an overlapping window. For ``long_only`` only positive
    surprises create a position (non-positive → flat / no entry).
    """
    cols: dict[str, pd.Series] = {}
    for sym in symbols:
        col = pd.Series(np.nan, index=union, dtype="float64")
        sub = sue[sue["symbol"] == sym].sort_values("announce_date")
        for _, row in sub.iterrows():
            s = row["sue"]
            if not np.isfinite(s) or (long_only and s <= 0.0):
                continue
            pos = int(union.searchsorted(row["announce_date"], side="right"))
            if pos >= len(union):
                continue
            col.iloc[pos : min(pos + window, len(union))] = float(s)
        cols[sym] = col
    return pd.DataFrame(cols, index=union)


def sue_leverage(
    sue: pd.DataFrame,
    closes: dict[str, pd.Series],
    *,
    long_only: bool,
    window: int = _DRIFT_WINDOW,
    cfg: ForecastConfig | None = None,
) -> pd.DataFrame:
    """Wide daily leverage matrix from overlapping SUE drift cohorts.

    Each active name is vol-parity sized on its (fixed-at-entry) SUE:
    ``lev_i = sue_i * (vol_target / vol_ann_i)`` with the causal ``ew_return_vol``
    (already ``.shift(1)``-ed). ``long_only=False`` then demeans the active cohort
    each day so the book is dollar-neutral (Σw = 0); ``long_only=True`` keeps the
    long-or-flat weights as-is (no shorts, no re-center). Columns = symbols,
    index = sorted union daily index.
    """
    cfg = cfg or ForecastConfig()
    union = _union(closes)
    symbols = list(closes.keys())
    active = _active_sue(sue, union, symbols, long_only=long_only, window=window)

    ann_sqrt = np.sqrt(cfg.annualization_days)
    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = (ew_return_vol(close, cfg.vol_span) * ann_sqrt).reindex(union)
        lev = active[sym] * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    lev_df = pd.DataFrame(lev_cols, index=union)

    if not long_only:
        lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)
    return lev_df
