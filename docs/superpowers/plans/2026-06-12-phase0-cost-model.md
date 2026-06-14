# Phase 0.4 Realistic Equity Cost Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat crypto taker-fee abstraction with a decomposed equity cost model (half-spread by liquidity bucket + square-root market impact + short borrow + commission), wired into the backtest engine behind a default-off `[backtest.cost_model]` TOML block — goldens stay byte-identical.

**Architecture:** New pure-math module `analytics/backtest/cost_model.py` (frozen `CostModel` + `cost_r(trade, ctx)`; no DB, no engine import — a `TradeLike` Protocol keeps it engine-free). `Trade` gains optional `cost_model`/`cost_ctx` fields; when set, `pnl_r` charges the decomposed cost instead of the flat `fee_pct`. `run_backtest` builds a causal per-signal `CostContext` (trailing ADV + daily sigma from OHLCV). The model's canonical JSON stamps `backtest_runs` (nullable `cost_model` column, migration-list only) and suffixes `_backtest_run_id` so the scanner's L2 cache can never serve a flat-fee snapshot for a cost-model config.

**Tech Stack:** Python 3.11, pandas/numpy, DuckDB, tomllib, pytest. mypy strict + ruff.

**Spec:** `docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md` §4.4. This plan is **step 1 of the two-step delivery**: default-off module + wiring + tests. Step 2 (flipping it on for live configs, which deliberately moves goldens → `make db-update` + golden refresh + recalibrate) is a separate, reviewed PR.

**Plan-time decisions on the spec's open questions:**

- **Flip-on granularity (step 2):** all configs at once, NOT strategy-by-strategy. Costs are a property of the market/execution venue, not of a strategy; per-strategy cost-on/off would corrupt cross-strategy comparability of `avg_r` (ratings, conflict resolver, hard-mode gate all compare across strategies).
- **Borrow rate:** conservative constant `0.01`/yr (above GC ~0.3%, well below hard-to-borrow), parameterized as `borrow_rate_annual` for a future per-symbol feed. Documented constraint in the module docstring.
- **Web `POST /api/backtest`:** stays flat-fee (request-driven, explicit `fee_pct` in the body; flipping a TOML must not silently change an explicit API contract). Revisit at step 2 if needed.
- **`run_backtest_cmd` single-combo CLI, combo/cross-TF engines, `param_sweep`:** not wired in step 1. Sweeps are frozen; combos are a step-2 question; single-combo mode takes explicit args, not TOML. Documented below.

**Cost math (single source of truth for tests):** position is sized to risk 1R over `risk = |entry − sl|`, so a cost `f` expressed as a fraction of notional converts to R as `f × entry_price / risk` (same conversion the flat-fee path uses).

- `spread_r = 2 × (half_spread_bps[bucket]/10⁴) × entry/risk` — per leg, bucket by dollar ADV
- `impact_r = 2 × impact_coef × σ_daily × √(notional_usd/ADV) × entry/risk` — per leg; 0 when ADV/σ unknown
- `borrow_r = borrow_rate_annual × (holding_days/365) × entry/risk` — shorts only, once (not per leg), calendar days
- `commission_r = 2 × (commission_bps/10⁴) × entry/risk`

Conservative fallbacks: unknown/non-positive ADV → widest spread bucket and zero impact; unknown σ → zero impact.

---

## File structure

| File | Action | Responsibility |
| ---- | ------ | -------------- |
| `analytics/backtest/cost_model.py` | Create | Frozen `CostModel`/`CostContext`/`CostBreakdown`, `TradeLike` Protocol, `build_cost_context`, `bars_per_day_for_tf`, `cost_model_from_toml` — pure math, zero project imports |
| `tests/test_cost_model.py` | Create | Hand-computed cost examples, bucket edges, context builder (incl. causality property), TOML parsing |
| `analytics/backtest/engine.py` | Modify | `Trade.cost_model`/`.cost_ctx` fields, `pnl_r` branch, `run_backtest(cost_model=...)` + per-signal ctx |
| `tests/test_backtest_lib.py` | Modify | `pnl_r` cost branch + engine wiring tests (default path identical, fills untouched) |
| `analytics/signal_config.py` | Modify | `BacktestFilterConfig.cost_model` parsed from `[backtest.cost_model]` |
| `analytics/backtest_config.py` | Modify | `BacktestSweepConfig.cost_model` parsed from `[backtest.cost_model]` |
| `tests/test_signal_config.py`, `tests/test_backtest_config.py` | Modify | Loader tests (absent/disabled → None; enabled → model with overrides) |
| `analytics/store/backtest_runs.py` | Modify | `_backtest_run_id(cost_model=...)` suffix; `upsert_backtest_run(cost_model=...)` column |
| `analytics/store/schema.py` | Modify | `("cost_model", "TEXT")` in the `backtest_runs` migration list (NEVER in CREATE TABLE) |
| `tests/test_data_store.py`, `tests/test_recalibrate_lib.py` | Modify | run_id suffix tests + one more NULL/`?` in the 4 positional `INSERT INTO backtest_runs` fixtures |
| `analytics/backtest_runner.py` | Modify | Sweep mode: 3 `run_backtest` call sites + the phase-3 `upsert_backtest_run` |
| `analytics/signal/bt_cache.py` | Modify | `_compute_backtest(cost_model=...)` pass-through |
| `analytics/signal/scanner.py` | Modify | 2 `_compute_backtest` calls, the `_backtest_run_id` cache-key call, the save-loop `upsert_backtest_run` |
| `config/strategy_params.toml` | Modify | Commented `[backtest.cost_model]` reference block |
| `README.md`, `CLAUDE.md` | Modify | Document the module + config surface |

Not touched: `analytics/backtest/__init__.py` / `backtest_lib.py` shims (follow the `LiveParityConfig` precedent — consumers import `analytics.backtest.cost_model` directly), combo/cross_tf/param_sweep, web routers, regression goldens (NO regen — `make test-regression` must pass as-is).

---

### Task 0: Branch setup

**Files:** none (git only)

- [ ] **Step 1: Verify identity and create branch**

```bash
cd /home/kng/repo/buibui-wifey-wall-street-bot
git config --local user.email   # MUST print ngkhaijian@gmail.com — stop if not
git checkout main && git pull --ff-only
git checkout -b feat/phase0-cost-model
```

---

### Task 1: `cost_model.py` core — dataclasses + cost math

**Files:**

- Create: `analytics/backtest/cost_model.py`
- Test: `tests/test_cost_model.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_model.py`:

