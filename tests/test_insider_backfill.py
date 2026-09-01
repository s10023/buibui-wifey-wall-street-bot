"""Symbol selection for the H-024 Form 4 backfill.

The pilot path is the one that decides whether phase 2 starts, so how it picks
its symbols is load-bearing rather than a convenience. ``--limit`` alone takes
the HEAD of `config/universe.json`, which is grouped by sector: measured
2026-09-01, ``--limit 15`` is fifteen Information Technology mega-caps carrying
18,549 Form 4 documents between them. That is both the slowest slice in the
universe and the one least able to support the >=80% parse-coverage gate, since
malformed filings concentrate among small and older filers the head excludes.
"""

from __future__ import annotations

import pytest

from analytics.insider.form4 import Form4Filing
from tools.insider_backfill import sample_filings, select_symbols

UNIVERSE = [f"S{i:03d}" for i in range(100)]


def test_stride_spreads_the_pick_across_the_file() -> None:
    picked = select_symbols(UNIVERSE, stride=33, limit=3)
    assert picked == ["S000", "S033", "S066"]


def test_limit_alone_takes_the_head() -> None:
    """Characterizes the trap rather than endorsing it.

    This is the shape that produced a sector-pure pilot; the test exists so a
    future change to the default cannot happen silently.
    """
    assert select_symbols(UNIVERSE, limit=3) == ["S000", "S001", "S002"]


def test_stride_is_applied_before_limit() -> None:
    # 3 members taken every 10th — not the first 3 then strided, which would
    # collapse back onto the head and silently reinstate the defect.
    assert select_symbols(UNIVERSE, stride=10, limit=3) == ["S000", "S010", "S020"]


def test_no_limit_returns_the_whole_strided_series() -> None:
    assert len(select_symbols(UNIVERSE, stride=25)) == 4


def test_default_is_the_untouched_universe() -> None:
    assert select_symbols(UNIVERSE) == UNIVERSE


def test_explicit_symbols_override_stride_and_limit() -> None:
    assert select_symbols(UNIVERSE, symbols="S007,S042", stride=33, limit=1) == [
        "S007",
        "S042",
    ]


def test_explicit_symbols_are_normalised() -> None:
    assert select_symbols(UNIVERSE, symbols=" s007 , s042 ") == ["S007", "S042"]


def test_an_unknown_explicit_symbol_refuses() -> None:
    """A typo must not silently shrink the run to the symbols that did match."""
    with pytest.raises(ValueError, match="NOPE"):
        select_symbols(UNIVERSE, symbols="S007,NOPE")


@pytest.mark.parametrize("stride", [0, -1])
def test_a_non_positive_stride_refuses(stride: int) -> None:
    # `universe[::0]` raises deep in the slice; `[::-1]` silently REVERSES the
    # universe, which would run and look fine. Refuse both at the boundary.
    with pytest.raises(ValueError, match="stride"):
        select_symbols(UNIVERSE, stride=stride)


def _filings(n: int) -> list[Form4Filing]:
    """Newest-first, the order `collect_filings` returns."""
    return [
        Form4Filing(
            accession=f"a{i:04d}",
            filing_date=f"{2026 - i // 100:04d}-01-01",
            acceptance="",
            primary_document=f"d{i}.xml",
        )
        for i in range(n)
    ]


class TestSampleFilings:
    """The per-symbol cap must not become a head sample.

    `collect_filings` is newest-first and recent filings are the most uniform, so
    a head cap measures the easy end and reports parse coverage that is too high —
    against a floor whose whole job is to catch the documents it would drop.
    """

    def test_a_cap_spreads_across_the_whole_range(self) -> None:
        picked = sample_filings(_filings(100), 5)
        assert [f.accession for f in picked] == [
            "a0000",
            "a0020",
            "a0040",
            "a0060",
            "a0080",
        ]

    def test_a_cap_is_not_the_head(self) -> None:
        """The positive control: a head sample would be the first 5 accessions."""
        picked = sample_filings(_filings(100), 5)
        assert [f.accession for f in picked] != [f"a{i:04d}" for i in range(5)]
        # and it must actually reach the oldest decile
        assert int(picked[-1].accession[1:]) >= 80

    def test_a_cap_at_or_above_the_count_is_a_no_op(self) -> None:
        f = _filings(7)
        assert sample_filings(f, 7) == f
        assert sample_filings(f, 99) == f

    def test_no_cap_returns_everything(self) -> None:
        f = _filings(7)
        assert sample_filings(f, None) == f

    def test_an_empty_list_is_safe(self) -> None:
        assert sample_filings([], 5) == []

    def test_a_cap_returns_exactly_that_many(self) -> None:
        assert len(sample_filings(_filings(1000), 40)) == 40

    def test_a_non_positive_cap_refuses(self) -> None:
        with pytest.raises(ValueError, match="max-filings-per-symbol"):
            sample_filings(_filings(10), 0)
