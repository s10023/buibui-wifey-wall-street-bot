"""`analytics/store/migration_bak.py`: every migration refuses a stale `.bak`.

The guard used to test existence only, so a 14-day-old copy (2026-09-03) and a
same-size pre-009 copy (2026-10-09) would both have passed. Pinned here: a
missing, stale, same-size-but-different, WAL-pending or unreadable `.bak` is
refused; a fresh copy passes, including after the no-write opens the guard must
tolerate; and every `migrations/0*.py` refuses a stale copy before it writes.
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import duckdb
import pytest

from analytics.store import migration_bak
from analytics.store.migration_bak import bak_problem, require_fresh_bak

_MIGRATIONS = sorted((Path(__file__).parent.parent / "migrations").glob("0*.py"))


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "a.db"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE t AS SELECT range AS x FROM range(1000)")
    conn.close()
    return db


def _bak(db: Path) -> Path:
    return Path(f"{db}.bak")


def _write_after_copy(db: Path) -> None:
    shutil.copyfile(db, _bak(db))
    conn = duckdb.connect(str(db))
    conn.execute("INSERT INTO t SELECT range FROM range(1000, 200000)")
    conn.close()
    # Positive control: the write reached the file, so a pass would be a miss.
    assert db.read_bytes() != _bak(db).read_bytes()


class TestBakProblem:
    def test_missing_bak_is_refused(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        assert "not found" in (bak_problem(db) or "")

    def test_fresh_copy_passes(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        shutil.copyfile(db, _bak(db))
        assert bak_problem(db) is None
        assert bak_problem(str(db)) is None

    def test_no_write_opens_keep_a_copy_fresh(self, tmp_path: Path) -> None:
        """Byte equality is not too strict: reads and no-write opens leave it."""
        db = _db(tmp_path)
        shutil.copyfile(db, _bak(db))
        for read_only in (True, False):
            conn = duckdb.connect(str(db), read_only=read_only)
            conn.execute("SELECT count(*) FROM t").fetchone()
            conn.close()
        assert bak_problem(db) is None

    def test_copy_taken_before_a_write_is_refused(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _write_after_copy(db)
        assert "not a copy of the current DB" in (bak_problem(db) or "")

    def test_same_size_different_bytes_is_refused(self, tmp_path: Path) -> None:
        """The 2026-10-09 case: a stale copy whose size matched exactly."""
        db = _db(tmp_path)
        data = bytearray(db.read_bytes())
        at = len(data) // 2 + 12_345
        data[at] ^= 0xFF
        _bak(db).write_bytes(bytes(data))
        assert _bak(db).stat().st_size == db.stat().st_size
        assert bak_problem(db) == (
            f"{_bak(db)} is not a copy of the current DB "
            f"(first difference at byte {at:,})."
        )

    def test_a_pending_wal_is_refused(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        shutil.copyfile(db, _bak(db))
        Path(f"{db}.wal").write_bytes(b"x")
        assert ".wal exists" in (bak_problem(db) or "")

    def test_an_unreadable_db_is_refused(self, tmp_path: Path) -> None:
        """On Windows a DuckDB writer makes the file unreadable to anyone else."""
        db = _db(tmp_path)
        shutil.copyfile(db, _bak(db))
        with patch.object(
            migration_bak, "_first_difference", side_effect=PermissionError("locked")
        ):
            assert "cannot read" in (bak_problem(db) or "")


class TestRequireFreshBak:
    def test_refusal_exits_1_and_says_how_to_fix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = _db(tmp_path)
        _write_after_copy(db)
        with pytest.raises(SystemExit) as exc:
            require_fresh_bak(db)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert out.startswith("Refusing to run: ")
        assert f"Copy-Item {db} {db}.bak -Force" in out

    def test_fresh_copy_returns(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        shutil.copyfile(db, _bak(db))
        require_fresh_bak(db)


def _load(script: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_{script.stem}", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEveryMigrationUsesTheGuard:
    def test_the_glob_found_the_scripts(self) -> None:
        assert len(_MIGRATIONS) >= 9

    @pytest.mark.parametrize("script", _MIGRATIONS, ids=lambda p: p.stem)
    def test_source_calls_the_shared_guard(self, script: Path) -> None:
        src = script.read_text(encoding="utf-8")
        assert "require_fresh_bak(db_path)" in src
        assert '.bak")' not in src, "a hand-rolled .bak check bypasses the guard"

    @pytest.mark.parametrize("script", _MIGRATIONS, ids=lambda p: p.stem)
    def test_apply_refuses_a_stale_bak_before_writing(
        self, script: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = _db(tmp_path)
        _write_after_copy(db)
        before = db.read_bytes()
        mod = _load(script)
        kwargs = (
            {"apply": True}
            if "apply" in inspect.signature(mod.migrate).parameters
            else {}
        )
        with pytest.raises(SystemExit) as exc:
            mod.migrate(str(db), **kwargs)
        assert exc.value.code == 1
        # The guard refused, not some later check in the script.
        assert "not a copy of the current DB" in capsys.readouterr().out
        assert db.read_bytes() == before
