from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.ewmac import combine_forecasts, raw_ewmac, scaled_forecast


def test_raw_ewmac_positive_in_uptrend() -> None:
    close = pd.Series(np.linspace(100.0, 200.0, 300))
    raw = raw_ewmac(close, fast=8, slow=32)
    # steady uptrend -> fast EMA above slow EMA -> positive once warmed up
    assert raw.iloc[-1] > 0.0


def test_raw_ewmac_matches_pandas_ewm() -> None:
    close = pd.Series([1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0])
    expected = (
        close.ewm(span=2, adjust=False).mean() - close.ewm(span=4, adjust=False).mean()
    )
    pd.testing.assert_series_equal(raw_ewmac(close, 2, 4), expected, check_names=False)


def test_scaled_forecast_is_capped() -> None:
    # explosive trend -> raw/price_vol large -> must clip to +cap
    close = pd.Series(np.geomspace(1.0, 1e6, 400))
    f = scaled_forecast(close, fast=8, slow=32, scalar=5.3, vol_span=32, cap=20.0)
    assert f.dropna().max() <= 20.0 + 1e-9
    assert f.dropna().min() >= -20.0 - 1e-9


def test_scaled_forecast_sign_follows_trend() -> None:
    up = pd.Series(np.linspace(100.0, 300.0, 400))
    down = pd.Series(np.linspace(300.0, 100.0, 400))
    assert scaled_forecast(up, 8, 32, 5.3, 32, 20.0).iloc[-1] > 0.0
    assert scaled_forecast(down, 8, 32, 5.3, 32, 20.0).iloc[-1] < 0.0


def test_combine_is_fdm_times_equal_weight_mean_then_capped() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    combined = combine_forecasts(close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0)

    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    f2 = scaled_forecast(close, 16, 64, 3.75, 32, 20.0)
    expected = ((f1 + f2) / 2.0 * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(combined, expected, check_names=False)


def test_combine_respects_cap_after_fdm() -> None:
    close = pd.Series(np.geomspace(1.0, 1e6, 400))
    combined = combine_forecasts(
        close,
        speeds=((8, 32, 5.3), (16, 64, 3.75)),
        fdm=3.0,
        vol_span=32,
        cap=20.0,
    )
    assert combined.dropna().abs().max() <= 20.0 + 1e-9


def test_equal_weights_matches_none_path() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75), (32, 128, 2.65))
    none_path = combine_forecasts(close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0)
    eq_path = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(1.0, 1.0, 1.0)
    )
    pd.testing.assert_series_equal(none_path, eq_path, check_names=False)


def test_weighted_mean_matches_manual_after_warmup() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    combined = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(3.0, 1.0)
    )
    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    f2 = scaled_forecast(close, 16, 64, 3.75, 32, 20.0)
    both = f1.notna() & f2.notna()
    expected = ((f1[both] * 0.75 + f2[both] * 0.25) * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(combined[both], expected, check_names=False)


def test_weighted_zero_weight_drops_a_speed() -> None:
    close = pd.Series(np.linspace(100.0, 130.0, 400))
    speeds = ((8, 32, 5.3), (16, 64, 3.75))
    # weight 0 on the slow speed -> equals the fast speed alone x FDM, capped
    combined = combine_forecasts(
        close, speeds=speeds, fdm=1.25, vol_span=32, cap=20.0, weights=(1.0, 0.0)
    )
    f1 = scaled_forecast(close, 8, 32, 5.3, 32, 20.0)
    expected = (f1 * 1.25).clip(-20.0, 20.0)
    pd.testing.assert_series_equal(
        combined[f1.notna()], expected[f1.notna()], check_names=False
    )
