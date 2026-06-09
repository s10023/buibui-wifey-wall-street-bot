# Phase 0.3a/b — Deflated Sharpe + Probability of Backtest Overfitting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development (red → green per step) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the WFO sweep's `tp_r` selection statistically honest. Today `tp_r` is picked by an `avg_r × win_rate × √n` grid score with **no Sharpe, no trial-count haircut, and a single contiguous IS/OOS split**. This plan adds (0.3a) a per-trade Sharpe + **Deflated Sharpe Ratio** (Bailey & López de Prado 2014) that deflates the selected config's Sharpe by the number of grid trials, and (0.3b) a **Probability of Backtest Overfitting** (CSCV — Bailey, Borwein, López de Prado & Zhu 2017) computed over the whole grid. Both are surfaced in the sweep report so every recommended `tp_r` carries a DSR + a sweep-level PBO.

**Architecture:** A new pure math module `analytics/backtest/stats_overfit.py` (no DB, no I/O, no engine import — depends only on `numpy` + stdlib `statistics`/`math`/`itertools`). It exposes the Sharpe/PSR/DSR functions, the CSCV `probability_of_backtest_overfitting`, and a `build_performance_matrix` adapter that turns per-config `(entry_time, pnl_r)` points into the time-aligned `T×N` matrix CSCV needs. `engine.BacktestResult` gains a `sharpe` property (one-directional import of `sharpe_ratio`). `param_sweep.run_param_sweep` computes the overfit stats over the **full grid** (all N rows, before truncating to `top_n`), attaches an `OverfitStats` to each `SweepRow`, and `format_sweep_results` prints a DSR line for the recommended config plus a sweep-level `trials N=… PBO=…` line.

**Why goldens stay byte-identical:** the regression goldens are built by `tests/test_regression.py::_extract_metrics`, which enumerates an **explicit** field list (`avg_r`, `long_avg_r`, …) — it never reflects over `BacktestResult` properties, so adding `.sharpe` does not move them. `param_sweep` output is console-only and not part of `tests/fixtures/`. This is an additive, measurement-only PR — **no `make db-update`**.

**Tech Stack:** Python 3.11+, numpy, stdlib `statistics.NormalDist` (Φ / Φ⁻¹ — **scipy is not a repo dependency, do not add it**), pytest + unittest.mock, ruff, mypy strict.

---

## Scope notes (locked decisions)

