"""Routing dedup for the ingest sinks (`/ingest-x`, `/ingest-video`).

The fetch layer dedups *fetches*; nothing dedups *routing*. Two duplicate classes
hide behind that gap, and they need different machinery:

1. identity — the same post/video routed twice across sessions. Deterministic, so
   a ledger blocks it outright.
2. semantic — the same thesis restated by a different author. Not detectable by id,
   so this module only ever *surfaces candidates* for the review digest. It never
   drops anything: a false positive costs a glance, a false negative costs a
   corrupted sink, and the operator's review gate stays the decision point.

Ported from the parent's ``tools/route_dedup.py`` (#518 + #521). **Advisory, never a
gate** — the identity layer is the only exact call in here, and even that one is
reported to the digest rather than enforced by this module.

Seven divergences from the parent, each forced by this repo's sinks and instruments,
and each found by running the ported code against the live sinks rather than reading it.
Four of the seven (1, 3, 6, 7) exist because wifey's Stream A sink is a **markdown
table** where the parent's is prose — that one structural difference breaks the entry
splitter, injects the deep-link into the text, dilutes symmetric similarity with a long
`gap note` column, and stamps a verdict on every row:

1. **Stream A splits on markdown table rows, not ``## `` headings.** The parent's
   thesis inbox is a run of level-two sections; wifey's is a *table* of ``H-`` rows
   (plus a second "Dropped this batch" table). The parent's splitter finds one
   heading in the whole file, so every claim would be scored against one giant blob,
   jaccard would collapse toward zero, and the pass would silently never fire — while
   ``semantic_scope`` still reported ``all-entries``. That is exactly the
   "checked and clean" illusion the scope field exists to prevent. Dropped-table rows
   are compared too: "you already dropped this as FROZEN-CATEGORY" is the more useful
   hit of the two.
2. **``normalize_levels`` strips month-anchored years and percentages**, reusing
   ``x_route``'s single definitions. US index and large-cap levels sit in the same
   1,900-2,100+ band as a year string, so the ``_MIN_LEVEL`` floor cannot separate
   them — "starting Aug-Sep 2026" would hand a 2,026 "level" to every entry that
   mentions a date. This is the defect PR #128 found in the scorer; ``route_dedup`` is
   its third consumer. Crypto never hit it because 69k sits far above the year band.
3. **URLs are stripped before *both* scoring layers.** Wifey's Stream A rows embed the
   source deep-link in a cell, and a YouTube ``&t=124s`` offset reads as a price level
   while ``https``/``youtu`` read as shared terms in every single row. The parent's
   prose headings carry no link, so it never needed this.
4. **Bare 4-digit years are not levels** (``_YEAR_BAND``). Divergence 2 only reaches a
   year sitting next to a month name, and prose does not oblige. Measured on the live
   inbox before this rule: **3 of 28 Stream A pairs flagged, and all three were pure
   year artifacts** — 2018/2022 cited as historical evidence by unrelated entries.
   That is not a false-positive rate to tolerate, it is the whole signal. Widening
   ``MONTH_YEAR_RE`` into a proximity match was the alternative and is worse: it would
   start eating real levels in the shared definition all three consumers read. The
   cost is stated and accepted — an index level written as a bare ``2050`` is lost,
   which is affordable while ^GSPC trades near 7,400 and almost no US single-name sits
   in the band.
5. **The ledger key truncates ``item_ts`` to whole seconds** (``_ts_key``), where the
   parent rounds to one decimal. An item offset reaches this module from two places
   that disagree: the row's own float ``ts`` (``566.81``) and the deep link the same
   row persists (``&t=566s``, *truncated*). Rounding cannot reconcile them —
   ``round(566.81)`` is ``567`` — so a ledger seeded from the sink would silently fail
   to block a re-route checked against the URL. Caught on the live ledger; sub-second
   precision buys nothing, since no two items in one video sit a second apart.
6. **Term overlap is containment, not jaccard** (``_overlap``). A wifey Stream A entry
   is a whole table row including a ``gap note`` column; the claim being routed is one
   sentence. Jaccard penalizes the entry for that length, so a near-verbatim
   restatement of the live H-002 row scored **1.86 and did not fire** despite sharing
   8 of the claim's 9 terms. Containment over the shorter side reads 0.89 on the same
   pair. ``_MIN_DENOM`` floors the denominator so a two-word claim cannot game it.
7. **The pipeline's own verdict vocabulary is excluded from term matching**
   (``_STOPWORDS``, derived from ``x_route.VERDICTS``). A verdict is stamped on every
   Stream A entry, so ``already``/``tested`` off ``ALREADY-TESTED`` matched unrelated
   rows before one word of claim overlapped — it carried *all three* remaining flags on
   the live inbox. Structurally the same defect ``_PUNDIT_CONTENT_FIELDS`` fixes for
   Stream C, in the shape a table takes.

Calibration after all seven, on the live sinks: **0 of 28 Stream A pairs and 0 of 7
same-source Stream C pairs flagged** (highest non-flagging score 2.5 against a 3.0
threshold), while a near-verbatim restatement of the live H-002 and H-003 rows scores
8.9 and 8.8 against the correct row. Discriminating, not inert — and the inert failure
is the one to watch for, since it looks identical to "checked and clean".

Two inherited limits are documented rather than "fixed", because both fixes are worse
than the disease:

- **Sub-$100 instruments contribute no numeric evidence** (``_MIN_LEVEL``). Rare in
  crypto, common here — SLV, TLT and a large slice of the S&P 500 trade below it, and
  those rows fall back to term overlap alone. Not lowered: below ~100 the band fills
  with bar counts (14, 20, 50, 200-day), fib ratios and position multiples, and an
  advisory tool that cries wolf stops being read.
- **Stream C's semantic pass is scoped to one ``source_id``**, so one author restating
  a call across uploads is invisible. That is the *common* case here — wifey's main
  Stream C source is a weekly channel. Deliberately not widened to same-author: two
  calls a week apart resolve from two different ``call_ts_utc`` against different bars
  and are two genuine observations that ``tools/pundit_score.py`` scores separately.
  Collapsing them would delete data, which is the opposite of this module's job.

Spec (parent): docs/superpowers/specs/2026-07-31-b2-routing-dedup-design.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# A bare `python3 tools/route_dedup.py` puts `tools/` on sys.path rather than the repo
# root, so the `tools.x_route` import below dies with ModuleNotFoundError; only the Make
# target and an explicit `PYTHONPATH=.` worked. ⚠ The parent's copy hits the same class
# on `analytics.*` — the RULE ports, the failing module name does not, because wifey's
# copy imports from `tools.`. The guarantee is `test_bare_invocation_works`, never this
# line, and the bootstrap is scoped to tools that actually import from the repo: in one
# that does not it is dead code, masking the breakage the moment the first import lands.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.x_route import MONTH_YEAR_RE, PCT_RE, VERDICTS  # noqa: E402

# The three routing targets, byte-identical to what `tools/x_route.route_target`
# returns — a drift guard in the test suite pins them together.
THESIS_SINK = "docs/plans/thesis-inbox.md"
MECHANICS_SINK = "docs/plans/mechanics-backlog.md"
PUNDIT_SINK = "docs/plans/pundit-calls.jsonl"

# The CLI's allowlist. `--sink` names a routing IDENTITY and `is_routed` keys on
# `(source_id, item_ts, sink)` via `_key`, so a value outside this tuple is dedup-BLIND
# rather than merely odd — it can never match the row a later round looks for.
# `find_similar` stays lenient on an unrecognised sink (unscopeable, not an error)
# because scoping genuinely cannot apply there, so the membership check belongs at the
# CLI boundary and nowhere else. ⚠ Ported from parent #706 as PREVENTION, not a repair:
# wifey's ledger carried 0 bad rows of 54 when this landed, where upstream had 30
# writable — do not quote that count as this repo's.
KNOWN_SINKS = (THESIS_SINK, MECHANICS_SINK, PUNDIT_SINK)

# Sinks where a near-duplicate is a defect between ANY two entries. Stream C is absent
# on purpose — there it is a defect only within one source. See `find_similar`.
SEMANTIC_SINKS = frozenset({THESIS_SINK, MECHANICS_SINK})

# The Stream C fields that carry what the call actually SAYS. Scoring the raw JSONL
# line instead is what made this sink look uniformly self-similar in the parent's
# original calibration pass: `source`, `author`, `symbol`, `direction`, `horizon`,
# `confidence` … are themselves terms, so any two rows share ~15 of them before one
# word of content matches. Reading only the values restores the signal.
_PUNDIT_CONTENT_FIELDS = (
    "symbol",
    "direction",
    "entry",
    "stop",
    "target",
    "raw_quote",
    "raw_quote_en",
)

DEFAULT_LEDGER = Path("docs/plans/routed-ledger.json")

_LEDGER_VERSION = 1


# item_ts is matched at whole-second precision so transcript float noise cannot split
# one item into two ledger rows. Truncated, not rounded, and that is divergence 5 —
# see the module docstring.
def _ts_key(item_ts: float) -> int:
    return int(item_ts)


# Below this, a number is a fib ratio, a bar count, a timeframe or a multiplier —
# not a price level. See the module docstring for why this floor costs more here
# than it did in the parent, and why it is still the right floor.
_MIN_LEVEL = 100.0

_LEVEL_WEIGHT = 3.0
_TERM_WEIGHT = 10.0

# Floor under the containment denominator, so a two-word claim cannot score 1.0
# against any entry that happens to use both words. See `_overlap`.
_MIN_DENOM = 8

# One shared price level clears this on its own. Deliberately loose: the output is
# advisory, and there is no labelled corpus of equity near-duplicates to fit against —
# a threshold claimed as "tuned" would be fit on the handful of rows in the ledger.
_MIN_SCORE = 3.0

_EXCERPT_CHARS = 240

# Ordered: the X status form is checked before the YouTube id forms.
_SOURCE_ID_PATTERNS = (
    re.compile(r"/status/(\d+)"),
    re.compile(r"[?&]v=([\w-]{11})"),
    re.compile(r"youtu\.be/([\w-]{11})"),
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"[~$]?(\d[\d,]*(?:\.\d+)?)\s*([kK])?")
_WORD = re.compile(r"[a-z]{4,}")

# A URL is an identifier, not content: its digits are ids and `&t=124s` offsets, its
# words are domain noise shared by every entry. Stripped before BOTH scoring layers.
_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# A *bare* 4-digit integer in this band is a year, not a price. `MONTH_YEAR_RE` only
# reaches years adjacent to a month name, and research prose does not oblige: the live
# inbox says "2022 topped mid-Aug (-19%), 2018 topped Sep", which puts two words
# between the two. See the module docstring for the measured impact and the cost.
_YEAR_BAND = (1900.0, 2099.0)

# A markdown table separator (`| --- | --- |`), and by extension the header row above
# it — neither is an entry.
_TABLE_SEP = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")

_STOPWORDS = frozenset(
    {
        "also",
        "been",
        "from",
        "have",
        "into",
        "here",
        "just",
        "more",
        "much",
        "only",
        "over",
        "same",
        "such",
        "than",
        "that",
        "them",
        "then",
        "they",
        "this",
        "very",
        "were",
        "what",
        "when",
        "which",
        "will",
        "with",
        "your",
    }
)

# Divergence 7: the pipeline's own vocabulary is not content. Wifey's Stream A sink is a
# table whose `verdict` and `status` columns hold a tiny closed set stamped on EVERY
# entry, so two unrelated rows match on it before a word of claim overlaps — the same
# defect `_PUNDIT_CONTENT_FIELDS` fixes for Stream C, in the shape a table takes.
# Measured on the live inbox: 3 of 3 remaining flags were carried by `already`+`tested`
# off `ALREADY-TESTED`. Derived from `x_route.VERDICTS` so the two cannot drift.
_STOPWORDS |= frozenset(
    w for verdict in VERDICTS for w in _WORD.findall(verdict.lower())
)


@dataclass(frozen=True)
class RoutedItem:
    source_id: str
    item_ts: float
    sink: str
    routed_ts_utc: str


@dataclass(frozen=True)
class DedupCandidate:
    sink: str
    excerpt: str
    score: float
    shared_levels: tuple[float, ...]
    shared_terms: tuple[str, ...]


@dataclass(frozen=True)
class DuplicatePair:
    """Two items from ONE source that look like the same call described twice."""

    left_ts: float
    right_ts: float
    score: float
    shared_levels: tuple[float, ...]
    shared_terms: tuple[str, ...]
    left_excerpt: str
    right_excerpt: str


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _key(source_id: str, item_ts: float, sink: str) -> tuple[str, int, str]:
    """Never `source_id` alone — one video legitimately yields several items (the
    first 美股峰哥 ingest produced four calls from one upload), and collapsing them
    would delete exactly the rows this module exists to protect. `sink` is in the key
    so one moment can yield both a claim and a setup.
    """
    return (source_id, _ts_key(item_ts), sink)


def load_ledger(path: Path) -> list[RoutedItem]:
    if not path.exists():
        return []
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"malformed routing ledger {path}: {exc} — refusing to silently reset "
            "(that would re-open every item ever routed); fix or move the file"
        ) from exc
    if not isinstance(raw, dict) or raw.get("version") != _LEDGER_VERSION:
        raise SystemExit(
            f"unrecognized routing-ledger shape/version in {path} — refusing to reset"
        )
    items = raw.get("items")
    if not isinstance(items, list):
        raise SystemExit(f"routing ledger {path} has no items list — refusing to reset")
    return [
        RoutedItem(
            source_id=str(row["source_id"]),
            item_ts=float(row["item_ts"]),
            sink=str(row["sink"]),
            routed_ts_utc=str(row["routed_ts_utc"]),
        )
        for row in items
    ]


def _write_ledger(path: Path, items: list[RoutedItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _LEDGER_VERSION,
        "items": [
            {
                "source_id": i.source_id,
                "item_ts": i.item_ts,
                "sink": i.sink,
                "routed_ts_utc": i.routed_ts_utc,
            }
            for i in items
        ],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def is_routed(
    ledger: list[RoutedItem], source_id: str, item_ts: float, sink: str
) -> bool:
    wanted = _key(source_id, item_ts, sink)
    return any(_key(i.source_id, i.item_ts, i.sink) == wanted for i in ledger)


def append_routed(path: Path, items: list[RoutedItem]) -> None:
    """Idempotent. Call this AFTER the sink write succeeds — never at check time.

    Marking before the write lets a dry run or an abandoned review consume an id and
    dedup away the real append later; that is the #68 watermark-on-send defect class.
    """
    existing = load_ledger(path)
    seen = {_key(i.source_id, i.item_ts, i.sink) for i in existing}
    for item in items:
        key = _key(item.source_id, item.item_ts, item.sink)
        if key not in seen:
            seen.add(key)
            existing.append(item)
    _write_ledger(path, existing)


def remove_routed(path: Path, *, source_id: str, item_ts: float, sink: str) -> None:
    """The escape hatch: you deleted a bad row and want the item re-routable."""
    wanted = _key(source_id, item_ts, sink)
    kept = [
        i for i in load_ledger(path) if _key(i.source_id, i.item_ts, i.sink) != wanted
    ]
    _write_ledger(path, kept)


# ---------------------------------------------------------------------------
# Seeding (pure)
# ---------------------------------------------------------------------------


def parse_source_id(url: str) -> str | None:
    """The ingest source id inside a persisted sink URL, or None if there isn't one.

    `None` is the right answer for anything that is not ingested content — the system
    quoting itself, a hand-written row, an empty field. Those must never enter the
    routing ledger, because nothing will ever re-route them.
    """
    for pattern in _SOURCE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def seed_items(
    rows: list[dict[str, Any]], *, sink: str, fallback_ts_utc: str
) -> list[RoutedItem]:
    """Ledger rows for content already routed into `sink`, from the sink's own records.

    Without this the identity layer would ship blind to every item routed before it
    existed — the cached-post-re-routed case this module was written to stop. Only
    Stream C can be seeded; Streams A and B persist no source id, which is why the
    ledger is a side file in the first place.

    `item_ts` comes from the row's own `ts` so a seeded key matches byte-for-byte
    what `mark` would have written at routing time.
    """
    items: list[RoutedItem] = []
    for row in rows:
        source_id = parse_source_id(str(row.get("url") or ""))
        if source_id is None:
            continue
        items.append(
            RoutedItem(
                source_id=source_id,
                item_ts=float(row.get("ts") or 0.0),
                sink=sink,
                routed_ts_utc=str(
                    row.get("ingested_ts_utc")
                    or row.get("call_ts_utc")
                    or fallback_ts_utc
                ),
            )
        )
    return items


# ---------------------------------------------------------------------------
# Similarity (pure)
# ---------------------------------------------------------------------------


def _strip_noise(text: str) -> str:
    """Drop URLs — see the module docstring's divergence 3."""
    return _URL.sub(" ", text)


