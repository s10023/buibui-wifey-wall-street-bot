"""Direction enum for the pundit ledger — pure, no IO.

Ported from parent `buibui-moon-trader-bot` #560. The code is verbatim; this
docstring is not, because **the bug that motivated it upstream does not exist
here**, and repeating the parent's account would be a false incident report.

Upstream, `score_call` special-cased only the literal `"neutral"` and then ran
`dirsign = 1.0 if direction == "long" else -1.0`, so every value outside the
enum — and every missing one — was booked as a **SHORT** with nothing raising.
It fired for real: `/ingest-video` emitted `direction: "range"` for a
range-trade plan, which scored as bearish.

This fork's `score_call` is shaped differently and is already immune::

    if call.direction not in ("long", "short"):
        return ScoredCall(call, None, family, STATE_UNSCORED,
                          note=f"direction '{call.direction}'")

`dirsign` is unreachable for anything outside the enum, and the offending value
is echoed into the row's note. A `"range"` row here becomes a visible UNSCORED
line, not a silent short. That divergence predates the port and is the reason
this module is **parity and prevention, not a fix**.

What it still buys, honestly stated and no more:

- **One definition instead of two.** `VALID_DIRECTIONS` is the set `score_call`
  branches on. Today that branch spells the enum inline as a tuple literal, so
  the two can drift; the test binds them.
- **Rejection at the read boundary, where the line number is still in scope.**
  An out-of-enum row currently survives `load_ledger` and surfaces later as one
  UNSCORED row among others. Rejected here it becomes a per-line load warning
  naming the line and the value — the same treatment every other malformed
  field already gets, and actionable without re-reading the ledger by eye.
- **A forward-port path that stays 1:1** with the parent, which is the standing
  reason this repo mirrors upstream module boundaries rather than merging them.

A **missing** direction is rejected on the same grounds as an unknown one: it
arrives as `""`, which is likewise not `"long"` or `"short"`. Upstream that
scored as a short; here it was already UNSCORED, so again this is a promotion
from late-and-quiet to early-and-loud.

Kept deliberately strict rather than coercing to a default: there is no safe
default for a direction. Guessing `neutral` would silently discard a real call,
and guessing `long`/`short` invents one.
"""

from __future__ import annotations

VALID_DIRECTIONS = frozenset({"long", "short", "neutral"})
"""The exact set ``pundit_score.score_call`` branches on.

``"neutral"`` returns UNSCORED; ``"long"`` and ``"short"`` set ``dirsign``.
If this set ever grows, that branch must be revisited in the same commit —
adding a member here without touching it re-creates the parent's bug in a
fork that does not currently have it.
"""


def normalize_direction(raw: str) -> str:
    """Canonical direction key, or raise ``ValueError``.

    Case and surrounding whitespace are decoration, not identity, so they are
    folded rather than rejected — the scorer already lowercased on read and
    dropping ``"SHORT"`` would be a regression. Anything else raises, and the
    message names both the field and the offending value so a caller that
    turns it into a per-line warning stays actionable.

    Idempotent, which is what lets it be applied at read time on both sides
    without a data migration.
    """
    value = raw.strip().lower()
    if value not in VALID_DIRECTIONS:
        raise ValueError(f"direction {raw!r} is not one of {sorted(VALID_DIRECTIONS)}")
    return value
