"""Phase 0.1 — universe policy printing/logging on the runner surfaces."""

import logging
from pathlib import Path

import duckdb
import pytest

from analytics.analytics_runner import _resolve_symbols
from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_runner import run_backtest_sweep
from analytics.data_store import init_schema
from utils.config_validation import UniversePolicy

_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note="Test caveat: survivors only.",
)


def test_sweep_prints_universe_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_backtest_sweep prints the policy paragraph with the symbol count."""
    db_path = tmp_path / "bt.db"
    conn = duckdb.connect(str(db_path))
    init_schema(conn)
    conn.close()
    monkeypatch.setattr(
        "analytics.backtest_runner.load_universe_policy", lambda: _POLICY
    )
    cfg = BacktestSweepConfig(symbols=["AAPL"], timeframes=["1d"], strategies=["bos"])
    run_backtest_sweep(cfg, db_path=db_path)
    out = capsys.readouterr().out
    assert "Universe: liquid_large_cap (as_of=today, 1 symbols)" in out
    assert "Test caveat: survivors only." in out


def test_resolve_symbols_logs_universe_policy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Implicit watchlist fallback logs the policy summary (DoD: no unstated set)."""
    monkeypatch.setattr(
        "analytics.analytics_runner.load_stocks_config",
        lambda: {"AAPL": {"sl_pct": 0.05}, "MSFT": {"sl_pct": 0.05}},
    )
    monkeypatch.setattr(
        "analytics.analytics_runner.load_universe_policy", lambda: _POLICY
    )
    with caplog.at_level(logging.INFO):
        resolved = _resolve_symbols(None)
    assert resolved == ["AAPL", "MSFT"]
    assert "liquid_large_cap (as_of=today)" in caplog.text


def test_resolve_symbols_explicit_list_skips_policy_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit symbol list is not an implicit universe — no policy log."""
    with caplog.at_level(logging.INFO):
        assert _resolve_symbols(["NVDA"]) == ["NVDA"]
    assert "Universe" not in caplog.text
