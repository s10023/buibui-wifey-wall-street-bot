"""Paired replay on an overlay frame: buy-and-hold against one or more overlays.

The book has no trades, only a daily position in ``[0, 1]``, so
``analytics/backtest/cost_model.py`` (which prices ``Trade`` objects) does not
apply. Cost is charged where it is incurred, ``bps`` per unit of
``|Δposition|``, as in the H1 audit. A fractional position (VM's weight) is
priced the same way. The first session carries no entry cost on any arm, so the
arms differ only in what the rules do.
"""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

# OV-1's arm: ``ov`` holds the frame's ``pos`` column.
OV1_POSITIONS: Mapping[str, str] = {"ov": "pos"}


def arm_returns(
    frame: pd.DataFrame,
    *,
    bps: float,
    positions: Mapping[str, str] = OV1_POSITIONS,
) -> pd.DataFrame:
    """Daily net returns for ``bh`` and each arm in ``positions``, plus ``rf``.

    ``positions`` maps an arm name to the frame column holding its position.
    ``bh`` earns ``mkt`` every session. Each arm earns ``mkt`` on the fraction
    it holds and ``rf`` on the rest, less ``bps`` per unit of position change.
    The position columns are carried through under their own names.
    """
    out = {"bh": frame["mkt"]}
    for arm, col in positions.items():
        pos = frame[col]
        turnover = pos.diff().abs().fillna(0.0)
        out[arm] = (
            pos * frame["mkt"] + (1.0 - pos) * frame["rf"] - turnover * (bps / 10_000.0)
        )
    out["rf"] = frame["rf"]
    for col in dict.fromkeys(positions.values()):
        out[col] = frame[col]
    return pd.DataFrame(out)
