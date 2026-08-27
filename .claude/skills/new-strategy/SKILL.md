---
name: new-strategy
description: >
  Guided checklist for adding a new trading strategy — covers the 4 mandatory
  edits (`analytics/strategies/<name>.py`, `analytics/strategies/_registry.py`,
  `signals/registry.py`, tests). Missing any one causes silent failures or 500s
  in the UI. Invoke when the user says "/new-strategy", proposes "add a new
  strategy", "implement strategy X", or starts wiring a fresh detector — even
  before any code is written.
allowed-tools: "*"
---

# New Strategy Wiring Checklist

⚠ **The TA detector book is FROZEN — no new boolean detectors without an explicit user
go** (inherited category verdict; CLAUDE.md § Fork lineage). This checklist stays
maintained for guard work and for any detector the operator explicitly unfreezes.

Guided workflow for adding a new trading strategy to wifey. All 4 locations must be updated together or the web UI will 500 on the strategy.

After strat-2 (PR #338) the detection layer is one file per detector under `analytics/strategies/`. There is no `analytics/indicators_lib.py` at all any more — it was removed in strat-3, so `analytics/strategies/` is the only import surface.

## The 4 mandatory edits

### 1. `analytics/strategies/<name>.py` — new per-detector file

Create one file per `detect_*` function. Mirror the layout of an existing simple detector (e.g. `wick_fills.py`):

```python
"""Detector: My Strategy — added via /new-strategy."""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _signals_to_df
# Optional: from analytics.strategies._shared import _fmt_time, volume_confirm, _find_bos_swing


def detect_my_strategy(
    df: pd.DataFrame,
    # ParamSpec args go here as keyword-only with defaults
) -> pd.DataFrame:
    """One-line description matching the StrategySpec.description.

    Returns a DataFrame with columns: open_time (int), direction (str), reason (str),
    sl_price (float), context (str). Empty DataFrame if no signals.
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []
    # ... detection logic ...
    return _signals_to_df(signals)
```

Rules:

- File name = function suffix without `detect_` (so `detect_wick_fills` → `wick_fills.py`; the *registry key* is `wick_fill`, which need not match the filename).
- One detector function per file. Do not stack helpers; put shared helpers into `analytics/strategies/_shared.py`.
- Always end with `return _signals_to_df(signals)` — that handles the empty case + column normalisation.
- No module-level side effects. No DB / network calls.

### 2. `analytics/strategies/_registry.py` — wire into both registries

Two edits in one file (the explicit-tuple-driven assembler):

```python
# 2a. Top-of-file imports — keep alphabetical
from analytics.strategies.my_strategy import detect_my_strategy

# 2b. STRATEGY_REGISTRY entry — alphabetical or grouped by strategy_type
STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    ...,
    "my_strategy": StrategySpec(
        name="my_strategy",
        description="One-line description for UI display.",
        strategy_type="price_action",   # one of: structural / fib / price_action / candlestick / flow / session
        params=[
            ParamSpec("threshold", "float", 0.5, 0.0, 1.0, "Param description for TOML tuning."),
        ],
        confidence={"4h": 2, "1d": 3, "1wk": 2},   # per-TF stars; recalibrate updates this
    ),
}

# 2c. DETECTOR_REGISTRY entry — bottom of file
DETECTOR_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    ...,
    "my_strategy": detect_my_strategy,
}
```

`KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, and `STRATEGY_TYPE_GROUPS` are auto-built from `STRATEGY_REGISTRY` — no manual update needed.

Also re-export from the package by adding the import + `__all__` entry to `analytics/strategies/__init__.py`.

### 3. `signals/registry.py` — live signal daemon

```python
from analytics.strategies import STRATEGY_REGISTRY, detect_my_strategy

SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    ...,
    "my_strategy": SignalPlugin(
        name="my_strategy",
        description=STRATEGY_REGISTRY["my_strategy"].description,
        detector=detect_my_strategy,
        confidence=STRATEGY_REGISTRY["my_strategy"].confidence,
    ),
}
```

### 4. `tests/test_my_strategy.py`

```python
import pandas as pd
from analytics.strategies.my_strategy import detect_my_strategy


def test_my_strategy_long() -> None:
    ohlcv = pd.DataFrame({...})
    result = detect_my_strategy(ohlcv)
    assert not result.empty
    assert result["direction"].iloc[0] == "long"


def test_my_strategy_no_signal() -> None:
    # Edge case: flat market → no signals
    ...
