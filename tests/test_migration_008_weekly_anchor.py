"""Tests for migrations/008_realign_off_monday_weekly_bars.py (#323).

The failure that matters is irreversible: deleting off-Monday rows for a symbol
the provider no longer serves. So the migration fetches before it deletes, and
that order is pinned here alongside the replacement itself, the untouched
Monday-only series and the `.bak` refusal.

Loaded by path because the module name starts with a digit.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import OHLCV_COLUMNS
from analytics.store import init_schema

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "008_realign_off_monday_weekly_bars.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_008", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bars(symbol: str, first: str, n: int = 4) -> pd.DataFrame:
    stamps = pd.date_range(f"{first}T05:00:00", periods=n, freq="7D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": "1wk",
            "open_time": stamps.values.astype("datetime64[ms]").astype("int64"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000.0,
        }
    )[OHLCV_COLUMNS]


def _seed(db: Path) -> None:
    conn = duckdb.connect(str(db))
    init_schema(conn)
    for frame in (
        _bars("ABBV", "2018-01-02"),  # Tuesday-anchored, provider serves it
        _bars("SATS", "2018-01-02"),  # Tuesday-anchored, provider serves nothing
        _bars("AAPL", "2018-01-01"),  # Monday-anchored, must not be touched
    ):
        conn.register("f", frame)
        conn.execute("INSERT INTO ohlcv SELECT * FROM f")
        conn.unregister("f")
    conn.close()
    (db.parent / (db.name + ".bak")).write_bytes(b"")


def _weekdays(db: Path, symbol: str) -> list[int]:
    conn = duckdb.connect(str(db), read_only=True)
    rows = conn.execute(
        "SELECT dayofweek(to_timestamp(open_time / 1000) AT TIME ZONE 'UTC') "
        "FROM ohlcv WHERE symbol = ? AND timeframe = '1wk' ORDER BY open_time",
        [symbol],
    ).fetchall()
    conn.close()
    return [int(r[0]) for r in rows]


def _fake_fetch(symbol: str, interval: str, start_ms: int, **_: Any) -> pd.DataFrame:
    if symbol == "SATS":
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return _bars(symbol, "2018-01-08")


def test_apply_replaces_off_monday_rows_and_keeps_unserved_ones(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    mod = _load()
    with patch.object(mod, "fetch_bars", side_effect=_fake_fetch) as fb:
        out = mod.migrate(str(db), apply=True)
    assert out == {"realigned": ["ABBV"], "kept": ["SATS"]}
    assert {c.args[0] for c in fb.call_args_list} == {"ABBV", "SATS"}
    assert _weekdays(db, "ABBV") == [1, 1, 1, 1]
    assert _weekdays(db, "SATS") == [2, 2, 2, 2]
    assert _weekdays(db, "AAPL") == [1, 1, 1, 1]


def test_a_failed_fetch_keeps_the_rows(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    mod = _load()
    with patch.object(mod, "fetch_bars", side_effect=ValueError("404")):
        out = mod.migrate(str(db), apply=True)
    assert out["realigned"] == []
    assert _weekdays(db, "ABBV") == [2, 2, 2, 2]


def test_a_second_run_finds_only_the_unserved_symbol(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    mod = _load()
    with patch.object(mod, "fetch_bars", side_effect=_fake_fetch):
        mod.migrate(str(db), apply=True)
        again = mod.migrate(str(db), apply=True)
    assert again == {"realigned": [], "kept": ["SATS"]}


def test_dry_run_writes_and_fetches_nothing(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    mod = _load()
    with patch.object(mod, "fetch_bars") as fb:
        mod.migrate(str(db), apply=False)
    fb.assert_not_called()
    assert _weekdays(db, "ABBV") == [2, 2, 2, 2]


def test_apply_refuses_without_a_bak(tmp_path: Path) -> None:
    db = tmp_path / "a.db"
    _seed(db)
    (tmp_path / "a.db.bak").unlink()
    with pytest.raises(SystemExit):
        _load().migrate(str(db), apply=True)