```python
"""Tests for the Phase 0.4 equity cost model (analytics/backtest/cost_model.py)."""

from dataclasses import dataclass

import pytest

from analytics.backtest.cost_model import (
    CostBreakdown,
    CostContext,
    CostModel,
)

_DAY_MS = 86_400_000


@dataclass
class _FakeTrade:
    """Minimal TradeLike — keeps this test file engine-free."""

    direction: str = "long"
    entry_price: float = 100.0
    sl_price: float = 98.0
    entry_time: int = 0
    exit_time: int | None = _DAY_MS


class TestCostModelValidation:
    def test_defaults_construct(self) -> None:
        model = CostModel()
        assert model.adv_thresholds == (5e6, 5e7, 5e8)
        assert model.half_spread_bps == (20.0, 8.0, 3.0, 1.0)
        assert model.borrow_rate_annual == 0.01

    def test_spread_bucket_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="one more entry"):
            CostModel(adv_thresholds=(1e6,), half_spread_bps=(10.0,))

    def test_non_ascending_thresholds_raise(self) -> None:
        with pytest.raises(ValueError, match="ascending"):
            CostModel(
                adv_thresholds=(5e7, 5e6), half_spread_bps=(20.0, 8.0, 3.0)
            )

    def test_negative_params_raise(self) -> None:
        with pytest.raises(ValueError):
            CostModel(impact_coef=-0.1)
        with pytest.raises(ValueError):
            CostModel(notional_usd=0.0)
        with pytest.raises(ValueError):
            CostModel(borrow_rate_annual=-0.01)
        with pytest.raises(ValueError):
            CostModel(commission_bps=-1.0)
        with pytest.raises(ValueError):
            CostModel(adv_window_days=0.0)


class TestSpreadBucket:
    def test_bucket_edges(self) -> None:
        model = CostModel()
        # 0 = least liquid (widest spread); an ADV exactly at a threshold
        # falls in the MORE liquid bucket (searchsorted side="right").
        assert model.spread_bucket(None) == 0
        assert model.spread_bucket(0.0) == 0
        assert model.spread_bucket(-1.0) == 0
        assert model.spread_bucket(1e6) == 0
        assert model.spread_bucket(5e6) == 1
        assert model.spread_bucket(1e7) == 1
        assert model.spread_bucket(5e7) == 2
        assert model.spread_bucket(1e8) == 2
        assert model.spread_bucket(5e8) == 3
        assert model.spread_bucket(1e9) == 3


class TestCostBreakdown:
    def test_hand_computed_long(self) -> None:
        # risk = |100 - 98| = 2 → notional_to_r = entry/risk = 50.
        # bucket(1e8) = 2 → 3 bps half-spread → spread_r = 2 × 3e-4 × 50 = 0.03
        # impact/leg = 1.0 × 0.02 × sqrt(10_000 / 1e8) = 2e-4 → impact_r = 2 × 2e-4 × 50 = 0.02
        model = CostModel()
        ctx = CostContext(adv_dollars=1e8, sigma_daily=0.02)
        bd = model.cost_breakdown(_FakeTrade(), ctx)
        assert bd.spread_r == pytest.approx(0.03)
        assert bd.impact_r == pytest.approx(0.02)
        assert bd.borrow_r == 0.0  # longs pay no borrow
        assert bd.commission_r == 0.0  # retail default
        assert bd.total_r == pytest.approx(0.05)
        assert model.cost_r(_FakeTrade(), ctx) == pytest.approx(0.05)

    def test_hand_computed_short_borrow(self) -> None:
        # 2 calendar days held → borrow_r = 0.01 × (2/365) × 50; charged once.
        model = CostModel()
        ctx = CostContext(adv_dollars=1e8, sigma_daily=0.02)
        trade = _FakeTrade(
            direction="short", sl_price=102.0, exit_time=2 * _DAY_MS
        )
        bd = model.cost_breakdown(trade, ctx)
        expected_borrow = 0.01 * (2.0 / 365.0) * 50.0
        assert bd.borrow_r == pytest.approx(expected_borrow)
        assert bd.total_r == pytest.approx(0.03 + 0.02 + expected_borrow)

    def test_open_short_pays_no_borrow(self) -> None:
        model = CostModel()
        trade = _FakeTrade(direction="short", sl_price=102.0, exit_time=None)
        bd = model.cost_breakdown(trade, CostContext(adv_dollars=1e8, sigma_daily=0.02))
        assert bd.borrow_r == 0.0

    def test_none_ctx_falls_back_conservatively(self) -> None:
        # No ADV → widest bucket (20 bps); no sigma → zero impact.
        model = CostModel()
        bd = model.cost_breakdown(_FakeTrade(), None)
        assert bd.spread_r == pytest.approx(2 * 0.0020 * 50.0)
        assert bd.impact_r == 0.0

    def test_zero_risk_is_zero_cost(self) -> None:
        model = CostModel()
        trade = _FakeTrade(sl_price=100.0)  # entry == sl
        assert model.cost_breakdown(trade, None) == CostBreakdown()

    def test_commission_bps(self) -> None:
        model = CostModel(commission_bps=5.0)
        bd = model.cost_breakdown(_FakeTrade(), None)
        assert bd.commission_r == pytest.approx(2 * 0.0005 * 50.0)

    def test_to_json_is_canonical(self) -> None:
        a = CostModel().to_json()
        b = CostModel().to_json()
        assert a == b
        assert '"borrow_rate_annual":0.01' in a
        # Different params → different stamp (feeds the run_id hash).
        assert CostModel(impact_coef=0.5).to_json() != a
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cost_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.backtest.cost_model'`

- [ ] **Step 3: Write the implementation**

Create `analytics/backtest/cost_model.py`:

