import numpy as np
import pandas as pd

from analytics.forecast import metrics


def _curve(daily_ret: float, n: int) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.Series((1.0 + daily_ret) ** np.arange(n), index=idx)


def test_sharpe_uses_252_by_default() -> None:
    # pure-drift curve has ~0 std -> degenerate guard returns 0.0
    assert metrics.sharpe(_curve(0.001, 500)) == 0.0


def test_sharpe_annualizes_with_252() -> None:
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0005, 0.01, size=1000)
    curve = pd.Series((1.0 + pd.Series(rets)).cumprod())
    r = curve.pct_change().dropna()
    expected = float(r.mean() / r.std(ddof=1) * np.sqrt(252.0))
    assert abs(metrics.sharpe(curve) - expected) < 1e-9


def test_flat_curve_is_zero_not_nan() -> None:
    flat = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert metrics.sharpe(flat) == 0.0
    assert metrics.max_drawdown(flat) == 0.0
    assert metrics.calmar(flat) == 0.0