- **This PR = 0.3a + 0.3b only.** 0.3c (purged + embargoed CV behind a `cv_mode` flag) is a **separate later PR** — this plan does **not** touch `_split_ohlcv`, does not add `cv_mode`, and does not change how the WFO IS/OOS split works.
- **Sharpe basis = per-trade R series** (spec §7 chosen): `SR = mean(r) / std(r)` over `Trade.pnl_r` of the closed trades. **Non-annualized** — the engine's native unit is per-trade R, and annualizing needs a trade-frequency assumption we deliberately defer (revisit if Phase 4's portfolio backtester wants a daily-returns Sharpe). Std uses sample `ddof=1`; skew/kurt are the standardized 3rd/4th moments with the same `ddof=1` scale; kurtosis is **non-excess** (normal = 3). Document the convention in the module docstring.
- **DSR null = expected-max-Sharpe over N trials.** `SR*_0 = √V · [ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(N·e)) ]` with γ = Euler–Mascheroni 0.5772156649, V = cross-trial variance of the per-config Sharpes, N = grid size. DSR = `PSR(SR*_0)`. N < 2 or V ≤ 0 ⇒ `SR*_0 = 0` ⇒ DSR = `PSR(0)` (no deflation possible with a single trial).
- **PBO performance metric = mean R per config per submatrix.** CSCV permits any performance metric; mean R is robust on sparse submatrices (a per-block Sharpe needs a non-zero per-block std, which a handful of trades won't guarantee). Documented in the function docstring.
- **PBO common axis = time-bucketed R over the full evaluated history.** Per-trade R across configs is not row-alignable (different `tp_r` ⇒ different trade counts/exit times). `build_performance_matrix` buckets each config's trades into `n_rows` equal time buckets over the shared `[min entry_time, max entry_time]` span; CSCV then re-partitions those rows combinatorially (independent of the WFO split — that is the point: CSCV is a more robust CV than the one contiguous split).
- **Graceful degeneracy.** PBO returns `pbo = NaN` when it cannot be computed (< 2 configs, or fewer timeline blocks than `n_splits` can halve). `param_sweep` renders `NaN` PBO as `n/a`. Sharpe/PSR/DSR return `0.0` on degenerate input (n < 2 trades, zero variance, ill-conditioned denominator) — never raise on real data.
- **No caller-signature changes.** `run_param_sweep` keeps returning `list[SweepRow]`; the overfit stats ride on each `SweepRow` via a new `overfit_stats` field (note: distinct from the existing `overfit: bool`). `cli/param.py`, `tools/multi_symbol_wfo.py`, `run_strategy_audit` are untouched except the additive formatter footer.

## File Structure

- **Create** `analytics/backtest/stats_overfit.py` — `sharpe_ratio`, `_moments`, `probabilistic_sharpe_ratio`, `expected_max_sharpe`, `deflated_sharpe_ratio`, `PBOResult`, `probability_of_backtest_overfitting`, `build_performance_matrix`, `OverfitStats`. Pure; no DB, no engine import.
- **Create** `tests/test_stats_overfit.py` — known-answer + property tests for every function.
- **Modify** `analytics/backtest/engine.py` — add `BacktestResult.sharpe` property.
- **Modify** `tests/test_backtest_lib.py` — `BacktestResult.sharpe` matches `sharpe_ratio`.
- **Modify** `analytics/param_sweep.py` — `SweepRow.overfit_stats` field; compute over the full grid in `run_param_sweep`; DSR/PBO footer in `format_sweep_results`.
- **Modify** `tests/test_param_sweep.py` — wiring: `overfit_stats` populated, `n_trials == grid size`, footer renders DSR + PBO, default path still returns `SweepRow`s.
- **Modify** docs: `CLAUDE.md` (backtest module bullet), `.claude/context/analytics.md` (new section). README only if it documents WFO scoring.

Implement on a fresh branch off `main`:

```bash
git checkout main && git pull && git checkout -b feat/phase0-overfit-controls
```

---

### Task 1: Sharpe ratio + return moments

**Files:**

- Create: `analytics/backtest/stats_overfit.py`
- Test: `tests/test_stats_overfit.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for analytics/backtest/stats_overfit.py."""

import math

from analytics.backtest.stats_overfit import _moments, sharpe_ratio


def test_sharpe_zero_mean_is_zero() -> None:
    assert sharpe_ratio([1.0, -1.0, 1.0, -1.0]) == 0.0


def test_sharpe_positive_for_positive_edge() -> None:
    sr = sharpe_ratio([2.0, 1.0, 2.0, 1.0])
    assert sr > 0.0


def test_sharpe_degenerate_returns_zero() -> None:
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([1.0]) == 0.0  # n < 2
    assert sharpe_ratio([3.0, 3.0, 3.0]) == 0.0  # zero variance


def test_sharpe_known_value() -> None:
    # mean=1.0, sample std (ddof=1) of [3,-1,3,-1] = sqrt(16/3) ≈ 2.3094
    sr = sharpe_ratio([3.0, -1.0, 3.0, -1.0])
    assert math.isclose(sr, 1.0 / math.sqrt(16.0 / 3.0), rel_tol=1e-9)


def test_moments_symmetric_zero_skew() -> None:
    mean, std, skew, kurt = _moments([3.0, -1.0, 3.0, -1.0])
    assert math.isclose(mean, 1.0, rel_tol=1e-9)
    assert std > 0.0
    assert math.isclose(skew, 0.0, abs_tol=1e-9)  # symmetric
    assert kurt > 0.0  # non-excess kurtosis (normal == 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_stats_overfit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics.backtest.stats_overfit'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Overfitting / multiple-testing controls for the WFO sweep (Phase 0.3a/b).

Pure math — no DB, no network, no engine import. Implements:

* ``sharpe_ratio`` — per-trade Sharpe over an R-multiple series, ``mean / std``
  (sample std, ddof=1; non-annualized — the engine's native unit is per-trade R).
* ``probabilistic_sharpe_ratio`` / ``deflated_sharpe_ratio`` — Bailey & López de
  Prado (2014). DSR deflates the selected config's Sharpe by the expected maximum
  Sharpe under ``N`` independent zero-edge trials.
* ``probability_of_backtest_overfitting`` — Combinatorially-Symmetric CV (Bailey,
  Borwein, López de Prado & Zhu 2017). Fraction of IS/OOS partitions where the
  IS-best config lands below the OOS median.

Φ / Φ⁻¹ come from stdlib ``statistics.NormalDist`` (scipy is not a dependency).
Skew/kurtosis are standardized 3rd/4th moments (ddof=1 scale); kurtosis is
non-excess (normal == 3). All functions return ``0.0`` / ``NaN`` on degenerate
input rather than raising.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_MIN_N = 2


def _moments(returns: Sequence[float]) -> tuple[float, float, float, float]:
    """Return (mean, std, skew, kurt) of an R series.

    std is the sample std (ddof=1). skew/kurt are standardized central moments
    over the sample std; kurt is non-excess (normal == 3). Returns zeros when
    n < 2 or std == 0.
    """
    arr = np.asarray(list(returns), dtype=float)
    n = arr.size
    if n < _MIN_N:
        return (float(arr.mean()) if n else 0.0, 0.0, 0.0, 0.0)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if std == 0.0:
        return (mean, 0.0, 0.0, 0.0)
    z = (arr - mean) / std
    skew = float((z**3).mean())
    kurt = float((z**4).mean())
    return (mean, std, skew, kurt)


def sharpe_ratio(returns: Sequence[float]) -> float:
    """Per-trade Sharpe ``mean / std`` (sample std). 0.0 on degenerate input."""
    mean, std, _, _ = _moments(returns)
    if std == 0.0:
        return 0.0
    return mean / std
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_stats_overfit.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/stats_overfit.py tests/test_stats_overfit.py
git commit -m "feat(overfit): per-trade Sharpe + return moments"
```

---

### Task 2: Probabilistic + Deflated Sharpe Ratio

**Files:**

- Modify: `analytics/backtest/stats_overfit.py`
- Test: `tests/test_stats_overfit.py`

- [ ] **Step 1: Write the failing test**

```python
from analytics.backtest.stats_overfit import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)


def test_psr_in_unit_interval_and_monotonic() -> None:
    lo = probabilistic_sharpe_ratio(0.2, n=100, skew=0.0, kurt=3.0)
    hi = probabilistic_sharpe_ratio(0.5, n=100, skew=0.0, kurt=3.0)
    assert 0.0 <= lo <= 1.0
    assert hi > lo  # larger observed Sharpe ⇒ higher confidence


def test_psr_half_at_benchmark() -> None:
    # observed Sharpe exactly equals the benchmark ⇒ PSR == 0.5
    p = probabilistic_sharpe_ratio(0.3, n=100, skew=0.0, kurt=3.0, sr_star=0.3)
    assert abs(p - 0.5) < 1e-9


def test_psr_degenerate_returns_zero() -> None:
    assert probabilistic_sharpe_ratio(0.3, n=1, skew=0.0, kurt=3.0) == 0.0


def test_expected_max_sharpe_grows_with_trials() -> None:
    a = expected_max_sharpe(0.04, n_trials=5)
    b = expected_max_sharpe(0.04, n_trials=500)
    assert 0.0 < a < b  # more trials ⇒ higher expected best-of-noise Sharpe


def test_expected_max_sharpe_degenerate() -> None:
    assert expected_max_sharpe(0.04, n_trials=1) == 0.0
    assert expected_max_sharpe(0.0, n_trials=100) == 0.0


def test_dsr_deflates_below_psr_with_many_trials() -> None:
    # a Sharpe that looks good standalone gets deflated once you account for
    # having searched many noisy trials with high cross-trial dispersion
    trials = [0.0, 0.05, -0.04, 0.5, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3]
    psr0 = probabilistic_sharpe_ratio(0.5, n=60, skew=0.0, kurt=3.0)
    dsr = deflated_sharpe_ratio(0.5, trials, n_returns=60, skew=0.0, kurt=3.0)
    assert dsr < psr0
    assert 0.0 <= dsr <= 1.0


def test_dsr_single_trial_equals_psr0() -> None:
    dsr = deflated_sharpe_ratio(0.4, [0.4], n_returns=50, skew=0.0, kurt=3.0)
    psr0 = probabilistic_sharpe_ratio(0.4, n=50, skew=0.0, kurt=3.0)
    assert abs(dsr - psr0) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_stats_overfit.py -k "psr or max_sharpe or dsr" -v`
Expected: FAIL — `ImportError` for the three new names

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `stats_overfit.py`:

```python
import math
from statistics import NormalDist
```

Add a module constant near `_MIN_N`:

```python
_EULER_MASCHERONI = 0.5772156649015329
_NORM = NormalDist()
```

Append the three functions:

```python
def probabilistic_sharpe_ratio(
    sr: float,
    n: int,
    skew: float,
    kurt: float,
    sr_star: float = 0.0,
) -> float:
    """Probabilistic Sharpe Ratio — P(true SR > sr_star) given the estimate.

    PSR = Φ( (SR - SR*)·√(n-1) / √(1 - skew·SR + (kurt-1)/4·SR²) ).
    Returns 0.0 when n < 2 or the variance term is non-positive (ill-conditioned).
    """
    if n < _MIN_N:
        return 0.0
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom <= 0.0:
        return 0.0
    stat = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(_NORM.cdf(stat))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum Sharpe under n_trials independent zero-edge trials.

    SR*_0 = √V · [ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(N·e)) ]  (Bailey & LdP 2014).
    Returns 0.0 when N < 2 or V <= 0 (no deflation possible).
    """
    if n_trials < _MIN_N or sr_variance <= 0.0:
        return 0.0
    max_z = (1.0 - _EULER_MASCHERONI) * _NORM.inv_cdf(1.0 - 1.0 / n_trials) + (
        _EULER_MASCHERONI * _NORM.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    )
    return math.sqrt(sr_variance) * max_z


def deflated_sharpe_ratio(
    observed_sr: float,
    trial_sharpes: Sequence[float],
    n_returns: int,
    skew: float,
    kurt: float,
) -> float:
    """Deflated Sharpe Ratio = PSR evaluated against the expected-max-Sharpe null.

    ``trial_sharpes`` is the per-config Sharpe of every grid trial (used to
    estimate the cross-trial Sharpe variance V and the trial count N). With a
    single trial, V is undefined ⇒ SR*_0 = 0 ⇒ DSR == PSR(0).
    """
    arr = np.asarray(list(trial_sharpes), dtype=float)
    if arr.size < _MIN_N:
        sr_star = 0.0
    else:
        sr_star = expected_max_sharpe(float(arr.var(ddof=1)), arr.size)
    return probabilistic_sharpe_ratio(observed_sr, n_returns, skew, kurt, sr_star)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_stats_overfit.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/stats_overfit.py tests/test_stats_overfit.py
git commit -m "feat(overfit): probabilistic + deflated Sharpe ratio"
```

---

### Task 3: PBO (CSCV) + performance-matrix builder

**Files:**

- Modify: `analytics/backtest/stats_overfit.py`
- Test: `tests/test_stats_overfit.py`

- [ ] **Step 1: Write the failing test**

```python
import numpy as np

from analytics.backtest.stats_overfit import (
    PBOResult,
    build_performance_matrix,
    probability_of_backtest_overfitting,
)

_RNG = np.random.default_rng(0)


def test_pbo_dominant_config_is_low() -> None:
    # config 0 is consistently best in every row ⇒ never overfit ⇒ PBO ≈ 0
    noise = _RNG.normal(0, 0.1, size=(200, 5))
    noise[:, 0] += 1.0
    res = probability_of_backtest_overfitting(noise, n_splits=10)
    assert isinstance(res, PBOResult)
    assert res.pbo < 0.1


def test_pbo_adversarial_is_high() -> None:
    # IS-best column is engineered to be OOS-worst on every split ⇒ PBO ≈ 1
    t = 200
    half = t // 2
    m = _RNG.normal(0, 0.05, size=(t, 4))
    m[:half, 0] += 1.0  # column 0 dominates the first half (IS on many combos)
    m[half:, 0] -= 1.0  # ...and is worst in the second half (OOS)
    res = probability_of_backtest_overfitting(m, n_splits=8)
    assert res.pbo > 0.5


def test_pbo_pure_noise_near_half() -> None:
    m = _RNG.normal(0, 1.0, size=(300, 8))
    res = probability_of_backtest_overfitting(m, n_splits=10)
    assert 0.25 < res.pbo < 0.75  # no real edge ⇒ coin-flip OOS rank


def test_pbo_degenerate_is_nan() -> None:
    one_col = _RNG.normal(0, 1.0, size=(100, 1))
    assert math.isnan(probability_of_backtest_overfitting(one_col).pbo)
    tiny = _RNG.normal(0, 1.0, size=(1, 5))  # fewer rows than splits can halve
    assert math.isnan(probability_of_backtest_overfitting(tiny, n_splits=16).pbo)


def test_build_performance_matrix_buckets_by_time() -> None:
    # two configs; entry times 0 and 100 ⇒ 2 buckets over [0, 100]
    cfg_a = [(0, 1.0), (100, 2.0)]
    cfg_b = [(0, -1.0), (50, 0.5)]
    m = build_performance_matrix([cfg_a, cfg_b], n_rows=2)
    assert m.shape == (2, 2)
    assert m[0, 0] == 1.0 and m[1, 0] == 2.0  # cfg_a: bucket0=1.0, bucket1=2.0
    assert m[0, 1] == -0.5  # cfg_b: -1.0 and 0.5 both land in bucket0
    assert m[1, 1] == 0.0


def test_build_performance_matrix_empty() -> None:
    assert build_performance_matrix([], n_rows=4).shape == (0, 0)
    assert build_performance_matrix([[], []], n_rows=4).shape == (0, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_stats_overfit.py -k "pbo or build_performance" -v`
Expected: FAIL — `ImportError` for `PBOResult` / the two functions

- [ ] **Step 3: Write minimal implementation**

Add to the imports:

```python
import itertools
from dataclasses import dataclass
```

Append:

```python
@dataclass(frozen=True)
class PBOResult:
    """Probability of Backtest Overfitting via CSCV.

    ``pbo`` is the fraction of IS/OOS partitions where the IS-best config lands
    below the OOS median (logit < 0); NaN when not computable (< 2 configs, or
    fewer timeline blocks than ``n_splits`` can halve).
    """

    pbo: float
    n_combinations: int
    logits: tuple[float, ...]


def _col_mean(block: np.ndarray) -> np.ndarray:
    """Per-config performance over a submatrix: mean R per column."""
    return block.mean(axis=0)


def probability_of_backtest_overfitting(
    perf_matrix: np.ndarray,
    n_splits: int = 16,
) -> PBOResult:
    """CSCV PBO over a T×N matrix (rows = time-aligned observations, cols = trials).

    Partitions the T rows into ``S`` (even, ≤ T) contiguous submatrices, then for
    every way of choosing S/2 of them as in-sample: ranks configs by IS mean R,
    takes the IS-best, and records its OOS rank as a logit. PBO = P(logit < 0).
    """
    m = np.asarray(perf_matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("perf_matrix must be 2-D (T observations × N configs)")
    t, n = m.shape
    s = n_splits - (n_splits % 2)
    while s > t:
        s -= 2
    if n < _MIN_N or s < _MIN_N:
        return PBOResult(float("nan"), 0, ())

    blocks = np.array_split(np.arange(t), s)
    logits: list[float] = []
    for is_combo in itertools.combinations(range(s), s // 2):
        is_set = set(is_combo)
        is_rows = np.concatenate([blocks[i] for i in is_combo])
        oos_rows = np.concatenate([blocks[i] for i in range(s) if i not in is_set])
        is_perf = _col_mean(m[is_rows])
        oos_perf = _col_mean(m[oos_rows])
        best = int(np.argmax(is_perf))
        # dense rank of every config's OOS perf (1 = worst … n = best)
        order = oos_perf.argsort()
        ranks = np.empty(n, dtype=float)
        ranks[order] = np.arange(1, n + 1)
        omega = ranks[best] / (n + 1)
        omega = min(max(omega, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(omega / (1.0 - omega)))

    arr = np.asarray(logits)
    pbo = float((arr < 0.0).mean())
    return PBOResult(pbo, len(logits), tuple(float(x) for x in logits))


def build_performance_matrix(
    trade_points: Sequence[Sequence[tuple[int, float]]],
    n_rows: int,
) -> np.ndarray:
    """Time-align per-config (entry_time_ms, pnl_r) points into a T×N matrix.

    Each column is one config; each row is an equal-width time bucket over the
    shared [min entry_time, max entry_time] span; the cell is the summed R of
    that config's trades in that bucket (0 where the config has no trade). The
    common time axis is what lets CSCV re-partition rows across configs.
    """
    n = len(trade_points)
    all_times = [t for cfg in trade_points for (t, _) in cfg]
    if n == 0 or not all_times or n_rows < 1:
        return np.zeros((0, n), dtype=float)
    t0 = min(all_times)
    span = (max(all_times) - t0) or 1
    m = np.zeros((n_rows, n), dtype=float)
    for j, cfg in enumerate(trade_points):
        for t, r in cfg:
            b = int((t - t0) / span * n_rows)
            if b >= n_rows:
                b = n_rows - 1
            m[b, j] += r
    return m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_stats_overfit.py -v`
Expected: PASS (18 tests). If `test_pbo_pure_noise_near_half` is flaky at the seed, widen the band — the assertion is "near 0.5", not exact.

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/stats_overfit.py tests/test_stats_overfit.py
git commit -m "feat(overfit): CSCV probability of backtest overfitting + matrix builder"
```

---

### Task 4: `BacktestResult.sharpe` property

**Files:**

- Modify: `analytics/backtest/engine.py`
- Test: `tests/test_backtest_lib.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backtest_lib.py` (it already builds `Trade` / `BacktestResult` in other tests — mirror that construction):

```python
def test_backtest_result_sharpe_matches_helper() -> None:
    from analytics.backtest.engine import BacktestResult, Trade
    from analytics.backtest.stats_overfit import sharpe_ratio

    trades = [
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=102.0, outcome="win"),
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=98.0, outcome="loss"),
        Trade(0, 1, 100.0, "long", 99.0, 102.0, exit_time=2, exit_price=101.0, outcome="win"),
    ]
    res = BacktestResult("AAPL", "1d", "bos", trades=trades)
    expected = sharpe_ratio([t.pnl_r for t in res.closed_trades if t.pnl_r is not None])
    assert res.sharpe == expected


