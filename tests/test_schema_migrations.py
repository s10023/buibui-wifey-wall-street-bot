"""Tests for the one-shot migrations' applied-state record (#467).

`schema_migrations` exists because a purge that never ran is invisible: 007 sat
unapplied on this host's DB until #445. These pin the four claims the record
rests on: every numbered script writes it, the seed covers exactly the scripts
that exist, the freshness leg reads the record rather than the predicates, and
002 refuses the re-run its own predicate can no longer judge.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.store import init_schema, upsert_ohlcv
from analytics.store.schema_migrations import (
    is_recorded,
    migration_id,
    record_applied,
    recorded_ids,
    script_ids,
    table_present,
)
from tools import session_digest as sd
from tools.freshness_check import (
    OhlcvReport,
    read_unrecorded_migrations,
    render_ohlcv,
    unrecorded,
)

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _load(name: str) -> ModuleType:
    path = _MIGRATIONS / name
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(path: Path) -> list[tuple[str, object, int | None, str | None]]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return [
            (str(m), a, r, n)
            for m, a, r, n in conn.execute(
                "SELECT migration_id, applied_at_utc, rows_affected, note"
                " FROM schema_migrations ORDER BY migration_id, recorded_at_utc"
            ).fetchall()
        ]
    finally:
        conn.close()


class TestRecord:
    def test_init_schema_creates_the_table(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        assert table_present(conn)

    def test_an_absent_table_reads_as_nothing_recorded(self) -> None:
        conn = duckdb.connect(":memory:")
        assert recorded_ids(conn) == frozenset()
        assert not is_recorded(conn, "migrations/007_x.py")

    def test_a_record_keys_on_the_file_stem(self) -> None:
        conn = duckdb.connect(":memory:")
        record_applied(conn, "/any/where/migrations/007_purge_x.py", 34, "n")
        assert recorded_ids(conn) == {"007_purge_x"}
        assert migration_id("migrations/007_purge_x.py") == "007_purge_x"

    def test_a_rerun_appends_rather_than_overwriting(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        conn = duckdb.connect(str(db))
        record_applied(conn, "007_x.py", 34)
        record_applied(conn, "007_x.py", 0)
        conn.close()
        assert [r[2] for r in _rows(db)] == [34, 0]

    def test_a_seeded_row_has_no_applied_time(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        conn = duckdb.connect(str(db))
        record_applied(conn, "001_x.py", None, "moot", applied=False)
        conn.close()
        ((mid, applied_at, rows, note),) = _rows(db)
        assert (mid, applied_at, rows, note) == ("001_x", None, None, "moot")


class TestEveryMigrationRecords:
    """The convention the freshness leg depends on: a script that never writes
    the record would read as unapplied forever, however often it ran."""

    @pytest.mark.parametrize(
        "path", sorted(_MIGRATIONS.glob("0*.py")), ids=lambda p: p.stem
    )
    def test_the_apply_path_calls_record_applied(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        assert re.search(r"\brecord_applied\(", source), (
            f"{path.name} never calls record_applied; see analytics/store/"
            "schema_migrations.py (#467)"
        )

    def test_the_glob_is_not_vacuous(self) -> None:
        assert len(script_ids(_MIGRATIONS)) >= 9


class TestAnApplyWritesTheRecord:
    """Positive control on a real script: the channel the leg reads is written."""

    def _db(self, tmp_path: Path, closes: list[float]) -> Path:
        db = tmp_path / "analytics.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        bars = [
            {
                "symbol": "BNY",
                "timeframe": "4h",
                "open_time": 1_770_399_000_000 - i * 14_400_000,
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1.0,
            }
            for i, c in enumerate(closes)
        ]
        if bars:
            upsert_ohlcv(conn, pd.DataFrame(bars, columns=OHLCV_COLUMNS))
        conn.close()
        shutil.copy(db, str(db) + ".bak")
        return db

    def test_009_records_its_deletions(self, tmp_path: Path) -> None:
        db = self._db(tmp_path, [10.2, 10.3])
        assert _load("009_purge_bny_4h_wrong_instrument.py").migrate(str(db), True) == 2
        assert [(r[0], r[2]) for r in _rows(db)] == [
            ("009_purge_bny_4h_wrong_instrument", 2)
        ]

    def test_009_records_a_zero_row_apply(self, tmp_path: Path) -> None:
        db = self._db(tmp_path, [])
        _load("009_purge_bny_4h_wrong_instrument.py").migrate(str(db), True)
        assert [r[2] for r in _rows(db)] == [0]

    def test_a_dry_run_records_nothing(self, tmp_path: Path) -> None:
        db = self._db(tmp_path, [10.2])
        _load("009_purge_bny_4h_wrong_instrument.py").migrate(str(db), False)
        assert _rows(db) == []


class TestSeed:
    def test_the_seed_covers_exactly_the_scripts_that_exist(self) -> None:
        seed = _load("seed_applied_2026_10_09.py")
        assert [row[0] for row in seed.SEED] == script_ids(_MIGRATIONS)

    def test_dry_run_writes_nothing_and_apply_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        seed = _load("seed_applied_2026_10_09.py")
        db = tmp_path / "analytics.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        record_applied(conn, "009_purge_bny_4h_wrong_instrument.py", 816)
        conn.close()

        assert len(seed.seed(str(db), False)) == len(seed.SEED) - 1
        assert len(_rows(db)) == 1

        assert len(seed.seed(str(db), True)) == len(seed.SEED) - 1
        assert seed.seed(str(db), True) == []
        rows = _rows(db)
        assert len(rows) == len(seed.SEED)
        seeded = [r for r in rows if r[0] != "009_purge_bny_4h_wrong_instrument"]
        assert all(r[1] is None and r[3] for r in seeded), "seeded rows carry a note"


class TestMigration002RefusesARerun:
    def test_a_recorded_002_refuses_apply(self, tmp_path: Path) -> None:
        db = tmp_path / "analytics.db"
        conn = duckdb.connect(str(db))
        init_schema(conn)
        record_applied(conn, "002_adr_threshold_executed.py", None, "seeded")
        conn.close()
        shutil.copy(db, str(db) + ".bak")
        mod = _load("002_adr_threshold_executed.py")
        with pytest.raises(SystemExit) as exc:
            mod.migrate(str(db), True)
        assert exc.value.code == 1
        assert len(_rows(db)) == 1, "the refusal must write nothing"


class TestFreshnessLeg:
    def test_unrecorded_keeps_script_order(self) -> None:
        assert unrecorded(["001_a", "002_b", "003_c"], frozenset({"002_b"})) == (
            "001_a",
            "003_c",
        )

    def test_the_reader_reads_the_record_not_the_predicates(
        self, tmp_path: Path
    ) -> None:
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        for name in ("001_a.py", "002_b.py", "seed_x.py"):
            (mdir / name).write_text("", encoding="utf-8")
        db = tmp_path / "analytics.db"
        conn = duckdb.connect(str(db))
        record_applied(conn, "001_a.py", 0)
        conn.close()
        assert read_unrecorded_migrations(db, mdir) == ("002_b",)

    def test_a_db_without_the_table_lists_every_script(self, tmp_path: Path) -> None:
        mdir = tmp_path / "migrations"
        mdir.mkdir()
        (mdir / "001_a.py").write_text("", encoding="utf-8")
        db = tmp_path / "analytics.db"
        duckdb.connect(str(db)).close()
        assert read_unrecorded_migrations(db, mdir) == ("001_a",)

    def test_an_absent_db_reads_none_not_clean(self, tmp_path: Path) -> None:
        assert read_unrecorded_migrations(tmp_path / "nope.db", _MIGRATIONS) is None

    def test_it_renders_but_never_fails_the_leg(self) -> None:
        report = OhlcvReport(
            [], 13, 0, None, None, unrecorded_migrations=("007_purge_avb",)
        )
        assert report.ok
        text = render_ohlcv(report)
        assert "UNRECORDED MIGRATION" in text and "007_purge_avb" in text

    def test_nothing_unrecorded_renders_no_section(self) -> None:
        for value in (None, ()):
            report = OhlcvReport([], 13, 0, None, None, unrecorded_migrations=value)
            assert "UNRECORDED" not in render_ohlcv(report)


class TestDigest:
    def _ohlcv(self) -> OhlcvReport:
        return OhlcvReport([], 13, 0, None, None)

    def test_an_unrecorded_migration_is_AMBER_and_named(self) -> None:
        report = replace(
            self._ohlcv(),
            unrecorded_migrations=("007_purge_avb_wrong_instrument_tail",),
        )
        (f,) = sd.freshness_findings(None, report)
        assert f.level == "AMBER"
        assert "007" in f.detail

    def test_unread_or_complete_is_silent(self) -> None:
        for value in (None, ()):
            report = replace(self._ohlcv(), unrecorded_migrations=value)
            assert sd.freshness_findings(None, report) == []
