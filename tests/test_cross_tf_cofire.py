"""Tests for D10 step 4: cross-TF co-firing backtest + live detection."""

import duckdb
import pandas as pd
import pytest

from analytics.backtest_lib import (
    CrossTfComboBacktestResult,
    _find_cross_tf_signals,
    run_cross_tf_combo_backtest,
)
from analytics.data_store import (
    get_cross_tf_combo_lookup,
    init_schema,
    upsert_cross_tf_combo_run,
)
from analytics.signal_lib import _find_cross_tf_cofire, _parse_htf_ltf_pairs
from signals.alert_formatter import ConfluenceData, SignalEvent

_BASE_TIME = 1_700_000_000_000  # arbitrary base ms timestamp
_1H_MS = 3_600_000
_4H_MS = 4 * _1H_MS
_15M_MS = 15 * 60 * 1_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signals(
    times_ms: list[int],
    direction: str = "long",
    sl_price: float = 98.0,
) -> pd.DataFrame:
    from analytics.strategies import SIGNAL_COLUMNS

    rows = [
        {
            "open_time": t,
            "direction": direction,
            "reason": f"test@{t}",
            "sl_price": sl_price,
            "context": "test",
            "low_volume": False,
            "tp_price": 0.0,
        }
        for t in times_ms
    ]
    return pd.DataFrame(rows, columns=SIGNAL_COLUMNS)


def _make_ohlcv_ltf(n: int = 50, tf_ms: int = _15M_MS) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [_BASE_TIME + i * tf_ms for i in range(n)],
            "open": [100.0] * n,
            "high": [102.0] * n,
            "low": [98.0] * n,
            "close": [101.0] * n,
            "volume": [1000.0] * n,
        }
    )


def _make_event(
    strategy: str = "fvg",
    direction: str = "long",
    open_time: int | None = None,
    tf: str = "15m",
) -> SignalEvent:
    t = open_time if open_time is not None else _BASE_TIME + 49 * _15M_MS
    return SignalEvent(
        symbol="BTCUSDT",
        timeframe=tf,
        strategy=strategy,
        direction=direction,
        reason=f"{strategy}_{direction}@100.00",
        open_time=t,
        price=100.0,
    )


def _make_cross_tf_lookup(
    symbol: str = "BTCUSDT",
    tf_htf: str = "4h",
    tf_ltf: str = "15m",
    strategy_htf: str = "order_block",
    strategy_ltf: str = "fvg",
    avg_r: float = 1.8,
    win_rate: float = 0.7,
    closed_trades: int = 10,
) -> dict:
    return {
        (symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf): {
            "avg_r": avg_r,
            "win_rate": win_rate,
            "closed_trades": closed_trades,
            "strategy_htf": strategy_htf,
            "strategy_ltf": strategy_ltf,
            "tf_htf": tf_htf,
            "tf_ltf": tf_ltf,
            "window_hours": 4.0,
        }
    }


# ---------------------------------------------------------------------------
# _parse_htf_ltf_pairs
# ---------------------------------------------------------------------------


def test_parse_htf_ltf_pairs_valid() -> None:
    result = _parse_htf_ltf_pairs(["1wk:1d", "4h:1h", "1d:4h"])
    assert result == [("1wk", "1d"), ("4h", "1h"), ("1d", "4h")]


def test_parse_htf_ltf_pairs_empty() -> None:
    assert _parse_htf_ltf_pairs([]) == []


def test_parse_htf_ltf_pairs_skips_malformed() -> None:
    result = _parse_htf_ltf_pairs(["1d:4h", "bad", "4h:1h"])
    assert result == [("1d", "4h"), ("4h", "1h")]


# ---------------------------------------------------------------------------
# _DEFAULT_HTF_LTF_PAIRS — fork-drift guard
# ---------------------------------------------------------------------------


