"""Tests for analytics/recalibrate_lib.py."""

import textwrap
from pathlib import Path

import duckdb
import pandas as pd

from analytics.data_store import (
    get_directional_confidence_ratings,
    init_schema,
)
from analytics.recalibrate_lib import (
    compute_directional_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    win_rate_to_stars,
    write_confidence_to_db,
    write_confidence_to_source,
)

# ---------------------------------------------------------------------------
# win_rate_to_stars — boundary values
# ---------------------------------------------------------------------------


class TestWinRateToStars:
    def test_negative_avg_r_returns_1_star(self) -> None:
        assert win_rate_to_stars(-0.5, 20) == 1

    def test_zero_avg_r_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.0, 20) == 2

    def test_boundary_0_2_exclusive_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.19, 20) == 2

    def test_boundary_0_2_inclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.2, 20) == 3

    def test_mid_range_3_stars(self) -> None:
        assert win_rate_to_stars(0.35, 20) == 3

    def test_boundary_0_5_exclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.499, 20) == 3

    def test_boundary_0_5_inclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.5, 20) == 4

    def test_boundary_0_9_exclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.89, 20) == 4

    def test_boundary_0_9_inclusive_returns_5_stars(self) -> None:
        assert win_rate_to_stars(0.9, 20) == 5

    def test_high_avg_r_returns_5_stars(self) -> None:
        assert win_rate_to_stars(1.5, 20) == 5

    def test_insufficient_trades_returns_none(self) -> None:
        assert win_rate_to_stars(0.8, 5, min_trades=10) is None

    def test_exactly_min_trades_returns_rating(self) -> None:
        assert win_rate_to_stars(0.8, 10, min_trades=10) == 4

    def test_custom_min_trades(self) -> None:
        assert win_rate_to_stars(0.5, 3, min_trades=3) == 4
        assert win_rate_to_stars(0.5, 2, min_trades=3) is None


# ---------------------------------------------------------------------------
# compute_recalibrated_ratings — in-memory DuckDB
# ---------------------------------------------------------------------------


