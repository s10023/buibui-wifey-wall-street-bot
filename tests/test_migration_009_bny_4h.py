"""Tests for migrations/009_purge_bny_4h_wrong_instrument.py (#468).

The deletion is irreversible from this provider, so the scope is what matters:
only BNY, only `4h`, only the cheap pre-seam band. BNY's own `1d` history holds
sub-$50 rows that are real (BK in 2018–2023), and another symbol's `4h` bars at
the same price must survive too.

Loaded by path because the module name starts with a digit.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.store import init_schema, upsert_ohlcv

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "009_purge_bny_4h_wrong_instrument.py"
)

_BAD_T = 1_770_399_000_000  # BNY 4h's last wrong-instrument bar, 2026-02-06
_GOOD_T = 1_779_456_600_000  # its first real bar, 2026-05-22
_STEP = 14_400_000


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_009", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bar(
    symbol: str, timeframe: str, open_time: int, close: float
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1_000.0,
    }


def _db(tmp_path: Path, extra: list[dict[str, object]] | None = None) -> str:
    path = str(tmp_path / "analytics.db")
    rows = [_bar("BNY", "4h", _BAD_T - i * _STEP, 10.2) for i in range(3)]
    rows += [_bar("BNY", "4h", _GOOD_T + i * _STEP, 140.6) for i in range(2)]
    rows += [_bar("BNY", "1d", _BAD_T, 30.0)]  # real BK-era band: must survive
    rows += [_bar("XYZ", "4h", _BAD_T, 10.2)]  # another symbol: must survive
    rows += extra or []
    conn = duckdb.connect(path)
    init_schema(conn)
    upsert_ohlcv(conn, pd.DataFrame(rows, columns=OHLCV_COLUMNS))
    conn.close()
    return path


def _rows(path: str) -> list[tuple[str, str, float]]:
    conn = duckdb.connect(path, read_only=True)
    try:
        return [
            (str(s), str(t), float(c))
            for s, t, c in conn.execute(
                "SELECT symbol, timeframe, close FROM ohlcv"
                " ORDER BY symbol, timeframe, open_time"
            ).fetchall()
        ]
    finally:
        conn.close()


class TestMigration009:
    def test_dry_run_counts_and_writes_nothing(self, tmp_path: Path) -> None:
        path = _db(tmp_path)
        before = _rows(path)
        assert _load().migrate(path, apply=False) == 3
        assert _rows(path) == before

    def test_apply_refuses_without_a_bak(self, tmp_path: Path) -> None:
        path = _db(tmp_path)
        with pytest.raises(SystemExit):
            _load().migrate(path, apply=True)

    def test_apply_deletes_only_the_wrong_band_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        path = _db(tmp_path)
        Path(path + ".bak").write_bytes(b"")
        mod = _load()
        assert mod.migrate(path, apply=True) == 3
        assert _rows(path) == [
            ("BNY", "1d", 30.0),
            ("BNY", "4h", 140.6),
            ("BNY", "4h", 140.6),
            ("XYZ", "4h", 10.2),
        ]
        assert mod.migrate(path, apply=True) == 0

    def test_overlapping_populations_refuse(self, tmp_path: Path) -> None:
        """A cheap bar after the seam means the rule no longer describes the data."""
        path = _db(tmp_path, extra=[_bar("BNY", "4h", _GOOD_T + 9 * _STEP, 10.2)])
        with pytest.raises(SystemExit):
            _load().migrate(path, apply=False)
