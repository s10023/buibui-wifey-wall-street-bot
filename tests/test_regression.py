"""Golden-file regression tests for the backtest pipeline.

Runs the full detector → backtest chain on frozen OHLCV fixtures and
compares every metric to committed golden JSON files.  Any unintentional
metric change (avg_r drift, trade count shift, directional skew) fails the
test with a focused diff report.

Intentional changes workflow:
    make regression-update          # regenerate golden files
    git diff tests/fixtures/        # review what changed
    git add tests/fixtures/         # commit alongside TOML/code change

Run:
    make test-regression            # compare against golden
    make regression-update          # regenerate golden files
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from analytics.backtest_lib import BacktestResult, run_backtest
from analytics.signal_config import SignalWatchConfig, load_signal_config
from analytics.strategies import DETECTOR_REGISTRY

# Analytics-only strategies — not covered in Phase 1 of the regression suite.
_SKIP_STRATEGIES = frozenset({"seasonality"})

FIXTURE_DIR = Path("tests/fixtures")
FIXTURE_SYMBOL = "AAPL"
FIXTURE_TIMEFRAMES = ["4h", "1d", "1wk"]
CONFIGS: list[tuple[str, str]] = [
    ("signal_watch", "config/signal_watch.toml"),
    ("weekdays", "config/signal_watch_weekdays.toml"),
]


def _load_fixtures(tfs: list[str]) -> dict[str, pd.DataFrame]:
    """Load parquet fixture files for the requested timeframes.

    Raises pytest.skip if any fixture is missing (run extraction script first).

    Normalises dtypes after loading so pandas version differences between the
    machine that generated the parquets and the machine running the tests don't
    cause fast_xs / ExtensionBlock incompatibilities.  Arrow-backed string
    columns (ExtensionBlock dtype) crash older pandas inside iloc; coercing to
    plain object avoids that.
    """
    result: dict[str, pd.DataFrame] = {}
    symbol_slug = FIXTURE_SYMBOL.lower()
    for tf in tfs:
        path = FIXTURE_DIR / f"{symbol_slug}_{tf}.parquet"
        if not path.exists():
            pytest.skip(
                f"Fixture missing: {path}. "
                "Run: poetry run python scripts/extract_regression_fixture.py"
            )
        df = pd.read_parquet(path)
        # Coerce string columns to plain object dtype.  pyarrow stores strings
        # as large_string, which pandas loads as a separate ExtensionBlock.
        # Multiple blocks make fast_xs iterate item-by-item over every iloc[]
        # call — catastrophically slow for the 20k-row 15m fixture.
        # df.copy() after coercion consolidates the two object blocks into one,
        # leaving two total blocks (object + float64) instead of three.
        for col in ("symbol", "timeframe"):
            if col in df.columns and df[col].dtype != object:
                df[col] = df[col].astype(object)
        df = df.reset_index(drop=True).copy()
        result[tf] = df
    return result


def _detect(
    strategy: str,
    ohlcv: pd.DataFrame,
    cfg: SignalWatchConfig,
) -> pd.DataFrame:
    """Call the correct detector for strategy, applying any TOML flags."""
    return DETECTOR_REGISTRY[strategy](ohlcv)


def _extract_metrics(result: BacktestResult) -> dict[str, Any]:
    def _r(v: float | None) -> float:
        return round(v, 4) if v is not None else 0.0

    return {
        "trade_count": len(result.closed_trades),
        "long_trade_count": len(result.long_closed_trades),
        "short_trade_count": len(result.short_closed_trades),
        "win_rate": round(result.win_rate, 4),
        "avg_r": round(result.avg_r, 4),
        "total_r": round(result.total_r, 4),
        "long_avg_r": _r(result.long_avg_r),
        "short_avg_r": _r(result.short_avg_r),
        "long_total_r": round(result.long_total_r, 4),
        "short_total_r": round(result.short_total_r, 4),
        "long_win_rate": _r(result.long_win_rate),
        "short_win_rate": _r(result.short_win_rate),
        "max_drawdown_r": round(result.max_drawdown_r, 4),
        "recovery_factor": round(result.recovery_factor, 4),
    }


def _diff_report(actual: dict[str, Any], golden: dict[str, Any]) -> str:
    lines = ["", "REGRESSION DETECTED:"]
    changed = False
    all_keys = sorted(set(actual) | set(golden))
    for strat in all_keys:
        actual_tfs = actual.get(strat, {})
        golden_tfs = golden.get(strat, {})
        all_tfs = sorted(set(actual_tfs) | set(golden_tfs))
        for tf in all_tfs:
            actual_m = actual_tfs.get(tf)
            golden_m = golden_tfs.get(tf)
            if actual_m is None:
                lines.append(f"  {strat} / {tf}: MISSING in actual (was in golden)")
                changed = True
                continue
            if golden_m is None:
                lines.append(f"  {strat} / {tf}: NEW (not in golden)")
                changed = True
                continue
            diffs = []
            for k in sorted(set(actual_m) | set(golden_m)):
                av = actual_m.get(k)
                gv = golden_m.get(k)
                if av != gv:
                    direction = "improved" if (av or 0) > (gv or 0) else "dropped"
                    diffs.append(f"    {k:<30} {gv!r:>12} → {av!r:<12}  ← {direction}")
            if diffs:
                lines.append(f"  {strat} / {tf}:")
                lines.extend(diffs)
                changed = True
    if not changed:
        return ""
    lines.append("")
    lines.append("Run: make regression-update  (if changes are intentional)")
    return "\n".join(lines)


@pytest.mark.parametrize("config_name,config_path", CONFIGS)
def test_golden_metrics(
    config_name: str, config_path: str, request: pytest.FixtureRequest
) -> None:
    """Assert backtest metrics match golden files for each config."""
    update: bool = request.config.getoption("--update-golden")
    cfg = load_signal_config(config_path)

    fixtures = _load_fixtures(FIXTURE_TIMEFRAMES)

    results: dict[str, dict[str, Any]] = {}

    for strategy, _detector_fn in DETECTOR_REGISTRY.items():
        if strategy in _SKIP_STRATEGIES:
            continue

        cfg_tfs = cfg.strategy_timeframes.get(strategy, cfg.timeframes)
        for tf in cfg_tfs:
            if tf not in fixtures:
                continue
            ohlcv = fixtures[tf]
            signals = _detect(strategy, ohlcv, cfg)
            if signals.empty:
                continue

            result = run_backtest(
                ohlcv,
                signals,
                symbol=FIXTURE_SYMBOL,
                timeframe=tf,
                strategy=strategy,
                sl_pct=cfg.sl_pct,
                tp_r=cfg.effective_tp_r(strategy, FIXTURE_SYMBOL, tf),
                fee_pct=cfg.backtest.fee_pct,
                min_sl_pct=cfg.min_sl_pct,
                volume_suppress=cfg.effective_volume_suppress(strategy),
                volume_spike_boost=cfg.effective_volume_spike_boost(strategy),
                tp_r_long=cfg.effective_tp_r(strategy, FIXTURE_SYMBOL, tf, "long"),
                tp_r_short=cfg.effective_tp_r(strategy, FIXTURE_SYMBOL, tf, "short"),
            )

            if not result.closed_trades:
                continue

            results.setdefault(strategy, {})[tf] = _extract_metrics(result)

    golden_path = FIXTURE_DIR / f"golden_{config_name}.json"

    if update:
        golden_path.write_text(
            json.dumps(
                {
                    "generated_at": date.today().isoformat(),
                    "config": config_path,
                    "strategies": results,
                },
                indent=2,
            )
            + "\n"
        )
        print(f"\nUpdated: {golden_path}")
        return

    if not golden_path.exists():
        pytest.fail(f"Golden file missing: {golden_path}\nRun: make regression-update")

    golden_data = json.loads(golden_path.read_text())
    golden = golden_data["strategies"]

    report = _diff_report(results, golden)
    assert not report, report
