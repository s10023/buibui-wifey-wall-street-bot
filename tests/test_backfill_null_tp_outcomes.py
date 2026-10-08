"""Tests for the retroactive NULL-tp outcome backfill tool.

Reconstructs SL/TP for already-fired `signal_alert_outcomes` rows that were
written with NULL tp_price (before the forward fix), so the existing
forward-walk resolver can score them. See
docs/superpowers/specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md.

Ported from parent PR #410. Reconciled to the equity surface: AAPL / 4h, and
the wifey ohlcv schema has no `taker_buy_volume` column.
"""

import duckdb
import pandas as pd

from analytics.store import init_schema, upsert_signal_outcome
from tools.backfill_null_tp_outcomes import reconstruct_null_outcomes

_STEP = 4 * 3_600_000  # 4h in ms


def _insert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    rows: list[dict[str, float | int]],
) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": r["open_time"],
                "open": r.get("open", r["close"]),
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 1.0,
            }
            for r in rows
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _insert_null_outcome(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str = "sig1",
    symbol: str = "AAPL",
    tf: str = "4h",
    strategy: str = "bos",
    direction: str = "long",
    candle_ts_ms: int = _STEP,
    entry: float = 100.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "tf": tf,
            "strategy": strategy,
            "direction": direction,
            "fired_at_ms": candle_ts_ms,
            "candle_ts_ms": candle_ts_ms,
            "entry_price": entry,
            "sl_price": None,  # the hole: written with NULL
            "tp_price": None,
            "rr_ratio": None,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )


def _row(conn: duckdb.DuckDBPyConnection, signal_id: str) -> dict[str, object]:
    cols = ["sl_price", "tp_price", "rr_ratio", "outcome", "outcome_r"]
    r = conn.execute(
        f"SELECT {', '.join(cols)} FROM signal_alert_outcomes WHERE signal_id = ?",
        [signal_id],
    ).fetchone()
    assert r is not None
    return dict(zip(cols, r, strict=True))


def _conn_with_null_row() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _insert_null_outcome(conn)
    # Candle one bar after the signal whose high clears the pct-fallback TP (104).
    _insert_ohlcv(
        conn,
        "AAPL",
        "4h",
        [
            {
                "open_time": 2 * _STEP,
                "open": 100.0,
                "high": 105.0,
                "low": 99.5,
                "close": 104.5,
            }
        ],
    )
    return conn


class TestReconstructNullOutcomes:
    def test_dry_run_does_not_write(self) -> None:
        conn = _conn_with_null_row()
        result = reconstruct_null_outcomes(
            conn,
            sl_pct=0.02,
            tp_r=2.0,
            min_sl_pct=0.0,
            strategy_params=None,
            apply=False,
        )
        assert result["null_rows"] == 1
        assert result["reconstructed"] == 1
        row = _row(conn, "sig1")
        assert row["sl_price"] is None  # unchanged on dry-run
        assert row["tp_price"] is None
        assert row["outcome"] is None

    def test_apply_reconstructs_and_resolves(self) -> None:
        conn = _conn_with_null_row()
        result = reconstruct_null_outcomes(
            conn,
            sl_pct=0.02,
            tp_r=2.0,
            min_sl_pct=0.0,
            strategy_params=None,
            apply=True,
            now_ms=4 * _STEP,
        )
        assert result["reconstructed"] == 1
        row = _row(conn, "sig1")
        # pct fallback: entry 100, sl_pct 0.02 → sl 98, tp 104, rr 2.0
        assert row["sl_price"] == 98.0
        assert row["tp_price"] == 104.0
        assert row["rr_ratio"] == 2.0
        # high 105 ≥ tp 104 → resolves win
        assert row["outcome"] == "win"
        assert row["outcome_r"] is not None

    def test_idempotent_second_run_finds_nothing(self) -> None:
        conn = _conn_with_null_row()
        reconstruct_null_outcomes(
            conn,
            sl_pct=0.02,
            tp_r=2.0,
            min_sl_pct=0.0,
            strategy_params=None,
            apply=True,
            now_ms=4 * _STEP,
        )
        again = reconstruct_null_outcomes(
            conn,
            sl_pct=0.02,
            tp_r=2.0,
            min_sl_pct=0.0,
            strategy_params=None,
            apply=True,
            now_ms=4 * _STEP,
        )
        # row already has tp_price set + resolved → not a NULL candidate
        assert again["null_rows"] == 0
        assert again["reconstructed"] == 0

    def test_skips_rows_without_entry_price(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "no_entry",
                "symbol": "AAPL",
                "tf": "4h",
                "strategy": "bos",
                "direction": "long",
                "fired_at_ms": _STEP,
                "candle_ts_ms": _STEP,
                "entry_price": None,
                "sl_price": None,
                "tp_price": None,
                "rr_ratio": None,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        result = reconstruct_null_outcomes(
            conn,
            sl_pct=0.02,
            tp_r=2.0,
            min_sl_pct=0.0,
            strategy_params=None,
            apply=True,
            now_ms=4 * _STEP,
        )
        assert result["reconstructed"] == 0
        assert result["skipped_no_entry"] == 1