def _seed_backtest_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Insert sample backtest_runs rows for testing."""
    rows = [
        # bos: avg_r=0.6 over 30 closed trades on 4h → 4★
        {
            "run_id": "aaa",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "bos",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 30,
            "closed_trades": 30,
            "win_count": 18,
            "loss_count": 12,
            "win_rate": 0.6,
            "avg_r": 0.6,
            "total_r": 18.0,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        # fvg: avg_r=-0.1 over 20 closed trades on 1h → 1★
        {
            "run_id": "bbb",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fvg",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 20,
            "closed_trades": 20,
            "win_count": 8,
            "loss_count": 12,
            "win_rate": 0.4,
            "avg_r": -0.1,
            "total_r": -2.0,
            "max_drawdown_r": 4.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        # pin_bar: only 5 trades on 4h — below default min_trades=10 → excluded
        {
            "run_id": "ccc",
            "symbol": "ETHUSDT",
            "timeframe": "4h",
            "strategy": "pin_bar",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0,
            "day_filter": "off",
            "total_signals": 5,
            "closed_trades": 5,
            "win_count": 4,
            "loss_count": 1,
            "win_rate": 0.8,
            "avg_r": 1.2,
            "total_r": 6.0,
            "max_drawdown_r": 0.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        # engulfing: 4h=0.7 (4★), 1h=-0.2 (1★) — different ratings per TF
        {
            "run_id": "ddd",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "engulfing",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 15,
            "closed_trades": 15,
            "win_count": 10,
            "loss_count": 5,
            "win_rate": 0.667,
            "avg_r": 0.7,
            "total_r": 10.5,
            "max_drawdown_r": 2.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        {
            "run_id": "eee",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "engulfing",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "total_signals": 12,
            "closed_trades": 12,
            "win_count": 4,
            "loss_count": 8,
            "win_rate": 0.333,
            "avg_r": -0.2,
            "total_r": -2.4,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
    ]
    conn.executemany(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        [
            [
                r["run_id"],
                r["symbol"],
                r["timeframe"],
                r["strategy"],
                r["data_start_ms"],
                r["data_end_ms"],
                r["days"],
                r["sl_pct"],
                r["tp_r"],
                r["fee_pct"],
                r["day_filter"],
                r["total_signals"],
                r["closed_trades"],
                r["win_count"],
                r["loss_count"],
                r["win_rate"],
                r["avg_r"],
                r["total_r"],
                r["max_drawdown_r"],
                r["run_at_ms"],
                r["sweep_id"],
            ]
            for r in rows
        ],
    )


class TestComputeRecalibratedRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty_dict(self) -> None:
        conn = self._make_conn()
        result = compute_recalibrated_ratings(conn)
        conn.close()
        assert result == {}

    def test_bos_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # bos avg_r=0.6 on 4h → 4★
        assert result["bos"] == {"4h": 4}

    def test_fvg_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # fvg avg_r=-0.1 on 1h → 1★
        assert result["fvg"] == {"1h": 1}

    def test_insufficient_trades_excluded(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # pin_bar has only 5 trades — should not appear
        assert "pin_bar" not in result

    def test_custom_min_trades_includes_pin_bar(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=5)
        conn.close()
        # pin_bar avg_r=1.2 on 4h → 5★ when min_trades=5
        assert result["pin_bar"] == {"4h": 5}

    def test_per_tf_divergence(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # engulfing: 4h=4★, 1h=1★ — different per TF
        assert result["engulfing"]["4h"] == 4
        assert result["engulfing"]["1h"] == 1

    def test_returns_only_strategies_with_data(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        assert set(result.keys()) == {"bos", "fvg", "engulfing"}


# ---------------------------------------------------------------------------
# format_recalibration_report
# ---------------------------------------------------------------------------


class TestFormatRecalibrationReport:
    def test_returns_non_empty_string(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
        win_rates = pd.DataFrame(
            [
                {
                    "strategy": "bos",
                    "timeframe": "4h",
                    "total_trades": 30,
                    "win_rate": 0.6,
                    "avg_r": 0.6,
                },
                {
                    "strategy": "fvg",
                    "timeframe": "1h",
                    "total_trades": 20,
                    "win_rate": 0.4,
                    "avg_r": -0.1,
                },
            ]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert len(report) > 0

    def test_contains_strategy_names(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "bos" in report
        assert "fvg" in report

    def test_shows_change_marker_for_changed_strategy(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report

    def test_shows_no_data_for_missing_new_rating(self) -> None:
        old: dict[str, dict[str, int] | int] = {"pin_bar": 2}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "no data" in report

    def test_unchanged_strategy_shows_equals_marker(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 3}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "=" in report

    def test_empty_new_ratings_all_no_data(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "No data" in report or "no data" in report

    def test_per_tf_dict_old_ratings_resolved_correctly(self) -> None:
        # old has per-TF dict; new has different rating for 4h
        old: dict[str, dict[str, int] | int] = {"engulfing": {"default": 1, "4h": 3}}
        new: dict[str, dict[str, int]] = {"engulfing": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report


# ---------------------------------------------------------------------------
# write_confidence_to_source — patches indicators_lib.py source in-place
# ---------------------------------------------------------------------------

_FAKE_SOURCE = textwrap.dedent("""\
    STRATEGY_REGISTRY: dict[str, StrategySpec] = {
        "fvg": StrategySpec(
            name="fvg",
            description="Fair Value Gap.",
            confidence=4,
        ),
        "bos": StrategySpec(
            name="bos",
            description="Break of Structure.",
            confidence=3,
        ),
        "pin_bar": StrategySpec(
            name="pin_bar",
            description="Pin Bar.",
            confidence=2,
        ),
    }
