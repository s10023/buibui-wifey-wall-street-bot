"""Score the pundit-call ledger against stored OHLCV — measured priors per author/family.

Read-only equity port of the parent repo's ``tools/pundit_score.py``: resolves every
``docs/plans/pundit-calls.jsonl`` call (free-text levels) against stored OHLCV and
reports hit-rate + R proxies per author x setup-family x direction, plus a
machine-readable ``docs/plans/pundit-priors.json`` sidecar. Descriptive priors only —
no ENABLE/BUILD verdicts (audit_guard gates come later, only if a cell earns n>=30).
Never writes to the DB; no schema change.

Parent spec: ``docs/superpowers/specs/2026-07-04-pundit-ledger-scorer-design.md``
(in ``buibui-moon-trader-bot``). Nine deliberate divergences. 1-8 are forced by equities
being a *sessioned* market rather than a 24/7 perp tape; 9 is a correctness fix that is
not equity-specific:

1. **Scoring frame follows the horizon** (``1h`` intraday, ``1d`` swing/unspecified).
   The parent walks 1h for everything and uses 1d only for ATR. Here that would strand
   every call on a symbol with no intraday history (yfinance serves ~730d of 1h, and
   several research symbols are 1d-only).
2. **No call-candle containment.** The parent requires the call timestamp to fall
   *inside* a 1h candle. Equity calls routinely land in a closed market (after the bell,
   weekends), where no candle contains them. Here the reference price is the last bar
   *fully closed* at/before the call, and the position starts at the first bar opening
   at/after it.
3. **Thesis entries fill at the next open, not the call bar's close.** The parent's
   convention reads a close that had not yet printed when the call was made — a
   look-ahead the Phase 0.2 harness would flag. The honest equity fill for "I'm long
   here" said after the close is the next session's open.
4. **Gap-aware, direction-aware level crossing.** The parent tests ``low <= level <=
   high`` (containment), which a gap jumps clean over and registers nothing. Here a long
   stops out when ``low <= stop`` and fills at ``min(open, stop)`` — an overnight gap
   through the stop is filled at the open, not wished back to the stop price.
5. **Adverse-first is resolved by the open.** When one bar reaches both levels: a bar
   that *opened* beyond the stop is a loss at the open; one that opened beyond the target
   is a win at the open; otherwise both were reached intrabar and the stop wins
   (conservative, as in the parent).
6. **Windows are counted in NYSE sessions, not wall-clock.** The parent's 48h intraday
   window spans zero trading hours over a weekend. Here horizons are 2 / 21 / 10
   sessions, resolved through ``analytics.trading_calendar``.
7. **Month-anchored years are stripped before level parsing.** US index levels occupy
   the same numeric band as year strings, so the ref-relative sanity gate cannot tell
   them apart — the first real run read "10-20% drawdown ... starting Aug-Sep 2026" as a
   2,026 target on a 7,436 index.
8. **Staleness is measured against the last closed session, not wall-clock now.**
   Otherwise every symbol reports STALE between the closing bell and the next open,
   which on a nightly cron is most of the time it runs.
9. **The geometry guard covers the target leg, not just the stop** (2026-08-04, and the
   one divergence here that is *not* equity-forced). The parent's ``_geometry_note``, from
   which this was ported, rejects only a wrong-sided stop. A wrong-sided **target** is the
   more dangerous of the two: it is already in profit at the fill, so the walk books an
   instant WIN at ~0.00 R — a phantom statistic rather than a visible error. Both legs now
   route through the shared ``tools.x_route.check_level_order``. Since the parent was
   ported from the same code, it likely carries this latent defect too — worth raising on
   the next ``/sync-parent`` rather than assuming it was fixed upstream.

Usage::

    PYTHONPATH=. poetry run python tools/pundit_score.py \
        [--ledger docs/plans/pundit-calls.jsonl] \
        [--overrides docs/plans/pundit-overrides.jsonl] \
        [--db analytics.db] [--as-of 2026-08-04T00:00:00Z] \
        [--json docs/plans/pundit-priors.json] [--min-n 5]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.pundit_authors import normalize_author
from analytics.pundit_direction import normalize_direction
from analytics.pundit_horizon import normalize_horizon
from analytics.store import DEFAULT_DB_PATH
from analytics.store.market_data import get_ohlcv
from analytics.trading_calendar import nyse_sessions
from tools.x_route import MONTH_YEAR_RE, check_level_order

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

#: Horizon -> scoring timeframe. Equity divergence 1: the parent scores every horizon on
#: 1h. yfinance caps 1h history at ~730 days and many research symbols are backfilled 1d
#: only, so a swing call walked on 1h would be UNRESOLVABLE for want of data it never
#: needed — a swing call resolves perfectly well on daily bars.
SCORE_TIMEFRAME: dict[str, str] = {
    "intraday": "1h",
    "swing": "1d",
    "unspecified": "1d",
}

#: Horizon -> window length in NYSE **sessions** (equity divergence 6). The parent's
#: wall-clock 48h / 30d / 14d become 2 / 21 / 10 sessions — the same economic horizons,
#: but a Friday-evening intraday call now gets Monday and Tuesday instead of a weekend.
SESSION_WINDOWS: dict[str, int] = {
    "intraday": 2,
    "swing": 21,
    "unspecified": 10,
}

#: Bar open_time -> bar close, per timeframe. Stored equity bars are anchored to NY
#: local time (1d opens at NY midnight, so it closes 16h later at the 16:00 bell; 1h
#: bars open 09:30..15:30 NY). Anchoring on the offset rather than a wall-clock UTC hour
#: keeps this DST-correct in both regimes.
TF_CLOSE_OFFSET_MS: dict[str, int] = {
    "1h": HOUR_MS,
    "4h": 4 * HOUR_MS,
    "1d": 16 * HOUR_MS,
    "1wk": 4 * DAY_MS + 16 * HOUR_MS,
}

#: ATR14 needs 15 closed bars. Buffers are calendar spans that comfortably cover that
#: many *sessions* — the parent's 20-bar buffer assumed a 24/7 tape and would fall ~5
#: sessions short on a daily equity frame once weekends and holidays are removed.
TF_WARMUP_BUFFER_MS: dict[str, int] = {
    "1h": 15 * DAY_MS,
    "1d": 45 * DAY_MS,
}

SANITY_LO = 0.2
SANITY_HI = 5.0

_UNSPECIFIED_MARKERS = {"", "unspecified", "none", "n/a", "not specified"}

# A field whose HEAD says no level was given carries no level, however many
# numbers trail it. Those numbers are incidental context -- a spot-price
# reference, a fib ratio, a historical-analog decade -- and harvesting them
# fabricates a precise call the pundit never made. `_UNSPECIFIED_MARKERS` alone
# cannot catch this: it matches the whole stripped string, so bare "not specified"
# was caught while "not stated (implied ~454 resistance)" fell through to
# `_NUM_RE` and returned the parenthetical as the level. The sanity gate in
# `select_level` is no backstop either, because the worst form of the phantom
# number IS the reference close.
#
# Anchored at the head ON PURPOSE. A negation that trails a stated level
# qualifies its PROVENANCE, not its existence -- "~420 (current market, no
# explicit entry stated)" is a real level, and dropping it would delete a genuine
# call. Those are flagged `hedged` instead.
_NEGATION_HEAD_RE = re.compile(
    r"^\W*(?:"
    r"un(?:specified|clear)\b"
    r"|not\s+(?:specified|stated|given|provided)\b"
    r"|no\s+(?:explicit|stated|specific|clear)\b"
    r"|none\b"
    r"|n/?a\b"
    r")",
    re.IGNORECASE,
)
# Hedged provenance anywhere in the field: the level stands, but it is the
# extractor's reading rather than the author's words, so it must not present as
# an exact quote. Feeds `low_confidence`, never a drop.
_HEDGE_RE = re.compile(
    r"not\s+(?:specified|stated|given|provided)"
    r"|no\s+explicit"
    r"|un(?:specified|clear)"
    r"|illustrative"
    r"|implied",
    re.IGNORECASE,
)
#: A Stream-C row with no resolved ticker is unscoreable. Today this rule lives only as
#: prose in `.claude/skills/ingest-video/SKILL.md` ("never route a setup item with
#: symbol: null"); without a guard here a JSON `null` stringifies to "None" and silently
#: queries a symbol that cannot exist, reporting UNRESOLVABLE for the wrong reason.
_INVALID_SYMBOLS = {"", "none", "null", "n/a", "unspecified", "tbd"}
_ZONE_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?\s*(?:-|–|\bto\b)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?"
)
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?")

#: Equity divergence 7 — month-anchored years are stripped before level extraction.
#: US index and large-cap levels sit in the same 1,900-2,100+ band as a year string, so
#: the parent's ref-relative sanity gate cannot separate them: scoring this ledger for
#: the first time read "10-20% drawdown ... starting Aug-Sep 2026" as a 2,026 target on
#: a 7,436 index. Only a *month-anchored* year is removed — a bare "2026" with no date
#: cue is indistinguishable from a real level and is left for the sanity gate to judge.
#: Defined in ``tools/x_route.py`` so the write-side sign-check shares one definition.
_MONTH_YEAR_RE = MONTH_YEAR_RE

_NY_TZ = "America/New_York"
_NYSE_CLOSE_HOUR = 16


def _expand(num_text: str, k_suffix: str | None) -> float:
    """'57,900' -> 57900.0; '60.5' + 'k' -> 60500.0."""
    return float(num_text.replace(",", "")) * (1000.0 if k_suffix else 1.0)


def session_date(ts_ms: int) -> date:
    """America/New_York session date for a UTC epoch-ms timestamp.

    Same convention as ``analytics.data_quality._et_date`` — daily bars are
    midnight-ET-in-UTC (04:00/05:00 by DST), so the ET calendar date is the session key.
    """
    return pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(_NY_TZ).date()


def session_close_ms(day: date) -> int:
    """UTC epoch-ms of the 16:00 ET closing bell on ``day``."""
    ts = pd.Timestamp(
        year=day.year, month=day.month, day=day.day, hour=_NYSE_CLOSE_HOUR, tz=_NY_TZ
    )
    return int(ts.tz_convert("UTC").timestamp() * 1000)


def _close_offset(timeframe: str) -> int:
    return TF_CLOSE_OFFSET_MS.get(timeframe, DAY_MS)


def bar_close_ms(open_time_ms: int, timeframe: str) -> int:
    """Close timestamp of the bar opening at ``open_time_ms`` (see TF_CLOSE_OFFSET_MS)."""
    return open_time_ms + _close_offset(timeframe)


@dataclass(frozen=True)
class ParsedField:
    """Raw level candidates extracted from one free-text field."""

    zones: tuple[tuple[float, float], ...]
    numbers: tuple[float, ...]
    unspecified: bool
    hedged: bool = False


def parse_level_field(text: str | None) -> ParsedField:
    """Extract zone and single-number candidates from a ledger level field.

    Fields whose head negates the level (see `_NEGATION_HEAD_RE`) yield no
    candidates at all -- the pundit stated no level, so scoring one against them
    would invent a call. Fields carrying a hedged provenance note keep their
    candidates and set `hedged`, which downgrades confidence rather than dropping
    a real level.
    """
    if text is None or str(text).strip().lower() in _UNSPECIFIED_MARKERS:
        return ParsedField(zones=(), numbers=(), unspecified=True)
    raw = str(text)
    if _NEGATION_HEAD_RE.match(raw):
        return ParsedField(zones=(), numbers=(), unspecified=True)
    cleaned = _MONTH_YEAR_RE.sub(" ", raw.replace("$", "").replace("~", ""))
    zones: list[tuple[float, float]] = []
    for zm in _ZONE_RE.finditer(cleaned):
        a = _expand(zm.group(1), zm.group(2))
        b = _expand(zm.group(3), zm.group(4))
        zones.append((min(a, b), max(a, b)))
    numbers = tuple(
        _expand(nm.group(1), nm.group(2)) for nm in _NUM_RE.finditer(cleaned)
    )
    return ParsedField(
        zones=tuple(zones),
        numbers=numbers,
        unspecified=False,
        hedged=bool(_HEDGE_RE.search(raw)),
    )


@dataclass(frozen=True)
class LedgerCall:
    """One line of docs/plans/pundit-calls.jsonl."""

    line_no: int
    source: str
    author: str
    url: str
    call_ts_utc: str
    symbol: str
    direction: str
    entry: str
    stop: str
    target: str
    horizon: str
    confidence: str
    raw_quote: str
    entry_px: float | None = None
    stop_px: float | None = None
    target_px: float | None = None

    @property
    def call_ts_ms(self) -> int:
        dt = datetime.fromisoformat(self.call_ts_utc.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)

    @property
    def timeframe(self) -> str:
        """Scoring frame for this call's horizon (equity divergence 1).

        The sibling of ``window_sessions``' fallback, and the reason an
        unrecognised horizon was worse here than upstream: it picked the wrong
        *bar series* as well as the wrong window, so a mistyped ``intraday``
        was walked on ``1d`` bars. Likewise unreachable now for anything
        ``load_ledger`` produced — see ``analytics/pundit_horizon.py``.
        """
        return SCORE_TIMEFRAME.get(self.horizon, SCORE_TIMEFRAME["unspecified"])


@dataclass(frozen=True)
class Override:
    """One line of docs/plans/pundit-overrides.jsonl — wins over parsed values."""

    url: str
    entry_px: float | None = None
    stop_px: float | None = None
    target_px: float | None = None
    family: str | None = None
    skip: bool = False
    note: str = ""


def _opt_float(obj: dict[str, object], key: str) -> float | None:
    val = obj.get(key)
    return float(val) if isinstance(val, (int, float)) else None


def load_ledger(path: Path) -> tuple[list[LedgerCall], list[str]]:
    """Parse the ledger JSONL; malformed lines become warnings, never crashes."""
    calls: list[LedgerCall] = []
    warnings: list[str] = []
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                warnings.append(f"ledger line {line_no}: skipped (not a JSON object)")
                continue
            symbol = str(obj["symbol"] or "").strip()
            if symbol.lower() in _INVALID_SYMBOLS:
                warnings.append(f"ledger line {line_no}: skipped (no symbol resolved)")
                continue
            call = LedgerCall(
                line_no=line_no,
                source=str(obj.get("source", "")),
                # Normalised at READ, so a ledger written with a mixed '@'
                # convention still groups as one person. This ledger already
                # carries both conventions at once (see analytics/
                # pundit_authors.py) — no key collides today, but nothing
                # stops the next routed row from starting a second track
                # record for an author already tracked here.
                author=normalize_author(str(obj.get("author", ""))),
                url=str(obj.get("url", "")),
                call_ts_utc=str(obj["call_ts_utc"]),
                symbol=symbol,
                # Raises on anything outside the enum, which the existing
                # handler below turns into a per-line warning. Unlike the
                # parent this scorer was never mis-booking these — score_call
                # already gates `direction not in ("long", "short")` to
                # UNSCORED — so this moves the rejection earlier and makes it
                # name the line, rather than fixing a wrong number.
                direction=normalize_direction(str(obj.get("direction", ""))),
                entry=str(obj.get("entry", "") or ""),
                stop=str(obj.get("stop", "") or ""),
                target=str(obj.get("target", "") or ""),
                # The one guard here that fixes a live silent defect. A present
                # but unrecognised horizon used to buy TWO wrong answers at
                # once — SCORE_TIMEFRAME's fallback (wrong bar timeframe) and
                # SESSION_WINDOWS' (wrong window length) — with nothing
                # raising. A MISSING one is still "unspecified": that is a real
                # horizon, not a violation, which is why this is not a copy of
                # the direction guard (analytics/pundit_horizon.py explains).
                horizon=normalize_horizon(obj.get("horizon")),
                confidence=str(obj.get("confidence", "") or ""),
                raw_quote=str(obj.get("raw_quote", "") or ""),
                entry_px=_opt_float(obj, "entry_px"),
                stop_px=_opt_float(obj, "stop_px"),
                target_px=_opt_float(obj, "target_px"),
            )
            _ = call.call_ts_ms  # eager-parse call_ts_utc now (raise here, not later)
            calls.append(call)
        except (ValueError, KeyError) as exc:
            warnings.append(f"ledger line {line_no}: skipped ({exc})")
    return calls, warnings


@dataclass(frozen=True)
class ResolvedLevels:
    """Numeric levels for one call after parsing, overrides, and fallbacks."""

    entry_px: float
    entry_is_thesis: bool
    stop_px: float | None
    target_px: float | None
    parse_confidence: str  # ok | low | override | fallback


def select_level(
    parsed: ParsedField, ref_close: float, role: str, direction: str
) -> tuple[float | None, bool]:
    """Pick a price from parsed candidates. Returns (price | None, low_confidence).

    Zone first (both edges must pass the sanity gate): entry -> mid, stop -> far
    edge, target -> near edge. Else the first single number passing the gate;
    multiple distinct sane numbers flag low confidence. Candidates present but all
    rejected also flag low confidence, as does a hedged provenance note -- the
    level is the extractor's reading of a chart, not the author's stated number.
    """

    def sane(x: float) -> bool:
        return SANITY_LO * ref_close <= x <= SANITY_HI * ref_close

    for lo, hi in parsed.zones:
        if sane(lo) and sane(hi):
            if role == "entry":
                return (lo + hi) / 2.0, parsed.hedged
            # stop: far edge (long stops sit below -> lo; short stops above -> hi)
            # target: near edge (long targets above -> lo is nearest; short -> hi)
            return (lo if direction == "long" else hi), parsed.hedged
    sane_nums = [x for x in parsed.numbers if sane(x)]
    if sane_nums:
        return sane_nums[0], parsed.hedged or len(set(sane_nums)) > 1
    return None, bool(parsed.numbers or parsed.zones)


def resolve_levels(
    call: LedgerCall, override: Override | None, ref_close: float
) -> ResolvedLevels:
    """Resolve entry/stop/target with precedence override > ledger px > text > fallback."""
    used_override = False
    low_flag = False

    def pick(
        ov_px: float | None, ledger_px: float | None, text: str, role: str
    ) -> float | None:
        nonlocal used_override, low_flag
        if ov_px is not None:
            used_override = True
            return ov_px
        if ledger_px is not None:
            return ledger_px
        px, low = select_level(parse_level_field(text), ref_close, role, call.direction)
        low_flag = low_flag or low
        return px

    ov = override
    entry = pick(ov.entry_px if ov else None, call.entry_px, call.entry, "entry")
    stop = pick(ov.stop_px if ov else None, call.stop_px, call.stop, "stop")
    target = pick(ov.target_px if ov else None, call.target_px, call.target, "target")

    entry_is_thesis = entry is None
    if entry is None:
        entry = ref_close
    if used_override:
        confidence = "override"
    elif entry_is_thesis:
        confidence = "fallback"
    elif low_flag:
        confidence = "low"
    else:
        confidence = "ok"
    return ResolvedLevels(
        entry_px=entry,
        entry_is_thesis=entry_is_thesis,
        stop_px=stop,
        target_px=target,
        parse_confidence=confidence,
    )


FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sweep_reclaim",
        (
            "sweep",
            "reclaim",
            "sfp",
            "stop hunt",
            "stop-hunt",
            "deviation below",
            "deviation above",
        ),
    ),
    (
        "vp_level",
        ("poc", "vah", "val ", "value area", "value-area", "volume profile", "vwap"),
    ),
    (
        "ref_level",
        (
            "pdl",
            "pdh",
            "pwh",
            "pwl",
            "pdval",
            "range low",
            "range high",
            "range-low",
            "range-high",
            "weekly open",
            "daily open",
            "monday",
        ),
    ),
    (
        "ema_trend",
        (
            "ema",
            "moving average",
            "50w",
            "200d",
            "trendline",
            "diagonal",
            "downtrend",
            "uptrend",
            "higher low",
            "lower high",
        ),
    ),
    (
        "flow",
        (
            "cvd",
            "open interest",
            " oi ",
            "absorption",
            "delta",
            "spot bid",
            "spot flow",
            "orderflow",
            "funding",
        ),
    ),
    (
        "accumulation_zone",
        ("accumulation", "dca", "demand zone", "spot-demand", "supply zone", "demand"),
    ),
    (
        "breakout_deviation",
        (
            "breakout",
            "break of",
            "break above",
            "break below",
            "acceptance",
            "deviation",
        ),
    ),
)


def _keyword_hit(t: str, keyword: str) -> bool:
    """Symmetric word-boundary substring match.

    ``keyword`` is stripped of its own leading/trailing spaces before matching
    (multi-word phrases keep their internal spaces, which match literally); a
    hit is accepted only when the character immediately before the match AND
    the character immediately after it are both non-letters (start/end of
    string count as non-letters). A digit on either side does NOT block a
    match, so ``"50ema"``/``"1W 50EMA"`` still hit ``"ema"``. This keeps bare
    short keywords from false-matching mid-word — ``"remains"`` (r-EMA-ins)
    and ``"demand"`` (d-EMA-nd) both contain ``"ema"`` but must not tag
    ``ema_trend`` — while a space-wrapped keyword like ``" oi "`` still hits
    inside ``"reported oi levels are climbing"`` (a left-boundary-only check
    on the raw, un-stripped ``" oi "`` match anchors on the leading space
    itself, whose *preceding* character is the last letter of "reported" —
    wrongly rejecting the hit) and ``"val "`` does not hit inside ``"value"``
    (the trailing ``"u"`` fails the right-boundary check). All occurrences
    are checked, not just the first, since an earlier occurrence can fail a
    boundary test while a later one passes.
    """
    kw = keyword.strip(" ")
    start = 0
    while True:
        idx = t.find(kw, start)
        if idx == -1:
            return False
        left_ok = idx == 0 or not t[idx - 1].isalpha()
        end = idx + len(kw)
        right_ok = end == len(t) or not t[end].isalpha()
        if left_ok and right_ok:
            return True
        start = idx + 1


def tag_family(text: str) -> str:
    """First-match keyword family — a grouping key, not a model (spec §Family).

    Kept byte-identical to the parent's taxonomy so the two ledgers stay comparable,
    crypto-flavoured keywords included: ``funding`` / ``open interest`` simply never
    fire on equity text, and a family is only ever a grouping key.
    """
    t = f" {text.lower()} "
    for family, keywords in FAMILY_KEYWORDS:
        if any(_keyword_hit(t, k) for k in keywords):
            return family
    return "other"


def load_overrides(path: Path) -> dict[str, Override]:
    """Parse the overrides sidecar; absent file means no overrides."""
    if not path.exists():
        return {}
    out: dict[str, Override] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        obj = json.loads(raw)
        url = str(obj["url"])
        out[url] = Override(
            url=url,
            entry_px=_opt_float(obj, "entry_px"),
            stop_px=_opt_float(obj, "stop_px"),
            target_px=_opt_float(obj, "target_px"),
            family=str(obj["family"]) if obj.get("family") else None,
            skip=bool(obj.get("skip", False)),
            note=str(obj.get("note", "") or ""),
        )
    return out


def window_sessions(horizon: str) -> int:
    """Pre-committed horizon window in NYSE sessions.

    The ``.get`` fallback is now **unreachable for anything ``load_ledger``
    produced** — ``normalize_horizon`` rejects an unrecognised value at the
    read boundary, where the raw field is still visible and a typo is still
    distinguishable from an honest ``unspecified``. It is kept as
    defence-in-depth for direct callers, not as the guard: by the time a
    string arrives here there is no way to tell those two cases apart, which
    is exactly why this line silently mis-scored calls before. Note this is
    one of **two** such fallbacks (see ``LedgerCall.timeframe``);
    ``tests/test_pundit_horizon.py`` binds both tables' keys to
    ``VALID_HORIZONS`` so they cannot drift apart.
    """
    return SESSION_WINDOWS.get(horizon, SESSION_WINDOWS["unspecified"])


def deadline_ms(call_ts_ms: int, horizon: str) -> int:
    """Closing bell of the Nth NYSE session whose close falls after ``call_ts_ms``.

    Equity divergence 6. A call placed mid-session counts that session as its first; one
    placed after the bell (or over a weekend) starts counting at the next session, so a
    Friday-evening "intraday" call is judged on Monday and Tuesday rather than on a
    window that contains no trading at all.
    """
    n = window_sessions(horizon)
    start = session_date(call_ts_ms)
    # n sessions never span more than ~2n calendar days once weekends and holidays are
    # allowed for; the +15 covers the year-end/Thanksgiving clusters at small n.
    span = nyse_sessions(start, start + timedelta(days=2 * n + 15))
    future = [d for d in span if session_close_ms(d) > call_ts_ms]
    if len(future) >= n:
        return session_close_ms(future[n - 1])
    # Calendar exhausted (out of the library's range, or an absurd horizon): degrade to
    # wall-clock rather than raise — the scorer must never crash on one odd row.
    return call_ts_ms + n * DAY_MS


def last_expected_close_ms(as_of_ms: int) -> int:
    """Close of the most recent NYSE session finished by ``as_of_ms``.

    Equity divergence 8 — how far the tape is *expected* to reach. The parent compares
    stored data against wall-clock now, which on a 24/7 tape is the same thing. In
    equities it is not: between the 16:00 bell and the next open there is no data to be
    had, so a plain ``data_end < now`` test reports every symbol as STALE every single
    evening. Staleness has to be measured against the last session that actually closed.
    """
    today = session_date(as_of_ms)
    closed = [
        session_close_ms(s)
        for s in nyse_sessions(today - timedelta(days=14), today)
        if session_close_ms(s) <= as_of_ms
    ]
    return closed[-1] if closed else as_of_ms


def find_ref_bar(df: pd.DataFrame, ts_ms: int, timeframe: str) -> int | None:
    """Index of the last bar **fully closed** at/before ``ts_ms``, or None.

    Equity divergence 2 + 3: the parent returns the bar *containing* the call and reads
    its close, which had not printed yet when the call was made. Requiring a closed bar
    keeps the reference price causal, and — unlike containment — still resolves calls
    made while the market is shut, which is where most video-sourced calls land.
    """
    if df.empty:
        return None
    closed = int((df["open_time"] + _close_offset(timeframe) <= ts_ms).sum())
    return closed - 1 if closed >= 1 else None


def find_entry_start(df: pd.DataFrame, ts_ms: int) -> int | None:
    """Index of the first bar opening at/after ``ts_ms`` — where the position may start."""
    if df.empty:
        return None
    idx = int(df["open_time"].searchsorted(ts_ms, side="left"))
    return idx if idx < len(df) else None


def find_fill(
    df: pd.DataFrame,
    start_idx: int,
    entry_px: float,
    is_thesis: bool,
    limit_ms: int,
) -> tuple[int, float] | None:
    """First bar at/after ``start_idx`` that fills the entry (spec §Trigger).

    Thesis entries fill at the open of the first bar after the call (equity divergence
    3). Level entries fill on the first bar whose range contains the price
    (direction-agnostic touch: covers pullback and breakout entries alike) or, when an
    overnight gap jumps clean over the level, at that bar's open (equity divergence 4 —
    the parent's containment test silently misses a gapped entry entirely). Bars opening
    after ``limit_ms`` never fill.
    """
    if start_idx >= len(df):
        return None
    if is_thesis:
        if int(df["open_time"].iloc[start_idx]) > limit_ms:
            return None
        return start_idx, float(df["open"].iloc[start_idx])
    for i in range(start_idx, len(df)):
        if int(df["open_time"].iloc[i]) > limit_ms:
            return None
        op = float(df["open"].iloc[i])
        if float(df["low"].iloc[i]) <= entry_px <= float(df["high"].iloc[i]):
            return i, entry_px
        if i > 0:
            prev_close = float(df["close"].iloc[i - 1])
            gapped = (prev_close < entry_px < op) or (op < entry_px < prev_close)
            if gapped:
                return i, op
    return None


STATE_WIN = "WIN"
STATE_LOSS = "LOSS"
STATE_OPEN = "OPEN"
STATE_NOT_TRIGGERED = "NOT_TRIGGERED"
STATE_UNSCORED = "UNSCORED"
STATE_UNRESOLVABLE = "UNRESOLVABLE"
STATE_STALE = "STALE"
STATE_SKIPPED = "SKIPPED"
RESOLVED_STATES = (STATE_WIN, STATE_LOSS)


def bar_outcome(
    direction: str,
    bar_open: float,
    bar_high: float,
    bar_low: float,
    stop_px: float | None,
    target_px: float | None,
) -> tuple[str, float] | None:
    """Resolve one bar into (state, exit price), or None if neither level was reached.

    Equity divergences 4 and 5. Order of tests *is* the tie-break policy:

    1. Opened beyond the stop -> LOSS at the open. An overnight gap through the stop
       fills where the market reopened, not back at the stop price.
    2. Opened beyond the target -> WIN at the open (the favourable mirror).
    3. Reached the stop intrabar -> LOSS at the stop.
    4. Reached the target intrabar -> WIN at the target.

    Steps 3-4 preserve the parent's adverse-first convention for the ambiguous case
    where a single bar touches both and only intrabar data (which we do not have) could
    say which came first.
    """
    if direction == "long":
        if stop_px is not None and bar_open <= stop_px:
            return STATE_LOSS, bar_open
        if target_px is not None and bar_open >= target_px:
            return STATE_WIN, bar_open
        if stop_px is not None and bar_low <= stop_px:
            return STATE_LOSS, stop_px
        if target_px is not None and bar_high >= target_px:
            return STATE_WIN, target_px
        return None
    if stop_px is not None and bar_open >= stop_px:
        return STATE_LOSS, bar_open
    if target_px is not None and bar_open <= target_px:
        return STATE_WIN, bar_open
    if stop_px is not None and bar_high >= stop_px:
        return STATE_LOSS, stop_px
    if target_px is not None and bar_low <= target_px:
        return STATE_WIN, target_px
    return None


@dataclass(frozen=True)
class ScoredCall:
    """One ledger call after resolution against OHLCV."""

    call: LedgerCall
    levels: ResolvedLevels | None
    family: str
    state: str
    fill_ts_ms: int | None = None
    fill_px: float | None = None
    exit_ts_ms: int | None = None
    exit_px: float | None = None
    r: float | None = None
    atr_r: float | None = None
    win: bool | None = None
    note: str = ""


def atr14_before(df: pd.DataFrame, ts_ms: int, timeframe: str) -> float | None:
    """ATR14 over the bars fully closed by ts_ms (engine TR-mean convention)."""
    if df.empty:
        return None
    closed = int((df["open_time"] + _close_offset(timeframe) <= ts_ms).sum())
    idx = closed - 1
    if idx < 1:
        return None
    return _compute_atr14(
        df["high"].to_numpy(dtype=np.float64),
        df["low"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        idx,
    )


def _geometry_note(
    direction: str, fill_px: float, stop_px: float | None, target_px: float | None
) -> str:
    """Non-empty when a level sits on the wrong side of the fill (R would be garbage).

    Delegates to the shared ``tools.x_route.check_level_order`` so the ledger's write
    path (``/ingest-x`` and ``/ingest-video`` step 8) and this read path enforce one
    definition of the rule rather than two that can drift apart.

    The **target** leg is the load-bearing half. A stop on the wrong side merely yields a
    nonsense R, but a target on the wrong side is already in profit at the fill, so the
    walk books an instant WIN at ~0.00 R — a phantom statistic rather than a visible
    error. That is exactly how an "unless it reclaims 29,200" *stop*, mis-written into a
    short's ``target``, produced a 100% hit rate with zero warnings on 2026-08-04. It is
    also why the check must live here and not only at write time: that row stated no
    entry, so the write-side pairwise rule had nothing to compare against, whereas here
    ``resolve_levels`` has already substituted the market price for the missing entry.
    """
    return check_level_order(direction, entry=fill_px, stop=stop_px, target=target_px)


def score_call(
    call: LedgerCall,
    override: Override | None,
    df: pd.DataFrame,
    as_of_ms: int,
) -> ScoredCall:
    """Resolve one call: trigger -> walk -> state + R (spec §Scoring semantics).

    ``df`` is the call's horizon frame (``call.timeframe``), already sorted and
    truncated at ``as_of_ms``.
    """
    family = (
        override.family
        if override is not None and override.family
        else tag_family(f"{call.raw_quote} {call.entry}")
    )
    tf = call.timeframe
    if override is not None and override.skip:
        return ScoredCall(call, None, family, STATE_SKIPPED, note=override.note)
    if call.direction not in ("long", "short"):
        return ScoredCall(
            call, None, family, STATE_UNSCORED, note=f"direction '{call.direction}'"
        )
    if df.empty:
        return ScoredCall(call, None, family, STATE_UNRESOLVABLE, note=f"no {tf} OHLCV")

    ref_idx = find_ref_bar(df, call.call_ts_ms, tf)
    if ref_idx is None:
        return ScoredCall(
            call, None, family, STATE_UNRESOLVABLE, note=f"{tf} data starts after call"
        )
    ref_close = float(df["close"].iloc[ref_idx])
    levels = resolve_levels(call, override, ref_close)

    deadline = deadline_ms(call.call_ts_ms, call.horizon)
    data_end_ms = bar_close_ms(int(df["open_time"].iloc[-1]), tf)
    # How far the tape is expected to reach right now (equity divergence 8). Staleness
    # is data_end vs this, never vs wall-clock now — otherwise every symbol reads STALE
    # between the closing bell and the next open.
    expected_end = last_expected_close_ms(as_of_ms)

    start_idx = find_entry_start(df, call.call_ts_ms)
    fill = (
        None
        if start_idx is None
        else find_fill(
            df,
            start_idx,
            levels.entry_px,
            levels.entry_is_thesis,
            min(deadline, as_of_ms),
        )
    )
    if fill is None:
        if deadline <= as_of_ms and data_end_ms >= deadline:
            return ScoredCall(call, levels, family, STATE_NOT_TRIGGERED)
        if data_end_ms < min(deadline, expected_end):
            return ScoredCall(
                call,
                levels,
                family,
                STATE_STALE,
                note=f"{tf} OHLCV ends in trigger window — sync first",
            )
        return ScoredCall(call, levels, family, STATE_OPEN, note="awaiting trigger")

    fill_idx, fill_px = fill
    fill_ts = int(df["open_time"].iloc[fill_idx])
    geometry = _geometry_note(call.direction, fill_px, levels.stop_px, levels.target_px)
    if geometry:
        return ScoredCall(
            call,
            levels,
            family,
            STATE_UNSCORED,
            fill_ts_ms=fill_ts,
            fill_px=fill_px,
            note=geometry,
        )

    # The window runs from the fill, matching the parent: a call that takes three
    # sessions to trigger still gets its full stated horizon once filled.
    expiry_ms = deadline_ms(fill_ts, call.horizon)
    dirsign = 1.0 if call.direction == "long" else -1.0
    risk = abs(fill_px - levels.stop_px) if levels.stop_px is not None else None
    scan_limit = min(expiry_ms, as_of_ms)

    state = ""
    exit_px: float | None = None
    exit_ts: int | None = None
    for i in range(fill_idx, len(df)):
        ot = int(df["open_time"].iloc[i])
        if ot > scan_limit:
            break
        # On the fill bar itself the entry has already been paid, so an open-gap test
        # against it is meaningless — only the intrabar excursion can resolve it.
        eff_open = fill_px if i == fill_idx else float(df["open"].iloc[i])
        outcome = bar_outcome(
            call.direction,
            eff_open,
            float(df["high"].iloc[i]),
            float(df["low"].iloc[i]),
            levels.stop_px,
            levels.target_px,
        )
        if outcome is not None:
            state, exit_px = outcome
            exit_ts = ot
            break

    if not state:
        if expiry_ms <= as_of_ms and data_end_ms >= expiry_ms:
            exp_idx = int(df["open_time"].searchsorted(expiry_ms, side="right")) - 1
            exit_px = float(df["close"].iloc[exp_idx])
            exit_ts = int(df["open_time"].iloc[exp_idx])
            # Expiry classified by sign; exactly flat counts as LOSS (conservative).
            state = STATE_WIN if dirsign * (exit_px - fill_px) > 0 else STATE_LOSS
        elif data_end_ms < min(expiry_ms, expected_end):
            return ScoredCall(
                call,
                levels,
                family,
                STATE_STALE,
                fill_ts_ms=fill_ts,
                fill_px=fill_px,
                note=f"{tf} OHLCV ends mid-window — sync first",
            )
        else:
            return ScoredCall(
                call,
                levels,
                family,
                STATE_OPEN,
                fill_ts_ms=fill_ts,
                fill_px=fill_px,
                note="in position",
            )

    assert exit_px is not None and exit_ts is not None
    r = dirsign * (exit_px - fill_px) / risk if risk else None
    atr = atr14_before(df, fill_ts, tf)
    atr_r = dirsign * (exit_px - fill_px) / atr if atr else None
    return ScoredCall(
        call,
        levels,
        family,
        state,
        fill_ts_ms=fill_ts,
        fill_px=fill_px,
        exit_ts_ms=exit_ts,
        exit_px=exit_px,
        r=r,
        atr_r=atr_r,
        win=state == STATE_WIN,
    )


@dataclass
class CellStats:
    """Roll-up counters for one report cell (author, or family x direction)."""

    n: int = 0
    triggered: int = 0
    open_: int = 0
    resolved: int = 0
    wins: int = 0
    r_sum: float = 0.0
    r_n: int = 0
    atr_r_sum: float = 0.0
    atr_r_n: int = 0

    def add(self, sc: ScoredCall) -> None:
        self.n += 1
        if sc.fill_ts_ms is not None:
            self.triggered += 1
        if sc.state == STATE_OPEN:
            self.open_ += 1
        if sc.state in RESOLVED_STATES:
            self.resolved += 1
            if sc.win:
                self.wins += 1
            if sc.r is not None:
                self.r_sum += sc.r
                self.r_n += 1
            if sc.atr_r is not None:
                self.atr_r_sum += sc.atr_r
                self.atr_r_n += 1

    @property
    def hit_rate(self) -> float | None:
        return self.wins / self.resolved if self.resolved else None

    @property
    def avg_r(self) -> float | None:
        return self.r_sum / self.r_n if self.r_n else None

    @property
    def avg_atr_r(self) -> float | None:
        return self.atr_r_sum / self.atr_r_n if self.atr_r_n else None

    @property
    def r_coverage(self) -> float | None:
        """Share of RESOLVED calls that ``avg_r`` was actually computed over.

        Load-bearing, not decoration. ``r`` needs a stated stop (see
        ``score_call``: ``if risk is not None and risk > 0``), and a call that
        stopped out necessarily has one while a win scored against a target
        often does not. So ``avg_r`` describes a loss-enriched subsample while
        ``n`` and ``resolved`` describe the whole cell, and printing them
        adjacent invites reading them as one population.

        Measured on THIS ledger 2026-08-12 (19 calls, 8 resolved):
        **WIN r-coverage 1/3 = 33%** against **LOSS 5/5 = 100%** — every loss
        carries an ``r``, a third of wins do. The censoring is severe enough to
        invert the headline: ``@fenggemeigu`` reads ``avg_r`` **-0.41** over
        r_n=6 while the complete ``atr_r`` sample over all 7 resolved calls is
        **+0.80**. The two statistics disagree in SIGN, not merely in
        significance, which is why the denominator ships beside the number.

        Reproduce: ``docs/plans/scripts/pundit_r_coverage.py`` (calls the
        production scorer). Do not carry the parent's 43%/79% across — that is a
        203-row crypto ledger sharing no rows with this one.
        """
        return self.r_n / self.resolved if self.resolved else None


def aggregate(
    scored: list[ScoredCall], key_fn: Callable[[ScoredCall], str]
) -> dict[str, CellStats]:
    cells: dict[str, CellStats] = {}
    for sc in scored:
        cells.setdefault(key_fn(sc), CellStats()).add(sc)
    return cells


def _fmt(x: float | None, nd: int = 2) -> str:
    return f"{x:.{nd}f}" if x is not None else "—"


def _fmt_ts(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _cell_table(cells: dict[str, CellStats], label: str, min_n: int) -> list[str]:
    lines = [
        f"| {label} | n | trig | open | resolved | wins | losses "
        "| hit% | avg ATR-R | avg R (cov) | |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key in sorted(cells):
        c = cells[key]
        hit = f"{100 * c.hit_rate:.0f}%" if c.hit_rate is not None else "—"
        mark = f"⚠ n<{min_n}" if c.n < min_n else ""
        # avg R carries its own denominator: it is computed only over calls that
        # stated a stop, and those skew toward losses (measured here: 100% of
        # losses vs 33% of wins). Without "(r_n/resolved)" beside it the reader
        # pairs it with `n` and compares across authors whose coverage differs.
        # avg ATR-R leads because it is the COMPLETE resolved sample.
        avg_r_cell = f"{_fmt(c.avg_r)} ({c.r_n}/{c.resolved})"
        lines.append(
            f"| {key} | {c.n} | {c.triggered} | {c.open_} | {c.resolved} "
            f"| {c.wins} | {c.resolved - c.wins} "
            f"| {hit} | {_fmt(c.avg_atr_r)} | {avg_r_cell} | {mark} |"
        )
    return lines


def render_report(
    scored: list[ScoredCall], warnings: list[str], as_of_iso: str, min_n: int
) -> str:
    """Full markdown report: roll-ups + per-call audit trail (spec §Outputs)."""
    lines = [
        "# Pundit-ledger scorecard",
        "",
        f"- as-of: {as_of_iso} · calls: {len(scored)} · ledger warnings: {len(warnings)}",
        "- Descriptive priors only — NO verdicts; cells below min-n are markers, not gates.",
        "- Windows are NYSE sessions (intraday 2 · swing 21 · unspecified 10); "
        "intraday scores on 1h bars, swing/unspecified on 1d.",
        "",
    ]
    for w in warnings:
        lines.append(f"- WARNING: {w}")
    if warnings:
        lines.append("")
    lines += ["## Per author", ""]
    lines += _cell_table(aggregate(scored, lambda sc: sc.call.author), "author", min_n)
    lines += ["", "## Per setup-family × direction", ""]
    lines += _cell_table(
        aggregate(scored, lambda sc: f"{sc.family}/{sc.call.direction}"),
        "family/direction",
        min_n,
    )
    lines += ["", "## Audit trail", ""]
    lines += [
        "| author | symbol | tf | dir | call ts (UTC) | entry | stop | target "
        "| conf | family | state | R | ATR-R | note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sc in scored:
        lv = sc.levels
        lines.append(
            f"| {sc.call.author} | {sc.call.symbol} | {sc.call.timeframe} "
            f"| {sc.call.direction} | {_fmt_ts(sc.call.call_ts_ms)} "
            f"| {_fmt(lv.entry_px) if lv else '—'}{' (thesis)' if lv and lv.entry_is_thesis else ''} "
            f"| {_fmt(lv.stop_px) if lv else '—'} | {_fmt(lv.target_px) if lv else '—'} "
            f"| {lv.parse_confidence if lv else '—'} | {sc.family} | {sc.state} "
            f"| {_fmt(sc.r)} | {_fmt(sc.atr_r)} | {sc.note} |"
        )
    return "\n".join(lines) + "\n"


def _cell_dict(c: CellStats) -> dict[str, object]:
    """Cell as JSON. ``avg_atr_r`` leads because it is the COMPLETE sample.

    ``avg_r`` keeps its key (consumers read it, and a pundit's own stated risk
    is a real question) but now ships with ``r_n`` / ``r_coverage`` beside it so
    its denominator is visible at the point of reading. Never compare ``avg_r``
    across authors without checking coverage first — see ``CellStats.r_coverage``.
    """
    return {
        "n": c.n,
        "triggered": c.triggered,
        "open": c.open_,
        "resolved": c.resolved,
        "wins": c.wins,
        "hit_rate": c.hit_rate,
        "avg_atr_r": c.avg_atr_r,
        "atr_r_n": c.atr_r_n,
        "avg_r": c.avg_r,
        "r_n": c.r_n,
        "r_coverage": c.r_coverage,
    }


def build_priors(
    scored: list[ScoredCall], as_of_iso: str, generated_at_iso: str, min_n: int
) -> dict[str, object]:
    """Machine-readable priors (spec §Outputs) — the daily-brief / trade-card hook."""
    by_author = aggregate(scored, lambda sc: sc.call.author)
    by_family = aggregate(scored, lambda sc: f"{sc.family}/{sc.call.direction}")
    authors: dict[str, object] = {}
    for author in sorted(by_author):
        fam_counts: dict[str, dict[str, int]] = {}
        for sc in scored:
            if sc.call.author == author:
                fam_counts.setdefault(sc.family, {"n": 0})["n"] += 1
        authors[author] = _cell_dict(by_author[author]) | {"families": fam_counts}
    # Nested {family: {direction: stats}} — the binding shape for the downstream
    # daily-brief consumer, NOT a flat "family/direction" key (spec §Outputs).
    families: dict[str, dict[str, object]] = {}
    for fam_key in sorted(by_family):  # fam_key == "family/direction"
        family, _, direction = fam_key.rpartition("/")
        families.setdefault(family, {})[direction] = _cell_dict(by_family[fam_key])
    return {
        "generated_at": generated_at_iso,
        "as_of": as_of_iso,
        "policy": {
            "windows_sessions": {"intraday": 2, "swing": 21, "unspecified": 10},
            "window_unit": "nyse_sessions",
            "score_timeframe": dict(SCORE_TIMEFRAME),
            "atr": "atr14 on the horizon frame (1h intraday / 1d swing)",
            "min_n_marker": min_n,
        },
        "authors": authors,
        "families": families,
    }


def load_ohlcv_for_calls(
    conn: duckdb.DuckDBPyConnection, calls: list[LedgerCall], as_of_ms: int
) -> dict[tuple[str, str], pd.DataFrame]:
    """One frame per (symbol, horizon timeframe), buffered back for ATR14 warm-up."""
    out: dict[tuple[str, str], pd.DataFrame] = {}
    wanted = {(c.symbol, c.timeframe) for c in calls}
    for symbol, tf in sorted(wanted):
        start = min(
            c.call_ts_ms for c in calls if c.symbol == symbol and c.timeframe == tf
        )
        buffer_ms = TF_WARMUP_BUFFER_MS.get(tf, 45 * DAY_MS)
        df = get_ohlcv(conn, symbol, tf, start - buffer_ms, as_of_ms)
        out[(symbol, tf)] = df.sort_values("open_time").reset_index(drop=True)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=Path("docs/plans/pundit-calls.jsonl"))
    p.add_argument(
        "--overrides", type=Path, default=Path("docs/plans/pundit-overrides.jsonl")
    )
    p.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    p.add_argument("--as-of", dest="as_of", default=None, help="ISO UTC; default: now")
    p.add_argument("--json", type=Path, default=Path("docs/plans/pundit-priors.json"))
    p.add_argument("--min-n", dest="min_n", type=int, default=5)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.as_of is not None:
        as_of_dt = datetime.fromisoformat(str(args.as_of).replace("Z", "+00:00"))
    else:
        as_of_dt = datetime.now(tz=UTC)
    as_of_ms = int(as_of_dt.timestamp() * 1000)
    as_of_iso = as_of_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    calls, warnings = load_ledger(args.ledger)
    overrides = load_overrides(args.overrides)
    with duckdb.connect(str(args.db), read_only=True) as conn:
        data = load_ohlcv_for_calls(conn, calls, as_of_ms)
    scored = [
        score_call(
            c,
            overrides.get(c.url),
            data.get((c.symbol, c.timeframe), pd.DataFrame()),
            as_of_ms,
        )
        for c in calls
    ]
    print(render_report(scored, warnings, as_of_iso, args.min_n))
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    priors = build_priors(scored, as_of_iso, generated_at, args.min_n)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(priors, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"priors written: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