def normalize_levels(text: str) -> frozenset[float]:
    """Price levels mentioned in `text`, normalized to floats.

    Handles `28k`, `1,982.10`, `~7436`, `$60,946.8`. Five exclusions are load-bearing,
    and the last three are this fork's: URLs go first (a `&t=124s` deep-link offset is
    not a level), then ISO dates (otherwise every dated entry shares "levels" with
    every other), then month-anchored years and percentages, then any *bare* 4-digit
    integer left in `_YEAR_BAND`. Anything below `_MIN_LEVEL` is dropped so fib ratios
    and bar counts do not make unrelated entries look alike.

    The bare-year rule leans on how the two are written rather than on how big they
    are, because in equities size cannot separate them: a level in that band carries a
    separator or a decimal (`1,982.10`, `2,050.5`) and a year never does.
    """
    stripped = PCT_RE.sub(
        " ", MONTH_YEAR_RE.sub(" ", _ISO_DATE.sub(" ", _strip_noise(text)))
    )
    levels: set[float] = set()
    for digits, suffix in _NUMBER.findall(stripped):
        try:
            value = float(digits.replace(",", ""))
        except ValueError:
            continue
        if suffix:
            value *= 1000.0
        elif (
            _YEAR_BAND[0] <= value <= _YEAR_BAND[1]
            and "," not in digits
            and "." not in digits
        ):
            continue
        if value >= _MIN_LEVEL:
            levels.add(value)
    return frozenset(levels)


