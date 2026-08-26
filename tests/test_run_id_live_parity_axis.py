"""The live-parity gate set is part of a backtest run's identity.

Five gates are on in `config/strategy_params.toml`'s shared base and they cut
closed trades by ~42%, while `cli/backtest.py` can override any of them per run.
Until this axis existed the `run_id` hash ignored all of that, so two runs
measuring different populations shared one id and `INSERT OR REPLACE` let an
ad-hoc override overwrite the routine sweep's row — the row `confidence_ratings`
is built from. Same shape as the writer collision `origin` closed.
"""

from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.backtest.engine import BacktestResult
from analytics.backtest.live_parity_config import LiveParityConfig
from analytics.backtest_config import BacktestSweepConfig
from analytics.data_store import init_schema
from analytics.store.backtest_runs import _backtest_run_id, upsert_backtest_run

_BASE_ARGS = ("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


class TestLiveParityIdentity:
    """The token itself: canonical, executed-state, and empty when nothing ran."""

    def test_all_gates_off_is_none(self) -> None:
        # The contract every optional axis in `_backtest_run_id` keeps: a
        # default config appends no suffix, so historical ids are unchanged.
        assert LiveParityConfig().identity() is None

    def test_token_follows_gates_order_not_construction_order(self) -> None:
        a = LiveParityConfig(cooldown=True, regime=True)
        b = LiveParityConfig(regime=True, cooldown=True)
        assert a.identity() == b.identity() == "regime+cooldown"

    def test_enabled_alone_is_not_a_gate(self) -> None:
        # `enabled` is expanded by the CLI/TOML resolver before the engine sees
        # it; `run_backtest` only ever consults `is_on`. Folding the master
        # switch in here would split one cell into two identities the engine
        # cannot tell apart.
        assert LiveParityConfig(enabled=True).identity() is None

    def test_cooldown_bars_join_the_token(self) -> None:
        token = LiveParityConfig(
            cooldown=True, cooldown_bars_per_tf={"1d": 1, "4h": 2}
        ).identity()
        assert token == "cooldown/bars(1d:1,4h:2)"

    def test_cooldown_bars_are_inert_while_cooldown_is_off(self) -> None:
        # The map only reaches the engine through the cooldown gate, so it must
        # not split identities while that gate is off.
        assert (
            LiveParityConfig(regime=True, cooldown_bars_per_tf={"1d": 1}).identity()
            == "regime"
        )

    def test_the_shipped_base_gate_set_round_trips(self) -> None:
        # The five gates `config/strategy_params.toml` actually turns on.
        shipped = LiveParityConfig(
            regime=True,
            direction_filter=True,
            f8_htf_ema=True,
            adr_bias=True,
            conflict_resolver=False,
            cooldown=True,
        )
        assert (
            shipped.identity() == "regime+direction_filter+f8_htf_ema+adr_bias+cooldown"
        )


class TestRunIdAxis:
    def test_none_leaves_historical_ids_unchanged(self) -> None:
        assert _backtest_run_id(*_BASE_ARGS) == _backtest_run_id(
            *_BASE_ARGS, live_parity=None
        )

    def test_positive_control_a_gate_set_moves_the_id(self) -> None:
        # Without this the assertion above is satisfied by an axis that is
        # wired to nothing at all.
        assert _backtest_run_id(*_BASE_ARGS) != _backtest_run_id(
            *_BASE_ARGS, live_parity="regime"
        )

    def test_distinct_gate_sets_are_distinct_ids(self) -> None:
        assert _backtest_run_id(*_BASE_ARGS, live_parity="regime") != _backtest_run_id(
            *_BASE_ARGS, live_parity="regime+cooldown"
        )

    def test_same_gate_set_is_deterministic(self) -> None:
        assert _backtest_run_id(
            *_BASE_ARGS, live_parity="regime+cooldown"
        ) == _backtest_run_id(*_BASE_ARGS, live_parity="regime+cooldown")


class TestGateSetDoesNotOverwrite:
    """The regression, at the table: an override must not eat the sweep row."""

    _PARAMS: dict[str, Any] = {
        "days": 365,
        "data_start_ms": 0,
        "data_end_ms": 1,
        "sl_pct": 0.02,
        "tp_r": 2.0,
        "fee_pct": 0.0,
        "day_filter": "tue_thu",
        "adr_suppress_threshold": None,
    }

    def test_ad_hoc_override_does_not_replace_the_sweep_row(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        shipped = "regime+direction_filter+f8_htf_ema+adr_bias+cooldown"
        upsert_backtest_run(
            conn,
            BacktestResult(symbol="SPY", timeframe="4h", strategy="bos"),
            sweep_id="sweep-1",
            origin="sweep",
            live_parity=shipped,
            **self._PARAMS,
        )
        # Same writer, same params, one gate dropped — `--no-live-parity-cooldown`.
        upsert_backtest_run(
            conn,
            BacktestResult(symbol="SPY", timeframe="4h", strategy="bos"),
            sweep_id="sweep-2",
            origin="sweep",
            live_parity="regime+direction_filter+f8_htf_ema+adr_bias",
            **self._PARAMS,
        )
        rows = conn.execute(
            "SELECT sweep_id FROM backtest_runs WHERE symbol = 'SPY'"
        ).fetchall()
        assert len(rows) == 2, "the override overwrote the routine sweep's row"
        assert {r[0] for r in rows} == {"sweep-1", "sweep-2"}

    def test_column_records_the_executed_gate_set(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        run_id = upsert_backtest_run(
            conn,
            BacktestResult(symbol="QQQ", timeframe="1d", strategy="doji"),
            sweep_id="sweep-3",
            origin="sweep",
            live_parity="regime+cooldown",
            **self._PARAMS,
        )
        row = conn.execute(
            "SELECT live_parity FROM backtest_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        assert row is not None and row[0] == "regime+cooldown"

    def test_column_is_null_when_no_gate_ran(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        run_id = upsert_backtest_run(
            conn,
            BacktestResult(symbol="IWM", timeframe="1d", strategy="doji"),
            sweep_id="sweep-4",
            origin="sweep",
            live_parity=None,
            **self._PARAMS,
        )
        row = conn.execute(
            "SELECT live_parity FROM backtest_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        assert row is not None and row[0] is None


class TestSweepWriterPassesItsOwnGateSet:
    """Scope, not just reachability.

    Every assertion above still passes if the sweep writer hardcodes None, which
    is the whole defect wearing a different hat. This drives the real collector
    and reads back what it handed the store.
    """

    @staticmethod
    def _capture(
        monkeypatch: pytest.MonkeyPatch, cfg: BacktestSweepConfig
    ) -> list[dict[str, Any]]:
        import analytics.backtest_runner as runner

        seen: list[dict[str, Any]] = []
        ohlcv = pd.DataFrame(
            {
                "open_time": [0, 1],
                "open": [1.0, 1.0],
                "high": [1.0, 1.0],
                "low": [1.0, 1.0],
                "close": [1.0, 1.0],
                "volume": [1.0, 1.0],
            }
        )
        signals = pd.DataFrame({"open_time": [0], "direction": ["long"]})

        def _fake_upsert(*_a: Any, **kw: Any) -> str:
            seen.append(kw)
            return "run-x"

        monkeypatch.setattr(runner, "get_ohlcv", lambda *a, **k: ohlcv)
        monkeypatch.setattr(
            runner, "detect_signals_for_strategy", lambda *a, **k: signals
        )
        monkeypatch.setattr(
            runner,
            "run_backtest",
            lambda *a, **k: BacktestResult(
                symbol="SPY", timeframe="4h", strategy="bos"
            ),
        )
        monkeypatch.setattr(runner, "upsert_backtest_run", _fake_upsert)
        monkeypatch.setattr(runner, "upsert_backtest_trades", lambda *a, **k: None)

        runner._collect_sweep_results(
            duckdb.connect(":memory:"),
            cfg,
            2.0,
            ["SPY"],
            ["bos"],
            0,
            1,
            sweep_id="sweep-1",
        )
        return seen

    def _cfg(self, live_parity: LiveParityConfig) -> BacktestSweepConfig:
        return BacktestSweepConfig(
            symbols=["SPY"],
            timeframes=["4h"],
            strategies=["bos"],
            save_results=True,
            live_parity=live_parity,
        )

    def test_writer_forwards_the_configured_gate_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._capture(
            monkeypatch, self._cfg(LiveParityConfig(regime=True, cooldown=True))
        )
        assert seen, "the collector never reached the store — vacuous otherwise"
        assert seen[0]["live_parity"] == "regime+cooldown"

    def test_writer_forwards_none_when_no_gate_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The other half of the control: it must not stamp a token that did not run.
        seen = self._capture(monkeypatch, self._cfg(LiveParityConfig()))
        assert seen and seen[0]["live_parity"] is None
