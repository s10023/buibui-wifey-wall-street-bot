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

import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from analytics.insider.form4 import Form4Filing, ParseOutcome
from analytics.store.insider import completed_symbols
from tools.insider_backfill import main, sample_filings, select_symbols

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


class TestResumeAndMarkers:
    """What a run records about itself, and what a resume therefore skips.

    Drives ``main`` with the network stubbed at the ``tools.insider_backfill``
    namespace, because the marker decision lives in the run loop rather than in
    any extractable pure unit.
    """

    SINCE = "2015-01-01"

    @staticmethod
    def _filing(acc: str) -> Form4Filing:
        return Form4Filing(
            accession=acc,
            primary_document="doc.xml",
            filing_date="2020-01-02",
            acceptance="2020-01-02T18:00:00",
        )

    def _wire(
        self,
        monkeypatch: pytest.MonkeyPatch,
        universe: list[str],
        *,
        failing: set[str] = frozenset(),  # type: ignore[assignment]
    ) -> None:
        """Two filings per symbol; every fetch for a symbol in ``failing`` errors."""
        import tools.insider_backfill as ib

        monkeypatch.setattr(
            ib,
            "load_research_universe",
            lambda: SimpleNamespace(stocks=lambda: universe),
        )
        monkeypatch.setattr(ib, "fetch_company_tickers", dict)
        monkeypatch.setattr(ib, "ticker_to_cik", lambda _t, sym: f"CIK{sym}")
        monkeypatch.setattr(ib, "fetch_submissions", lambda _cik: {})
        monkeypatch.setattr(
            ib,
            "collect_filings",
            lambda _subs, _since: [self._filing("a1"), self._filing("a2")],
        )
        monkeypatch.setattr(ib, "raw_document_name", lambda d: d)

        def fetch(cik: str, _acc: str, _doc: str) -> bytes:
            if cik.removeprefix("CIK") in failing:
                raise urllib.error.URLError("offline")
            return b"<xml/>"

        monkeypatch.setattr(ib, "fetch_archive_document", fetch)
        monkeypatch.setattr(
            ib,
            "parse_form4",
            lambda _xml: ParseOutcome(transactions=[], failures=[]),
        )

    def _run(self, db: str, *extra: str) -> int:
        return main(["--db", db, "--since", self.SINCE, *extra])

    def test_a_clean_symbol_is_marked_complete(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db = str(tmp_path / "t.db")
        self._wire(monkeypatch, ["AAA", "BBB"])
        assert self._run(db) == 0
        c = duckdb.connect(db)
        assert completed_symbols(c, self.SINCE) == {"AAA", "BBB"}

    def test_a_symbol_whose_fetches_FAILED_is_NOT_marked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The whole point. An interrupted symbol must be retried, not assumed
        done — the failure this guards is silent incompleteness, not a crash."""
        db = str(tmp_path / "t.db")
        self._wire(monkeypatch, ["AAA", "BBB"], failing={"BBB"})
        self._run(db)
        c = duckdb.connect(db)
        assert completed_symbols(c, self.SINCE) == {"AAA"}

    def test_resume_skips_only_the_marked_symbol(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        db = str(tmp_path / "t.db")
        self._wire(monkeypatch, ["AAA", "BBB"], failing={"BBB"})
        self._run(db)
        # second pass, now with the network healthy
        self._wire(monkeypatch, ["AAA", "BBB"])
        capsys.readouterr()
        assert self._run(db, "--resume") == 0
        out = capsys.readouterr().out
        assert "1 symbol(s) already complete, 1 to go" in out
        c = duckdb.connect(db)
        assert completed_symbols(c, self.SINCE) == {"AAA", "BBB"}

    def test_parse_failures_do_NOT_deny_a_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A code-M option exercise carries no price and reports a failure on
        every run. Counting those as errors would deny the symbol a marker
        forever and re-fetch it on each resume — resume that never resumes."""
        import tools.insider_backfill as ib

        db = str(tmp_path / "t.db")
        self._wire(monkeypatch, ["AAA"])
        monkeypatch.setattr(
            ib,
            "parse_form4",
            lambda _xml: ParseOutcome(
                transactions=[], failures=["txn 0 [code M]: missing price"]
            ),
        )
        self._run(db)
        c = duckdb.connect(db)
        assert completed_symbols(c, self.SINCE) == {"AAA"}

    def test_a_sustained_outage_ABORTS_rather_than_grinding(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
    ) -> None:
        """Without this the run walks the whole universe recording empty results
        and still prints a coverage figure."""
        db = str(tmp_path / "t.db")
        universe = [f"S{i:02d}" for i in range(20)]
        self._wire(monkeypatch, universe, failing=set(universe))
        assert self._run(db, "--max-consecutive-failures", "3") == 1
        assert "ABORTED" in capsys.readouterr().out
        c = duckdb.connect(db)
        assert completed_symbols(c, self.SINCE) == set()

    def test_a_healthy_run_does_not_abort(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Positive control for the breaker: it is not tripping on everything."""
        db = str(tmp_path / "t.db")
        universe = [f"S{i:02d}" for i in range(20)]
        self._wire(monkeypatch, universe)
        assert self._run(db, "--max-consecutive-failures", "3") == 0