def _terms(text: str) -> frozenset[str]:
    return frozenset(
        w for w in _WORD.findall(_strip_noise(text).lower()) if w not in _STOPWORDS
    )


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Containment over the shorter side, not the parent's jaccard — divergence 6.

    "Does this sink already say what my claim says" is a containment question, and
    jaccard answers a symmetric one: it penalizes the existing entry for being long.
    That is fatal here, because a wifey Stream A entry is a whole table row carrying a
    `gap note` column while the claim being routed is one sentence. Measured on the
    live inbox: a near-verbatim restatement of H-002 shares 8 of its 9 terms — 0.89
    containment, but only 0.19 jaccard, which scores 1.86 and never fires.

    `_MIN_DENOM` floors the denominator so a two-word claim cannot reach containment
    1.0 against any entry that happens to use both words.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / max(min(len(a), len(b)), _MIN_DENOM)


def _score(a: str, b: str) -> tuple[float, frozenset[float], frozenset[str]]:
    """Shared by both entry points so a claim-vs-entry and an item-vs-item comparison
    can never drift onto different scales."""
    a_terms, b_terms = _terms(a), _terms(b)
    shared_levels = normalize_levels(a) & normalize_levels(b)
    score = _LEVEL_WEIGHT * len(shared_levels) + _TERM_WEIGHT * _overlap(
        a_terms, b_terms
    )
    return score, shared_levels, a_terms & b_terms


