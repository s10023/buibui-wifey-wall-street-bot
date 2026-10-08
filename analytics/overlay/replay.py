"""Two-arm replay on an overlay frame: buy-and-hold against the overlay.

The book has no trades, only a daily position in ``[0, 1]``, so
``analytics/backtest/cost_model.py`` (which prices ``Trade`` objects) does not
apply. Cost is charged where it is incurred, ``bps`` per unit of
``|Δposition|``, as in the H1 audit. The first session carries no entry cost on
either arm, so the arms differ only in what the rule does.
"""

from __future__ import annotations

import pandas as pd


def arm_returns(frame: pd.DataFrame, *, bps: float) -> pd.DataFrame:
    """Daily net returns for ``bh`` and ``ov``, plus ``rf`` and ``pos``.

    ``bh`` earns ``mkt`` every session. ``ov`` earns ``mkt`` on the fraction it
    holds and ``rf`` on the rest, less ``bps`` per unit of position change.
    """
    pos = frame["pos"]
    turnover = pos.diff().abs().fillna(0.0)
    ov = pos * frame["mkt"] + (1.0 - pos) * frame["rf"] - turnover * (bps / 10_000.0)
    return pd.DataFrame({"bh": frame["mkt"], "ov": ov, "rf": frame["rf"], "pos": pos})