def test_backtest_result_sharpe_empty_is_zero() -> None:
    from analytics.backtest.engine import BacktestResult

    assert BacktestResult("AAPL", "1d", "bos").sharpe == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_backtest_lib.py -k sharpe -v`
Expected: FAIL — `AttributeError: 'BacktestResult' object has no attribute 'sharpe'`

- [ ] **Step 3: Write minimal implementation**

In `analytics/backtest/engine.py`, add the import near the top (module level — `stats_overfit` does not import `engine`, so no cycle):

```python
from analytics.backtest.stats_overfit import sharpe_ratio
```

Add the property to `BacktestResult`, next to `avg_r` (around line 184):

```python
    @property
    def sharpe(self) -> float:
        """Per-trade Sharpe over the closed-trade R series (0.0 when < 2 trades)."""
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sharpe_ratio(r_values)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_backtest_lib.py -k sharpe -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add analytics/backtest/engine.py tests/test_backtest_lib.py
git commit -m "feat(overfit): BacktestResult.sharpe per-trade Sharpe property"
```

---

### Task 5: Wire overfit stats into the sweep + report

**Files:**

- Modify: `analytics/param_sweep.py`
- Test: `tests/test_param_sweep.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_param_sweep.py` (it already constructs `SweepRow` / `BacktestResult` / runs `format_sweep_results`; mirror the existing helpers). Two angles — the dataclass field and the formatter footer:

```python
def test_sweep_row_carries_overfit_stats() -> None:
    from analytics.backtest.stats_overfit import OverfitStats
    from analytics.param_sweep import SweepRow
    from analytics.backtest.engine import BacktestResult

    row = SweepRow(
        params={"tp_r": 2.0},
        is_result=BacktestResult("AAPL", "1d", "bos"),
        oos_result=BacktestResult("AAPL", "1d", "bos"),
        is_score=0.0,
        oos_score=0.0,
        decay=float("nan"),
        overfit=False,
        overfit_stats=OverfitStats(0.3, 0.2, 0.6, n_trials=9, pbo=0.4),
    )
    assert row.overfit_stats is not None
    assert row.overfit_stats.deflated_sharpe == 0.6
    assert row.overfit_stats.n_trials == 9