def pundit_content_text(row: dict[str, Any]) -> str:
    """The comparable content of one Stream C row — values only, never the keys."""
    return " ".join(str(row.get(f) or "") for f in _PUNDIT_CONTENT_FIELDS)


def _parse_jsonl_entry(entry: str) -> dict[str, Any] | None:
    try:
        row = json.loads(entry)
    except json.JSONDecodeError:
        return None  # a hand-edited line should not abort a check
    return row if isinstance(row, dict) else None


def _table_rows(text: str) -> list[str]:
    """Data rows of every markdown table in `text` — no header, no separator.

    A row is a data row when it opens with `|`, is not itself a `| --- |` separator,
    and is not immediately followed by one (which would make it a header). That last
    clause is what keeps the column names — `id`, `captured`, `claim`, `status` — out
    of the term pool, where they would appear in every comparison at once.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    separators = [bool(_TABLE_SEP.match(line)) for line in lines]
    return [
        line.strip()
        for i, line in enumerate(lines)
        if line.startswith("|")
        and not separators[i]
        and not (i + 1 < len(lines) and separators[i + 1])
    ]


def split_entries(sink: str, text: str) -> list[str]:
    """Split a sink's raw text into the entries a duplicate would land beside."""
    if sink.endswith(".jsonl"):
        return [line for line in text.splitlines() if line.strip()]
    if sink == THESIS_SINK:
        # Divergence 1: wifey's thesis inbox is a table of `H-` rows, not `## ` sections.
        return _table_rows(text)
    if sink != MECHANICS_SINK:
        raise ValueError(f"no entry splitter for sink {sink!r}")
    starts = [m.start() for m in re.finditer(r"^- ", text, flags=re.MULTILINE)]
    if not starts:
        return []
    bounds = [*starts[1:], len(text)]
    return [text[a:b].strip() for a, b in zip(starts, bounds, strict=True)]


