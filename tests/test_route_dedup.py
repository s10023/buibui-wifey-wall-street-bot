"""Tests for tools/route_dedup.py — pure matching + a ledger; no network anywhere.

Ported from the parent's suite alongside the module, with the fixtures recast to this
fork's sinks and instruments. Blocks covering the seven wifey divergences are marked
inline with the divergence number; each of those asserts a case where the parent's code
returned a *silently* wrong answer on this fork's live sinks, so they are the tests to
read first if any of this is ever re-ported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.route_dedup import (
    MECHANICS_SINK,
    PUNDIT_SINK,
    THESIS_SINK,
    RoutedItem,
    _score,
    append_routed,
    find_similar,
    find_source_duplicate_pairs,
    is_routed,
    load_ledger,
    main,
    normalize_levels,
    parse_source_id,
    remove_routed,
    seed_items,
    semantic_scope,
    split_entries,
)
from tools.x_route import route_target


def _item(
    source_id: str = "vid1",
    item_ts: float = 0.0,
    sink: str = PUNDIT_SINK,
) -> RoutedItem:
    return RoutedItem(
        source_id=source_id,
        item_ts=item_ts,
        sink=sink,
        routed_ts_utc="2026-08-05T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Drift guard: these constants must stay byte-identical to what the shared router
# actually returns, or dedup silently checks the wrong file forever.
# ---------------------------------------------------------------------------


def test_sink_constants_match_the_router() -> None:
    assert route_target("setup", "") == PUNDIT_SINK
    assert route_target("mechanic", "") == MECHANICS_SINK
    assert route_target("claim", "NOVEL") == THESIS_SINK


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------


def test_load_ledger_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_ledger(tmp_path / "nope.json") == []


def test_append_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    assert load_ledger(path) == [_item()]


def test_append_routed_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    append_routed(path, [_item()])
    assert len(load_ledger(path)) == 1


def test_append_routed_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    assert [p.name for p in tmp_path.iterdir()] == ["routed.json"]


# A silent reset would re-queue everything ever routed, which is precisely the
# duplicate flood this module exists to prevent.
def test_malformed_ledger_aborts_loudly(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="malformed"):
        load_ledger(path)


def test_unrecognized_ledger_shape_aborts_loudly(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    path.write_text(json.dumps({"version": 999, "items": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="version"):
        load_ledger(path)


def test_remove_routed_lets_a_deleted_row_be_re_routed(tmp_path: Path) -> None:
    path = tmp_path / "routed.json"
    append_routed(path, [_item()])
    remove_routed(path, source_id="vid1", item_ts=0.0, sink=PUNDIT_SINK)
    assert load_ledger(path) == []


# ---------------------------------------------------------------------------
# Key collisions. Keying on source_id ALONE would silently eat real rows: the first
# 美股峰哥 ingest produced four Stream C calls from a single upload.
# ---------------------------------------------------------------------------


def test_same_video_different_timestamps_are_distinct_items() -> None:
    ledger = [_item("MZWWTIuaVH8", 375.0)]
    assert is_routed(ledger, "MZWWTIuaVH8", 375.0, PUNDIT_SINK)
    assert not is_routed(ledger, "MZWWTIuaVH8", 566.0, PUNDIT_SINK)


def test_same_moment_routed_to_different_sinks_are_distinct_items() -> None:
    ledger = [_item("vid1", 30.0, PUNDIT_SINK)]
    assert not is_routed(ledger, "vid1", 30.0, THESIS_SINK)


def test_item_ts_is_matched_at_whole_seconds() -> None:
    # Float noise off the transcript must not split one item into two ledger rows.
    ledger = [_item("vid1", 375.0)]
    assert is_routed(ledger, "vid1", 375.04, PUNDIT_SINK)


# Divergence 5 (wifey-only), and the reason it is truncation rather than the parent's
# rounding. Caught on the live ledger: an item offset arrives from two places that
# disagree, and only one of the two reconciliations actually works.
def test_a_rows_float_ts_and_its_url_offset_produce_one_key() -> None:
    """The live row for `MZWWTIuaVH8` carries `ts: 566.81` and persists the deep link
    `&t=566s`. Seeded from the first and checked against the second, the ledger has to
    block — `round(566.81)` is 567, which would silently let the re-route through."""
    ledger = [_item("MZWWTIuaVH8", 566.81)]
    assert is_routed(ledger, "MZWWTIuaVH8", 566.0, PUNDIT_SINK)


def test_is_routed_touches_no_filesystem(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    is_routed([_item()], "vid1", 0.0, PUNDIT_SINK)
    assert list(tmp_path.iterdir()) == before


# ---------------------------------------------------------------------------
# Level normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nasdaq sweep of 28k then reclaim", {28000.0}),
        ("trail to 1,982.10", {1982.1}),
        ("bid ~7436", {7436.0}),
        ("supply $60,946.8", {60946.8}),
        ("range 180.50-205.00", {180.5, 205.0}),
    ],
)
def test_normalize_levels_reads_price_forms(text: str, expected: set[float]) -> None:
    assert normalize_levels(text) == expected


# Two negatives inherited from the parent that would otherwise make every entry look
# like every other entry.
def test_iso_dates_are_not_price_levels() -> None:
    assert normalize_levels("| H-001 | 2026-07-31 | @someone on SPY |") == frozenset()


def test_fib_ratios_and_small_numbers_are_not_price_levels() -> None:
    assert (
        normalize_levels("0.618 retrace, 14-bar RSI, 4h chart, 2x size") == frozenset()
    )


# ---------------------------------------------------------------------------
# Divergence 2 (wifey-only): equity levels share the year band, so the parent's ISO
# strip is not enough. Same defect PR #128 found in the scorer; same shared fix.
# ---------------------------------------------------------------------------


def test_a_month_anchored_year_is_not_a_price_level() -> None:
    assert normalize_levels("a 10-20% drawdown starting Aug-Sep 2026") == frozenset()


def test_a_percentage_is_not_a_price_level() -> None:
    assert normalize_levels("NVDA is up 150% off the April low") == frozenset()


def test_an_index_level_survives_the_year_strip() -> None:
    """The positive control: stripping the year must not eat the level beside it."""
    assert normalize_levels("the S&P topped at 7,436 in Aug 2026") == {7436.0}


# Divergence 4 (wifey-only). `MONTH_YEAR_RE` only reaches a year adjacent to a month,
# and the live inbox writes "2022 topped mid-Aug" — two words in between. Before this
# rule, 3 of 28 real Stream A pairs flagged and every one was a year artifact.
def test_a_bare_year_is_not_a_price_level() -> None:
    text = "2022 topped mid-Aug (-19%), 2018 topped Sep (-20%), 2014 topped Sep"
    assert normalize_levels(text) == frozenset()


def test_a_separated_level_inside_the_year_band_survives() -> None:
    """The rule keys on how the number is written, not how big it is: a level in the
    band carries a separator or a decimal, a year never does."""
    assert normalize_levels("trail to 1,982.10 and add at 2,050") == {1982.1, 2050.0}


def test_a_bare_level_inside_the_year_band_is_lost() -> None:
    """The stated cost of divergence 4, asserted so it stays a decision rather than a
    surprise. Affordable while ^GSPC trades near 7,400 and almost no US single-name
    sits in the band; revisit if that stops being true."""
    assert normalize_levels("S&P tags 2050 then rolls") == frozenset()


# ---------------------------------------------------------------------------
# Divergence 3 (wifey-only): this fork's Stream A rows embed the source deep-link in
# a cell. Its `&t=124s` offset is not a price level and its host is not a shared term.
# ---------------------------------------------------------------------------


def test_a_deep_link_offset_is_not_a_price_level() -> None:
    text = "[@a](https://youtu.be/q0-LgUAyf-o?si=Tzpp22WFXWkF2O29&t=124s) NVDA 180.50"
    assert normalize_levels(text) == {180.5}


# ---------------------------------------------------------------------------
# Sink-shaped splitting
# ---------------------------------------------------------------------------

# Divergence 1 (wifey-only): the thesis inbox is a TABLE of `H-` rows plus a second
# "Dropped this batch" table — not the parent's run of `## ` sections. Splitting this
# on headings yields one blob, and every comparison silently scores against it.
_INBOX = """# Thesis inbox