def test_format_sweep_results_renders_dsr_and_pbo() -> None:
    from analytics.backtest.stats_overfit import OverfitStats
    from analytics.param_sweep import SweepRow, format_sweep_results
    from analytics.backtest.engine import BacktestResult, Trade

    wins = [
        Trade(0, 1, 100.0, "long", 99.0, 103.0, exit_time=2, exit_price=103.0, outcome="win")
        for _ in range(8)
    ]
    res = BacktestResult("AAPL", "1d", "bos", trades=wins)
    row = SweepRow(
        params={"tp_r": 2.0},
        is_result=res,
        oos_result=res,
        is_score=1.0,
        oos_score=1.0,
        decay=1.0,
        overfit=False,
        overfit_stats=OverfitStats(0.5, 0.4, 0.7, n_trials=9, pbo=0.33),
    )
    out = format_sweep_results([row], "bos", "AAPL", "1d")
    assert "Deflated Sharpe" in out
    assert "PBO" in out
    assert "N=9" in out


def test_overfit_stats_nan_pbo_renders_na() -> None:
    from analytics.backtest.stats_overfit import OverfitStats
    from analytics.param_sweep import SweepRow, format_sweep_results
    from analytics.backtest.engine import BacktestResult, Trade

    wins = [
        Trade(0, 1, 100.0, "long", 99.0, 103.0, exit_time=2, exit_price=103.0, outcome="win")
        for _ in range(8)
    ]
    res = BacktestResult("AAPL", "1d", "bos", trades=wins)
    row = SweepRow(
        params={"tp_r": 2.0}, is_result=res, oos_result=res,
        is_score=1.0, oos_score=1.0, decay=1.0, overfit=False,
        overfit_stats=OverfitStats(0.5, 0.4, 0.7, n_trials=1, pbo=float("nan")),
    )
    out = format_sweep_results([row], "bos", "AAPL", "1d")
    assert "n/a" in out