def semantic_scope(sink: str, source_id: str | None) -> str:
    """What `find_similar` will actually compare against — `check` reports this so a
    digest can never read an empty candidate list as "checked and clean"."""
    if sink in SEMANTIC_SINKS:
        return "all-entries"
    # Scoping to one source needs entries that persist their own URL, which today
    # means the JSONL sink. Anything else is unscopeable, so nothing is compared.
    return "same-source" if source_id and sink.endswith(".jsonl") else "none"


def _comparable_entries(
    sink: str, sink_text: str, source_id: str | None
) -> list[tuple[str, str]]:
    """`(text to score, excerpt)` per in-scope entry."""
    if semantic_scope(sink, source_id) == "none":
        return []
    entries = split_entries(sink, sink_text)
    if sink in SEMANTIC_SINKS:
        return [(e, e) for e in entries]
    scoped: list[tuple[str, str]] = []
    for entry in entries:
        row = _parse_jsonl_entry(entry)
        if row is None or parse_source_id(str(row.get("url") or "")) != source_id:
            continue
        content = pundit_content_text(row)
        scoped.append((content, content))
    return scoped


def find_similar(
    claim: str,
    sink: str,
    sink_text: str,
    *,
    top_n: int = 3,
    min_score: float = _MIN_SCORE,
    source_id: str | None = None,
) -> list[DedupCandidate]:
    """Entries in `sink_text` that look like they already say what `claim` says.

    Advisory only — the caller surfaces these in the review digest and a human
    decides new row / corroboration / drop.

    Outside `SEMANTIC_SINKS` (today: Stream C) the comparison is narrowed to entries
    from `source_id`, and without one there is nothing to compare and the result is
    `[]`. Two pundits making the same call are two genuine observations that
    `pundit_score.py` scores separately, so matching ACROSS sources would destroy
    signal — but that argument never covered one video restating its own call, which
    is how an entry leg and a target leg of a single position become two ledger rows.
    Same-source entries are scored on their content fields only; see
    `_PUNDIT_CONTENT_FIELDS` for why the raw line cannot be used.
    """
    hits: list[DedupCandidate] = []
    for text, excerpt in _comparable_entries(sink, sink_text, source_id):
        score, shared_levels, shared_terms = _score(claim, text)
        if score >= min_score:
            hits.append(
                DedupCandidate(
                    sink=sink,
                    excerpt=excerpt[:_EXCERPT_CHARS],
                    score=round(score, 3),
                    shared_levels=tuple(sorted(shared_levels)),
                    shared_terms=tuple(sorted(shared_terms)),
                )
            )
    hits.sort(key=lambda c: -c.score)
    return hits[:top_n]


