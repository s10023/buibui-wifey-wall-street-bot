"""Where a resting order actually fills when the bar gaps past its level.

A stop or a limit resting at `level` does not fill at `level` when the bar opens
already beyond it — it fills at the open. Booking the level regardless is free
money on the loss side and a haircut on the win side, and until 2026-08-20 both
books did exactly that (`engine.py`'s `exit_price = sl_price` / `= tp_price`, and
the live resolver's flat `-1.0` / `implied_tp_r`).

**Symmetric by construction, and that is the whole point.** The 2026-08-19 audit
measured only the adverse tail and the fix was very nearly shipped one-sided.
Pricing the adverse gap alone does not remove a bias, it replaces one with its
mirror: on the live ledger 21.1% of losses gap through their stop, but **26.1% of
wins gap through their target** — the higher rate of the two, since a favourable
gap is exactly what carries price past a distant level. One-sided, the correction
overstates the real bias by ~65%.

Kept as a separate pure module for the same reason `cost_model.py` is: the live
resolver imports it without pulling in the engine, so the two books cannot drift
apart on the one rule they must share.
"""

from __future__ import annotations

__all__ = ["gap_fill_price", "level_is_on_the_expected_side", "triggers_downward"]


def triggers_downward(*, direction: str, side: str) -> bool:
    """True when the resting order is reached by a FALLING price.

    A long's stop and a short's target both sit below entry and trigger on the way
    down; a long's target and a short's stop sit above and trigger on the way up.
    That is the only thing the gap test needs to know, and expressing it once keeps
    the four cases from being written out (and mistyped) at every call site.
    """
    return (direction == "long") == (side == "stop")


def gap_fill_price(
    *,
    level: float,
    bar_open: float,
    direction: str,
    side: str,
) -> float:
    """The price an order resting at `level` fills at on a bar that reached it.

    Returns `bar_open` when the bar opened at or beyond `level` (the order was
    already through the money at the print), else `level` itself.

    The caller has already established that this bar reaches the level — this
    decides only *where*, never *whether*. Passing a bar that never touched the
    level returns `level`, which is the caller's bug, not a fill.

    `side` is `"stop"` or `"target"`; `direction` is `"long"` or `"short"`.
    """
    if triggers_downward(direction=direction, side=side):
        gapped = bar_open <= level
    else:
        gapped = bar_open >= level
    return bar_open if gapped else level


def level_is_on_the_expected_side(
    *,
    level: float,
    entry: float,
    direction: str,
    side: str,
) -> bool:
    """True when `level` sits where its side belongs relative to `entry`.

    A long's stop is below entry and its target above; a short's are mirrored.
    A level on the wrong side is not a level — it is a malformed row, and pricing
    a gap against one turns a loss into a positive R (or a win into a negative
    one) with nothing to flag it.

    This is the SINGLE definition of that test. `implied_tp_r` used to spell it
    `implied > 0.0` inline; both now route here, because the gap-fill override
    has to gate on exactly the condition the credit already gated on, and two
    spellings of one rule is how the declared/effective split diverged before.
    """
    if triggers_downward(direction=direction, side=side):
        return level < entry
    return level > entry
