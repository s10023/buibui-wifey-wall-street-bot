# Research-Guards Parity (N2, PR 1 — pure-math foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the parent repo's `analytics/research_guards/` pure-math package (MinTRL, bootstrap CIs, multiple-testing haircut, plus PSR/DSR/PBO at the parent's API) and its two commit-gate consumers (`sweep_guard.py`, `audit_guard.py`) into wifey, fully tested — with **zero wiring** so backtest goldens and sweep output stay byte-identical.

**Architecture:** Mirror the parent package **verbatim** as a new, self-contained subsystem (`analytics/research_guards/` + `analytics/sweep_guard.py` + `analytics/audit_guard.py`). Leave the existing `analytics/backtest/stats_overfit.py` (Phase 0.3a/b sweep-footer figures) untouched. The minor DSR/PBO math duplication between the two is the deliberate cost of keeping future `/sync-parent` ports 1:1; the two serve distinct roles (footer *display* vs commit *gate*). Wiring `evaluate_commit_gate` into `run_param_sweep` is a deferred follow-up (PR 2) — out of scope here.

**Tech Stack:** Python 3.11+, numpy, stdlib `statistics.NormalDist` (no scipy), pytest, mypy strict, ruff. Source of truth for every verbatim port: `/home/kng/repo/buibui-moon-trader-bot/` (parent, frozen — read each file and copy it unchanged unless a task says otherwise).

---

## File Structure

New files (all under wifey `/home/kng/repo/buibui-wifey-wall-street-bot/`):

- `analytics/research_guards/__init__.py` — eager re-exports of the package's public API
- `analytics/research_guards/psr.py` — Probabilistic Sharpe Ratio (parent signature: `sr_benchmark`, raises on degenerate input)
- `analytics/research_guards/dsr.py` — Deflated Sharpe Ratio + `expected_max_sharpe(n_trials, sr_variance)`
- `analytics/research_guards/pbo.py` — CSCV PBO (`cscv_pbo`, per-trial Sharpe metric, `degradation_slope`)
- `analytics/research_guards/mintrl.py` — Minimum Track Record Length
- `analytics/research_guards/bootstrap.py` — block/stationary bootstrap CI
- `analytics/research_guards/haircut.py` — Harvey-Liu multiple-testing Sharpe haircut
- `analytics/sweep_guard.py` — `evaluate_commit_gate` (DSR ∧ PBO ∧ MinTRL → COMMIT/BLOCK/INSUFFICIENT)
- `analytics/audit_guard.py` — `evaluate_audit_cells` (bootstrap CI ∧ Holm haircut → ENABLE/DISABLE/CONCENTRATE/INSUFFICIENT)
- `tests/test_psr.py`, `tests/test_mintrl.py`, `tests/test_dsr.py`, `tests/test_pbo.py`, `tests/test_bootstrap.py`, `tests/test_haircut.py`, `tests/test_sweep_guard.py`, `tests/test_audit_guard.py` — verbatim from parent `tests/`

Untouched: `analytics/backtest/stats_overfit.py`, all golden fixtures, `param_sweep.py`, `recalibrate_lib.py`.

**Note on `psr.py` / `dsr.py` / `pbo.py`:** these intentionally duplicate math already in `stats_overfit.py`, but at the *parent's* API (which `sweep_guard`/`audit_guard` depend on). Do not try to merge them into `stats_overfit.py` — that would change the 0.3a/b footer numbers and break the forward-port path. The two coexist by design.

---

### Task 1: PSR module (package root + first module)

**Files:**

- Create: `analytics/research_guards/__init__.py` (empty for now)
- Create: `analytics/research_guards/psr.py`
- Test: `tests/test_psr.py`

- [ ] **Step 1: Create the package marker**

Create `analytics/research_guards/__init__.py` as an **empty file** (one trailing newline). It will be filled with re-exports in Task 7. An empty marker is enough for the submodule imports the tests use (`from analytics.research_guards.psr import ...`).

- [ ] **Step 2: Port the failing test**

Read `/home/kng/repo/buibui-moon-trader-bot/tests/test_psr.py` and copy it **verbatim** to `tests/test_psr.py` (no edits — imports are `from analytics.research_guards.psr import probabilistic_sharpe_ratio`).

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/test_psr.py -q`
Expected: collection/import error — `ModuleNotFoundError: No module named 'analytics.research_guards.psr'`.

- [ ] **Step 4: Write the implementation**

Create `analytics/research_guards/psr.py` with exactly:

```python
"""Probabilistic Sharpe Ratio (Bailey & López de Prado, 2012).

Pure-math helper for the research-guards package — no DB / IO / network.
"""