def find_source_duplicate_pairs(
    rows: list[dict[str, Any]], *, min_score: float = _MIN_SCORE
) -> list[DuplicatePair]:
    """Pairs among ONE source's pending Stream C items that look like one call twice.

    `find_similar` cannot see these. Every check in the review digest runs BEFORE the
    approval that writes anything, so when a video's items are checked none of them
    are on disk yet — the pair is only findable item-vs-item, which is why this is a
    separate pass rather than another sink comparison.

    Advisory, like everything else here: a hit is printed in the digest and the human
    decides one row or two. Two legs of one position and two genuinely distinct calls
    on the same symbol look similar by construction, and only the human knows which
    they are watching.
    """
    pairs: list[DuplicatePair] = []
    texts = [pundit_content_text(r) for r in rows]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            score, shared_levels, shared_terms = _score(texts[i], texts[j])
            if score < min_score:
                continue
            pairs.append(
                DuplicatePair(
                    left_ts=float(rows[i].get("ts") or 0.0),
                    right_ts=float(rows[j].get("ts") or 0.0),
                    score=round(score, 3),
                    shared_levels=tuple(sorted(shared_levels)),
                    shared_terms=tuple(sorted(shared_terms)),
                    left_excerpt=texts[i][:_EXCERPT_CHARS],
                    right_excerpt=texts[j][:_EXCERPT_CHARS],
                )
            )
    pairs.sort(key=lambda p: -p.score)
    return pairs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.ledger))
    already = is_routed(ledger, args.source_id, args.item_ts, args.sink)
    sink_path = Path(args.sink_path or args.sink)
    text = sink_path.read_text(encoding="utf-8") if sink_path.exists() else ""
    candidates = find_similar(
        args.text, args.sink, text, top_n=args.top_n, source_id=args.source_id
    )
    scope = semantic_scope(args.sink, args.source_id)
    print(
        json.dumps(
            {
                "already_routed": already,
                # Tells the digest what was actually checked, so "no candidates"
                # is never mistaken for "checked and found clean". Stream C is
                # checked too, but only against itself — `semantic_scope` is the
                # field that says which, and `semantic_checked` alone cannot.
                "semantic_checked": scope != "none",
                "semantic_scope": scope,
                "candidates": [
                    {
                        "sink": c.sink,
                        "excerpt": c.excerpt,
                        "score": c.score,
                        "shared_levels": list(c.shared_levels),
                        "shared_terms": list(c.shared_terms),
                    }
                    for c in candidates
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_mark(args: argparse.Namespace) -> int:
    append_routed(
        Path(args.ledger),
        [
            RoutedItem(
                source_id=args.source_id,
                item_ts=args.item_ts,
                sink=args.sink,
                routed_ts_utc=datetime.now(UTC).isoformat(),
            )
        ],
    )
    print(f"marked {args.source_id}@{args.item_ts} -> {args.sink}")
    return 0


def _cmd_unmark(args: argparse.Namespace) -> int:
    remove_routed(
        Path(args.ledger),
        source_id=args.source_id,
        item_ts=args.item_ts,
        sink=args.sink,
    )
    print(f"unmarked {args.source_id}@{args.item_ts} -> {args.sink}")
    return 0


def _cmd_pairs(args: argparse.Namespace) -> int:
    raw: Any = json.loads(Path(args.items).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"{args.items} must hold a JSON array of Stream C items")
    rows = [r for r in raw if isinstance(r, dict)]
    pairs = find_source_duplicate_pairs(rows)
    print(
        json.dumps(
            {
                "n_items": len(rows),
                "pairs": [
                    {
                        "left_ts": p.left_ts,
                        "right_ts": p.right_ts,
                        "score": p.score,
                        "shared_levels": list(p.shared_levels),
                        "shared_terms": list(p.shared_terms),
                        "left_excerpt": p.left_excerpt,
                        "right_excerpt": p.right_excerpt,
                    }
                    for p in pairs
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    sink_path = Path(args.sink_path or PUNDIT_SINK)
    rows: list[dict[str, Any]] = []
    if sink_path.exists():
        for line in sink_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a hand-edited line should not abort the migration
    items = seed_items(
        rows, sink=PUNDIT_SINK, fallback_ts_utc=datetime.now(UTC).isoformat()
    )
    skipped = len(rows) - len(items)
    # Report against the ledger's actual contents. "would seed 10" when 6 are
    # already recorded is the sort of misleading count this module exists to stop.
    existing = load_ledger(Path(args.ledger))
    new = [i for i in items if not is_routed(existing, i.source_id, i.item_ts, i.sink)]
    tail = (
        f"{len(new)} new, {len(items) - len(new)} already in the ledger, "
        f"{skipped} with no ingest source id"
    )
    if not args.apply:
        print(f"[dry run] {len(rows)} rows in {sink_path}: {tail}. --apply to write.")
        return 0
    append_routed(Path(args.ledger), items)
    print(f"seeded {args.ledger}: {tail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_text in (
        ("check", "report whether an item was already routed, plus lookalike entries"),
        ("mark", "record a routed item — run AFTER the sink write succeeds"),
        ("unmark", "forget a routed item so it can be re-routed"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--source-id",
            required=True,
            help="X status id or video id. ⚠ Use the = form for a `-`-leading id "
            "(--source-id=-mx3UwwJ5P4); the space form makes argparse read it as a "
            "flag. That fails LOUDLY here (usage error), unlike yt_feed mark.",
        )
        p.add_argument(
            "--item-ts",
            type=float,
            required=True,
            help="offset within the video; 0 for a whole X post",
        )
        p.add_argument(
            "--sink",
            required=True,
            choices=KNOWN_SINKS,
            metavar="SINK",
            help="routing target from x_route — one of the three FULL paths in "
            f"KNOWN_SINKS ({', '.join(KNOWN_SINKS)}); a bare filename is rejected "
            "because it would key a ledger row nothing can match",
        )
        p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
        if name == "check":
            p.add_argument("--text", required=True, help="the claim being routed")
            p.add_argument(
                "--sink-path", default="", help="read the sink here instead of --sink"
            )
            p.add_argument("--top-n", type=int, default=3)

    p_pairs = sub.add_parser(
        "pairs",
        help="flag one source's pending Stream C items that restate each other",
    )
    p_pairs.add_argument(
        "--items",
        required=True,
        help="path to a JSON array of this source's pending Stream C items",
    )

    p_seed = sub.add_parser(
        "seed",
        help="backfill the ledger from pundit-calls.jsonl — read-only without --apply",
    )
    p_seed.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p_seed.add_argument("--sink-path", default="", help="read Stream C from here")
    p_seed.add_argument("--apply", action="store_true", help="actually write")

    args = parser.parse_args(argv)
    handlers = {
        "check": _cmd_check,
        "mark": _cmd_mark,
        "unmark": _cmd_unmark,
        "pairs": _cmd_pairs,
        "seed": _cmd_seed,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