```python
"""Phase 0.4 realistic equity cost model.

Replaces the flat crypto taker-fee abstraction with a decomposed equity cost
stack: half-spread by liquidity bucket + square-root market impact + short
borrow over the holding period + commission. Pure math over trade/context
primitives — no DB, no engine import, no module-level side effects.

Cost is expressed in R (1R = amount risked at the stop). Position is sized to
risk 1R over ``risk = |entry - sl|``, so a cost ``f`` (fraction of notional)
converts to R as ``f * entry_price / risk`` — the same conversion the
engine's flat-fee path uses.

Defaults are deliberately conservative (overestimate cost):

- Spread buckets by trailing dollar ADV: <$5M -> 20 bps half-spread,
  $5M-$50M -> 8, $50M-$500M -> 3, >$500M -> 1. Unknown ADV -> widest bucket.
- Impact: sqrt law ``impact_coef * sigma_daily * sqrt(notional / ADV)``,
  charged per leg. Unknown ADV/sigma -> 0 (the spread bucket already
  defaulted wide).
- Borrow (shorts only): flat 1%/yr on notional over the holding period in
  calendar days, charged once. Free per-symbol borrow data is scarce —
  documented constraint, parameterized for a later feed.
- Commission: 0 bps (US retail).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

#: Closed US-equity bars per trading day for the supported timeframes.
#: 1h = 7 yfinance session bars; 4h = 2 resampled bars; 1wk = 1/5 day.
#: Used to scale per-bar dollar volume to daily ADV and per-bar return
#: stdev to daily sigma. Unknown TFs fall back to 1.0 (treat bars as days).
BARS_PER_DAY: dict[str, float] = {"1h": 7.0, "4h": 2.0, "1d": 1.0, "1wk": 0.2}

_MS_PER_DAY = 86_400_000.0
_DAYS_PER_YEAR = 365.0


def bars_per_day_for_tf(timeframe: str) -> float:
    """Closed bars per trading day for a timeframe; unknown TFs -> 1.0."""
    return BARS_PER_DAY.get(timeframe, 1.0)


class TradeLike(Protocol):
    """Structural view of engine.Trade — keeps this module engine-free."""

    direction: str
    entry_price: float
    sl_price: float
    entry_time: int
    exit_time: int | None


@dataclass(frozen=True)
class CostContext:
    """Per-trade market context, built causally from bars up to the signal bar.

    None fields mean "not computable from available history" — cost_r then
    falls back conservatively (widest spread bucket) or to zero (impact).
    """

    adv_dollars: float | None = None
    sigma_daily: float | None = None


@dataclass(frozen=True)
class CostBreakdown:
    """Per-trade cost decomposition, each component in R."""

    spread_r: float = 0.0
    impact_r: float = 0.0
    borrow_r: float = 0.0
    commission_r: float = 0.0

    @property
    def total_r(self) -> float:
        return self.spread_r + self.impact_r + self.borrow_r + self.commission_r


@dataclass(frozen=True)
class CostModel:
    """Equity cost-model parameters. Frozen so a single instance can be
    shared across every Trade of a backtest run.

    adv_thresholds: ascending dollar-ADV bucket edges. An ADV exactly at a
    threshold falls in the more liquid bucket.
    half_spread_bps: one entry per bucket, index 0 = least liquid; must have
    exactly ``len(adv_thresholds) + 1`` entries.
    """

    adv_thresholds: tuple[float, ...] = (5e6, 5e7, 5e8)
    half_spread_bps: tuple[float, ...] = (20.0, 8.0, 3.0, 1.0)
    impact_coef: float = 1.0
    notional_usd: float = 10_000.0
    borrow_rate_annual: float = 0.01
    commission_bps: float = 0.0
    adv_window_days: float = 20.0

    def __post_init__(self) -> None:
        if len(self.half_spread_bps) != len(self.adv_thresholds) + 1:
            raise ValueError(
                "half_spread_bps must have exactly one more entry than "
                "adv_thresholds (one spread per bucket)"
            )
        if any(t <= 0.0 for t in self.adv_thresholds):
            raise ValueError("adv_thresholds must be positive")
        if any(
            b <= a
            for a, b in zip(self.adv_thresholds, self.adv_thresholds[1:], strict=False)
        ):
            raise ValueError("adv_thresholds must be strictly ascending")
        if any(s < 0.0 for s in self.half_spread_bps):
            raise ValueError("half_spread_bps must be non-negative")
        if self.impact_coef < 0.0:
            raise ValueError("impact_coef must be non-negative")
        if self.notional_usd <= 0.0:
            raise ValueError("notional_usd must be positive")
        if self.borrow_rate_annual < 0.0:
            raise ValueError("borrow_rate_annual must be non-negative")
        if self.commission_bps < 0.0:
            raise ValueError("commission_bps must be non-negative")
        if self.adv_window_days <= 0.0:
            raise ValueError("adv_window_days must be positive")

    def spread_bucket(self, adv_dollars: float | None) -> int:
        """Liquidity bucket index; 0 = least liquid (widest spread).

        Unknown or non-positive ADV falls back to bucket 0 — conservative.
        """
        if adv_dollars is None or adv_dollars <= 0.0:
            return 0
        return int(np.searchsorted(self.adv_thresholds, adv_dollars, side="right"))

    def cost_breakdown(
        self, trade: TradeLike, ctx: CostContext | None
    ) -> CostBreakdown:
        """Decomposed per-trade cost in R. Zero when risk is zero (the
        engine's pnl_r already returns None for those trades)."""
        risk = abs(trade.entry_price - trade.sl_price)
        if risk == 0.0:
            return CostBreakdown()
        notional_to_r = trade.entry_price / risk
        adv = ctx.adv_dollars if ctx is not None else None
        sigma = ctx.sigma_daily if ctx is not None else None

        half_spread_frac = self.half_spread_bps[self.spread_bucket(adv)] / 10_000.0
        spread_r = 2.0 * half_spread_frac * notional_to_r

        impact_r = 0.0
        if adv is not None and adv > 0.0 and sigma is not None and sigma > 0.0:
            impact_frac = self.impact_coef * sigma * math.sqrt(self.notional_usd / adv)
            impact_r = 2.0 * impact_frac * notional_to_r

        borrow_r = 0.0
        if trade.direction == "short" and trade.exit_time is not None:
            holding_days = max(0.0, (trade.exit_time - trade.entry_time) / _MS_PER_DAY)
            borrow_r = (
                self.borrow_rate_annual * (holding_days / _DAYS_PER_YEAR) * notional_to_r
            )

        commission_r = 2.0 * (self.commission_bps / 10_000.0) * notional_to_r
        return CostBreakdown(
            spread_r=spread_r,
            impact_r=impact_r,
            borrow_r=borrow_r,
            commission_r=commission_r,
        )

    def cost_r(self, trade: TradeLike, ctx: CostContext | None) -> float:
        """Total per-trade cost in R (spread + impact + borrow + commission)."""
        return self.cost_breakdown(trade, ctx).total_r

    def to_json(self) -> str:
        """Canonical JSON of the parameters. Stamps backtest_runs rows and
        feeds the run_id hash, so it must be deterministic."""
        return json.dumps(
            {
                "adv_thresholds": list(self.adv_thresholds),
                "half_spread_bps": list(self.half_spread_bps),
                "impact_coef": self.impact_coef,
                "notional_usd": self.notional_usd,
                "borrow_rate_annual": self.borrow_rate_annual,
                "commission_bps": self.commission_bps,
                "adv_window_days": self.adv_window_days,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
```