def test_default_htf_ltf_pairs_use_supported_intervals() -> None:
    """Every default cross-TF pair must name a fetchable interval.

    An unsupported timeframe here does NOT raise — the cross-TF worker reads
    from DuckDB, so the chunk silently returns "no data" for every symbol and
    the pair contributes nothing. The fork inherited `15m` pairs this way and
    3 of 5 defaults were dead with no error surfaced.
    """
    from analytics.backtest_runner import _DEFAULT_HTF_LTF_PAIRS
    from analytics.data_fetcher import _INTERVAL_CONFIG

    for htf, ltf in _DEFAULT_HTF_LTF_PAIRS:
        assert htf in _INTERVAL_CONFIG, f"unsupported HTF {htf!r} in defaults"
        assert ltf in _INTERVAL_CONFIG, f"unsupported LTF {ltf!r} in defaults"


# ---------------------------------------------------------------------------
# _find_cross_tf_signals
# ---------------------------------------------------------------------------


def test_find_cross_tf_signals_match() -> None:
    """LTF signal within window_hours of HTF signal is matched."""
    htf_time = _BASE_TIME  # HTF fires at t=0
    ltf_time = _BASE_TIME + 2 * _1H_MS  # LTF fires 2h later — within 4h window

    sigs_htf = _make_signals([htf_time] * 5, direction="long")
    sigs_ltf = _make_signals([ltf_time] * 5, direction="long")

    result = _find_cross_tf_signals(sigs_htf, sigs_ltf, window_hours=4.0, min_signals=3)
    assert len(result) == 5
    assert all(result["open_time"] == ltf_time)


def test_find_cross_tf_signals_outside_window() -> None:
    """LTF signal outside window_hours is not matched."""
    htf_time = _BASE_TIME
    ltf_time = _BASE_TIME + 6 * _1H_MS  # 6h after HTF — outside 4h window

    sigs_htf = _make_signals([htf_time] * 5, direction="long")
    sigs_ltf = _make_signals([ltf_time] * 5, direction="long")

    result = _find_cross_tf_signals(sigs_htf, sigs_ltf, window_hours=4.0, min_signals=3)
    assert len(result) == 0


def test_find_cross_tf_signals_direction_mismatch() -> None:
    """LTF long signal is not matched to HTF short signal."""
    htf_time = _BASE_TIME
    ltf_time = _BASE_TIME + _1H_MS

    sigs_htf = _make_signals([htf_time] * 5, direction="short")
    sigs_ltf = _make_signals([ltf_time] * 5, direction="long")

    result = _find_cross_tf_signals(sigs_htf, sigs_ltf, window_hours=4.0, min_signals=3)
    assert len(result) == 0


def test_find_cross_tf_signals_below_min_signals() -> None:
    """Returns empty when either set has fewer than min_signals."""
    sigs_htf = _make_signals([_BASE_TIME], direction="long")  # only 1
    sigs_ltf = _make_signals([_BASE_TIME + _1H_MS] * 5, direction="long")

    result = _find_cross_tf_signals(sigs_htf, sigs_ltf, window_hours=4.0, min_signals=3)
    assert len(result) == 0


def test_find_cross_tf_signals_multiple_ltf_per_htf() -> None:
    """Multiple LTF signals confirmed by the same HTF signal (no exclusivity)."""
    htf_time = _BASE_TIME
    ltf_times = [_BASE_TIME + i * _15M_MS for i in range(5)]  # 5 LTF in window

    sigs_htf = _make_signals([htf_time] * 5, direction="long")
    sigs_ltf = _make_signals(ltf_times * 5, direction="long")  # 25 total

    result = _find_cross_tf_signals(sigs_htf, sigs_ltf, window_hours=4.0, min_signals=3)
    # All LTF signals are within 4h of the HTF signal, so all 25 should match.
    assert len(result) == 25


# ---------------------------------------------------------------------------
# run_cross_tf_combo_backtest
# ---------------------------------------------------------------------------


