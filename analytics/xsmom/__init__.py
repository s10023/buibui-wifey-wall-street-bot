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
from analytics.xsmom.replay import (
    replay_residual_grid,
    replay_xs,
    replay_xs_trials,
)
from analytics.xsmom.report import (
    ResidualGridReport,
    XSReport,
    evaluate_residual_grid,
    evaluate_xs,
)
from analytics.xsmom.residual import (
    residual_closes,
    rolling_beta,
    xs_residual_leverage,
)

__all__ = [
    "BetaAttribution",
    "PersistenceReport",
    "ResidualGridReport",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_residual_grid",
    "evaluate_xs",
    "replay_residual_grid",
    "replay_xs",
    "replay_xs_trials",
    "residual_closes",
    "rolling_beta",
    "run_xs_backtest",
    "subperiod_sharpe",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
    "xs_residual_leverage",
]