(`Any` is imported now because Task 2 adds `build_cost_context` with
`np.ndarray[Any, ...]` annotations; if ruff flags it unused at this step,
add it in Task 2 instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cost_model.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint + typecheck, then commit**

```bash
make lint-py && make typecheck
git add analytics/backtest/cost_model.py tests/test_cost_model.py
git commit -m "feat(cost): Phase 0.4 CostModel core — spread/impact/borrow/commission in R"
```

---

### Task 2: `build_cost_context` — causal ADV + daily sigma from OHLCV arrays

**Files:**

- Modify: `analytics/backtest/cost_model.py` (append)
- Test: `tests/test_cost_model.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_model.py` (and extend the import block with
`BARS_PER_DAY`, `bars_per_day_for_tf`, `build_cost_context`, plus
`import numpy as np` at the top):

```python
class TestBarsPerDay:
    def test_known_timeframes(self) -> None:
        assert BARS_PER_DAY["4h"] == 2.0
        assert bars_per_day_for_tf("1d") == 1.0
        assert bars_per_day_for_tf("1wk") == 0.2

    def test_unknown_timeframe_falls_back_to_one(self) -> None:
        assert bars_per_day_for_tf("15m") == 1.0


class TestBuildCostContext:
    def test_hand_computed_adv_and_sigma(self) -> None:
        # closes 100 -> 102 (+2%) -> 99.96 (-2%); population stdev of
        # [0.02, -0.02] = 0.02; bars_per_day=4 -> sigma_daily = 0.02*sqrt(4).
        closes = np.array([100.0, 102.0, 99.96])
        volumes = np.array([1000.0, 1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 2, 4.0, 20)
        expected_adv = (100_000.0 + 102_000.0 + 99_960.0) / 3.0 * 4.0
        assert ctx.adv_dollars == pytest.approx(expected_adv)
        assert ctx.sigma_daily == pytest.approx(0.04)

    def test_window_truncates_old_bars(self) -> None:
        # window_bars=2 -> only the last two bars feed ADV/sigma.
        closes = np.array([1.0, 100.0, 100.0])
        volumes = np.array([1e9, 1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 2, 1.0, 2)
        assert ctx.adv_dollars == pytest.approx(100_000.0)

    def test_causal_future_bars_do_not_change_context(self) -> None:
        closes = np.array([100.0, 101.0, 99.0, 102.0, 98.0, 103.0])
        volumes = np.array([1e6, 2e6, 3e6, 4e6, 5e6, 6e6])
        before = build_cost_context(closes[:3], volumes[:3], 2, 1.0, 20)
        after = build_cost_context(closes, volumes, 2, 1.0, 20)
        assert before == after

    def test_insufficient_history_yields_nones(self) -> None:
        closes = np.array([100.0, 101.0])
        volumes = np.array([1000.0, 1000.0])
        ctx = build_cost_context(closes, volumes, 1, 1.0, 20)
        assert ctx.adv_dollars is not None  # 2 bars are enough for ADV
        assert ctx.sigma_daily is None  # but not for a return stdev

    def test_zero_volume_yields_no_adv(self) -> None:
        closes = np.array([100.0, 100.0, 100.0])
        volumes = np.array([0.0, 0.0, 0.0])
        ctx = build_cost_context(closes, volumes, 2, 1.0, 20)
        assert ctx.adv_dollars is None

    def test_flat_series_has_zero_sigma_not_none(self) -> None:
        closes = np.array([100.0, 100.0, 100.0, 100.0])
        volumes = np.array([1000.0] * 4)
        ctx = build_cost_context(closes, volumes, 3, 1.0, 20)
        assert ctx.sigma_daily == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cost_model.py -v -k "BarsPerDay or BuildCostContext"`
Expected: FAIL with `ImportError` (cannot import `build_cost_context`)

- [ ] **Step 3: Write the implementation**

Append to `analytics/backtest/cost_model.py`:

```python
def build_cost_context(
    closes: np.ndarray[Any, np.dtype[np.float64]],
    volumes: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
    bars_per_day: float,
    window_bars: int,
) -> CostContext:
    """Trailing-window dollar ADV + daily sigma ending at bar ``idx`` (inclusive).

    Causal by construction: uses only bars [idx-window_bars+1, idx]. The
    signal bar's close/volume are known once the bar closes; the engine
    enters at the NEXT bar's open.

    ADV = mean(close x volume) x bars_per_day (scales per-bar dollar volume
    to a daily figure). sigma_daily = population stdev of per-bar simple
    returns x sqrt(bars_per_day). Fields are None when not computable
    (too little history, zero volume, non-finite values) — cost_breakdown
    then falls back conservatively.
    """
    lo = max(0, idx - window_bars + 1)
    c = closes[lo : idx + 1]
    v = volumes[lo : idx + 1]

    adv: float | None = None
    if len(c) >= 1:
        adv = float(np.mean(c * v)) * bars_per_day
        if not math.isfinite(adv) or adv <= 0.0:
            adv = None

    sigma: float | None = None
    if len(c) >= 3:
        with np.errstate(divide="ignore", invalid="ignore"):
            rets = np.diff(c) / c[:-1]
        sigma = float(np.std(rets)) * math.sqrt(bars_per_day)
        if not math.isfinite(sigma):
            sigma = None

    return CostContext(adv_dollars=adv, sigma_daily=sigma)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cost_model.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint + typecheck, then commit**

```bash
make lint-py && make typecheck
git add analytics/backtest/cost_model.py tests/test_cost_model.py
git commit -m "feat(cost): causal trailing-window CostContext builder (ADV + daily sigma)"
```

---

### Task 3: `cost_model_from_toml` — `[backtest.cost_model]` parser

**Files:**

- Modify: `analytics/backtest/cost_model.py` (append)
- Test: `tests/test_cost_model.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_model.py` (extend the import block with
`cost_model_from_toml`):

```python
class TestCostModelFromToml:
    def test_absent_block_returns_none(self) -> None:
        assert cost_model_from_toml(None) is None

    def test_disabled_block_returns_none(self) -> None:
        assert cost_model_from_toml({"enabled": False}) is None
        assert cost_model_from_toml({}) is None  # enabled defaults to false

    def test_non_table_raises(self) -> None:
        with pytest.raises(ValueError, match="TOML table"):
            cost_model_from_toml(True)

    def test_enabled_with_defaults(self) -> None:
        model = cost_model_from_toml({"enabled": True})
        assert model == CostModel()

    def test_enabled_with_overrides(self) -> None:
        model = cost_model_from_toml(
            {
                "enabled": True,
                "adv_thresholds": [1e6, 1e7],
                "half_spread_bps": [25.0, 10.0, 4.0],
                "impact_coef": 0.5,
                "notional_usd": 25_000.0,
                "borrow_rate_annual": 0.02,
                "commission_bps": 1.0,
                "adv_window_days": 10.0,
            }
        )
        assert model is not None
        assert model.adv_thresholds == (1e6, 1e7)
        assert model.half_spread_bps == (25.0, 10.0, 4.0)
        assert model.impact_coef == 0.5
        assert model.notional_usd == 25_000.0
        assert model.borrow_rate_annual == 0.02
        assert model.commission_bps == 1.0
        assert model.adv_window_days == 10.0

    def test_invalid_values_raise_with_block_name(self) -> None:
        with pytest.raises(ValueError, match="backtest.cost_model"):
            cost_model_from_toml(
                {"enabled": True, "adv_thresholds": [1e7, 1e6]}
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_cost_model.py -v -k CostModelFromToml`
Expected: FAIL with `ImportError` (cannot import `cost_model_from_toml`)

- [ ] **Step 3: Write the implementation**

Append to `analytics/backtest/cost_model.py`:

```python
def cost_model_from_toml(raw: object) -> CostModel | None:
    """Parse a ``[backtest.cost_model]`` TOML table into a CostModel.

    Returns None when the block is absent or ``enabled`` is falsy — the
    engine then keeps the legacy flat-fee path (byte-identical default).
    Raises ValueError on a malformed block.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            "backtest.cost_model must be a TOML table (e.g. [backtest.cost_model])"
        )
    if not raw.get("enabled", False):
        return None
    defaults = CostModel()
    try:
        return CostModel(
            adv_thresholds=tuple(
                float(x) for x in raw.get("adv_thresholds", defaults.adv_thresholds)
            ),
            half_spread_bps=tuple(
                float(x) for x in raw.get("half_spread_bps", defaults.half_spread_bps)
            ),
            impact_coef=float(raw.get("impact_coef", defaults.impact_coef)),
            notional_usd=float(raw.get("notional_usd", defaults.notional_usd)),
            borrow_rate_annual=float(
                raw.get("borrow_rate_annual", defaults.borrow_rate_annual)
            ),
            commission_bps=float(raw.get("commission_bps", defaults.commission_bps)),
            adv_window_days=float(
                raw.get("adv_window_days", defaults.adv_window_days)
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid [backtest.cost_model] block: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_cost_model.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint + typecheck, then commit**

```bash
make lint-py && make typecheck
git add analytics/backtest/cost_model.py tests/test_cost_model.py
git commit -m "feat(cost): cost_model_from_toml parser for [backtest.cost_model]"
```

---

### Task 4: Engine wiring — `Trade` fields, `pnl_r` branch, `run_backtest(cost_model=...)`

**Files:**

- Modify: `analytics/backtest/engine.py`
- Test: `tests/test_backtest_lib.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest_lib.py` (add to its imports:
`from analytics.backtest.cost_model import CostContext, CostModel`):

```python
class TestTradePnlRWithCostModel:
    def test_cost_model_replaces_flat_fee(self) -> None:
        trade = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_time=2,
            exit_price=104.0,
            outcome="win",
            fee_pct=0.01,  # must be IGNORED when cost_model is set
            cost_model=CostModel(),
            cost_ctx=CostContext(adv_dollars=1e8, sigma_daily=0.02),
        )
        # raw_r = (104-100)/2 = 2.0; cost = 0.03 spread + 0.02 impact = 0.05
        assert trade.pnl_r == pytest.approx(2.0 - 0.05)

    def test_default_none_keeps_flat_fee_path(self) -> None:
        flat = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_time=2,
            exit_price=104.0,
            outcome="win",
            fee_pct=0.001,
        )
        # fee_drag_r = 2 × 0.001 × 100 / 2 = 0.1
        assert flat.pnl_r == pytest.approx(2.0 - 0.1)


class TestRunBacktestCostModel:
    def _ohlcv(self) -> pd.DataFrame:
        # 10 hourly bars; signal at bar 5 → entry bar 6 open=100,
        # sl_pct=0.02 → SL 98 (never hit, lows 99.5), tp_r=2 → TP 104
        # hit at bar 8 (high 105).
        n = 10
        return pd.DataFrame(
            {
                "open_time": [i * 3_600_000 for i in range(n)],
                "open": [100.0] * n,
                "high": [101.0] * 8 + [105.0, 105.0],
                "low": [99.5] * n,
                "close": [100.0] * n,
                "volume": [1_000_000.0] * n,
            }
        )

    def _signals(self) -> pd.DataFrame:
        return _make_signals([{"open_time": 5 * 3_600_000, "direction": "long"}])

    def test_cost_model_none_is_identical(self) -> None:
        base = run_backtest(
            self._ohlcv(), self._signals(), "SPY", "1h", "fvg", fee_pct=0.001
        )
        explicit = run_backtest(
            self._ohlcv(),
            self._signals(),
            "SPY",
            "1h",
            "fvg",
            fee_pct=0.001,
            cost_model=None,
        )
        assert [t.__dict__ for t in base.trades] == [
            t.__dict__ for t in explicit.trades
        ]

    def test_cost_model_changes_pnl_not_fills(self) -> None:
        model = CostModel()
        base = run_backtest(self._ohlcv(), self._signals(), "SPY", "1h", "fvg")
        costed = run_backtest(
            self._ohlcv(), self._signals(), "SPY", "1h", "fvg", cost_model=model
        )
        assert len(base.trades) == len(costed.trades) == 1
        b, c = base.trades[0], costed.trades[0]
        # Fills/outcomes untouched — the model only re-prices pnl_r.
        assert (c.entry_time, c.exit_time, c.outcome, c.entry_price, c.exit_price) == (
            b.entry_time,
            b.exit_time,
            b.outcome,
            b.entry_price,
            b.exit_price,
        )
        assert c.cost_model is model
        assert c.cost_ctx is not None
        assert c.cost_ctx.adv_dollars is not None and c.cost_ctx.adv_dollars > 0.0
        expected_cost = model.cost_r(c, c.cost_ctx)
        assert expected_cost > 0.0
        assert b.pnl_r is not None and c.pnl_r is not None
        assert c.pnl_r == pytest.approx(b.pnl_r - expected_cost)
```

(If `pytest` or `pd` are not already imported at the top of
`tests/test_backtest_lib.py`, they are — this file already uses both.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_backtest_lib.py -v -k CostModel`
Expected: FAIL with `TypeError: Trade.__init__() got an unexpected keyword argument 'cost_model'`

- [ ] **Step 3: Wire the engine**

In `analytics/backtest/engine.py`:

3a. Add the import after the `stats_overfit` import (line ~22):

```python
from analytics.backtest.cost_model import (
    CostContext,
    CostModel,
    bars_per_day_for_tf,
    build_cost_context,
)
```

3b. Extend `Trade` — append after `volume_spike: bool = False`:

```python
    # Phase 0.4: when set, pnl_r charges the decomposed equity cost stack
    # (spread + impact + borrow + commission) instead of the flat fee_pct.
    cost_model: CostModel | None = None
    cost_ctx: CostContext | None = None
```

3c. Replace the tail of `pnl_r` — the existing lines

```python
        fee_drag_r = 2.0 * self.fee_pct * self.entry_price / risk
        return raw_r - fee_drag_r
```

become

```python
        if self.cost_model is not None:
            return raw_r - self.cost_model.cost_r(self, self.cost_ctx)
        fee_drag_r = 2.0 * self.fee_pct * self.entry_price / risk
        return raw_r - fee_drag_r
```

and extend the `pnl_r` docstring with one paragraph:

```text
        Cost-model path (Phase 0.4): when cost_model is set, the flat fee is
        replaced by the decomposed equity cost stack (spread + impact +
        borrow + commission) priced from cost_ctx — fee_pct is ignored.
```

3d. Add the keyword-only parameter to `run_backtest` after
`htf_slope_series_by_anchor=...`:

```python
    cost_model: CostModel | None = None,
```

and document it at the end of the docstring's parameter notes:

```text
    cost_model: Phase 0.4 equity cost model. When set, each Trade carries the
        model plus a causal CostContext (trailing ADV + daily sigma ending at
        the signal bar) and pnl_r replaces the flat fee_pct with the
        decomposed cost. None (default) keeps the flat-fee path byte-identical.
```

3e. After the `closes_np = ...` extraction line, add:

```python
    # Phase 0.4: pre-extract volume + resolve the context window only when a
    # cost model is active — the default path stays untouched.
    volumes_np: np.ndarray[Any, np.dtype[np.float64]] | None = None
    cost_bars_per_day = 1.0
    cost_window_bars = 0
    if cost_model is not None:
        volumes_np = ohlcv["volume"].to_numpy(dtype=float)
        cost_bars_per_day = bars_per_day_for_tf(timeframe)
        cost_window_bars = max(
            2, round(cost_model.adv_window_days * cost_bars_per_day)
        )
```

3f. Immediately before the `trade = Trade(` construction, add:

```python
        cost_ctx: CostContext | None = None
        if cost_model is not None and volumes_np is not None:
            cost_ctx = build_cost_context(
                closes_np, volumes_np, sig_idx, cost_bars_per_day, cost_window_bars
            )
```

and extend the constructor's last two kwargs:

```python
            low_volume=is_low_vol,
            volume_spike=is_spike,
            cost_model=cost_model,
            cost_ctx=cost_ctx,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_backtest_lib.py tests/test_cost_model.py -v`
Expected: PASS (all — including every pre-existing engine test, untouched)

- [ ] **Step 5: Prove goldens unmoved, then commit**

```bash
make lint-py && make typecheck
poetry run pytest tests/test_regression.py -v   # NO regen — must pass as-is
git add analytics/backtest/engine.py tests/test_backtest_lib.py
git commit -m "feat(cost): wire CostModel into Trade.pnl_r + run_backtest (default-off, byte-identical)"
```

---

### Task 5: Config loaders — `[backtest.cost_model]` in both TOML surfaces

**Files:**

- Modify: `analytics/signal_config.py` (`BacktestFilterConfig` + `load_signal_config`)
- Modify: `analytics/backtest_config.py` (`BacktestSweepConfig` + `load_backtest_config`)
- Test: `tests/test_signal_config.py`, `tests/test_backtest_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_signal_config.py` (add
`from analytics.backtest.cost_model import CostModel` to its imports):

```python
class TestCostModelConfig:
    def test_absent_block_is_none(self, tmp_path: Path) -> None:
        p = _write_toml(tmp_path, 'timeframes = ["4h"]\n')
        assert load_signal_config(p).backtest.cost_model is None

    def test_disabled_block_is_none(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]

[backtest.cost_model]
enabled = false
"""
        p = _write_toml(tmp_path, content)
        assert load_signal_config(p).backtest.cost_model is None

    def test_enabled_block_parses_overrides(self, tmp_path: Path) -> None:
        content = """
timeframes = ["4h"]

[backtest.cost_model]
enabled = true
impact_coef = 0.5
borrow_rate_annual = 0.02
"""
        p = _write_toml(tmp_path, content)
        model = load_signal_config(p).backtest.cost_model
        assert model is not None
        assert model.impact_coef == 0.5
        assert model.borrow_rate_annual == 0.02
        # Unset keys keep the conservative defaults.
        assert model.half_spread_bps == CostModel().half_spread_bps
```

Append to `tests/test_backtest_config.py`:

```python
class TestCostModelBacktestConfig:
    def test_defaults_to_none(self) -> None:
        assert BacktestSweepConfig().cost_model is None

    def test_loads_enabled_block(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["SPY"]\n'
            "\n"
            "[backtest.cost_model]\n"
            "enabled = true\n"
            "notional_usd = 25000.0\n"
        )
        cfg = load_backtest_config(p)
        assert cfg.cost_model is not None
        assert cfg.cost_model.notional_usd == 25_000.0

    def test_disabled_block_is_none(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.toml"
        p.write_text(
            'symbols = ["SPY"]\n'
            "\n"
            "[backtest.cost_model]\n"
            "enabled = false\n"
        )
        assert load_backtest_config(p).cost_model is None
```

(Match the existing import style of each test file — both already import
`Path`, `load_signal_config` / `load_backtest_config`, and
`BacktestSweepConfig`; add what is missing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_signal_config.py tests/test_backtest_config.py -v -k CostModel`
Expected: FAIL with `AttributeError: ... no attribute 'cost_model'`

- [ ] **Step 3: Wire both loaders**

3a. `analytics/signal_config.py` — add to the imports:

```python
from analytics.backtest.cost_model import CostModel, cost_model_from_toml
```

In `BacktestFilterConfig`, after the `fee_pct: float = 0.0` field:

```python
    # Phase 0.4 equity cost model — None = legacy flat fee_pct path.
    # Parsed from the [backtest.cost_model] TOML block; when set it replaces
    # fee_pct inside pnl_r. Off by default so goldens stay byte-identical.
    cost_model: CostModel | None = None
```

In `load_signal_config`'s `BacktestFilterConfig(...)` construction, after the
`volume_spike_boost=...` line:

```python
        cost_model=cost_model_from_toml(raw_bt.get("cost_model")),
```

3b. `analytics/backtest_config.py` — extend the existing
`from analytics.backtest.live_parity_config import LiveParityConfig` import
area with:

```python
from analytics.backtest.cost_model import CostModel, cost_model_from_toml
```

In `BacktestSweepConfig`, after the `live_parity:` field:

```python
    # Phase 0.4 equity cost model — None = legacy flat fee_pct path. Loaded
    # from `[backtest.cost_model]`; off by default (goldens byte-identical).
    cost_model: CostModel | None = None
```

In `load_backtest_config`, after the `live_parity_cfg = LiveParityConfig(...)`
block:

```python
    cost_model_cfg = cost_model_from_toml(_bt_section.get("cost_model"))
```

and in the returned `BacktestSweepConfig(...)`, after `live_parity=live_parity_cfg,`:

```python
        cost_model=cost_model_cfg,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_signal_config.py tests/test_backtest_config.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
make lint-py && make typecheck
git add analytics/signal_config.py analytics/backtest_config.py tests/test_signal_config.py tests/test_backtest_config.py
git commit -m "feat(cost): [backtest.cost_model] TOML block in both config loaders (off by default)"
```

---

### Task 6: Persistence identity — run_id suffix + `cost_model` column

**Files:**

- Modify: `analytics/store/backtest_runs.py` (`_backtest_run_id`, `upsert_backtest_run`)
- Modify: `analytics/store/schema.py` (migration list)
- Test: `tests/test_data_store.py` (append + fixture NULLs), `tests/test_recalibrate_lib.py` (fixture NULLs only)

Rationale: the scanner's L2 cache key is `run_id + last_candle_ts`. Without a
cost suffix, flipping the model on in step 2 would serve stale flat-fee
snapshots for identical params. The nullable `cost_model` JSON column makes
saved runs self-describing (same recipe as Phase 0.1 `universe_policy`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_data_store.py` (it already imports `_backtest_run_id`
locally inside tests — do the same):

```python
class TestBacktestRunIdCostModel:
    def test_none_cost_model_keeps_legacy_hash(self) -> None:
        from analytics.data_store import _backtest_run_id

        legacy = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        explicit = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model=None
        )
        assert legacy == explicit

    def test_cost_model_changes_hash(self) -> None:
        from analytics.data_store import _backtest_run_id

        legacy = _backtest_run_id("SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off")
        costed = _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model='{"x":1}'
        )
        assert legacy != costed
        # Deterministic for the same stamp.
        assert costed == _backtest_run_id(
            "SPY", "4h", "bos", 90, 0.02, 2.0, 0.0, "off", cost_model='{"x":1}'
        )

    def test_upsert_persists_cost_model_column(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        from analytics.backtest_lib import BacktestResult
        from analytics.data_store import upsert_backtest_run

        result = BacktestResult(symbol="SPY", timeframe="4h", strategy="bos")
        run_id = upsert_backtest_run(
            conn,
            result,
            days=90,
            data_start_ms=0,
            data_end_ms=1,
            sl_pct=0.02,
            tp_r=2.0,
            fee_pct=0.0,
            day_filter="off",
            cost_model='{"impact_coef":1.0}',
        )
        row = conn.execute(
            "SELECT cost_model FROM backtest_runs WHERE run_id = ?", [run_id]
        ).fetchone()
        assert row is not None and row[0] == '{"impact_coef":1.0}'
```

(Reuse the file's existing `conn` fixture — `TestGetWinRateByStrategy` and
neighbours already use one; place the class near them.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_store.py -v -k CostModel`
Expected: FAIL with `TypeError: _backtest_run_id() got an unexpected keyword argument 'cost_model'`

- [ ] **Step 3: Implement the store changes**

3a. `analytics/store/backtest_runs.py` — `_backtest_run_id` gains a final
parameter after `atr_sl_floor: bool = False`:

```python
    cost_model: str | None = None,
```

and before the `return hashlib...` line, after the `atr_floor` suffix:

```python
    if cost_model is not None:
        key += f"|cost:{cost_model}"
```

3b. `upsert_backtest_run` gains a final parameter after
`universe_policy: str | None = None`:

```python
    cost_model: str | None = None,
```

forwards it to the internal `_backtest_run_id(...)` call as a trailing
keyword argument:

```python
        cost_model=cost_model,
```

adds it to the row dict after `"universe_policy": universe_policy,`:

```python
        "cost_model": cost_model,
```

and appends the column to the positional `INSERT OR REPLACE ... SELECT`
column list — the segment `"recovery_factor, universe_policy "` becomes
`"recovery_factor, universe_policy, cost_model "`.

3c. `analytics/store/schema.py` — append to the `backtest_runs` migration
list, after `("universe_policy", "TEXT"),`:

```python
        # Phase 0.4: cost-model stamp (canonical JSON of the active CostModel;
        # NULL = flat-fee run) — migration-list only, same positional-INSERT
        # constraint as universe_policy above.
        ("cost_model", "TEXT"),
```

3d. Fix the 4 raw positional fixtures (one more trailing NULL / `?` each —
the table now has 36 columns):

- `tests/test_data_store.py` (~line 604 and ~line 620): both occurrences of

  ```text
  "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
  ```

  become

  ```text
  "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
  ```

- `tests/test_recalibrate_lib.py` (~line 200): the fragment `"NULL, NULL)"`
   at the end of the first INSERT becomes `"NULL, NULL, NULL)"`.
- `tests/test_recalibrate_lib.py` `_seed_directional_runs` (~line 580): the
   placeholder line `"?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"` gains one
   more `?` → `"?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"`, the value
   list gains a final entry after the `universe_policy` one:

  ```python
            None,  # cost_model (added via ALTER TABLE, last column)
  ```

  and the prior comment `# universe_policy (added via ALTER TABLE, last
  column)` drops its `, last column` suffix.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_store.py tests/test_recalibrate_lib.py -v`
Expected: PASS (all — including the patched fixtures)

- [ ] **Step 5: Commit**

```bash
make lint-py && make typecheck
git add analytics/store/backtest_runs.py analytics/store/schema.py tests/test_data_store.py tests/test_recalibrate_lib.py
git commit -m "feat(cost): stamp cost_model JSON on backtest_runs + run_id hash suffix"
```

---

### Task 7: Pass-through call sites — sweep runner, live bt_cache, scanner

**Files:**

- Modify: `analytics/backtest_runner.py`
- Modify: `analytics/signal/bt_cache.py`
- Modify: `analytics/signal/scanner.py`

No new behaviour when the block is off — every addition forwards a `None`.
The full suite + regression goldens are the test (plus mypy, which forces
every signature to line up).

- [ ] **Step 1: Wire `analytics/backtest_runner.py` (sweep mode only)**

Four edits, all inside `_collect_sweep_results` / sweep mode:

- Phase-3 `run_backtest(...)` call (~line 522): after the
  `htf_slope_series_by_anchor=...` argument add

  ```python
            cost_model=cfg.cost_model,
  ```

- The `upsert_backtest_run(...)` call right below (~line 557): after
  `universe_policy=universe_policy,` add

  ```python
                cost_model=cfg.cost_model.to_json()
                if cfg.cost_model is not None
                else None,
  ```

- The tp-sweep `run_backtest(...)` call (~line 656) and the ATR-sweep
  `run_backtest(...)` call (~line 715): same
  `cost_model=cfg.cost_model,` addition after their
  `htf_slope_series_by_anchor=...` argument.

(`run_backtest_cmd` single-combo mode is deliberately NOT wired — it takes
explicit CLI args, not a TOML; step 2 can add a flag if ever needed.)

- [ ] **Step 2: Wire `analytics/signal/bt_cache.py`**

Add to its imports:

```python
from analytics.backtest.cost_model import CostModel
```

`_compute_backtest` gains a final parameter after
`tp_r_short: float | None = None`:

```python
    cost_model: CostModel | None = None,
```

documented in its docstring with:

```text
    cost_model: Phase 0.4 equity cost model — forwarded to run_backtest;
    None keeps the flat fee_pct path.
```

and forwarded in its `run_backtest(...)` call after `tp_r_short=tp_r_short,`:

```python
        cost_model=cost_model,
```

- [ ] **Step 3: Wire `analytics/signal/scanner.py`**

Three kinds of edits:

- The `_backtest_run_id(...)` cache-key call (~line 613, positional args
  ending `eff_adr_exempt, eff_atr_floor,`): add a trailing keyword argument

  ```python
                        cost_model=backtest_cfg.cost_model.to_json()
                        if backtest_cfg.cost_model is not None
                        else None,
  ```

- Both `_compute_backtest(...)` calls (~line 645 cache-miss branch and
  ~line 682 cache-disabled branch): after `tp_r_short=tp_r_short_eff,` add

  ```python
                                cost_model=backtest_cfg.cost_model,
  ```

  (match each branch's indentation).

- The save-loop `upsert_backtest_run(...)` (~line 1189): after
  `universe_policy=universe_policy_json,` add

  ```python
                    cost_model=backtest_cfg.cost_model.to_json()
                    if backtest_cfg.cost_model is not None
                    else None,
  ```

- [ ] **Step 4: Full verification — suite + goldens byte-identical**

```bash
make lint-py && make typecheck && make test
make test-regression   # NO regen — must pass against existing goldens
```

Expected: all green. If `test-regression` fails, the default path is not
byte-identical — stop and fix before committing (do NOT regenerate goldens).

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest_runner.py analytics/signal/bt_cache.py analytics/signal/scanner.py
git commit -m "feat(cost): thread cost_model through sweep runner, live bt_cache and scanner"
```

---

### Task 8: Config reference block + docs

**Files:**

- Modify: `config/strategy_params.toml`
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Add the commented reference block**

In `config/strategy_params.toml`, after the `[backtest]` section's last key
(`min_avg_r = 0.0`) and before the per-strategy flags comment, insert:

```toml
# Phase 0.4 equity cost model — replaces the flat fee_pct with half-spread
# (by dollar-ADV liquidity bucket) + sqrt market impact + short borrow +
# commission, all charged in R. Off by default; enabling it MOVES regression
# goldens (deliberate step-2 decision — see spec §4.4: db-update + golden
# refresh + recalibrate). Values below are the built-in conservative defaults.
# [backtest.cost_model]
# enabled = false
# adv_thresholds = [5e6, 5e7, 5e8]          # dollar-ADV bucket edges
# half_spread_bps = [20.0, 8.0, 3.0, 1.0]   # per bucket, least → most liquid
# impact_coef = 1.0                         # sqrt-law coefficient
# notional_usd = 10000.0                    # assumed order size
# borrow_rate_annual = 0.01                 # shorts: conservative constant
# commission_bps = 0.0                      # US retail ≈ 0
# adv_window_days = 20.0                    # trailing ADV / sigma window
```

- [ ] **Step 2: Update CLAUDE.md and README.md**

CLAUDE.md — in the `backtest/` bullet, change "backtest engine split into
9 modules" to "10 modules" and add to the module list (after the
`cv_splits.py` entry):

```text
`cost_model.py` (Phase 0.4 equity cost model — frozen `CostModel` +
`cost_r(trade, ctx)` decomposed as spread (dollar-ADV liquidity buckets) +
sqrt impact + short borrow + commission, all in R; `build_cost_context`
derives causal trailing ADV/sigma from OHLCV; `cost_model_from_toml` parses
the `[backtest.cost_model]` block. Wired into `Trade.pnl_r` /
`run_backtest(cost_model=...)`, both config loaders, the sweep runner, the
live bt_cache/scanner path, and stamped on `backtest_runs.cost_model`
(nullable TEXT, migration-list only) + the run_id hash. **Default-off —
flat fee_pct path byte-identical; flipping it on for live configs is the
deliberate step-2 golden-moving change.**),
```

README.md — in the backtest feature section, add one bullet:

```text
- **Equity cost model (Phase 0.4, default off):** `[backtest.cost_model]`
  TOML block replaces the flat `fee_pct` with half-spread by liquidity
  bucket + square-root market impact + short borrow + commission, charged
  per-trade in R. See `config/strategy_params.toml` for the commented
  reference block; enabling it intentionally changes backtest P&L.
```

(Adapt placement to the README's existing structure; run `make lint-md` and
hand-format to the full default markdownlint ruleset — CI is stricter than
local.)

- [ ] **Step 3: Lint markdown + commit**

```bash
make lint-md
git add config/strategy_params.toml README.md CLAUDE.md
git commit -m "docs(cost): [backtest.cost_model] reference block + module docs"
```

---

### Task 9: Final verification + PR

- [ ] **Step 1: Full gate run**

```bash
make lint-py && make typecheck && make test && make test-regression && make lint-md
```

Expected: suite ≈ 1480+ pass / 3 skip, regression goldens byte-identical
(zero diffs, NO regen performed anywhere in this branch).

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/phase0-cost-model   # remote uses the github.com-personal SSH alias
gh pr create --repo s10023/buibui-wifey-wall-street-bot \
  --title "feat(cost): Phase 0.4 realistic equity cost model (default-off)" \
  --body-file /tmp/pr-feat-phase0-cost-model.md
```

(Write the body via the `/pr-summary` skill first; then run `/post-branch`
for the docs sweep + MEMORY.md Current State update, including the step-2
follow-up note: "flip `[backtest.cost_model] enabled = true` on for all
live configs at once → `make db-update` + golden refresh + recalibrate, as
its own reviewed PR".)

---

## Self-review checklist (run after writing, before executing)

1. **Spec coverage** — §4.4 design bullets: module ✓ (Tasks 1–3), engine wiring with byte-identical None default ✓ (Task 4), `[backtest.cost_model]` off by default ✓ (Task 5, 8); DoD "tested, documented" ✓ (hand-computed example per §6, docs Task 8); golden-moving flip-on deferred to step 2 ✓ (documented in header + Task 9).
2. **Placeholders** — none; every step carries the code or the exact string edit.
3. **Type consistency** — `CostModel.cost_r(trade, ctx)` / `cost_breakdown` / `spread_bucket` / `to_json` names used identically in Tasks 1, 4, 6, 7; `cost_model_from_toml` in Tasks 3, 5; `build_cost_context(closes, volumes, idx, bars_per_day, window_bars)` in Tasks 2, 4.