def test_run_cross_tf_combo_backtest_returns_result() -> None:
    """Basic smoke test: result has correct TF labels."""
    ohlcv = _make_ohlcv_ltf(50)
    # 5 HTF signals spread across range
    htf_times = [_BASE_TIME + i * _4H_MS for i in range(5)]
    sigs_htf = _make_signals(htf_times, direction="long", sl_price=95.0)
    # 10 LTF signals close after each HTF
    ltf_times = [t + _1H_MS for t in htf_times for _ in range(2)]
    sigs_ltf = _make_signals(ltf_times, direction="long", sl_price=95.0)

    result = run_cross_tf_combo_backtest(
        ohlcv,
        sigs_htf,
        sigs_ltf,
        symbol="BTCUSDT",
        tf_htf="4h",
        tf_ltf="15m",
        strategy_htf="order_block",
        strategy_ltf="fvg",
        window_hours=4.0,
        sl_pct=0.02,
        tp_r=2.0,
    )

    assert isinstance(result, CrossTfComboBacktestResult)
    assert result.tf_htf == "4h"
    assert result.tf_ltf == "15m"
    assert result.strategy_htf == "order_block"
    assert result.strategy_ltf == "fvg"
    assert result.result.timeframe == "15m"


def test_run_cross_tf_combo_backtest_no_match_empty_trades() -> None:
    """When no LTF signals fall within window, result has zero closed trades."""
    ohlcv = _make_ohlcv_ltf(50)
    # HTF at t=0; LTF 12h later (outside 4h window)
    sigs_htf = _make_signals([_BASE_TIME] * 5, direction="long", sl_price=95.0)
    sigs_ltf = _make_signals(
        [_BASE_TIME + 12 * _1H_MS] * 5, direction="long", sl_price=95.0
    )

    result = run_cross_tf_combo_backtest(
        ohlcv,
        sigs_htf,
        sigs_ltf,
        symbol="BTCUSDT",
        tf_htf="4h",
        tf_ltf="15m",
        strategy_htf="order_block",
        strategy_ltf="fvg",
        window_hours=4.0,
    )

    assert len(result.result.closed_trades) == 0


# ---------------------------------------------------------------------------
# DB: upsert + lookup
# ---------------------------------------------------------------------------


def test_upsert_and_get_cross_tf_combo_lookup() -> None:
    """Round-trip: upsert a result, then load it via get_cross_tf_combo_lookup."""
    ohlcv = _make_ohlcv_ltf(50)
    htf_times = [_BASE_TIME + i * _4H_MS for i in range(5)]
    sigs_htf = _make_signals(htf_times, direction="long", sl_price=95.0)
    ltf_times = [t + _1H_MS for t in htf_times for _ in range(2)]
    sigs_ltf = _make_signals(ltf_times, direction="long", sl_price=95.0)

    combo = run_cross_tf_combo_backtest(
        ohlcv,
        sigs_htf,
        sigs_ltf,
        symbol="BTCUSDT",
        tf_htf="4h",
        tf_ltf="15m",
        strategy_htf="order_block",
        strategy_ltf="fvg",
        window_hours=4.0,
    )

    conn = duckdb.connect(":memory:")
    init_schema(conn)

    upsert_cross_tf_combo_run(
        conn,
        combo,
        days=90,
        data_start_ms=_BASE_TIME,
        data_end_ms=_BASE_TIME + 50 * _15M_MS,
        sl_pct=0.02,
        tp_r=2.0,
        fee_pct=0.0,
        day_filter="off",
    )

    lookup = get_cross_tf_combo_lookup(conn)
    conn.close()

    key = ("BTCUSDT", "4h", "15m", "order_block", "fvg")
    assert key in lookup
    assert lookup[key]["tf_htf"] == "4h"
    assert lookup[key]["tf_ltf"] == "15m"


