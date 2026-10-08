"""Risk overlays on the market premium, judged on the overlay yardstick (OV-1, #418).

An overlay never claims an edge: it is judged against buy-and-hold on survival
(ulcer index) with Sharpe non-inferiority, per ``docs/north-star.md`` § Two
yardsticks, not on ``GATE_SHARPE``.
"""

from analytics.overlay.frame import PANEL_START, build_frame, load_gspc_close
from analytics.overlay.replay import arm_returns
from analytics.overlay.report import (
    ArmSummary,
    BetaAttribution,
    OverlayGate,
    beta_attribution,
    evaluate_overlay,
    overlay_verdict,
    summarize_arm,
)
from analytics.overlay.rules import MA_WINDOW, ma_signal, position_on

__all__ = [
    "MA_WINDOW",
    "PANEL_START",
    "ArmSummary",
    "BetaAttribution",
    "OverlayGate",
    "arm_returns",
    "beta_attribution",
    "build_frame",
    "evaluate_overlay",
    "load_gspc_close",
    "ma_signal",
    "overlay_verdict",
    "position_on",
    "summarize_arm",
]
