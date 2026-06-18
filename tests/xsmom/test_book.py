from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_forecasts


def _closes() -> dict[str, pd.Series]:
    idx = pd.date_range("2021-01-01", periods=500, freq="D")
    # STRONG/WEAK are monotone ramps that saturate the EWMAC cap (+/-20).
    # FLAT is a seeded random walk with tiny positive drift so its forecast is
    # defined (non-NaN) and sub-cap, sitting between STRONG and WEAK.
    rng = np.random.default_rng(42)
    log_returns = rng.normal(0.0005, 0.01, 500)
    flat_rw = 200.0 * np.exp(np.cumsum(log_returns))
    return {
        "STRONG": pd.Series(np.linspace(100.0, 400.0, 500), index=idx),
        "FLAT": pd.Series(flat_rw, index=idx),
        "WEAK": pd.Series(np.linspace(400.0, 100.0, 500), index=idx),
    }


def test_xs_forecasts_aligned_to_union_index() -> None:
    closes = _closes()
    f = xs_forecasts(closes, ForecastConfig())
    assert list(f.columns) == ["STRONG", "FLAT", "WEAK"]
    assert len(f) == 500
    assert f.iloc[-1].notna().all()


def test_demeaned_forecast_rows_sum_to_zero_over_active() -> None:
    closes = _closes()
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    warm = g.dropna(how="any")
    assert len(warm) > 0
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    last = g.iloc[-1]
    assert last["STRONG"] > last["FLAT"] > last["WEAK"]


def test_demean_over_active_set_with_staggered_history() -> None:
    # Heterogeneous histories: the late instrument is absent/warming on early union
    # days. The demean over the *present* (warmed) instruments must still sum to ~0,
    # and the late instrument must stay NaN — not pulled into the mean as 0. This is
    # exactly what a `fillna(0.0)` on the forecast would have broken.
    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    g = xs_demeaned_forecasts(closes, ForecastConfig())
    # days where A & B are warmed but C is not yet defined (absent or still warming)
    early = g.loc[g["C"].isna() & g[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_sign_long_strong_short_weak() -> None:
    from analytics.xsmom.book import xs_leverage

    lev = xs_leverage(_closes(), ForecastConfig())
    last = lev.iloc[-1]
    # strong uptrend held long, weak downtrend held short
    assert last["STRONG"] > 0.0
    assert last["WEAK"] < 0.0


def test_xs_leverage_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    base = xs_leverage(closes, ForecastConfig())

    # Perturb a MIDDLE bar of ONE instrument. Leverage at index k is sized from
    # demeaned forecasts through k-1, so close[k] must not affect leverage[:k+1]
    # for ANY column (the cross-sectional demean couples instruments).
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, ForecastConfig())

    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )


def _fundings(closes: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {s: pd.Series(0.0, index=c.index) for s, c in closes.items()}


def test_run_xs_backtest_long_winner_short_loser_nets_positive() -> None:
    from analytics.xsmom.book import equity_curve, run_xs_backtest

    closes = _closes()
    res = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    assert res.portfolio_return.shape[0] == 500
    assert not np.isnan(res.portfolio_return).any()
    # long the strong / short the weak over a clean cross-section compounds up
    assert equity_curve(res).iloc[-1] > 1.0
    assert res.active_count.max() == 3


def test_run_xs_backtest_short_leg_receives_funding() -> None:
    from analytics.xsmom.book import run_xs_backtest

    closes = _closes()
    pos_fund = {s: pd.Series(0.001, index=c.index) for s, c in closes.items()}
    res = run_xs_backtest(closes, pos_fund, ForecastConfig())
    # WEAK is held short; positive funding on a short is a CREDIT -> net funding
    # cost on that leg is negative over the warmed-up tail.
    weak_net = res.per_instrument_net["WEAK"].dropna()
    no_fund = run_xs_backtest(closes, _fundings(closes), ForecastConfig())
    weak_net_nf = no_fund.per_instrument_net["WEAK"].dropna()
    # with positive funding the short leg nets HIGHER than with zero funding
    assert weak_net.sum() > weak_net_nf.sum()


def test_xs_leverage_dollar_neutral_rows_sum_to_zero() -> None:
    from analytics.xsmom.book import xs_leverage

    cfg = ForecastConfig(xs_dollar_neutral=True)
    lev = xs_leverage(_closes(), cfg)
    warm = lev.dropna(how="any")
    assert len(warm) > 0
    # Each active day's positions net to zero (truly dollar-neutral).
    np.testing.assert_allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_xs_leverage_dollar_neutral_changes_net_exposure() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    off = xs_leverage(closes, ForecastConfig())
    on = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    # Off path keeps a residual net exposure; the flag removes it.
    off_net = off.dropna(how="any").sum(axis=1).abs().max()
    on_net = on.dropna(how="any").sum(axis=1).abs().max()
    assert off_net > 1e-6
    assert on_net < 1e-9


def test_xs_leverage_dollar_neutral_active_set_with_staggered_history() -> None:
    from analytics.xsmom.book import xs_leverage

    idx_full = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_late = pd.date_range("2021-06-01", periods=400, freq="D")
    closes = {
        "A": pd.Series(np.linspace(100.0, 400.0, 500), index=idx_full),
        "B": pd.Series(np.linspace(400.0, 100.0, 500), index=idx_full),
        "C": pd.Series(np.linspace(100.0, 300.0, 400), index=idx_late),
    }
    lev = xs_leverage(closes, ForecastConfig(xs_dollar_neutral=True))
    early = lev.loc[lev["C"].isna() & lev[["A", "B"]].notna().all(axis=1)]
    assert len(early) > 0
    # Re-center over the active set only; the absent instrument stays NaN.
    np.testing.assert_allclose(early[["A", "B"]].sum(axis=1).to_numpy(), 0.0, atol=1e-9)
    assert early["C"].isna().all()


def test_xs_leverage_dollar_neutral_is_causal_no_lookahead() -> None:
    from analytics.xsmom.book import xs_leverage

    closes = _closes()
    cfg = ForecastConfig(xs_dollar_neutral=True)
    base = xs_leverage(closes, cfg)
    k = 250
    bumped = {s: c.copy() for s, c in closes.items()}
    bumped["STRONG"].iloc[k] *= 1.5
    after = xs_leverage(bumped, cfg)
    # Row k itself is included (`: k + 1`): leverage at k is sized from demeaned
    # forecasts through k-1 (the `.shift(1)`), so close[k] cannot affect it.
    pd.testing.assert_frame_equal(
        base.iloc[: k + 1], after.iloc[: k + 1], check_names=False
    )