```

Add an end-to-end wiring test if the module already has a `run_param_sweep` integration test with an in-memory DB; otherwise the dataclass + formatter tests above are sufficient for the unit gate (the real-data path is exercised by the existing sweep tests, which must still pass with `overfit_stats` defaulting to `None`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_param_sweep.py -k "overfit or dsr or pbo" -v`
Expected: FAIL — `OverfitStats` import error / `SweepRow` has no `overfit_stats`.

- [ ] **Step 3: Write minimal implementation**

**3a.** Add `OverfitStats` to `analytics/backtest/stats_overfit.py` (so the sweep and tests share one type):

```python
@dataclass(frozen=True)
class OverfitStats:
    """Per-config + sweep-level overfitting figures attached to a SweepRow.

    is_sharpe / oos_sharpe / deflated_sharpe are per-config; n_trials and pbo are
    sweep-level (identical on every row of a given sweep).
    """

    is_sharpe: float
    oos_sharpe: float
    deflated_sharpe: float
    n_trials: int
    pbo: float
```

**3b.** In `analytics/param_sweep.py`, import the helpers and add the field:

```python
from analytics.backtest.stats_overfit import (
    OverfitStats,
    build_performance_matrix,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from analytics.backtest.stats_overfit import _moments as _r_moments
```

