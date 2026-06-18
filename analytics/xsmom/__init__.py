"""Cross-sectional momentum sleeve (P3) — demeaned EWMAC relative-strength book."""

from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)
from analytics.xsmom.diagnostics import (
    BetaAttribution,
    PersistenceReport,
    beta_attribution,
    equal_weight_market_return,
    subperiod_sharpe,
)
from analytics.xsmom.replay import replay_xs, replay_xs_trials
from analytics.xsmom.report import XSReport, evaluate_xs

__all__ = [
    "BetaAttribution",
    "PersistenceReport",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_xs",
    "replay_xs",
    "replay_xs_trials",
    "run_xs_backtest",
    "subperiod_sharpe",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
