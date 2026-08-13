"""Pure routing decision + level sign-check for ingested posts/videos (no I/O).

``route_target`` is the content_type gate, then the parent pipeline's 4-bucket verdict
taxonomy on the claim path. See docs/superpowers/specs/2026-06-30-x-post-ingest-design.md.

This module also owns the **level sign-check** shared by ``/ingest-x`` and
``/ingest-video`` step 8 (write path) and by ``tools/pundit_score.py`` (read path): a
call's levels must be ordered ``stop < entry < target`` for a long and
``target < entry < stop`` for a short. It lives in code rather than skill prose for the
same reason call-time resolution moved into ``tools/video_calltime.py`` and the pass-1
cutoff into ``tools/video_marks.keep_items`` — a rule stated only in a prompt drifts, and
a mis-encoded row is indistinguishable from a real call once it is in the ledger.

The rule warns; it never drops or rewrites a row. The failure mode being fixed is
*silence*, and a guard that quietly discards rows reproduces it in the other direction.

CLI (advisory; exit 1 iff any row warned, nothing is ever mutated)::

    PYTHONPATH=. poetry run python tools/x_route.py --check-levels [FILE]
    PYTHONPATH=. poetry run python tools/x_route.py --check-levels < candidate-rows.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

#: Claim verdicts that route nowhere. Also the single definition of the pipeline's own
#: vocabulary, which ``tools/route_dedup.py`` excludes from term matching — a verdict is
#: stamped on every routed entry, so it says nothing about what any one of them claims.
DROP_VERDICTS = frozenset({"ALREADY-TESTED", "FROZEN-CATEGORY", "NOT-FALSIFIABLE"})
VERDICTS = DROP_VERDICTS | {"NOVEL"}


def route_target(
    content_type: str,
    verdict: str,
    *,
    retrospective: bool = False,
    rejected: bool = False,
) -> str | None:
    """The sink for one ingested item, or None to drop it.

    Both suppressors apply to `setup` only — a mechanic or a claim has no entry to
    decline, and both skills already pin the flags to false there, so honouring them
    outside `setup` would let one mis-set field silently delete a routable item.

    They default to False so every existing caller keeps its behaviour; a source that
    cannot express the distinction (an X post today) simply never sets them.

    - `retrospective` — the speaker is reviewing a position entered BEFORE this item.
      Scoring it forward from this timestamp flatters the author, because part of the
      outcome is already known.
    - `rejected` — the speaker walked through the trade and then argued AGAINST taking
      it. Routing it scores the author on a trade they declined. This shipped once
      upstream (2026-07-31 round 3) and was caught only by the human reading the digest.

    Ported from parent #521 (2026-08-01), which landed **one day after** wifey ported
    this file in #123 — so the gap was a timing artifact, not an equity divergence.
    wifey's #130 then cited #521 in its own title while taking only its `route_dedup`
    half, which is why the miss survived a re-check. `unattributable` is the parent's
    THIRD suppressor and is deliberately absent: it belongs to the relay-attribution
    work (#558), which this fork has refused.
    """
    if content_type == "setup":
        if retrospective or rejected:
            return None
        return "docs/plans/pundit-calls.jsonl"
    if content_type == "mechanic":
        return "docs/plans/mechanics-backlog.md"
    if content_type == "claim":
        if verdict == "NOVEL":
            return "docs/plans/thesis-inbox.md"
        if verdict in DROP_VERDICTS:
            return None
    raise ValueError(f"unroutable: content_type={content_type!r} verdict={verdict!r}")


_MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
#: Month-anchored years, stripped before level extraction. US index and large-cap levels
#: sit in the same 1,900-2,100+ band as a year string, so no ref-relative sanity gate can
#: separate them: the ledger's first real scoring run read "10-20% drawdown ... starting
#: Aug-Sep 2026" as a 2,026 target on a 7,436 index. Only a *month-anchored* year is
#: removed — a bare "2026" with no date cue is indistinguishable from a real level.
#: Single definition, shared with ``tools/pundit_score.py`` (its equity divergence 7).
MONTH_YEAR_RE = re.compile(
    rf"\b(?:{_MONTHS})[a-z]*\.?\s*(?:[-–—/]\s*(?:{_MONTHS})[a-z]*\.?\s*)?(?:19|20)\d{{2}}\b",
    re.IGNORECASE,
)

#: Percentages are magnitudes, not levels ("10-20% drawdown" must not read as a 10 entry).
#: The scorer suppresses these with its ref-relative sanity gate, which is unavailable at
#: write time, so they are stripped outright here. The optional range prefix matters: the
#: ``%`` binds to the *second* number, so matching only "20%" would leave a bare "10"
#: behind and read it as the level.
#: Single definition, shared with ``tools/route_dedup.py``'s ``normalize_levels`` — which
#: has no sanity gate either, and would otherwise read "up 150%" as a shared price level.
PCT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:[-–—]\s*\d[\d,]*(?:\.\d+)?\s*)?%")

#: Chart timeframes are not price levels ("break above the 4h descending trendline" must
#: not read as a 4 entry). Third artifact of the same class as ``MONTH_YEAR_RE`` and
#: ``PCT_RE``, and the one a TA-narrating pundit emits most often — a Chinese-language
#: channel names the frame on nearly every setup ("4小时级别的下降趋势线"). Found
#: 2026-08-05 when a gold long whose entry read "break above the 4h descending trendline"
#: sign-checked as ``stop 4000 on the wrong side of entry 4``.
#: That instance failed *loudly*, but the same artifact fails silently in the other
#: direction: a long with entry "the 4h trendline" and a stop of 3 computes 4 > 3 and
#: passes as OK, and a short whose target reads "the 1d level" yields 1 — below any real
#: entry — and likewise passes. A wrong-sided leg that passes is the fake-``WIN`` class
#: this guard exists to catch.
#: Integers only, so a decimal magnitude ("4.5m" = 4.5 million) is left alone; the
#: accepted cost is a bare integer magnitude written with a unit suffix ("5m" meaning
#: 5 million) being read as a 5-minute frame and dropped. Ledger level fields quote
#: prices plainly, so that shape has not appeared; timeframes appear constantly.
_TF_LATIN = (
    r"m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks"
)
_TF_CJK = r"分钟|分鐘|小时|小時|日线|日線|周线|週線|月线|月線|日|天|周|週"
#: The ``(?<![\d.])`` guard is what makes "integers only" true: without it ``\b`` matches
#: at the decimal point, so "4.5m" matches its own "5m" tail and leaves a bare "4." behind
#: — turning a magnitude into a level, the very failure being fixed.
TIMEFRAME_RE = re.compile(
    rf"(?<![\d.])\d{{1,4}}\s*(?:{_TF_LATIN})\b|(?<![\d.])\d{{1,4}}\s*(?:{_TF_CJK})",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?")

#: Ascending order the legs must appear in, per direction.
_LADDER: dict[str, tuple[str, str, str]] = {
    "long": ("stop", "entry", "target"),
    "short": ("target", "entry", "stop"),
}


def first_level(text: str | None) -> float | None:
    """First price-like number in a free-text level field, else ``None``.

    Deliberately cruder than ``pundit_score.parse_level_field``: at write time there is
    no reference close, so there is no sanity gate to lean on. Ledger rows state the
    level first and the context after ("Last week's high, roughly 1,038-1,040"), so the
    first surviving number is the right one to judge ordering by.

    Years, percentages and chart timeframes are stripped first — each is a number that
    reads as a level but is not one. See ``MONTH_YEAR_RE`` / ``PCT_RE`` / ``TIMEFRAME_RE``.
    """
    if text is None:
        return None
    cleaned = TIMEFRAME_RE.sub(
        " ",
        PCT_RE.sub(" ", MONTH_YEAR_RE.sub(" ", text.replace("$", "").replace("~", ""))),
    )
    match = _NUM_RE.search(cleaned)
    if match is None:
        return None
    return float(match.group(1).replace(",", "")) * (1000.0 if match.group(2) else 1.0)


def check_level_order(
    direction: str,
    *,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
) -> str:
    """Warning text when a call's levels are ordered impossibly, ``''`` when they are not.

    A long must satisfy ``stop < entry < target``, a short ``target < entry < stop``.
    Every *pair* whose legs are both present is judged independently, so a row stating
    only ``entry`` + ``target`` is still checked and a missing leg simply drops its
    pairs. Equality counts as a violation: a stop at the entry is zero risk and a target
    at the entry is zero reward, both of which produce a garbage R rather than a trade.

    Note the limit of the pairwise rule at write time: a row stating *one* level and
    nothing else cannot be judged, because there is no second leg to contradict it. That
    is the shape of the 2026-08-04 ``^NDX`` defect, and it is caught on the read side
    instead, where ``pundit_score`` substitutes the market price for a missing entry.
    """
    ladder = _LADDER.get(direction.strip().lower())
    if ladder is None:
        return f"direction {direction!r} is neither long nor short — level order unverifiable"
    levels = {"entry": entry, "stop": stop, "target": target}
    problems = [
        f"{lower} {levels[lower]:g} on the wrong side of {upper} {levels[upper]:g}"
        for i, lower in enumerate(ladder)
        for upper in ladder[i + 1 :]
        if levels[lower] is not None
        and levels[upper] is not None
        and not levels[lower] < levels[upper]  # type: ignore[operator]
    ]
    if not problems:
        return ""
    return f"{'; '.join(problems)} for a {direction.strip().lower()}"


def _row_level(row: Mapping[str, object], role: str) -> float | None:
    """Numeric leg for ``role``, ledger ``<role>_px`` overriding the free text."""
    px = row.get(f"{role}_px")
    if isinstance(px, (int, float)) and not isinstance(px, bool):
        return float(px)
    text = row.get(role)
    return first_level(text) if isinstance(text, str) else None


def check_row_levels(row: Mapping[str, object]) -> str:
    """``check_level_order`` over one ledger-shaped Stream C row."""
    return check_level_order(
        str(row.get("direction", "")),
        entry=_row_level(row, "entry"),
        stop=_row_level(row, "stop"),
        target=_row_level(row, "target"),
    )


def _check_levels_cli(source: str | None) -> int:
    lines = (
        Path(source).read_text(encoding="utf-8") if source else sys.stdin.read()
    ).splitlines()
    warned = 0
    for line_no, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError as exc:
            print(f"line {line_no}: WARN unparseable ({exc})")
            warned += 1
            continue
        if not isinstance(row, dict):
            print(f"line {line_no}: WARN not a JSON object")
            warned += 1
            continue
        label = f"{row.get('symbol', '?')} {row.get('direction', '?')}"
        note = check_row_levels(row)
        print(
            f"line {line_no}: {'WARN' if note else 'OK'} {label}{f' — {note}' if note else ''}"
        )
        warned += bool(note)
    print(f"\n{len(lines)} line(s) read · {warned} warning(s) · nothing was modified")
    return 1 if warned else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-levels",
        action="store_true",
        help="sign-check Stream C rows read as JSONL from FILE or stdin",
    )
    parser.add_argument("file", nargs="?", help="JSONL file (default: stdin)")
    args = parser.parse_args()
    if not args.check_levels:
        parser.error("nothing to do — pass --check-levels")
    return _check_levels_cli(args.file)


if __name__ == "__main__":
    raise SystemExit(main())