Add to `SweepRow` (after the `overfit: bool` field, default `None` so existing constructors stay valid):

```python
    overfit_stats: OverfitStats | None = None
```

Module constant near `_SEP`:

```python
_PBO_MATRIX_ROWS = 100  # timeline buckets fed to CSCV
```

**3c.** In `run_param_sweep`, after the grid completes and `rows` is populated, **before** the sort/truncate (`all_zero = ...`), compute and attach the stats over the **full grid**:

```python
    # --- Overfitting controls (Phase 0.3a/b) over the full grid -------------
    n_trials = len(rows)
    is_sharpes = [
        sharpe_ratio([t.pnl_r for t in r.is_result.closed_trades if t.pnl_r is not None])
        for r in rows
    ]
    trade_points = [
        [
            (t.entry_time, t.pnl_r)
            for t in (*r.is_result.closed_trades, *r.oos_result.closed_trades)
            if t.pnl_r is not None
        ]
        for r in rows
    ]
    pbo = probability_of_backtest_overfitting(
        build_performance_matrix(trade_points, _PBO_MATRIX_ROWS)
    ).pbo
    for r, is_sr in zip(rows, is_sharpes, strict=True):
        oos_sr = sharpe_ratio(
            [t.pnl_r for t in r.oos_result.closed_trades if t.pnl_r is not None]
        )
        _, _, skew, kurt = _r_moments(
            [t.pnl_r for t in r.is_result.closed_trades if t.pnl_r is not None]
        )
        n_is = len(r.is_result.closed_trades)
        dsr = deflated_sharpe_ratio(is_sr, is_sharpes, n_is, skew, kurt)
        r.overfit_stats = OverfitStats(is_sr, oos_sr, dsr, n_trials, pbo)
```

