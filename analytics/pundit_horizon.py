"""Horizon enum for the pundit ledger — pure, no IO.

Ported from parent `buibui-moon-trader-bot` #561. The code is verbatim; this
docstring is not, because **the defect is materially worse here than upstream**
and copying the parent's account of it would understate it.

`horizon` is written by the ingest *skills* as free JSON — there is no Python
write path — so nothing ever asserted its domain, while the scorer reads it
through two separate `.get(…, default)` tables:

    LedgerCall.timeframe  ->  SCORE_TIMEFRAME.get(horizon, SCORE_TIMEFRAME["unspecified"])
    window_sessions()     ->  SESSION_WINDOWS.get(horizon, SESSION_WINDOWS["unspecified"])

The parent has only the second. Here an unrecognised horizon therefore buys
**two** wrong answers at once, neither of which raises:

- the wrong **bar timeframe** — a mistyped `intraday` silently scores on `1d`
  bars instead of `1h`, so the fill, the level crossings and the MFE/MAE are
  all read off the wrong series (equity divergence 1);
- the wrong **window** — 10 NYSE sessions instead of intraday's 2 or swing's
  21 (equity divergence 6). A scalp stopped out inside two sessions can be
  booked a WIN because price recovered by session eight.

Both fallbacks were docstringed as deliberate, which is the only thing that
separated them from a bug rather than a reason they were safe.

**Where this diverges from `direction`: absence is legitimate here.**
`unspecified` is a real member of the enum — a pundit who states no timeframe
has still made a scoreable call — so a missing or empty field normalises to
`unspecified` and is *not* an error. Only a value that is **present and
unrecognised** is a defect, because that is the only case where the writer
meant something the scorer cannot honour. Neither `.get` can tell those two
apart from the inside; by then the raw field is gone. That is precisely why
the check belongs at the read boundary, where the difference is still visible.

Not live at time of writing: all 19 rows in `docs/plans/pundit-calls.jsonl`
were in-enum (`swing` 19). This is prevention, and no published number was
wrong. It is worth noting the fork's ledger has never yet exercised the
`intraday` / `unspecified` paths at all, so the two fallbacks have never been
observed firing — absence of evidence, not evidence of absence.

Applied at the scorer's read boundary only. Unlike the parent this repo has no
`analytics/brief/` board, so there is no second reader to keep in step; if one
is ever added it must import from here rather than re-deriving the enum.
"""

from __future__ import annotations

VALID_HORIZONS = frozenset({"intraday", "swing", "unspecified"})
"""Exactly the keys of BOTH `pundit_score.SCORE_TIMEFRAME` and
`pundit_score.SESSION_WINDOWS`.

Bound to both tables by `tests/test_pundit_horizon.py` — the parent binds only
one, because upstream only has one. Adding a member here without an entry in
*each* table re-creates the original bug in a new place: the new horizon would
fall through to unspecified's `1d` bars and 10 sessions, silently.
"""

_ABSENT = "unspecified"


def normalize_horizon(raw: str | None) -> str:
    """Canonical horizon key, or raise ``ValueError``.

    ``None`` and blank normalise to ``"unspecified"`` — see the module
    docstring: absence is a member of the enum, not a violation of it. Case
    and surrounding whitespace are folded, matching ``normalize_direction``.
    Anything else raises, naming the field and the offending value so a
    caller that turns it into a per-line warning stays actionable.

    Idempotent, which is what lets it be applied at read time on both sides
    without a data migration.
    """
    if raw is None:
        return _ABSENT
    if not isinstance(raw, str):
        raise ValueError(f"horizon {raw!r} is not a string")
    value = raw.strip().lower()
    if not value:
        return _ABSENT
    if value not in VALID_HORIZONS:
        raise ValueError(f"horizon {raw!r} is not one of {sorted(VALID_HORIZONS)}")
    return value
