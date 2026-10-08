"""Phase 2 profile baseline.

Runs cProfile over four hot paths, 3x each, reports median wall-clock + IQR
plus top 20 cumulative functions per profile. Used by `perf-1` (PR 1) and
`perf-2` (PR 12) to detect any wall-clock regression introduced by the
intervening split PRs.

The `_bench_*` functions are written against the current signatures of
`get_ohlcv`, `run_backtest`, `run_param_sweep`, `run_scan_cycle`, and
`run_combo_backtest` (4 benches, 3 runs each, median + IQR, cProfile top-20
cumulative).

Reads OHLCV from a read-only handle on the production analytics DB; the
scan-cycle bench operates on a one-shot clone in /tmp so it never mutates
production data.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.backtest_lib import (  # noqa: E402
    BacktestResult,
    ComboBacktestResult,
    run_backtest,
    run_combo_backtest,
)
from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv, init_schema  # noqa: E402
from analytics.param_sweep import ParamRange, run_param_sweep  # noqa: E402
from analytics.signal_lib import run_scan_cycle  # noqa: E402
from analytics.strategies import DETECTOR_REGISTRY  # noqa: E402
from signals.cooldown_store import CooldownStore  # noqa: E402

BENCH_SYMBOL = "BTCUSDT"
BENCH_TF = "1h"
BENCH_DAYS = 30
MS_PER_DAY = 86_400_000


def _time_one(fn: Callable[[], object]) -> tuple[float, str]:
    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    fn()
    pr.disable()
    elapsed = time.perf_counter() - t0
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(20)
    return elapsed, s.getvalue()


def _bench(label: str, fn: Callable[[], object], runs: int = 3) -> None:
    samples: list[float] = []
    last_dump = ""
    for _ in range(runs):
        elapsed, dump = _time_one(fn)
        samples.append(elapsed)
        last_dump = dump
    median = statistics.median(samples)
    if len(samples) >= 4:
        q = statistics.quantiles(samples, n=4)
        iqr = q[2] - q[0]
    else:
        iqr = max(samples) - min(samples)
    print(f"\n=== {label} ===")
    print(f"runs: {[round(x, 4) for x in samples]}")
    print(f"median: {median:.3f}s   IQR/range: {iqr:.3f}s")
    print(last_dump)


def _open_readonly() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DEFAULT_DB_PATH, read_only=True)


def _bench_window_ms(conn: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    row = conn.execute(
        "SELECT MAX(open_time) FROM ohlcv WHERE symbol=? AND timeframe=?",
        [BENCH_SYMBOL, BENCH_TF],
    ).fetchone()
    end_ms = int(row[0]) if row and row[0] is not None else int(time.time() * 1000)
    return end_ms - BENCH_DAYS * MS_PER_DAY, end_ms


def _load_ohlcv(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    start_ms, end_ms = _bench_window_ms(conn)
    return get_ohlcv(conn, BENCH_SYMBOL, BENCH_TF, start_ms, end_ms)


def _bench_backtest() -> BacktestResult:
    conn = _open_readonly()
    try:
        ohlcv = _load_ohlcv(conn)
        signals = DETECTOR_REGISTRY["wick_fill"](ohlcv)
        return run_backtest(
            ohlcv=ohlcv,
            signals=signals,
            symbol=BENCH_SYMBOL,
            timeframe=BENCH_TF,
            strategy="wick_fill",
            tp_r=4.0,
        )
    finally:
        conn.close()


def _bench_param_sweep() -> object:
    conn = _open_readonly()
    try:
        return run_param_sweep(
            conn=conn,
            strategy="wick_fill",
            symbol=BENCH_SYMBOL,
            timeframe=BENCH_TF,
            days=BENCH_DAYS,
            param_ranges=[
                ParamRange(name="min_wick_body_ratio", values=[1.2, 1.5, 1.8]),
                ParamRange(name="lookback", values=[15, 20]),
            ],
            wfo_split=0.7,
            min_trades=5,
            fee_pct=0.0,
            top_n=10,
        )
    finally:
        conn.close()


def _bench_scan_cycle() -> object:
    src = Path(DEFAULT_DB_PATH)
    if not src.exists():
        raise FileNotFoundError(f"analytics DB missing at {src}")
    tmp_db = Path("/tmp/phase2-perf-scan.db")
    tmp_state = Path("/tmp/phase2-perf-scan-state.json")
    if tmp_db.exists():
        tmp_db.unlink()
    if tmp_state.exists():
        tmp_state.unlink()
    shutil.copyfile(src, tmp_db)
    conn = duckdb.connect(str(tmp_db))
    try:
        init_schema(conn)
        store = CooldownStore(state_file=str(tmp_state))
        return run_scan_cycle(
            conn=conn,
            symbols=[BENCH_SYMBOL],
            timeframes=["15m"],
            strategies=["wick_fill"],
            store=store,
            tp_r=4.0,
            send_telegram=False,
            days=BENCH_DAYS,
        )
    finally:
        conn.close()
        if tmp_db.exists():
            tmp_db.unlink()
        if tmp_state.exists():
            tmp_state.unlink()


def _bench_combo() -> ComboBacktestResult:
    conn = _open_readonly()
    try:
        ohlcv = _load_ohlcv(conn)
        signals_a = DETECTOR_REGISTRY["wick_fill"](ohlcv)
        signals_b = DETECTOR_REGISTRY["bos"](ohlcv)
        return run_combo_backtest(
            ohlcv=ohlcv,
            signals_a=signals_a,
            signals_b=signals_b,
            symbol=BENCH_SYMBOL,
            timeframe=BENCH_TF,
            strategy_a="wick_fill",
            strategy_b="bos",
            window=5,
            tp_r=4.0,
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional log path; default stdout only.",
    )
    parser.parse_args()
    _bench(f"backtest {BENCH_SYMBOL}/{BENCH_TF} wick_fill", _bench_backtest)
    _bench("param_sweep wick_fill 1h", _bench_param_sweep)
    _bench("run_scan_cycle BTCUSDT/15m wick_fill (cloned DB)", _bench_scan_cycle)
    _bench(f"combo backtest wick_fill+bos {BENCH_SYMBOL}/{BENCH_TF}", _bench_combo)


if __name__ == "__main__":
    main()
