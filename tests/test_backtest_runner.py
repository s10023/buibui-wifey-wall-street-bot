"""Tests for analytics/backtest_runner.py — format_sweep_table."""

from analytics.backtest_lib import BacktestResult, Trade
from analytics.backtest_runner import format_sweep_table


def _make_result(
    symbol: str,
    timeframe: str,
    strategy: str,
    wins: int,
    losses: int,
    avg_r: float,
) -> BacktestResult:
    """Build a BacktestResult stub with the given win/loss counts and avg_r."""
    trades: list[Trade] = []
    # Add wins
    for _ in range(wins):
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
        )
        trades.append(t)
    # Add losses with pnl_r calibrated so avg_r comes out to avg_r
    # avg_r = (wins * win_r + losses * loss_r) / total
    # win_r = 2.0 (entry=100, sl=98, tp=104 → risk=2, gain=4 → 2R)
    # solve for loss_r: avg_r * total = wins * 2 + losses * loss_r
    total = wins + losses
    win_r = 2.0
    loss_r = (avg_r * total - wins * win_r) / losses if losses > 0 else -1.0

    for _ in range(losses):
        # entry=100, sl=98 → risk=2; to get loss_r we want exit such that (entry-exit)/risk = |loss_r|
        exit_price = 100.0 - abs(loss_r) * 2.0
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=exit_price,
            exit_time=2,
            outcome="loss",
        )
        trades.append(t)

    result = BacktestResult(symbol=symbol, timeframe=timeframe, strategy=strategy)
    result.trades = trades
    return result


class TestFormatSweepTable:
    def test_basic_output_has_header_and_rows(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 30, 18, 0.8),
            _make_result("ETHUSDT", "1d", "bos", 25, 20, 0.5),
            _make_result("SOLUSDT", "1h", "order_block", 22, 18, 0.3),
        ]
        table = format_sweep_table(results, min_trades=10)
        assert "Symbol" in table
        assert "BTCUSDT" in table
        assert "ETHUSDT" in table
        assert "SOLUSDT" in table
        assert "fvg" in table

    def test_sorted_by_avg_r_descending(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 20, 20, 0.3),
            _make_result("ETHUSDT", "4h", "bos", 30, 10, 1.2),
            _make_result("SOLUSDT", "4h", "order_block", 25, 15, 0.7),
        ]
        table = format_sweep_table(results, min_trades=5)
        eth_pos = table.index("ETHUSDT")
        sol_pos = table.index("SOLUSDT")
        btc_pos = table.index("BTCUSDT")
        assert eth_pos < sol_pos < btc_pos

    def test_min_trades_filter_excludes_low_count(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 15, 10, 0.9),  # 25 closed
            _make_result("ETHUSDT", "4h", "bos", 5, 3, 0.4),  # 8 closed — below min
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "BTCUSDT" in table
        assert "ETHUSDT" not in table
        assert "Hidden: 1" in table

    def test_all_below_min_trades_shows_no_results(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 3, 2, 0.5),
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "No results" in table

    def test_empty_results_list(self) -> None:
        table = format_sweep_table([], min_trades=20)
        assert "No results" in table

    def test_footer_hidden_count_correct(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 25, 15, 0.8),  # 40 closed
            _make_result("ETHUSDT", "4h", "bos", 2, 1, 0.2),  # 3 closed
            _make_result("SOLUSDT", "4h", "wick_fill", 1, 1, 0.1),  # 2 closed
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "Hidden: 2" in table

    def test_win_pct_and_avg_r_appear_in_row(self) -> None:
        results = [_make_result("BTCUSDT", "4h", "fvg", 20, 20, 0.5)]
        table = format_sweep_table(results, min_trades=5)
        assert "50.0%" in table
        assert "+0.50R" in table
