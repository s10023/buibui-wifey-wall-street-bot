"""Tests for migrations/004_live_ledger_net_of_cost.py.

The migration restates resolved `outcome_r` from gross to net. Its two silent
failure modes are the ones pinned here:

(a) **Not idempotent.** The guard is `outcome_cost_r IS NULL` rather than a date,
    so a second run must find nothing. If it did not, every re-run would charge
    the ledger again and the error would compound invisibly — there is no
    independent record of gross to check against once the column is written.

(b) **Charging a row it cannot price.** A row without geometry has no R to
    charge against, and stamping `0.0` there would assert a measured zero. It
    must stay NULL, which is also what keeps it visible to a later run.

The `.bak` refusal is pinned too: it is the only thing between a fat-fingered
`--apply` and an unrecoverable ledger.

`test_the_live_configs_agree_on_one_cost_basis` is not a unit test of the
migration so much as a standing check on the repo: `signal_alert_outcomes` has
no column recording which config produced a row, so the migration is only
well-defined while both configs price identically. The day that stops being true
this fails, which is the point.

Loaded by path because the module name starts with a digit.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import duckdb
import pandas as pd
import pytest

from analytics.store import init_schema, upsert_signal_outcome

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "004_live_ledger_net_of_cost.py"
)

_DAY = 86_400_000
_GROSS_WIN = 2.0
_GROSS_LOSS = -1.0


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_004", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path: Path, *, with_ungeometric_row: bool = False) -> None:
    """One resolved win, one resolved loss, both on the gross basis."""
    conn = duckdb.connect(str(db_path))
    init_schema(conn)

    bars = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "open_time": i * _DAY,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1_000_000.0,
            }
            for i in range(6)
        ]
    )
    conn.register("_bars", bars)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _bars")
    conn.unregister("_bars")

    rows = [
        ("win-row", "long", _GROSS_WIN, "win"),
        ("loss-row", "short", _GROSS_LOSS, "loss"),
    ]
    for signal_id, direction, outcome_r, outcome in rows:
        upsert_signal_outcome(
            conn,
            {
                "signal_id": signal_id,
                "symbol": "AAPL",
                "tf": "1d",
                "strategy": "fvg",
                "direction": direction,
                "fired_at_ms": _DAY,
                "candle_ts_ms": _DAY,
                "entry_price": 100.0,
                "sl_price": 95.0,
                "tp_price": 110.0,
                "rr_ratio": 2.0,
                "outcome": outcome,
                "outcome_r": outcome_r,
                "outcome_filled_at_ms": 3 * _DAY,
            },
        )

    if with_ungeometric_row:
        # Resolved, but no entry/sl — cost in R is undefined without the
        # geometry that defines R.
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "no-geometry",
                "symbol": "AAPL",
                "tf": "1d",
                "strategy": "fvg",
                "direction": "long",
                "fired_at_ms": _DAY,
                "candle_ts_ms": _DAY,
                "outcome": "expired",
                "outcome_r": 0.5,
                "outcome_filled_at_ms": 3 * _DAY,
            },
        )
    conn.close()


def _read(db_path: Path) -> dict[str, tuple[float | None, float | None]]:
    conn = duckdb.connect(str(db_path), read_only=True)
    out = {
        str(r[0]): (r[1], r[2])
        for r in conn.execute(
            "SELECT signal_id, outcome_r, outcome_cost_r FROM signal_alert_outcomes"
        ).fetchall()
    }
    conn.close()
    return out


class TestMigration004:
    def test_the_live_configs_agree_on_one_cost_basis(self) -> None:
        """Both configs must price identically — rows cannot be attributed."""
        mod = _load()
        cost_model, _fee_pct = mod._resolve_cost_model(mod.LIVE_CONFIGS)
        # The shared base enables the equity stack; if this is ever None the
        # migration would refuse to run rather than stamp unpriced zeros.
        assert cost_model is not None

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

    def test_apply_charges_both_wins_and_losses(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _seed(db)
        shutil.copy(db, str(db) + ".bak")
        _load().migrate(str(db), True)

        after = _read(db)
        for signal_id, gross in (("win-row", _GROSS_WIN), ("loss-row", _GROSS_LOSS)):
            net, cost = after[signal_id]
            assert cost is not None and cost > 0.0, signal_id
            # Net is strictly worse than gross in BOTH directions — a loss is
            # charged too, so -1.0 must not survive as a flat -1.0.
            assert net is not None and net < gross, signal_id
            # Gross stays recoverable; that is what the second column buys.
            assert net + cost == pytest.approx(gross), signal_id

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """Compounding here would be undetectable — gross is not stored."""
        db = tmp_path / "a.db"
        _seed(db)
        shutil.copy(db, str(db) + ".bak")
        mod = _load()
        mod.migrate(str(db), True)
        once = _read(db)
        shutil.copy(db, str(db) + ".bak")  # the first apply staled it
        mod.migrate(str(db), True)
        assert _read(db) == once

    def test_a_row_without_geometry_stays_null_not_zero(self, tmp_path: Path) -> None:
        """NULL means unpriced; 0.0 would claim a measured zero and hide it."""
        db = tmp_path / "a.db"
        _seed(db, with_ungeometric_row=True)
        shutil.copy(db, str(db) + ".bak")
        _load().migrate(str(db), True)

        net, cost = _read(db)["no-geometry"]
        assert cost is None
        assert net == pytest.approx(0.5)