Captured hypotheses from the research-ingestion pipeline.

| id | captured | source | claim | implied primitive | gap note | status |
| --- | --- | --- | --- | --- | --- | --- |
| H-001 | 2026-07-31 | [@benjaminjcowen](https://youtu.be/q0-LgUAyf-o?si=Tzpp22WFXWkF2O29&t=124s) | US equities follow a repeating midterm-election-year phase pattern, topping near 7,436 before a 10-20% drawdown into year-end. | 4-year political-cycle conditioning of index drawdown timing | Free data. | new |
| H-002 | 2026-07-31 | [@someone](https://x.com/someone/status/2072102057767735427) | The 2-year Treasury yield leads the Fed funds rate rather than following it. | short-rate spread as a macro regime input | Needs FRED. | new |

## Dropped this batch (recorded, not routed)

| source | claim | verdict | why |
| --- | --- | --- | --- |
| Gold @35.3 | Gold's drawdown is comparable to prior cycle lows | NOT-FALSIFIABLE | Backward-looking magnitude colour. |
"""


def test_split_thesis_inbox_on_table_rows() -> None:
    entries = split_entries(THESIS_SINK, _INBOX)
    assert len(entries) == 3
    assert all(e.startswith("|") for e in entries)
    assert "H-001" in entries[0]
    assert "H-002" in entries[1]


def test_split_thesis_inbox_drops_headers_and_separators() -> None:
    """Column names appear in every row at once, so leaving the header in would put
    `claim`, `status` and `verdict` into the term pool of every comparison."""
    joined = " ".join(split_entries(THESIS_SINK, _INBOX))
    assert "implied primitive" not in joined
    assert "---" not in joined


def test_split_thesis_inbox_keeps_the_dropped_table() -> None:
    """ "You already dropped this as NOT-FALSIFIABLE" is the more useful hit of the
    two, so the second table is in scope rather than skipped."""
    assert any("NOT-FALSIFIABLE" in e for e in split_entries(THESIS_SINK, _INBOX))


def test_split_mechanics_on_top_level_bullets_keeping_continuations() -> None:
    text = "# Mechanics\n\nintro\n\n- 2026-06-30 (@a): first rule\n  continued here\n- 2026-07-01 (@b): second rule\n"
    entries = split_entries(MECHANICS_SINK, text)
    assert len(entries) == 2
    assert "continued here" in entries[0]


def test_split_pundit_calls_one_entry_per_line() -> None:
    text = '{"author":"a"}\n\n{"author":"b"}\n'
    assert len(split_entries(PUNDIT_SINK, text)) == 2


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def test_find_similar_flags_a_restated_thesis() -> None:
    claim = (
        "US equities repeat a midterm-election-year phase pattern, topping near 7,436"
    )
    hits = find_similar(claim, THESIS_SINK, _INBOX)
    assert hits
    assert "H-001" in hits[0].excerpt
    assert 7436.0 in hits[0].shared_levels


def test_find_similar_ignores_an_unrelated_entry() -> None:
    claim = "unfilled opening gaps act as intraday magnets in a range regime"
    assert find_similar(claim, THESIS_SINK, _INBOX) == []


def test_find_similar_ignores_an_entry_sharing_only_its_deep_link() -> None:
    """The sharp edge of divergence 3. Every H-row carries a `youtu.be/…&t=<n>s`
    link, so without the URL strip a drafted row would share the level 124.0 — and
    that alone clears `_MIN_SCORE`, flagging two unrelated hypotheses as duplicates.
    """
    drafted_row = (
        "| H-009 | 2026-08-05 | [@x](https://youtu.be/aaaaaaaaaaa?t=124s) | "
        "bond auction tails widen into quarter end | primary-dealer takedown | new |"
    )
    assert find_similar(drafted_row, THESIS_SINK, _INBOX) == []


def test_find_similar_on_an_empty_sink_is_empty() -> None:
    assert find_similar("anything at all", THESIS_SINK, "") == []


# ---------------------------------------------------------------------------
# Divergences 6 and 7 (wifey-only). Both are the same underlying fact — a Stream A
# entry is a whole table row, not a sentence — and both were caught by running the
# ported code against the live inbox, where each produced a SILENT wrong answer.
# ---------------------------------------------------------------------------


def test_a_restatement_beats_the_length_of_the_entry_it_restates() -> None:
    """Divergence 6. The entry carries a long `gap note` column and the claim is one
    sentence, so jaccard scores this near-verbatim restatement at 1.86 and never fires.
    Containment over the shorter side reads it correctly."""
    inbox = (
        "| id | captured | claim | gap note | status |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| H-002 | 2026-07-31 | Gold shows the same midterm-year seasonal weakness, "
        "bottoming in the Jul-Oct window. | Same political-cycle primitive as H-001 on "
        "a different asset. Not covered by the cross-asset TSMOM sleeve, which holds "
        "GLD and SLV but tests trend speed rather than calendar-cycle conditioning. "
        "Free data already wired into the sleeve. | new |\n"
    )
    claim = "gold shows midterm-year seasonal weakness, bottoming in the Jul-Oct window"
    hits = find_similar(claim, THESIS_SINK, inbox)
    assert hits and hits[0].score >= 3.0


def test_a_verdict_stamp_is_not_shared_content() -> None:
    """Divergence 7. `ALREADY-TESTED` is stamped on every dropped row, so `already`
    and `tested` match unrelated entries before one word of claim overlaps — it
    carried all three remaining flags on the live inbox."""
    inbox = (
        "| source | claim | verdict | why |\n"
        "| --- | --- | --- | --- |\n"
        "| A | July has closed green every year since listing | ALREADY-TESTED | x |\n"
        "| B | Unfilled opening gaps act as intraday magnets | ALREADY-TESTED | y |\n"
    )
    assert (
        find_similar("bond auction tails widen into quarter end", THESIS_SINK, inbox)
        == []
    )
    entries = split_entries(THESIS_SINK, inbox)
    assert _score(entries[0], entries[1])[0] < 3.0


# Stream C's pass is scoped to one source, so on an absent sink there is nothing to
# compare — but the scope is still reported, because "no candidates" and "no pass"
# have to stay distinguishable in the digest.
def test_check_on_an_absent_stream_c_sink_still_reports_its_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(tmp_path / "absent.jsonl"),
            "--text",
            "7436",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["semantic_scope"] == "same-source"
    assert out["candidates"] == []


def test_check_reports_a_semantic_pass_on_stream_a(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "7436",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert json.loads(capsys.readouterr().out)["semantic_checked"] is True


def test_find_similar_respects_top_n() -> None:
    claim = (
        "midterm-election-year phase pattern topping near 7,436, and the 2-year "
        "Treasury yield leads the Fed funds rate too"
    )
    assert len(find_similar(claim, THESIS_SINK, _INBOX, top_n=1)) <= 1


# ---------------------------------------------------------------------------
# Stream C's same-source blind spot. The exemption is right ACROSS sources — two
# pundits making the same call are two real observations and pundit_score.py scores
# both authors — but it was applied WITHIN one video too, which is how an entry leg
# and a target leg of ONE position became two ledger rows.
# ---------------------------------------------------------------------------

_VIDEO_ID = "MZWWTIuaVH8"
_OTHER_VIDEO_ID = "zyx98765432"


def _call_row(
    *,
    video_id: str = _VIDEO_ID,
    ts: float = 0.0,
    symbol: str = "NVDA",
    direction: str = "long",
    entry: str = "180.50",
    stop: str = "172.00",
    target: str = "205.00",
    raw_quote_en: str = "",
) -> dict[str, Any]:
    return {
        "source": "youtube",
        "author": "@somepundit",
        "url": f"https://www.youtube.com/watch?v={video_id}&t={int(ts)}s",
        "ts": ts,
        "horizon": "swing",
        "confidence": "",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "raw_quote_en": raw_quote_en,
    }


# Deliberately NOT the same field values. Pass 2 extracts each leg from the moment it
# was spoken, so the entry leg carries no target and the target leg carries no stop —
# the shared entry price is the whole of the evidence that they are one position.
_ENTRY_LEG = _call_row(
    ts=375.0,
    target="",
    raw_quote_en="we are long NVDA from 180.50 with the stop under 172",
)
_TARGET_LEG = _call_row(
    ts=566.0,
    stop="",
    raw_quote_en="the first target for this NVDA long sits at 205",
)
_UNRELATED_CALL = _call_row(
    ts=780.0,
    symbol="AMD",
    direction="short",
    entry="165.00",
    stop="178.00",
    target="142.00",
    raw_quote_en="amd looks heavy into the 178 supply shelf",
)

# Two genuinely different NVDA longs from one upload — the rows this module exists to
# PROTECT. Same symbol, same direction, different trade; must not be collapsed.
_SWEEP_BUY = _call_row(
    ts=375.6,
    stop="",
    target="",
    entry="conditional add on a sweep below the 158.40 August low",
    raw_quote_en="if we sweep the august low i am adding down there",
)
_BREAKOUT_LONG = _call_row(
    ts=1046.2,
    stop="",
    entry="breakout long on confirmed acceptance above 210.00",
    target="240.00",
    raw_quote_en="reclaiming two hundred ten opens the path to new highs",
)


def _jsonl(*rows: dict[str, Any]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def test_stream_c_stays_exempt_without_a_source_id() -> None:
    """Back-compat: no source id means no same-source scope, so nothing is checked."""
    assert find_similar("NVDA long from 180.50", PUNDIT_SINK, _jsonl(_ENTRY_LEG)) == []


def test_an_unrecognized_sink_is_quietly_unscopeable_not_an_error() -> None:
    """Scoping needs entries that persist a URL. A sink that has none — including a
    typo'd `--sink` — returns [] as it always did, rather than raising out of the
    entry splitter."""
    assert find_similar("anything", "docs/plans/typo.md", "", source_id=_VIDEO_ID) == []
    assert semantic_scope("docs/plans/typo.md", _VIDEO_ID) == "none"


def test_stream_c_flags_a_same_source_restatement() -> None:
    hits = find_similar(
        "long 180.50 targeting 205.00",
        PUNDIT_SINK,
        _jsonl(_ENTRY_LEG),
        source_id=_VIDEO_ID,
    )
    assert hits
    assert 180.5 in hits[0].shared_levels


def test_stream_c_ignores_an_identical_call_from_a_different_source() -> None:
    """The cross-author exemption, unchanged: this is the case that must NOT fire."""
    other = _call_row(
        video_id=_OTHER_VIDEO_ID, ts=40.0, raw_quote_en="long NVDA from 180.50"
    )
    assert (
        find_similar(
            "long 180.50 targeting 205.00",
            PUNDIT_SINK,
            _jsonl(other),
            source_id=_VIDEO_ID,
        )
        == []
    )


def test_stream_c_scoring_ignores_schema_keys() -> None:
    """Two unrelated calls from ONE video must not match on shared JSON keys.

    Scoring the raw line makes every Stream C pair look alike — `source`, `author`,
    `symbol`, `direction`, `horizon`, `confidence` are terms in every row — which is
    why the sink read as uniformly self-similar in the parent's original calibration
    pass and the semantic layer was switched off for it wholesale.
    """
    assert (
        find_similar(
            "amd short into the 178 supply shelf",
            PUNDIT_SINK,
            _jsonl(_ENTRY_LEG),
            source_id=_VIDEO_ID,
        )
        == []
    )


# ---------------------------------------------------------------------------
# Intra-batch pairs. Every check in the review digest runs BEFORE approval, so when
# a video's items are checked none of them are on disk yet — the pair that shipped
# the defect is invisible to any sink-file comparison. It is only findable item-vs-item.
# ---------------------------------------------------------------------------


def test_two_legs_of_one_position_are_flagged() -> None:
    pairs = find_source_duplicate_pairs([_ENTRY_LEG, _TARGET_LEG])
    assert len(pairs) == 1
    assert (pairs[0].left_ts, pairs[0].right_ts) == (375.0, 566.0)
    assert 180.5 in pairs[0].shared_levels


def test_genuinely_different_calls_in_one_video_are_not_flagged() -> None:
    assert find_source_duplicate_pairs([_ENTRY_LEG, _UNRELATED_CALL]) == []


def test_two_distinct_trades_on_one_symbol_survive() -> None:
    """The adversarial negative: matching symbol AND direction, different trade."""
    assert find_source_duplicate_pairs([_SWEEP_BUY, _BREAKOUT_LONG]) == []


def test_pairs_needs_at_least_two_items() -> None:
    assert find_source_duplicate_pairs([]) == []
    assert find_source_duplicate_pairs([_ENTRY_LEG]) == []


def test_pairs_are_reported_once_not_in_both_orders() -> None:
    pairs = find_source_duplicate_pairs([_ENTRY_LEG, _TARGET_LEG, _UNRELATED_CALL])
    assert len(pairs) == 1


def test_pairs_cli_reads_an_items_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    items = tmp_path / "items.json"
    items.write_text(json.dumps([_ENTRY_LEG, _TARGET_LEG]), encoding="utf-8")
    main(["pairs", "--items", str(items)])
    out = json.loads(capsys.readouterr().out)
    assert len(out["pairs"]) == 1
    assert out["pairs"][0]["left_ts"] == 375.0


def test_check_reports_the_semantic_scope_it_actually_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`semantic_checked` alone can no longer describe Stream C — it is checked, but
    only against its own source. The digest has to be able to say which."""
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text(_jsonl(_ENTRY_LEG), encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            _VIDEO_ID,
            "--item-ts",
            "566",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "long 180.50 targeting 205.00",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["semantic_checked"] is True
    assert out["semantic_scope"] == "same-source"
    assert out["candidates"]


def test_check_reports_full_scope_on_stream_a(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    main(
        [
            "check",
            "--source-id",
            _VIDEO_ID,
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "7436",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert json.loads(capsys.readouterr().out)["semantic_scope"] == "all-entries"


# ---------------------------------------------------------------------------
# Seeding. Without this the identity layer ships blind to everything already routed —
# exactly the cached-post-re-routed case. Every URL form in this fork's live ledger
# is covered below, which is why its 10 rows seed unmodified.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://x.com/luckychartape/status/2072102057767735427",
            "2072102057767735427",
        ),
        ("https://twitter.com/a/status/123", "123"),
        ("https://www.youtube.com/watch?v=MZWWTIuaVH8&t=375s", "MZWWTIuaVH8"),
        ("https://youtu.be/TOKUXlPBhOs?si=UndD51H6MkgdxCAP&t=548s", "TOKUXlPBhOs"),
    ],
)
def test_parse_source_id_reads_every_url_shape(url: str, expected: str) -> None:
    assert parse_source_id(url) == expected


# Anything that is not ingested content has no re-route path, so it must never enter
# the routing ledger.
@pytest.mark.parametrize("url", ["ai-card://1783907021091-SPY", "", "nonsense"])
def test_parse_source_id_returns_none_for_non_ingested_urls(url: str) -> None:
    assert parse_source_id(url) is None


def test_seed_items_uses_the_rows_own_ts_so_it_matches_what_mark_would_write() -> None:
    rows = [
        {"url": "https://youtu.be/TOKUXlPBhOs?si=UndD51H6MkgdxCAP&t=548s", "ts": 548.0}
    ]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-08-05T00:00:00Z")
    assert items[0].source_id == "TOKUXlPBhOs"
    assert items[0].item_ts == 548.0


def test_seed_items_defaults_an_x_post_to_zero_ts() -> None:
    rows = [{"url": "https://x.com/a/status/99"}]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-08-05T00:00:00Z")
    assert items[0].item_ts == 0.0


def test_seed_items_skips_underivable_urls() -> None:
    rows = [{"url": "ai-card://x"}, {"url": "https://x.com/a/status/99"}]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-08-05T00:00:00Z")
    assert len(items) == 1


def test_seed_items_prefers_the_rows_own_ingest_time() -> None:
    rows = [
        {"url": "https://x.com/a/status/99", "ingested_ts_utc": "2026-07-01T00:00:00Z"}
    ]
    items = seed_items(rows, sink=PUNDIT_SINK, fallback_ts_utc="2026-08-05T00:00:00Z")
    assert items[0].routed_ts_utc == "2026-07-01T00:00:00Z"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_seed_writes_nothing_without_apply(tmp_path: Path) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    assert main(["seed", "--sink-path", str(sink), "--ledger", str(ledger)]) == 0
    assert not ledger.exists()


def test_seed_with_apply_populates_and_then_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    capsys.readouterr()
    main(
        [
            "check",
            "--source-id",
            "99",
            "--item-ts",
            "0",
            "--sink",
            PUNDIT_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "x",
            "--ledger",
            str(ledger),
        ]
    )
    assert json.loads(capsys.readouterr().out)["already_routed"] is True


# A count that ignores what's already in the ledger is exactly the kind of
# misleading number this module exists to stop.
def test_seed_dry_run_counts_only_what_is_actually_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text(
        '{"url":"https://x.com/a/status/99"}\n{"url":"https://x.com/a/status/100"}\n',
        encoding="utf-8",
    )
    ledger = tmp_path / "routed.json"
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    capsys.readouterr()
    main(["seed", "--sink-path", str(sink), "--ledger", str(ledger)])
    out = capsys.readouterr().out
    assert "0 new" in out
    assert "2 already" in out


def test_seed_is_idempotent(tmp_path: Path) -> None:
    sink = tmp_path / "pundit-calls.jsonl"
    sink.write_text('{"url":"https://x.com/a/status/99"}\n', encoding="utf-8")
    ledger = tmp_path / "routed.json"
    for _ in range(2):
        main(["seed", "--sink-path", str(sink), "--ledger", str(ledger), "--apply"])
    assert len(load_ledger(ledger)) == 1


def test_check_reports_not_routed_and_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = tmp_path / "thesis-inbox.md"
    sink.write_text(_INBOX, encoding="utf-8")
    rc = main(
        [
            "check",
            "--source-id",
            "vid1",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(sink),
            "--text",
            "US equities repeat a midterm-election-year phase pattern topping near 7,436",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["already_routed"] is False
    assert payload["candidates"][0]["shared_levels"] == [7436.0]


def test_check_reports_already_routed_after_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "routed.json"
    args = ["--source-id", "vid1", "--item-ts", "0", "--sink", PUNDIT_SINK]
    assert main(["mark", *args, "--ledger", str(ledger)]) == 0
    capsys.readouterr()
    assert main(["check", *args, "--ledger", str(ledger), "--text", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["already_routed"] is True


def test_mark_is_idempotent(tmp_path: Path) -> None:
    ledger = tmp_path / "routed.json"
    args = ["mark", "--source-id", "v", "--item-ts", "1", "--sink", PUNDIT_SINK]
    main([*args, "--ledger", str(ledger)])
    main([*args, "--ledger", str(ledger)])
    assert len(load_ledger(ledger)) == 1


def test_unmark_clears_the_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "routed.json"
    args = ["--source-id", "v", "--item-ts", "1", "--sink", PUNDIT_SINK]
    main(["mark", *args, "--ledger", str(ledger)])
    main(["unmark", *args, "--ledger", str(ledger)])
    capsys.readouterr()
    main(["check", *args, "--ledger", str(ledger), "--text", "x"])
    assert json.loads(capsys.readouterr().out)["already_routed"] is False


def test_check_with_a_missing_sink_file_reports_no_candidates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "check",
            "--source-id",
            "v",
            "--item-ts",
            "0",
            "--sink",
            THESIS_SINK,
            "--sink-path",
            str(tmp_path / "absent.md"),
            "--text",
            "midterm-election-year pattern",
            "--ledger",
            str(tmp_path / "routed.json"),
        ]
    )
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["candidates"] == []
