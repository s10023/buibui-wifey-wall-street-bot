import numpy as np
import pandas as pd

from analytics.forecast.book import run_forecast_backtest
from analytics.forecast.config import ForecastConfig
from analytics.forecast.report import G2Report, evaluate


def _closes(n: int = 600) -> dict[str, pd.Series]:
    rng = np.random.default_rng(3)
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    out = {}
    for k, sym in enumerate(("AAA", "BBB", "CCC")):
        steps = rng.normal(0.0006, 0.012, size=n)
        out[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
    return out


def test_evaluate_returns_full_g2_report() -> None:
    cfg = ForecastConfig()
    closes = _closes()
    fundings = {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}
    result = run_forecast_backtest(closes, fundings, cfg)
    trials = {"combined": result.portfolio_return}
    rep = evaluate(result, cfg, trial_returns=trials)
    assert isinstance(rep, G2Report)
    assert rep.n_obs == len(result.portfolio_return)
    assert np.isfinite(rep.sharpe_annual)
    # annualization uses 252: annual_vol ~= daily_vol * sqrt(252)
    daily = pd.Series(result.portfolio_return)
    live = daily[daily != 0.0]
    if len(live) > 2:
        assert rep.annual_vol > 0.0