```

Rules:

- `duckdb.connect(":memory:")` for any DB-touching tests — never touch `analytics.db`.
- Pass `MagicMock` for the yfinance client where applicable.
- No real network calls.

## Strategy function signature

```python
def detect_X(
    df: pd.DataFrame,
    # Optional: extra params your strategy needs (mirror the ParamSpec list)
) -> pd.DataFrame:
```

Returns a DataFrame with columns:

- `open_time` (`int`) — Unix ms timestamp of the signal candle
- `direction` (`str`) — `"long"` or `"short"` (lowercase)
- `reason` (`str`) — human-readable description for alerts
- `sl_price` (`float`) — structural SL price (`0.0` if using fixed `sl_pct` instead)
- `context` (`str`) — extra context for Telegram alert formatting
- (optional) `tp_price` (`float`) — only if your strategy emits a structural TP (e.g. fib 1.618 ext)
- (optional) `low_volume` (`bool`) — if your detector calls `volume_confirm`

Always return via `_signals_to_df(signals)` from `_shared.py` — it normalises columns and dedups.

## Strategies needing extra data (NOT in DETECTOR_REGISTRY)

Detectors that need a second positional arg (funding rates, secondary OHLCV) cannot fit the `Callable[[pd.DataFrame], pd.DataFrame]` signature DETECTOR_REGISTRY expects. Wire them with explicit branches in `analytics/backtest_runner.py` and the `analytics/signal/cofire.py` / scanner paths instead, and exclude them from `DETECTOR_REGISTRY`.

Examples in the current codebase:

- `funding_extreme` — crypto-era leftover: the module still exists at `analytics/strategies/funding_extreme.py` but is **not registered**, and its `get_funding_rates` source was dropped at the fork. Equities have no funding rate; treat it as dead code, not a template
- `seasonality` — returns stats DataFrame, not signals; uses `seasonality_stats` from `analytics/strategies/_seasonality.py`

For these, also update `backtest_runner.detect_signals_for_strategy()` with a new branch, and add
the name to `_REGISTRY_EXCLUDED` in `tests/test_signal_registry.py`. That set is the deliberate
opt-out: `test_detector_registry_is_wired_to_the_other_two` set-diffs all three registries in both
directions and checks that SIGNAL_REGISTRY and DETECTOR_REGISTRY bind the *same* function object,
so an unlisted omission now fails `make test` rather than silently skipping backtest dispatch.

## After adding the strategy

```bash
# Run tests
make test

# Typecheck (all functions must be annotated)
make typecheck

# Format
make lint-py

# Run a quick single-symbol backtest to confirm signals fire
wifey backtest --symbol AAPL --strategy my_strategy --interval 4h

# Run full sweep and save to DB
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1

# Recalibrate star ratings in confidence_ratings DB table
wifey recalibrate --apply --config config/signal_watch.toml
```

## Adding to active signal watch config

After confirming the strategy has positive avg R in backtest:

```toml
# config/signal_watch.toml
strategies = [
    ...,
    'my_strategy',
]

# Optionally restrict to specific TFs
[strategy_timeframes]
my_strategy = ["4h", "1d"]

# Optionally set optimal tp_r from sweep
[strategy_params.my_strategy]
tp_r = 3.0
```

## Implementation files reference

| File | What to update |
| ------ | ---------------- |
| `analytics/strategies/<name>.py` | Create new file with the `detect_X()` function (one detector per file) |
| `analytics/strategies/_registry.py` | Add the import, the `STRATEGY_REGISTRY` entry, and the `DETECTOR_REGISTRY` entry |
| `analytics/strategies/__init__.py` | Add the import + `__all__` entry for eager re-export |
| `signals/registry.py` | `SignalPlugin` entry (only if the strategy is actionable for live alerts — `seasonality` is the registered-but-not-actionable case) |
| `tests/test_signal_registry.py` | Only when the strategy is a deliberate DETECTOR_REGISTRY exclusion — add it to `_REGISTRY_EXCLUDED` |
| `tests/test_<name>.py` | Unit tests for the new detector |
| `analytics/backtest_runner.py` | Only for strategies needing funding / secondary OHLCV data |

## Task: add a new strategy

When the user asks to add a new strategy:

1. Ask for: strategy name, signal logic (entry condition, SL logic, direction), any extra data requirements, target timeframes.
2. Create `analytics/strategies/<name>.py` with `detect_<name>()` — pure function, no side effects, ends with `_signals_to_df(signals)`.
3. Add the import + `StrategySpec` entry to `STRATEGY_REGISTRY` in `analytics/strategies/_registry.py`.
4. Add the `DETECTOR_REGISTRY` entry (or skip + add explicit branch in `backtest_runner.py` if needs extra data).
5. Add the eager import + `__all__` entry to `analytics/strategies/__init__.py`.
6. Add `SignalPlugin` entry to `signals/registry.py` (skip for non-actionable strategies).
7. Write at least 2 tests: one that fires a signal, one edge case that produces no signal.
8. Run `make lint-py && make typecheck && make test` (must end clean).
9. Run quick backtest: `wifey backtest --symbol AAPL --strategy <name> --interval 4h`.
10. If positive results, add to `config/signal_watch.toml` strategies list.
