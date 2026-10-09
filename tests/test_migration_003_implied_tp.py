"""Tests for migrations/003_outcome_r_implied_tp.py.

The migration rewrites two columns with different rules — `rr_ratio` on every
divergent row, `outcome_r` on resolved WINS only — so the two ways it can fail
silently are (a) crediting a loss or an expired row, whose R never came from
`rr_ratio`, and (b) not being idempotent, which would compound the correction on
a second run. Both are pinned here. The `.bak` refusal is pinned too: it is the
only thing standing between a fat-fingered `--apply` and an unrecoverable ledger.

Loaded by path because the module name starts with a digit.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

from analytics.store import init_schema, upsert_signal_outcome

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "003_outcome_r_implied_tp.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_003", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    init_schema(conn)
    rows = [
        # (signal_id, direction, entry, sl, tp, rr, outcome, outcome_r)
        # Structural TP nearer than declared → over-credited win.
        ("win_struct", "long", 100.0, 95.0, 110.0, 5.0, "win", 5.0),
        # Structural TP further than declared → under-credited win.
        ("win_far", "long", 100.0, 95.0, 125.0, 2.0, "win", 2.0),
        # pct fallback: tp == entry + sl_dist*rr, so nothing to correct.
        ("win_pct", "long", 100.0, 95.0, 115.0, 3.0, "win", 3.0),
        # Divergent, but a loss books -1.0 — outcome_r must NOT move.
        ("loss_struct", "long", 100.0, 95.0, 110.0, 5.0, "loss", -1.0),
        # Divergent, but an expired row marks to market — outcome_r must NOT move.
        ("exp_struct", "long", 100.0, 95.0, 110.0, 5.0, "expired", 0.6),
        # Divergent and still open — rr_ratio corrected, outcome_r stays NULL.
        ("open_struct", "long", 100.0, 95.0, 110.0, 5.0, None, None),
    ]
    for sid, direction, entry, sl, tp, rr, outcome, outcome_r in rows:
        upsert_signal_outcome(
            conn,
            {
                "signal_id": sid,
                "symbol": "AAPL",
                "tf": "1d",
                "strategy": "fvg",
                "direction": direction,
                "fired_at_ms": 0,
                "candle_ts_ms": 0,
                "entry_price": entry,
                "sl_price": sl,
                "tp_price": tp,
                "rr_ratio": rr,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        if outcome is not None:
            conn.execute(
                "UPDATE signal_alert_outcomes SET outcome = ?, outcome_r = ? "
                "WHERE signal_id = ?",
                [outcome, outcome_r, sid],
            )
    conn.close()


def _read(db_path: Path) -> dict[str, tuple[float, float | None]]:
    conn = duckdb.connect(str(db_path), read_only=True)
    out = {
        str(r[0]): (float(r[1]), None if r[2] is None else float(r[2]))
        for r in conn.execute(
            "SELECT signal_id, rr_ratio, outcome_r FROM signal_alert_outcomes"
        ).fetchall()
    }
    conn.close()
    return out


class TestMigration003:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed(db)
        before = _read(db)
        _load().migrate(str(db), False)
        assert _read(db) == before

    def test_apply_refuses_without_a_bak(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed(db)
        before = _read(db)
        with pytest.raises(SystemExit):
            _load().migrate(str(db), True)
        assert _read(db) == before

    def test_apply_corrects_wins_and_targets_only(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed(db)
        shutil.copy(db, str(db) + ".bak")
        _load().migrate(str(db), True)
        after = _read(db)

        # Wins: both columns move, in BOTH directions.
        assert after["win_struct"][0] == pytest.approx(2.0)
        assert after["win_struct"][1] == pytest.approx(2.0)
        assert after["win_far"][0] == pytest.approx(5.0)
        assert after["win_far"][1] == pytest.approx(5.0)

        # Agreeing row: untouched.
        assert after["win_pct"] == (pytest.approx(3.0), pytest.approx(3.0))

        # Non-win rows: the TARGET is corrected, the CREDIT is not — a loss books
        # -1.0 and an expired row marks to market, neither from rr_ratio.
        assert after["loss_struct"][0] == pytest.approx(2.0)
        assert after["loss_struct"][1] == pytest.approx(-1.0)
        assert after["exp_struct"][0] == pytest.approx(2.0)
        assert after["exp_struct"][1] == pytest.approx(0.6)

        # Open row: target corrected so it resolves correctly later, credit NULL.
        assert after["open_struct"][0] == pytest.approx(2.0)
        assert after["open_struct"][1] is None

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """Idempotent — otherwise a re-run would compound the correction."""
        db = tmp_path / "a.db"
        _seed(db)
        shutil.copy(db, str(db) + ".bak")
        mod = _load()
        mod.migrate(str(db), True)
        once = _read(db)
        shutil.copy(db, str(db) + ".bak")  # the first apply staled it
        mod.migrate(str(db), True)
        assert _read(db) == once
