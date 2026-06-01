"""Tests for T6 live-parity conflict resolver runner restructure (PR-4b).

Covers the two runner-level helpers `_build_confidence_ratings_map` (loads
`confidence_ratings.avg_r` keyed for the resolver) and
`_resolve_conflicts_for_signals_map` (pools strategies per (symbol, tf) and
applies the lifted conflict resolver across them), plus the helper-widened
return type in `analytics/signal/gates.py::_apply_conflict_resolver`.

The pure conflict-resolver helper itself is covered by
`tests/test_signal_gates_conflict_resolver.py`.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_runner import (
    _build_confidence_ratings_map,
    _resolve_conflicts_for_signals_map,
)
from analytics.data_store import init_schema

# Match the runner's signals_map shape so mypy can verify in-place mutation.
type SignalsMap = dict[
    tuple[str, str, str], tuple[pd.DataFrame, pd.DataFrame, str | None]
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_confidence_ratings(
    conn: duckdb.DuckDBPyConnection,
    rows: list[tuple[str, str, str, str, float]],
) -> None:
    """rows: (config_name, strategy, tf, direction, avg_r)."""
    for cfg_name, strat, tf, direction, avg_r in rows:
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [cfg_name, strat, tf, direction, 3, avg_r, 0.5, 0, "off"],
        )


def _signals(
    open_times: list[int], directions: list[str], price: float = 100.0
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": open_times,
            "direction": directions,
            "reason": ["t"] * len(open_times),
            "price": [price] * len(open_times),
            "sl_price": [98.0] * len(open_times),
            "context": ["c"] * len(open_times),
            "low_volume": [False] * len(open_times),
            "tp_price": [104.0] * len(open_times),
        }
    )


def _ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [0, 3_600_000, 7_200_000, 10_800_000],
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [101.0, 102.0, 103.0, 104.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )


# ---------------------------------------------------------------------------
# _build_confidence_ratings_map
# ---------------------------------------------------------------------------


class TestBuildConfidenceRatingsMap:
    def test_gate_off_returns_none(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        cfg = BacktestSweepConfig(
            config_name="signal_watch",
            live_parity=LiveParityConfig(conflict_resolver=False),
        )
        assert _build_confidence_ratings_map(conn, cfg) is None

    def test_gate_on_empty_db_returns_empty_map(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        cfg = BacktestSweepConfig(
            config_name="signal_watch",
            live_parity=LiveParityConfig(conflict_resolver=True),
        )
        out = _build_confidence_ratings_map(conn, cfg)
        assert out == {}

    def test_gate_on_loads_directional_and_combined_rows(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _seed_confidence_ratings(
            conn,
            [
                ("signal_watch", "bos", "4h", "long", 0.32),
                ("signal_watch", "bos", "4h", "short", -0.14),
                ("signal_watch", "bos", "4h", "combined", 0.10),
                ("signal_watch", "engulfing", "4h", "long", 0.85),
            ],
        )
        cfg = BacktestSweepConfig(
            config_name="signal_watch",
            live_parity=LiveParityConfig(conflict_resolver=True),
        )
        out = _build_confidence_ratings_map(conn, cfg)
        assert out is not None
        # confidence_ratings.avg_r is DuckDB REAL (float32) — use approx.
        assert out[("bos", "4h", "long")] == pytest.approx(0.32)
        assert out[("bos", "4h", "short")] == pytest.approx(-0.14)
        assert out[("bos", "4h", "combined")] == pytest.approx(0.10)
        assert out[("engulfing", "4h", "long")] == pytest.approx(0.85)

    def test_gate_on_filters_by_config_name(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _seed_confidence_ratings(
            conn,
            [
                ("signal_watch", "bos", "4h", "long", 0.32),
                ("signal_watch_weekdays", "bos", "4h", "long", 0.99),
            ],
        )
        cfg = BacktestSweepConfig(
            config_name="signal_watch",
            live_parity=LiveParityConfig(conflict_resolver=True),
        )
        out = _build_confidence_ratings_map(conn, cfg)
        assert out is not None
        assert list(out.keys()) == [("bos", "4h", "long")]
        assert out[("bos", "4h", "long")] == pytest.approx(0.32)

    def test_gate_on_skips_null_avg_r(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Insert a row where avg_r is explicitly NULL.
        conn.execute(
            "INSERT INTO confidence_ratings "
            "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
            "updated_at_ms, day_filter) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
            ["signal_watch", "bos", "4h", "long", 3, 0, "off"],
        )
        cfg = BacktestSweepConfig(
            config_name="signal_watch",
            live_parity=LiveParityConfig(conflict_resolver=True),
        )
        out = _build_confidence_ratings_map(conn, cfg)
        assert out == {}

    def test_gate_on_with_missing_config_name_uses_empty_string(self) -> None:
        # Default-built cfg has config_name=None — falls back to "" so the
        # query is well-formed but matches nothing in production tables.
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _seed_confidence_ratings(
            conn,
            [("", "bos", "4h", "long", 0.5)],  # config_name "" (degenerate)
        )
        cfg = BacktestSweepConfig(
            live_parity=LiveParityConfig(conflict_resolver=True),
        )
        out = _build_confidence_ratings_map(conn, cfg)
        assert out is not None
        assert list(out.keys()) == [("bos", "4h", "long")]
        assert out[("bos", "4h", "long")] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _resolve_conflicts_for_signals_map
# ---------------------------------------------------------------------------


class TestResolveConflictsForSignalsMap:
    def test_none_ratings_map_is_noop(self) -> None:
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        before_bos = signals_map[("AAPL", "4h", "bos")][1].copy()
        before_eng = signals_map[("AAPL", "4h", "engulfing")][1].copy()
        _resolve_conflicts_for_signals_map(signals_map, None)
        # Untouched.
        pd.testing.assert_frame_equal(signals_map[("AAPL", "4h", "bos")][1], before_bos)
        pd.testing.assert_frame_equal(
            signals_map[("AAPL", "4h", "engulfing")][1], before_eng
        )

    def test_single_strategy_cell_unchanged(self) -> None:
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
        }
        before = signals_map[("AAPL", "4h", "bos")][1].copy()
        _resolve_conflicts_for_signals_map(signals_map, {("bos", "4h", "long"): 0.5})
        pd.testing.assert_frame_equal(signals_map[("AAPL", "4h", "bos")][1], before)

    def test_multi_strategy_no_candle_overlap_unchanged(self) -> None:
        # Both strategies fire — but on different candles. No moment with both
        # directions present, so no conflict resolution kicks in.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([7_200_000], ["short"]),
                None,
            ),
        }
        ratings = {
            ("bos", "4h", "long"): 0.3,
            ("engulfing", "4h", "short"): 0.9,  # higher, but different candle
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert signals_map[("AAPL", "4h", "bos")][1]["open_time"].tolist() == [
            3_600_000
        ]
        assert signals_map[("AAPL", "4h", "engulfing")][1]["open_time"].tolist() == [
            7_200_000
        ]

    def test_multi_strategy_opposing_same_candle_loser_dropped(self) -> None:
        # bos long vs engulfing short on the same candle — engulfing has the
        # higher avg_r, so bos long is dropped.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {
            ("bos", "4h", "long"): 0.10,
            ("engulfing", "4h", "short"): 0.50,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        # Loser dropped:
        assert signals_map[("AAPL", "4h", "bos")][1].empty
        # Winner kept:
        assert signals_map[("AAPL", "4h", "engulfing")][1]["open_time"].tolist() == [
            3_600_000
        ]

    def test_combined_fallback_used_when_directional_absent(self) -> None:
        # Directional rows missing → resolver falls back to 'combined' for
        # tiebreaker. bos combined=0.4, engulfing combined=0.1 → bos wins.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {
            ("bos", "4h", "combined"): 0.4,
            ("engulfing", "4h", "combined"): 0.1,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert signals_map[("AAPL", "4h", "bos")][1]["open_time"].tolist() == [
            3_600_000
        ]
        assert signals_map[("AAPL", "4h", "engulfing")][1].empty

    def test_tie_keeps_both_sides(self) -> None:
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {
            ("bos", "4h", "long"): 0.25,
            ("engulfing", "4h", "short"): 0.25,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert len(signals_map[("AAPL", "4h", "bos")][1]) == 1
        assert len(signals_map[("AAPL", "4h", "engulfing")][1]) == 1

    def test_missing_keys_default_to_zero(self) -> None:
        # bos rating absent (→ 0.0); engulfing has positive rating → engulfing wins.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {("engulfing", "4h", "short"): 0.10}
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert signals_map[("AAPL", "4h", "bos")][1].empty
        assert len(signals_map[("AAPL", "4h", "engulfing")][1]) == 1

    def test_distinct_symbols_or_tfs_do_not_interfere(self) -> None:
        # AAPL 4h has a conflict; MSFT 4h has its own independent conflict; the
        # resolver must NOT pool across symbols / TFs.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
            ("MSFT", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("MSFT", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {
            # AAPL: engulfing wins
            ("bos", "4h", "long"): 0.10,
            ("engulfing", "4h", "short"): 0.30,
            # MSFT inherits the same keys (resolver lookup is by strategy+tf+dir)
            # → engulfing wins on MSFT too. Verify both symbols resolve.
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert signals_map[("AAPL", "4h", "bos")][1].empty
        assert signals_map[("MSFT", "4h", "bos")][1].empty
        assert len(signals_map[("AAPL", "4h", "engulfing")][1]) == 1
        assert len(signals_map[("MSFT", "4h", "engulfing")][1]) == 1

    def test_independent_candles_each_resolved_separately(self) -> None:
        # Two candles: at t=3.6M bos long wins; at t=7.2M engulfing short wins.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000, 7_200_000], ["long", "long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000, 7_200_000], ["short", "short"]),
                None,
            ),
        }
        # Constant ratings → engulfing wins both candles.
        ratings = {
            ("bos", "4h", "long"): 0.10,
            ("engulfing", "4h", "short"): 0.40,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert signals_map[("AAPL", "4h", "bos")][1].empty
        assert signals_map[("AAPL", "4h", "engulfing")][1]["open_time"].tolist() == [
            3_600_000,
            7_200_000,
        ]

    def test_three_strategies_mixed_directions(self) -> None:
        # bos long, orb_breakout long, engulfing short on same candle.
        # Side max: long = max(bos=0.30, orb=0.50) = 0.50; short = engulfing=0.20.
        # Long wins → both long events kept, engulfing dropped.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "orb_breakout"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        ratings = {
            ("bos", "4h", "long"): 0.30,
            ("orb_breakout", "4h", "long"): 0.50,
            ("engulfing", "4h", "short"): 0.20,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        assert len(signals_map[("AAPL", "4h", "bos")][1]) == 1
        assert len(signals_map[("AAPL", "4h", "orb_breakout")][1]) == 1
        assert signals_map[("AAPL", "4h", "engulfing")][1].empty

    def test_signals_dtype_preserved_after_filter(self) -> None:
        # _events_to_df should preserve the original frame's columns/dtypes.
        signals_map: SignalsMap = {
            ("AAPL", "4h", "bos"): (
                _ohlcv(),
                _signals([3_600_000], ["long"]),
                None,
            ),
            ("AAPL", "4h", "engulfing"): (
                _ohlcv(),
                _signals([3_600_000], ["short"]),
                None,
            ),
        }
        original_cols = signals_map[("AAPL", "4h", "engulfing")][1].columns.tolist()
        ratings = {
            ("bos", "4h", "long"): 0.10,
            ("engulfing", "4h", "short"): 0.50,
        }
        _resolve_conflicts_for_signals_map(signals_map, ratings)
        # Winner frame retains all original columns.
        assert (
            signals_map[("AAPL", "4h", "engulfing")][1].columns.tolist()
            == original_cols
        )
        # Loser frame is empty but still has the original columns (matches
        # `signals.iloc[0:0]` semantics from `_events_to_df`).
        assert signals_map[("AAPL", "4h", "bos")][1].columns.tolist() == original_cols