(`SweepRow` is a mutable `@dataclass`, so in-place assignment is fine. The `tp_r` selection and `rows[:top_n]` truncation that follow are unchanged.)

**3d.** In `format_sweep_results`, in the recommended-config block (after the `OOS: avg_r=…` line, before the directional hint), render the figures:

```python
        stats = best.overfit_stats
        if stats is not None:
            pbo_str = "n/a" if math.isnan(stats.pbo) else _fmt_pct(stats.pbo)
            lines.append(
                f"  Overfit controls: trials N={stats.n_trials}  PBO={pbo_str}"
            )
            lines.append(
                f"  Recommended Sharpe={_fmt_r(stats.is_sharpe)}  "
                f"Deflated Sharpe={_fmt_r(stats.deflated_sharpe)}"
            )
```

(`math` is already imported in `param_sweep.py`; `_fmt_pct` / `_fmt_r` already exist.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_param_sweep.py -v`
Expected: PASS — new overfit tests green; all pre-existing sweep tests still pass (`overfit_stats` defaults to `None`, untouched paths unaffected).

- [ ] **Step 5: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py analytics/backtest/stats_overfit.py
git commit -m "feat(overfit): surface DSR + PBO in the WFO sweep report"
```

---

### Task 6: Docs sync + regression-golden verification + final gate

**Files:**

- Modify: `CLAUDE.md`, `.claude/context/analytics.md`

- [ ] **Step 1: `CLAUDE.md` — backtest module bullet**

In the `backtest/` module description, add `stats_overfit.py` to the module list (mirror the neighbouring style):

```markdown
  - `stats_overfit.py` — overfitting / multiple-testing controls (Phase 0.3a/b). Pure math (numpy + stdlib `statistics.NormalDist`; no scipy, no DB, no engine import): `sharpe_ratio` (per-trade R Sharpe, non-annualized), `probabilistic_sharpe_ratio` / `expected_max_sharpe` / `deflated_sharpe_ratio` (Bailey & LdP 2014 — deflates the selected config's Sharpe by the grid trial count N), `probability_of_backtest_overfitting` (CSCV — Bailey et al. 2017) + `build_performance_matrix`, and the `OverfitStats` carrier. `BacktestResult` gains a `sharpe` property; `run_param_sweep` computes DSR per config + a sweep-level PBO over the full grid and attaches `OverfitStats` to each `SweepRow`; `format_sweep_results` prints `trials N=… PBO=…` + the recommended config's Sharpe/DSR. 0.3c purged-embargoed CV is a later PR (this does not touch `_split_ohlcv`).
```

- [ ] **Step 2: `.claude/context/analytics.md` — reference section**

Add a short section documenting `stats_overfit.py` (the function list, the per-trade-R Sharpe basis, the DSR null = expected-max-Sharpe over N trials, the CSCV PBO common-axis = time-bucketed R), mirroring the style of neighbouring entries.

- [ ] **Step 3: Verify regression goldens are byte-identical**

Run: `make test-regression`
Expected: PASS / SKIP unchanged. The sweep report is not in the fixture pipeline and `_extract_metrics` enumerates fields explicitly (no `.sharpe`), so **no golden moves**. If any golden moves, STOP and investigate before proceeding.

- [ ] **Step 4: Full project gate**

Run: `make lint-py && make typecheck && make test && make lint-md`
Expected: all green (ruff format+lint, mypy strict, full pytest suite, markdownlint). Note the `_moments` import alias `_r_moments` in `param_sweep` — keep the underscore-prefixed name so ruff does not flag a "private import" lint; if it does, expose a public `return_moments` alias from `stats_overfit` instead.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md .claude/context/analytics.md
git commit -m "docs(overfit): document Phase 0.3a/b DSR + PBO controls"
```

- [ ] **Step 6: Push + open PR**

```bash
git push -u origin feat/phase0-overfit-controls
gh pr create --repo s10023/buibui-wifey-wall-street-bot --base main \
  --title "feat(overfit): Phase 0.3a/b Deflated Sharpe + PBO controls" \
  --body "Implements Phase 0.3a (Deflated Sharpe Ratio) + 0.3b (Probability of Backtest Overfitting via CSCV) from docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md §4.3. New pure module analytics/backtest/stats_overfit.py; BacktestResult.sharpe; DSR + PBO surfaced in the WFO sweep report. Additive measurement-only — regression goldens byte-identical, no db-update. 0.3c purged-embargoed CV deferred to a later PR."
```

Then run `/post-branch` before reporting the PR URL.

---

## Self-Review

**Spec coverage (§4.3, sub-deliverables 0.3a + 0.3b):**

- "Compute the strategy Sharpe on the per-trade R-multiple series (`SR = mean(r)/std(r)`)" → `sharpe_ratio` (Task 1). ✓ Annualization deliberately deferred (Scope notes; spec §7 leaves the basis as per-trade R).
- "Add Deflated Sharpe (Bailey & LdP 2014) using the trial count N (= grid size) and the SR's sampling variance" → `expected_max_sharpe` + `deflated_sharpe_ratio` (Task 2); N = `len(grid)` (Task 5). ✓
- "`BacktestResult` gains `sharpe`" → Task 4. ✓
- "`param_sweep` records N and emits DSR per config" → Task 5 (`OverfitStats.n_trials`, per-row `deflated_sharpe`). ✓
- "0.3b PBO — CSCV: partition the trade series into S submatrices, rank configs IS, measure the OOS rank of the IS-best, report PBO = P(logit < 0)" → `probability_of_backtest_overfitting` (Task 3). ✓ Common-axis design (time-bucketed R) recorded in Scope notes.
- "Add to `stats_overfit.py`; surface in the sweep report" → Task 5 footer. ✓
- DoD "every committed `tp_r` carries a Deflated-Sharpe and PBO figure; sweeps log trial counts" → recommended config (the one that becomes the committed `tp_r`) prints Sharpe + DSR; sweep prints N + PBO; grid size N is already printed by `run_param_sweep`. ✓
- "Default flags reproduce current goldens" → additive only; `_extract_metrics` is explicit; sweep output not in fixtures → goldens unmoved (Task 6 Step 3). ✓

**Explicitly out of scope (deferred to later PRs):** 0.3c purged + embargoed CV / `cv_mode` flag (no change to `_split_ohlcv`); annualized Sharpe; per-row DSR table column (kept to the recommended-config footer to avoid widening the 100-col table).

**Placeholder scan:** none — every code block is complete and runnable. Task 6 Step 2 references concrete neighbouring style rather than a TODO.

**Type consistency:** `sharpe_ratio(Sequence[float]) -> float`, `probabilistic_sharpe_ratio(sr, n, skew, kurt, sr_star=0.0) -> float`, `expected_max_sharpe(sr_variance, n_trials) -> float`, `deflated_sharpe_ratio(observed_sr, trial_sharpes, n_returns, skew, kurt) -> float`, `PBOResult(pbo, n_combinations, logits)`, `probability_of_backtest_overfitting(perf_matrix, n_splits=16) -> PBOResult`, `build_performance_matrix(trade_points, n_rows) -> np.ndarray`, `OverfitStats(is_sharpe, oos_sharpe, deflated_sharpe, n_trials, pbo)` are used identically across Tasks 1–6. `SweepRow.overfit_stats: OverfitStats | None = None` defaults preserve every existing constructor. ✓

**Circular-import check:** `stats_overfit` imports only numpy + stdlib; `engine` imports `sharpe_ratio` from `stats_overfit`; `param_sweep` imports from both. One-directional — no cycle. ✓

**No-caller-break check:** `run_param_sweep` still returns `list[SweepRow]`; `format_sweep_results` signature unchanged; `cli/param.py`, `tools/multi_symbol_wfo.py`, `run_strategy_audit` untouched. ✓