def test_upsert_overwrites_on_rerun() -> None:
    """Re-running with the same params overwrites (same combo_id)."""
    ohlcv = _make_ohlcv_ltf(50)
    htf_times = [_BASE_TIME + i * _4H_MS for i in range(5)]
    sigs_htf = _make_signals(htf_times, direction="long", sl_price=95.0)
    ltf_times = [t + _1H_MS for t in htf_times for _ in range(2)]
    sigs_ltf = _make_signals(ltf_times, direction="long", sl_price=95.0)

    def _make_combo() -> CrossTfComboBacktestResult:
        return run_cross_tf_combo_backtest(
            ohlcv,
            sigs_htf,
            sigs_ltf,
            symbol="BTCUSDT",
            tf_htf="4h",
            tf_ltf="15m",
            strategy_htf="order_block",
            strategy_ltf="fvg",
            window_hours=4.0,
        )

    conn = duckdb.connect(":memory:")
    init_schema(conn)

    upsert_cross_tf_combo_run(
        conn,
        _make_combo(),
        days=90,
        data_start_ms=0,
        data_end_ms=1,
        sl_pct=0.02,
        tp_r=2.0,
        fee_pct=0.0,
        day_filter="off",
    )
    upsert_cross_tf_combo_run(
        conn,
        _make_combo(),
        days=90,
        data_start_ms=0,
        data_end_ms=1,
        sl_pct=0.02,
        tp_r=2.0,
        fee_pct=0.0,
        day_filter="off",
    )

    row = conn.execute("SELECT COUNT(*) FROM backtest_cross_tf_combos").fetchone()
    count = row[0] if row else 0
    conn.close()
    assert count == 1  # second upsert overwrote, not duplicated


# ---------------------------------------------------------------------------
# _find_cross_tf_cofire
# ---------------------------------------------------------------------------


def _seed_htf_signal(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    strategy: str,
    direction: str,
    open_time: int,
) -> None:
    """Insert a minimal signal row into the signals table for testing."""
    conn.execute(
        """
        INSERT OR REPLACE INTO signals
            (symbol, timeframe, strategy, direction, open_time,
             entry_price, sl_price, reason, confidence, fired_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            symbol,
            tf,
            strategy,
            direction,
            open_time,
            100.0,
            95.0,
            f"{strategy}_{direction}",
            3,
            open_time,
        ],
    )


def test_find_cross_tf_cofire_returns_confluence() -> None:
    """Returns ConfluenceData when a matching HTF signal is in DB within window."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf_time = current_time - 2 * _1H_MS  # 2h before current LTF signal

    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "long", htf_time)

    events = [_make_event("fvg", "long", open_time=current_time)]
    cross_tf_lookup = _make_cross_tf_lookup(
        symbol="BTCUSDT",
        tf_htf="4h",
        tf_ltf="15m",
        strategy_htf="order_block",
        strategy_ltf="fvg",
    )
    pairs = [("4h", "15m")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,
    )
    conn.close()

    assert result is not None
    assert isinstance(result, ConfluenceData)
    assert result.co_strategy == "order_block"
    assert result.htf_tf == "4h"
    assert result.ltf_tf == "15m"
    assert result.avg_r == pytest.approx(1.8)
    assert result.candles_ago > 0  # expressed in LTF candles


def test_find_cross_tf_cofire_returns_none_outside_window() -> None:
    """Returns None when HTF signal is outside window_hours."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf_time = current_time - 8 * _1H_MS  # 8h before — outside 4h window

    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "long", htf_time)

    events = [_make_event("fvg", "long", open_time=current_time)]
    cross_tf_lookup = _make_cross_tf_lookup()
    pairs = [("4h", "15m")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,
    )
    conn.close()
    assert result is None


def test_find_cross_tf_cofire_returns_none_direction_mismatch() -> None:
    """Returns None when HTF signal direction doesn't match LTF signal direction."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf_time = current_time - 2 * _1H_MS

    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "short", htf_time)  # short

    events = [_make_event("fvg", "long", open_time=current_time)]  # long
    cross_tf_lookup = _make_cross_tf_lookup()
    pairs = [("4h", "15m")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,
    )
    conn.close()
    assert result is None


