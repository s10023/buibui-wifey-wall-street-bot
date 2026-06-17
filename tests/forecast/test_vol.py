from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.vol import annualize, ew_return_vol, price_vol


def test_annualize() -> None:
    assert annualize(0.02, 365.0) == 0.02 * math.sqrt(365.0)


def test_ew_return_vol_is_causal_and_shifted() -> None:
    # rising then a shock: the vol at index t must NOT include return at t.
    close = pd.Series([100.0, 101.0, 102.0, 103.0, 80.0, 81.0])
    vol = ew_return_vol(close, span=3)
    # index 0 has no prior return -> NaN; index 1 uses only return at idx1 via shift -> still NaN
    assert math.isnan(vol.iloc[0])
    # the big -22% shock lands at index 4; the position-sizing vol AT index 4
    # must come from data through index 3 (pre-shock, small vol).
    assert vol.iloc[4] < 0.05
    # by index 5 the shock is in the estimate -> vol jumps.
    assert vol.iloc[5] > vol.iloc[4]


def test_price_vol_is_return_vol_times_price() -> None:
    close = pd.Series([100.0, 110.0, 121.0, 133.1, 146.41])
    rv = ew_return_vol(close, span=3)
    pv = price_vol(close, span=3)
    # where both are defined, pv == rv * close
    mask = ~rv.isna()
    np.testing.assert_allclose(
        pv[mask].to_numpy(), (rv[mask] * close[mask]).to_numpy(), rtol=1e-12
    )


def test_annualize_defaults_to_252_trading_days() -> None:
    # equity adaptation D1: NYSE sessions, not 365
    assert annualize(0.01) == 0.01 * (252.0**0.5)