""")


class TestWriteConfidenceToSource:
    def test_patches_single_strategy_int(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5}, src)
        assert patched == ["fvg"]
        assert "confidence=5" in src.read_text()

    def test_patches_multiple_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5, "bos": 1}, src)
        assert set(patched) == {"fvg", "bos"}
        content = src.read_text()
        assert "confidence=5" in content
        assert "confidence=1" in content

    def test_patches_per_tf_dict(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": {"default": 1, "4h": 4}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 1' in content
        assert '"4h": 4' in content

    def test_patches_dict_over_existing_dict(self, tmp_path: Path) -> None:
        # source already has a dict confidence; patch replaces it
        source = textwrap.dedent("""\
            "fvg": StrategySpec(
                name="fvg",
                confidence={"default": 1, "4h": 3},
            ),
        """)
        src = tmp_path / "indicators_lib.py"
        src.write_text(source)
        patched = write_confidence_to_source({"fvg": {"default": 2, "4h": 5}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 2' in content
        assert '"4h": 5' in content

    def test_unknown_strategy_not_in_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"nonexistent": 3}, src)
        assert patched == []
        assert src.read_text() == _FAKE_SOURCE  # file unchanged

    def test_same_value_still_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"bos": 3}, src)
        assert "bos" in patched  # regex matched and wrote

    def test_does_not_corrupt_other_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        write_confidence_to_source({"fvg": 5}, src)
        content = src.read_text()
        # bos and pin_bar untouched
        assert '"bos": StrategySpec' in content
        assert '"pin_bar": StrategySpec' in content


# ---------------------------------------------------------------------------
# StrategySpec.get_confidence — TF resolution
# ---------------------------------------------------------------------------

from analytics.strategies import StrategySpec  # noqa: E402


class TestStrategySpecGetConfidence:
    def test_int_confidence_returns_same_for_any_tf(self) -> None:
        spec = StrategySpec(name="x", description="", confidence=3)
        assert spec.get_confidence("15m") == 3
        assert spec.get_confidence("1h") == 3
        assert spec.get_confidence("4h") == 3

    def test_dict_confidence_returns_tf_value(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 1, "4h": 4}
        )
        assert spec.get_confidence("4h") == 4

    def test_dict_confidence_falls_back_to_default(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 2, "4h": 4}
        )
        assert spec.get_confidence("1h") == 2
        assert spec.get_confidence("15m") == 2

    def test_dict_confidence_falls_back_to_3_when_no_default(self) -> None:
        spec = StrategySpec(name="x", description="", confidence={"4h": 4})
        assert spec.get_confidence("1h") == 3


# ---------------------------------------------------------------------------
# write_confidence_to_db
# ---------------------------------------------------------------------------


class TestWriteConfidenceToDb:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_writes_ratings_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}
        win_rates = pd.DataFrame(
            [
                {"strategy": "fvg", "timeframe": "1h", "avg_r": 0.35, "win_rate": 0.55},
                {"strategy": "fvg", "timeframe": "4h", "avg_r": 0.72, "win_rate": 0.60},
            ]
        )
        write_confidence_to_db(conn, "signal_watch", ratings, win_rates)
        from analytics.data_store import get_confidence_ratings

        result = get_confidence_ratings(conn, "signal_watch")
        assert result == {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}

    def test_different_configs_do_not_interfere(self) -> None:
        conn = self._conn()
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(conn, "signal_watch", {"fvg": {"1h": 2}}, empty_wr)
        write_confidence_to_db(
            conn, "signal_watch_weekdays", {"fvg": {"1h": 4}}, empty_wr
        )
        from analytics.data_store import get_confidence_ratings

        assert get_confidence_ratings(conn, "signal_watch")["fvg"]["1h"] == 2
        assert get_confidence_ratings(conn, "signal_watch_weekdays")["fvg"]["1h"] == 4

    def test_writes_directional_stars_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        result = get_directional_confidence_ratings(conn, "signal_watch")
        assert result["fvg"]["1h"]["long"] == 5
        assert result["fvg"]["1h"]["short"] == 1

    def test_combined_stars_unaffected_by_directional(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        from analytics.data_store import get_confidence_ratings

        combined = get_confidence_ratings(conn, "signal_watch")
        assert combined["fvg"]["1h"] == 3


# ---------------------------------------------------------------------------
# compute_directional_ratings
# ---------------------------------------------------------------------------


def _seed_directional_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed backtest_runs with directional long/short split data."""
    conn.execute(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "dir_bos",
            "BTCUSDT",
            "4h",
            "bos",
            0,
            1,
            90,
            0.02,
            2.0,
            0.0,
            "off",
            20,
            20,
            12,
            8,
            0.6,
            0.4,
            8.0,
            4.0,
            1000,
            None,
            # long: 10 trades, avg_r=0.9 → 5★ at min_trades=5
            10,
            8,
            0.8,
            0.9,
            # short: 10 trades, avg_r=-0.1 → 1★
            10,
            4,
            0.4,
            -0.1,
            9.0,  # long_total_r
            -1.0,  # short_total_r
            None,  # adr_suppress_threshold (added via ALTER TABLE)
            None,  # recovery_factor (added via ALTER TABLE)
            None,  # volume_suppress (added via ALTER TABLE, last column)
        ],
    )


class TestComputeDirectionalRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty(self) -> None:
        conn = self._make_conn()
        result = compute_directional_ratings(conn)
        conn.close()
        assert result == {}

    def test_directional_stars_computed_per_direction(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=5)
        conn.close()
        assert "bos" in result
        assert "4h" in result["bos"]
        # long avg_r=0.9 → 5★; short avg_r=-0.1 → 1★
        assert result["bos"]["4h"]["long"] == 5
        assert result["bos"]["4h"]["short"] == 1

    def test_direction_excluded_when_below_min_trades(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=15)
        conn.close()
        # 10 trades per direction < min_trades=15 → neither direction rated
        assert result == {}