def test_find_cross_tf_cofire_returns_none_below_min_avg_r() -> None:
    """Returns None when the combo avg_r is below min_avg_r threshold."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf_time = current_time - 2 * _1H_MS
    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "long", htf_time)

    events = [_make_event("fvg", "long", open_time=current_time)]
    cross_tf_lookup = _make_cross_tf_lookup(avg_r=0.5)  # low avg_r
    pairs = [("4h", "15m")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,  # threshold = 1.0
    )
    conn.close()
    assert result is None


def test_find_cross_tf_cofire_returns_none_wrong_ltf() -> None:
    """Returns None when no pair in cross_tf_pairs has ltf matching current TF."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf_time = current_time - 2 * _1H_MS
    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "long", htf_time)

    events = [_make_event("fvg", "long", open_time=current_time, tf="15m")]
    cross_tf_lookup = _make_cross_tf_lookup(tf_htf="1d", tf_ltf="4h")  # wrong LTF
    pairs = [("1d", "4h")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",  # current TF is 15m, not 4h
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,
    )
    conn.close()
    assert result is None


def test_find_cross_tf_cofire_best_avg_r_wins() -> None:
    """When multiple HTF matches exist, the one with higher avg_r is returned."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    current_time = _BASE_TIME + 49 * _15M_MS
    htf1_time = current_time - 1 * _1H_MS
    htf2_time = current_time - 2 * _1H_MS

    _seed_htf_signal(conn, "BTCUSDT", "4h", "order_block", "long", htf1_time)
    _seed_htf_signal(conn, "BTCUSDT", "4h", "fvg", "long", htf2_time)

    events = [_make_event("bos", "long", open_time=current_time)]

    # order_block has avg_r=1.8, fvg has avg_r=2.5 — fvg should win
    cross_tf_lookup: dict = {
        ("BTCUSDT", "4h", "15m", "order_block", "bos"): {
            "avg_r": 1.8,
            "win_rate": 0.6,
            "closed_trades": 10,
            "strategy_htf": "order_block",
            "strategy_ltf": "bos",
            "tf_htf": "4h",
            "tf_ltf": "15m",
            "window_hours": 4.0,
        },
        ("BTCUSDT", "4h", "15m", "fvg", "bos"): {
            "avg_r": 2.5,
            "win_rate": 0.75,
            "closed_trades": 12,
            "strategy_htf": "fvg",
            "strategy_ltf": "bos",
            "tf_htf": "4h",
            "tf_ltf": "15m",
            "window_hours": 4.0,
        },
    }
    pairs = [("4h", "15m")]

    result = _find_cross_tf_cofire(
        events,
        conn,
        "BTCUSDT",
        "15m",
        cross_tf_lookup,
        pairs,
        window_hours=4.0,
        min_avg_r=1.0,
    )
    conn.close()

    assert result is not None
    assert result.co_strategy == "fvg"
    assert result.avg_r == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# ConfluenceData: cross-TF alert formatting
# ---------------------------------------------------------------------------


def test_confluence_data_htf_ltf_fields() -> None:
    """Cross-TF ConfluenceData carries htf_tf/ltf_tf for display."""
    cd = ConfluenceData(
        co_strategy="order_block",
        candles_ago=8,
        avg_r=1.8,
        trades=10,
        win_rate=0.7,
        type_a="structural",
        type_b="structural",
        htf_tf="4h",
        ltf_tf="15m",
    )
    assert cd.htf_tf == "4h"
    assert cd.ltf_tf == "15m"


def test_same_tf_confluence_data_empty_tf_fields() -> None:
    """Same-TF ConfluenceData has empty htf_tf/ltf_tf by default."""
    cd = ConfluenceData(
        co_strategy="ote_entry",
        candles_ago=2,
        avg_r=1.63,
        trades=9,
        win_rate=0.89,
        type_a="fib",
        type_b="structural",
    )
    assert cd.htf_tf == ""
    assert cd.ltf_tf == ""
