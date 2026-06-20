"""Tests for analytics.exits.mfe_mae (exit spec §2 MFE/MAE diagnostic).

Covers the conservative intrabar conventions per cohort (loss excludes the
exit bar's favorable extreme; win clamps post-TP overshoot; expired counts
every in-window bar), short-direction sign handling, the zero floor,
zero-risk / missing-OHLCV row skips, (symbol, tf) batching, and the cohort
aggregation (reach fractions + min_n gate).

Equity port of the parent's tests/test_mfe_mae.py (PR #433): the only fixture
change is dropping `taker_buy_volume` (not a column in the wifey ohlcv table).
"""

import duckdb
import pandas as pd
import pytest

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.store import init_schema, upsert_signal_outcome

_HOUR = 3_600_000


def _insert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    rows: list[dict[str, int | float]],
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


def _insert_resolved(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    outcome: str,
    filled_at_ms: int,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    strategy: str = "fvg",
    direction: str = "long",
    candle_ts_ms: int = 0,
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    rr: float = 2.0,
    outcome_r: float = 0.0,
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
            "sl_price": sl,
            "tp_price": tp,
            "rr_ratio": rr,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )
    conn.execute(
        "UPDATE signal_alert_outcomes "
        "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
        "WHERE signal_id = ?",
        [outcome, outcome_r, filled_at_ms, signal_id],
    )


class TestExcursionConventions:
    def test_long_loss_excludes_exit_bar_favorable_extreme(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # risk = 5. Bar 1: fav 0.8R. Bar 2 (SL exit): high would be 2.4R but
        # must NOT count; low 94 gaps past the stop -> MAE 1.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 103.0},
                {"open_time": 2 * _HOUR, "high": 112.0, "low": 94.0, "close": 96.0},
            ],
        )
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=2 * _HOUR)
        exc = compute_excursions(conn)
        assert len(exc) == 1
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(0.8)
        assert row["mae_r"] == pytest.approx(1.2)
        assert row["bars_held"] == 2

    def test_long_win_clamps_exit_bar_overshoot(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Bar 1: fav 0.6R, adv 0.2R. Bar 2 (TP exit): high 118 = 3.6R
        # overshoot -> clamped to rr 2.0; its adv 0.8R counts.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 99.0, "close": 102.0},
                {"open_time": 2 * _HOUR, "high": 118.0, "low": 96.0, "close": 115.0},
            ],
        )
        _insert_resolved(conn, signal_id="s1", outcome="win", filled_at_ms=2 * _HOUR)
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(2.0)
        assert row["mae_r"] == pytest.approx(0.8)

    def test_short_direction_signs(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Short, entry 100, sl 105 (risk 5). Bar 1: low 96 -> fav 0.8R,
        # high 103 -> adv 0.6R. Bar 2: low 94 -> fav 1.2R, high 101 -> 0.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 96.0, "close": 98.0},
                {"open_time": 2 * _HOUR, "high": 101.0, "low": 94.0, "close": 95.0},
            ],
        )
        _insert_resolved(
            conn,
            signal_id="s1",
            outcome="expired",
            filled_at_ms=2 * _HOUR,
            direction="short",
            sl=105.0,
            tp=90.0,
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.2)
        assert row["mae_r"] == pytest.approx(0.6)

    def test_expired_counts_all_bars_both_extremes(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Peak fav on bar 2 (high 107 -> 1.4R), worst adv on bar 3
        # (low 96 -> 0.8R) — the LAST bar still counts for expired.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 107.0, "low": 100.0, "close": 105.0},
                {"open_time": 3 * _HOUR, "high": 104.0, "low": 96.0, "close": 97.0},
            ],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="expired", filled_at_ms=3 * _HOUR
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.4)
        assert row["mae_r"] == pytest.approx(0.8)
        assert row["bars_held"] == 3

    def test_mfe_floors_at_zero_on_first_bar_stopout(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Single-bar loss: no prior bars -> MFE 0.0 (never favorable).
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 99.0, "low": 94.0, "close": 95.0}],
        )
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR)
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == 0.0
        assert row["mae_r"] == pytest.approx(1.2)


class TestComputeExcursionsRobustness:
    def test_zero_risk_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR, sl=100.0
        )
        assert compute_excursions(conn).empty

    def test_missing_ohlcv_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR)
        assert compute_excursions(conn).empty

    def test_open_rows_excluded(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "s-open",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "fvg",
                "direction": "long",
                "fired_at_ms": 0,
                "candle_ts_ms": 0,
                "entry_price": 100.0,
                "sl_price": 95.0,
                "tp_price": 110.0,
                "rr_ratio": 2.0,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        assert compute_excursions(conn).empty

    def test_batches_multiple_symbol_tf_groups(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 99.0}],
        )
        _insert_ohlcv(
            conn,
            "ETHUSDT",
            "4h",
            [{"open_time": 4 * _HOUR, "high": 12.0, "low": 9.7, "close": 11.0}],
        )
        _insert_resolved(conn, signal_id="b1", outcome="expired", filled_at_ms=_HOUR)
        _insert_resolved(
            conn,
            signal_id="e1",
            outcome="expired",
            filled_at_ms=4 * _HOUR,
            symbol="ETHUSDT",
            tf="4h",
            entry=10.0,
            sl=9.5,
            tp=11.0,
        )
        exc = compute_excursions(conn)
        assert set(exc["symbol"]) == {"BTCUSDT", "ETHUSDT"}
        eth = exc[exc["symbol"] == "ETHUSDT"].iloc[0]
        assert eth["mfe_r"] == pytest.approx(4.0)  # (12-10)/0.5
        assert eth["mae_r"] == pytest.approx(0.6)  # (10-9.7)/0.5


class TestAggregateCohorts:
    def _exc_df(self) -> pd.DataFrame:
        rows = [
            # 4 expired in one cell: MFE 0.2 / 0.6 / 1.5 / 0.1
            ("e1", "expired", 0.2, 0.3),
            ("e2", "expired", 0.6, 0.5),
            ("e3", "expired", 1.5, 0.4),
            ("e4", "expired", 0.1, 1.1),
            # 1 loss in same cell (filtered out at min_n=2)
            ("l1", "loss", 0.8, 1.2),
        ]
        return pd.DataFrame(
            [
                {
                    "signal_id": sid,
                    "symbol": "BTCUSDT",
                    "tf": "1h",
                    "strategy": "fvg",
                    "direction": "long",
                    "outcome": outcome,
                    "outcome_r": -0.1,
                    "rr_ratio": 2.0,
                    "mfe_r": mfe,
                    "mae_r": mae,
                    "bars_held": 10,
                }
                for sid, outcome, mfe, mae in rows
            ]
        )

    def test_reach_fractions_and_min_n(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), min_n=2)
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["outcome"] == "expired"
        assert row["n"] == 4
        assert row["reach_05"] == pytest.approx(0.5)
        assert row["reach_10"] == pytest.approx(0.25)
        assert row["tp_r_p50"] == pytest.approx(2.0)

    def test_overall_rollup_groups_by_outcome_only(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), by=(), min_n=1)
        assert set(agg["outcome"]) == {"expired", "loss"}
        assert "strategy" not in agg.columns

    def test_empty_input_returns_empty(self) -> None:
        assert aggregate_cohorts(pd.DataFrame(), min_n=1).empty