import math
from statistics import NormalDist

_NORM = NormalDist()


def probabilistic_sharpe_ratio(
    sr: float,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """Probability that the true Sharpe exceeds ``sr_benchmark``.

    ``kurtosis`` is **non-excess** (a normal distribution has kurtosis 3.0).
    scipy/pandas report *excess* kurtosis, so a caller passing one of those
    moments must add 3.0 first.

    Returns a probability in ``[0, 1]``.
    """
    if n_obs < 2:
        raise ValueError("n_obs must be >= 2")
    variance = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    if variance <= 0.0:
        raise ValueError("degenerate higher moments: non-positive PSR variance term")
    z = (sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(variance)
    return _NORM.cdf(z)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/test_psr.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add analytics/research_guards/__init__.py analytics/research_guards/psr.py tests/test_psr.py
git commit -m "feat(guards): port PSR into research_guards package (N2)"
```

---

### Task 2: MinTRL module

**Files:**

- Create: `analytics/research_guards/mintrl.py`
- Test: `tests/test_mintrl.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_mintrl.py` **verbatim** to `tests/test_mintrl.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_mintrl.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.research_guards.mintrl'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/mintrl.py` with exactly:

```python
"""Minimum Track Record Length (López de Prado).

The number of observations a strategy needs before its PSR against
``target_sr`` reaches ``confidence``. Pure math; round-trips with ``psr.py``.
"""

from statistics import NormalDist

_NORM = NormalDist()


def min_track_record_length(
    sr: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    target_sr: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """Fractional number of observations needed for PSR to reach ``confidence``.

    ``kurtosis`` is non-excess (normal = 3.0). Returns ``inf`` when ``sr`` does
    not exceed ``target_sr`` (the confidence bound is unreachable). The result
    is fractional — callers typically ceil it.
    """
    if sr <= target_sr:
        return float("inf")
    variance = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    z = _NORM.inv_cdf(confidence)
    return 1.0 + variance * (z / (sr - target_sr)) ** 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_mintrl.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/mintrl.py tests/test_mintrl.py
git commit -m "feat(guards): port Minimum Track Record Length (N2)"
```

---

### Task 3: DSR module

**Files:**

- Create: `analytics/research_guards/dsr.py`
- Test: `tests/test_dsr.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_dsr.py` **verbatim** to `tests/test_dsr.py` (imports `deflated_sharpe_ratio, expected_max_sharpe`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_dsr.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.research_guards.dsr'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/dsr.py` with exactly:

```python
"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

Deflates an observed Sharpe by the expected-maximum Sharpe that ``N`` trials
would produce by chance, then expresses the result as a PSR. Pure math.
"""

import math
import statistics
from collections.abc import Sequence
from statistics import NormalDist

from analytics.research_guards.psr import probabilistic_sharpe_ratio

EULER_MASCHERONI = 0.5772156649015329
_NORM = NormalDist()


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe across ``n_trials`` independent trials.

    Uses the Gumbel-tail approximation from Bailey & LdP (2014). Returns 0.0
    when there is no effective trial multiplicity (``n_trials < 2``) or no
    cross-trial dispersion (``sr_variance <= 0``) — i.e. no deflation.
    """
    if n_trials < 2 or sr_variance <= 0.0:
        return 0.0
    std = math.sqrt(sr_variance)
    n = float(n_trials)
    gamma = EULER_MASCHERONI
    term = (1.0 - gamma) * _NORM.inv_cdf(1.0 - 1.0 / n) + gamma * _NORM.inv_cdf(
        1.0 - 1.0 / (n * math.e)
    )
    return std * term


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    *,
    trial_srs: Sequence[float] | None = None,
    n_trials: int | None = None,
    sr_variance: float | None = None,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR with the benchmark set to the expected-maximum Sharpe.

    Provide **exactly one** source of trial multiplicity:

    * ``trial_srs`` — the per-trial Sharpe samples (``N`` and variance derived),
      or
    * ``n_trials`` + ``sr_variance`` — both required.

    Returns a probability in ``[0, 1]``. Equals
    :func:`probabilistic_sharpe_ratio` (benchmark 0) when there is no
    multiplicity to deflate against.
    """
    path_a = trial_srs is not None
    path_b = n_trials is not None or sr_variance is not None
    if path_a == path_b:
        raise ValueError("provide exactly one of trial_srs or (n_trials + sr_variance)")
    if path_a:
        assert trial_srs is not None  # narrowed by path_a
        srs = list(trial_srs)
        sr0 = (
            0.0
            if len(srs) < 2
            else expected_max_sharpe(len(srs), statistics.variance(srs))
        )
    else:
        if n_trials is None or sr_variance is None:
            raise ValueError("path B requires both n_trials and sr_variance")
        sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(sr, n_obs, skew, kurtosis, sr_benchmark=sr0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_dsr.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/dsr.py tests/test_dsr.py
git commit -m "feat(guards): port Deflated Sharpe Ratio (N2)"
```

---

### Task 4: PBO (CSCV) module

**Files:**

- Create: `analytics/research_guards/pbo.py`
- Test: `tests/test_pbo.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_pbo.py` **verbatim** to `tests/test_pbo.py` (imports `cscv_pbo`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_pbo.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.research_guards.pbo'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/pbo.py` with exactly:

```python
"""Probability of Backtest Overfitting via CSCV (Bailey, Borwein, LdP, 2015).

Combinatorially Symmetric Cross-Validation: split the per-period performance
matrix into ``n_splits`` blocks, and over every balanced train/test partition
ask whether the in-sample-best trial stays good out-of-sample. PBO is the
fraction of partitions where it lands in the bottom half OOS. Pure math.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    logits: list[float]
    degradation_slope: float
    n_combinations: int


def _sharpe(col: npt.NDArray[np.float64]) -> float:
    """Per-period Sharpe of one trial's returns (the default CSCV metric)."""
    if col.shape[0] < 2:
        return 0.0
    sd = float(np.std(col, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(col)) / sd


def _relative_rank(values: npt.NDArray[np.float64], target: int, n: int) -> float:
    """Average-rank relative position of ``target`` in ``values``, in (0, 1)."""
    v = values[target]
    less = int(np.sum(values < v))
    equal = int(np.sum(values == v))  # includes the target itself
    rank = less + (equal + 1) / 2.0  # 1-indexed average rank
    return rank / (n + 1)


def _ols_slope(x: list[float], y: list[float]) -> float:
    """OLS slope of ``y`` on ``x`` (0.0 when ``x`` has no variance)."""
    xa = np.asarray(x, dtype=np.float64)
    ya = np.asarray(y, dtype=np.float64)
    xm = float(xa.mean())
    denom = float(np.sum((xa - xm) ** 2))
    if denom == 0.0:
        return 0.0
    ym = float(ya.mean())
    return float(np.sum((xa - xm) * (ya - ym)) / denom)


def cscv_pbo(
    perf_matrix: npt.NDArray[np.float64],
    n_splits: int = 14,
    metric: Callable[[npt.NDArray[np.float64]], float] | None = None,
) -> PBOResult:
    """Probability of Backtest Overfitting over a (T_periods, N_trials) matrix.

    Splits the ``T`` rows into ``n_splits`` equal blocks (remainder dropped) and
    iterates every balanced train/test partition — ``C(n_splits, n_splits/2)``
    combinations (e.g. ``C(14, 7) = 3432`` at the default). ``metric`` defaults
    to per-trial Sharpe.
    """
    if n_splits < 4 or n_splits % 2 != 0:
        raise ValueError("n_splits must be even and >= 4")
    m = np.asarray(perf_matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError("perf_matrix must be 2-D (T_periods, N_trials)")
    t_periods, n_trials = int(m.shape[0]), int(m.shape[1])
    if n_trials < 2:
        raise ValueError("need >= 2 trials")
    block_size = t_periods // n_splits
    if block_size < 2:
        raise ValueError("not enough periods for n_splits (need >= 2 rows per block)")
    score = _sharpe if metric is None else metric

    blocks = [m[i * block_size : (i + 1) * block_size] for i in range(n_splits)]
    half = n_splits // 2
    logits: list[float] = []
    is_perf: list[float] = []
    oos_perf: list[float] = []
    for train_ids in combinations(range(n_splits), half):
        train_set = set(train_ids)
        test_ids = [i for i in range(n_splits) if i not in train_set]
        train = np.vstack([blocks[i] for i in train_ids])
        test = np.vstack([blocks[i] for i in test_ids])
        is_metrics = np.array([score(train[:, c]) for c in range(n_trials)])
        oos_metrics = np.array([score(test[:, c]) for c in range(n_trials)])
        n_star = int(np.argmax(is_metrics))
        omega = _relative_rank(oos_metrics, n_star, n_trials)
        omega = min(max(omega, 1.0 / (n_trials + 1)), n_trials / (n_trials + 1))
        logits.append(math.log(omega / (1.0 - omega)))
        is_perf.append(float(is_metrics[n_star]))
        oos_perf.append(float(oos_metrics[n_star]))

    pbo = sum(1 for lam in logits if lam <= 0.0) / len(logits)
    return PBOResult(
        pbo=pbo,
        logits=logits,
        degradation_slope=_ols_slope(is_perf, oos_perf),
        n_combinations=len(logits),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_pbo.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/pbo.py tests/test_pbo.py
git commit -m "feat(guards): port CSCV PBO (N2)"
```

---

### Task 5: Bootstrap CI module

**Files:**

- Create: `analytics/research_guards/bootstrap.py`
- Test: `tests/test_bootstrap.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_bootstrap.py` **verbatim** to `tests/test_bootstrap.py` (imports `block_bootstrap_ci`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_bootstrap.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.research_guards.bootstrap'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/bootstrap.py` with exactly:

```python
"""Block / stationary bootstrap confidence intervals (Politis & Romano, 1994).

Resamples wrap-around blocks of a return series to build a percentile CI for an
arbitrary statistic, preserving short-range autocorrelation that an iid
bootstrap would destroy. Pure math (numpy only).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class BootstrapCI:
    point: float
    lo: float
    hi: float
    alpha: float
    n_valid: int


def _stationary_indices(
    n: int, block: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """Stationary bootstrap (geometric block length, mean ``block``)."""
    p = 1.0 / block
    idx = np.empty(n, dtype=np.int64)
    idx[0] = rng.integers(0, n)
    draws = rng.random(n)
    restarts = rng.integers(0, n, size=n)
    for t in range(1, n):
        if draws[t] < p:
            idx[t] = restarts[t]
        else:
            idx[t] = (idx[t - 1] + 1) % n
    return idx


def _circular_indices(
    n: int, block: int, rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """Circular block bootstrap (fixed block length, wrap-around)."""
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=n_blocks)
    offsets = np.arange(block)
    idx = ((starts[:, None] + offsets[None, :]) % n).reshape(-1)
    return idx[:n].astype(np.int64)


def block_bootstrap_ci(
    returns: npt.NDArray[np.float64],
    stat_fn: Callable[[npt.NDArray[np.float64]], float],
    n_boot: int = 10_000,
    block: int | None = None,
    alpha: float = 0.05,
    method: Literal["stationary", "circular"] = "stationary",
    seed: int | None = None,
) -> BootstrapCI:
    """Percentile bootstrap CI for ``stat_fn`` over a (possibly serially
    correlated) return series.

    ``block`` defaults to ``round(len(returns) ** (1/3))`` and is clamped to
    ``[1, len-1]``. ``stat_fn`` results that are NaN are dropped (tracked via
    ``n_valid``). ``seed`` makes the resampling reproducible.
    """
    arr = np.asarray(returns, dtype=np.float64)
    n = int(arr.shape[0])
    if n < 2:
        raise ValueError("returns must have length >= 2")
    if block is None:
        block = max(1, round(n ** (1.0 / 3.0)))
    block = max(1, min(block, n - 1))
    rng = np.random.default_rng(seed)
    point = float(stat_fn(arr))
    samples: list[float] = []
    for _ in range(n_boot):
        if method == "stationary":
            idx = _stationary_indices(n, block, rng)
        else:
            idx = _circular_indices(n, block, rng)
        value = stat_fn(arr[idx])
        if not math.isnan(value):
            samples.append(value)
    if samples:
        lo = float(np.quantile(samples, alpha / 2.0))
        hi = float(np.quantile(samples, 1.0 - alpha / 2.0))
    else:
        lo = float("nan")
        hi = float("nan")
    return BootstrapCI(point=point, lo=lo, hi=hi, alpha=alpha, n_valid=len(samples))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(guards): port block/stationary bootstrap CI (N2)"
```

---

### Task 6: Haircut module

**Files:**

- Create: `analytics/research_guards/haircut.py`
- Test: `tests/test_haircut.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_haircut.py` **verbatim** to `tests/test_haircut.py` (imports `haircut_sharpe`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_haircut.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.research_guards.haircut'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/research_guards/haircut.py` with exactly:

```python
"""Multiple-testing Sharpe haircut (Harvey & Liu, 2014 — classic core).

Adjusts a single-test p-value for the number of strategies tried, then backs
the adjustment out into a haircut Sharpe. v1 ships the three classic
adjustments (Bonferroni / Holm / BHY) on p-values; the full Harvey-Liu
empirical-t procedure is a later refinement.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

_NORM = NormalDist()

_Method = Literal["bonferroni", "holm", "bhy"]

# Φ⁻¹ blows up at the open endpoints; keep adjusted p-values strictly inside.
_P_FLOOR = 1e-15


@dataclass(frozen=True)
class HaircutResult:
    adjusted_pvalue: float
    haircut_sharpe: float
    haircut_pct: float
    method: str
    fell_back: bool


def _adjust_pvalues(pvals: Sequence[float], method: _Method) -> list[float]:
    """Holm (FWER) or BHY (FDR) adjusted p-values in the original order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    if method == "holm":
        running = 0.0
        for rank, idx in enumerate(order):  # ascending; factor = m - rank
            val = min(1.0, (m - rank) * pvals[idx])
            running = max(running, val)
            adjusted[idx] = running
    else:  # bhy — Benjamini-Hochberg-Yekutieli (FDR under dependence)
        c_m = sum(1.0 / k for k in range(1, m + 1))
        prev = 1.0
        for rank in range(m - 1, -1, -1):  # step-up from the largest p-value
            idx = order[rank]
            val = min(1.0, c_m * m / (rank + 1) * pvals[idx])
            prev = min(prev, val)
            adjusted[idx] = prev
    return adjusted


def haircut_sharpe(
    sr: float,
    n_obs: int,
    n_tests: int,
    method: _Method = "holm",
    pvalues_all: Sequence[float] | None = None,
) -> HaircutResult:
    """Adjust ``sr`` for having searched ``n_tests`` strategies.

    ``pvalues_all`` (the full set of per-test p-values) is required for the
    ``holm`` / ``bhy`` step-down ordering; if it is omitted the function falls
    back to Bonferroni and sets ``fell_back=True``. For ``holm`` / ``bhy`` pass
    ``pvalues_all`` with ``len == n_tests``.
    """
    if n_tests < 1:
        raise ValueError("n_tests must be >= 1")
    if sr <= 0.0:
        return HaircutResult(1.0, 0.0, 0.0, method, False)

    t = sr * math.sqrt(n_obs)
    p = 2.0 * (1.0 - _NORM.cdf(abs(t)))
    fell_back = False
    if n_tests == 1:
        p_adj = p
    elif method == "bonferroni":
        p_adj = min(1.0, p * n_tests)
    elif pvalues_all is None:
        p_adj = min(1.0, p * n_tests)
        fell_back = True
    else:
        adjusted = _adjust_pvalues(list(pvalues_all), method)
        nearest = min(range(len(pvalues_all)), key=lambda i: abs(pvalues_all[i] - p))
        p_adj = adjusted[nearest]

    p_clamped = min(max(p_adj, _P_FLOOR), 1.0)
    t_adj = _NORM.inv_cdf(1.0 - p_clamped / 2.0)
    haircut = max(0.0, t_adj) / math.sqrt(n_obs)
    haircut_pct = 1.0 - haircut / sr  # sr > 0 here
    return HaircutResult(p_adj, haircut, haircut_pct, method, fell_back)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_haircut.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/haircut.py tests/test_haircut.py
git commit -m "feat(guards): port multiple-testing Sharpe haircut (N2)"
```

---

### Task 7: Package re-exports (`__init__.py`)

**Files:**

- Modify: `analytics/research_guards/__init__.py` (currently empty)

- [ ] **Step 1: Write the full re-export module**

Replace the empty `analytics/research_guards/__init__.py` with exactly:

```python
"""Research guardrails: overfitting & multiple-testing controls.

Pure-math statistics (no DB / IO / network) used to gate strategy selection
against in-sample mirages: Probabilistic & Deflated Sharpe, PBO/CSCV, the
multiple-testing Sharpe haircut, Minimum Track Record Length, and block /
stationary bootstrap confidence intervals.

Eager re-exports so callers can do
``from analytics.research_guards import deflated_sharpe_ratio, cscv_pbo``.
"""

from analytics.research_guards.bootstrap import BootstrapCI, block_bootstrap_ci
from analytics.research_guards.dsr import (
    EULER_MASCHERONI,
    deflated_sharpe_ratio,
    expected_max_sharpe,
)
from analytics.research_guards.haircut import HaircutResult, haircut_sharpe
from analytics.research_guards.mintrl import min_track_record_length
from analytics.research_guards.pbo import PBOResult, cscv_pbo
from analytics.research_guards.psr import probabilistic_sharpe_ratio

__all__ = [
    "EULER_MASCHERONI",
    "BootstrapCI",
    "HaircutResult",
    "PBOResult",
    "block_bootstrap_ci",
    "cscv_pbo",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "haircut_sharpe",
    "min_track_record_length",
    "probabilistic_sharpe_ratio",
]
```

- [ ] **Step 2: Verify the package imports cleanly**

Run: `poetry run python -c "from analytics.research_guards import cscv_pbo, deflated_sharpe_ratio, min_track_record_length, block_bootstrap_ci, haircut_sharpe; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add analytics/research_guards/__init__.py
git commit -m "feat(guards): research_guards package re-exports (N2)"
```

---

### Task 8: Sweep commit-gate (`sweep_guard.py`)

**Files:**

- Create: `analytics/sweep_guard.py`
- Test: `tests/test_sweep_guard.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_sweep_guard.py` **verbatim** to `tests/test_sweep_guard.py` (imports `from analytics.sweep_guard import ...`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sweep_guard.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.sweep_guard'`.

- [ ] **Step 3: Write the implementation**

Read `/home/kng/repo/buibui-moon-trader-bot/analytics/sweep_guard.py` and copy it **verbatim** to `analytics/sweep_guard.py`. It imports only `from analytics.research_guards import (cscv_pbo, deflated_sharpe_ratio, min_track_record_length)` plus stdlib/numpy — all satisfied by Tasks 1–7. No edits required. (Its `deflated_sharpe_ratio` call uses the `n_trials=...`, `sr_variance=...` keyword path defined in Task 3.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_sweep_guard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sweep_guard.py tests/test_sweep_guard.py
git commit -m "feat(guards): port sweep commit-gate (N2)"
```

---

### Task 9: Audit commit-gate (`audit_guard.py`)

**Files:**

- Create: `analytics/audit_guard.py`
- Test: `tests/test_audit_guard.py`

- [ ] **Step 1: Port the failing test**

Copy `/home/kng/repo/buibui-moon-trader-bot/tests/test_audit_guard.py` **verbatim** to `tests/test_audit_guard.py` (imports `from analytics import audit_guard` + `from analytics.audit_guard import ...`).

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_audit_guard.py -q`
Expected: `ModuleNotFoundError: No module named 'analytics.audit_guard'`.

- [ ] **Step 3: Write the implementation**

Read `/home/kng/repo/buibui-moon-trader-bot/analytics/audit_guard.py` and copy it **verbatim** to `analytics/audit_guard.py`. It imports only `from analytics.research_guards import block_bootstrap_ci, haircut_sharpe` plus stdlib/numpy — all satisfied by Tasks 5–7. No edits required.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_audit_guard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/audit_guard.py tests/test_audit_guard.py
git commit -m "feat(guards): port audit commit-gate (N2)"
```

---

### Task 10: Full verification, docs sync, memory

**Files:**

- Modify: `CLAUDE.md` (Project Structure — add the three new module entries)
- Modify: `.claude/context/analytics.md` (full API reference for the new package + guards)
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/MEMORY.md` (Current State)
- Modify: `~/.claude-personal/projects/-home-kng-repo-buibui-wifey-wall-street-bot/memory/project_parent_fresh_eyes_port.md` (research-guards delta — mark math + guards ported, wiring still pending)

- [ ] **Step 1: Confirm git identity**

Run: `git config --local user.email`
Expected: `ngkhaijian@gmail.com`. If not, set it: `git config --local user.email ngkhaijian@gmail.com` and `git config --local user.name s10023`.

- [ ] **Step 2: Run the full quality gate**

```bash
make lint-py
make typecheck
make test
```

Expected: ruff format+lint clean; mypy strict clean; full suite green (prior ~1490 pass / 3 skip, now +8 new test files, no regressions). If mypy flags anything in the ported files, it is an environment/config drift — fix minimally to satisfy strict mode without changing behavior, and note it.

- [ ] **Step 3: Confirm goldens are byte-identical (no wiring → no movement)**

Run: `make test-regression`
Expected: PASS with no golden diffs (these modules are not imported by any backtest/sweep path yet).

- [ ] **Step 4: Update `CLAUDE.md` Project Structure**

Under the `analytics/` bullet list, add an entry near `backtest/`’s `stats_overfit.py` description noting:

- `research_guards/` — pure-math research-integrity package mirrored verbatim from parent (`psr.py`, `dsr.py`, `pbo.py` = `cscv_pbo`, `mintrl.py`, `bootstrap.py`, `haircut.py`; eager re-exports). Distinct from `backtest/stats_overfit.py`: that owns the 0.3a/b **sweep-footer display** figures; this owns the **commit-gate** subsystem (parent API; kept separate for 1:1 `/sync-parent` forward-port). Not yet wired into any sweep/recalibrate path (N2 PR 1 = math only; wiring deferred to PR 2).
- `sweep_guard.py` — `evaluate_commit_gate(chosen, all_trials, n_grid=...)` → `CommitGateVerdict` (COMMIT/DO_NOT_COMMIT/INSUFFICIENT); commit rule DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n ≥ MinTRL.
- `audit_guard.py` — `evaluate_audit_cells(cells, ...)` → `CellVerdict[]` (ENABLE/DISABLE/CONCENTRATE/INSUFFICIENT) via bootstrap CI ∧ Holm haircut.

- [ ] **Step 5: Update `.claude/context/analytics.md`**

Add a short section documenting the `research_guards` package public API (the 11 re-exported names + their one-line contracts) and the two guard entry points (`evaluate_commit_gate`, `evaluate_audit_cells`) with their verdict dataclasses (`CommitGateVerdict`, `CellVerdict`, `TrialPerf`, `AuditCell`). Mirror the surrounding doc style.

- [ ] **Step 6: Markdown lint the docs**

Run: `make lint-md`
Expected: clean. Hand-format to the full default ruleset (CI is stricter than local — watch MD013/MD060 etc.).

- [ ] **Step 7: Commit docs**

```bash
git add CLAUDE.md .claude/context/analytics.md
git commit -m "docs(guards): document research_guards package + commit gates (N2)"
```

- [ ] **Step 8: Update memory**

In `MEMORY.md` Current State: add a one-line "Last session" entry — N2 PR 1 shipped: research_guards pure-math package (PSR/DSR/PBO/MinTRL/bootstrap/haircut) + `sweep_guard`/`audit_guard` ported verbatim from parent, zero wiring → goldens byte-identical; wiring into `run_param_sweep`/recalibrate deferred to PR 2. In `project_parent_fresh_eyes_port.md` "Research-guards delta": strike `mintrl.py`/`bootstrap.py`/`haircut.py` from the to-port list, leaving only the commit-gate *wiring* (sweep return-type + footer + `/param-sweep-apply` + recalibrate) as the remaining N2 work.

- [ ] **Step 9: PR**

Push the branch via the SSH alias and open the PR with `--repo s10023/buibui-wifey-wall-street-bot`. Then run `/pr-summary` and `/post-branch`.

```bash
git push -u origin feat/research-guards-parity
gh pr create --repo s10023/buibui-wifey-wall-street-bot --title "feat(guards): Phase N2 PR 1 — port research_guards pure-math + commit gates (unwired)" --body-file /tmp/pr-feat-research-guards-parity.md
```

---

## Deferred to PR 2 (not in this plan)

- Change `run_param_sweep` to return a report carrying a `CommitGateVerdict` (parent: `ParamSweepReport{rows, gate, n_grid}` + `_row_to_trialperf` / `_compute_sweep_gate` adapters), print the verdict in the CLI sweep footer, and teach `/param-sweep-apply` to refuse `DO_NOT_COMMIT` cells.
- Wire `audit_guard.evaluate_audit_cells` into the gate/ADR audit tools (parent uses it in place of the ±0.05R bar).
- Any recalibrate-side gating that depends on the sweep verdict.

These move live behavior and touch the CLI/formatters — kept out of PR 1 so the math foundation lands byte-identical first. Lower urgency while TA sweeps are frozen.
