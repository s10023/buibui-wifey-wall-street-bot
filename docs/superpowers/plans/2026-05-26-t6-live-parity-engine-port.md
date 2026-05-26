# T6 Live-Parity Engine Port — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire wifey's live signal gates (regime, direction_filter, F8 HTF-EMA, ADR bias, conflict resolver, cooldown) into `run_backtest` behind a **default-off** `LiveParityConfig` / `--live-parity` flag, porting parent's 6-PR T6 series — so backtests can replay the exact gate stack the live scanner applies. This is the principled fix for wifey's documented WFO filter-divergence debt (`project_wfo_filter_divergence.md`).

**Architecture:** This is a **port, not a green-field build**. Parent (`buibui-moon-trader-bot`) shipped this as 6 sequential PRs against the *same shared codebase* wifey forked from. Each task = one wifey PR = one parent PR, applied in dependency order. The parent commit is the **literal source of truth**; each task's job is to (a) read the parent diff, (b) apply it onto wifey's current file layout, (c) reconcile the handful of fork divergences listed in §Global Reconciliation. Every gate **reuses an existing wifey live function** in `analytics/signal/gates.py` (or `signals/cooldown_store.py` semantics) verbatim via a thin engine adapter — reconcile-and-rewire, never reimplement.

**Tech Stack:** Python 3.11+, Poetry, DuckDB, pandas, pytest + unittest.mock, mypy strict, ruff.

---

## Source of Truth — parent commits (on `main` of `/home/kng/repo/buibui-moon-trader-bot`)

<!-- markdownlint-disable MD060 -->

| Step | Parent PR | Parent main hash | Wifey branch | Reuses live fn |
|------|-----------|------------------|--------------|----------------|
| Task 0 | #387 foundation | `3f2769e` (docs squash incl. `2edf47e`) | `feat/t6-live-parity-foundation` | — (scaffold only) |
| Task 1 | #388 regime | `cc25833` | `feat/t6-regime-gate` | `_apply_regime_gate` |
| Task 2 | #389 direction_filter + F8 HTF-EMA | `d931ef6` | `feat/t6-direction-htf-gates` | `_apply_direction_filter_gate`, `_apply_htf_ema_gate` |
| Task 3 | #393 ADR bias + conflict-resolver *lift* | `ab47e91` | `feat/t6-adr-conflict-lift` | `_filter_signals_by_adr`, `_is_adr_exempt` |
| Task 4 | #394 conflict resolver (runner pooling) | `995f13e` | `feat/t6-conflict-resolver` | `_apply_conflict_resolver` (created in Task 3) |
| Task 5 | #395 cooldown | `77ebb25` | `feat/t6-cooldown` | cooldown semantics (new `_CooldownState`) |

<!-- markdownlint-enable MD060 -->

**To read any parent diff:** `git -C /home/kng/repo/buibui-moon-trader-bot show <hash>` (whole PR) or `... show <hash> -- <path>` (one file). These hashes are the squash-merges on parent `main`; the branch-local hashes cited in wifey MEMORY.md (`95a0571`, `f14af07`, `9dcea7d`, `965d237`) are **stale post-rebase — do not use them**.

---

## Global Reconciliation — fork divergences (apply in EVERY task)

These are the only systematic differences between parent's source and wifey. Apply them whenever they appear in a parent diff:

1. **Timeframes are equity, not crypto.** Wifey trades **4h / 1d / 1wk** only (no 15m, no 1h). Anywhere parent hard-codes `{"15m":4,"1h":3,"4h":2,"1d":1}` (the cooldown default map, Task 5) reconcile to **`{"4h": 2, "1d": 1, "1wk": 1}`**. Acceptance commands use `--timeframes 4h` / `1d` / `1wk`.
2. **Symbols are tickers.** Acceptance / manual-verification commands use `AAPL`, `MSTR`, `SPY`, `NVDA` — never `BTCUSDT` / `ETHUSDT` / `SOLUSDT`.
3. **Strategy registry differs.** Wifey has **no** `smt_divergence`, `cvd_divergence`, `fib_golden_zone`, `liquidity_sweep`. Use wifey strategies in acceptance + tests: `bos`, `engulfing`, `pin_bar`, `inside_bar`, `order_block`, `eqh_eql`, `hammer_hanging_man`, `doji`, `morning_evening_star`, `trend_day`, `orb`, `ema`, `fvg`, `marubozu_retest`, `wick_fill`, `ote_entry`.
4. **`volume_spike_boost` is STILL present in wifey** (parent's deprecation #397 is NOT ported). So parent's #387-era context lines that reference `volume_spike_boost_long=cfg.effective_volume_spike_boost_long(strategy)` etc. **match wifey verbatim** — apply cleanly, do not strip.
5. **`extends` is single-level.** Wifey's `signal_watch.toml` / `signal_watch_weekdays.toml` use `extends = "strategy_params.toml"`. When a task loads `[bias]` (Task 1) or per-strategy params (Task 3) via `load_signal_config(path)`, the same single-level caveat parent hit applies: an overlay extending `signal_watch.toml` does NOT transitively resolve `strategy_params.toml`. For acceptance, point `--config` at the leaf TOML with real values, or inline overrides.
6. **`BacktestSweepConfig` is missing three fields** that parent added incrementally — add them in the task that needs them:
   - `bias: BiasConfig | None = None` → **Task 1**
   - `live_strategy_params: dict[str, StrategyOverride] = field(default_factory=dict)` (for `adr_exempt` lookup) → **Task 3**
   - `config_name: str = ""` (set by `load_backtest_config` to `Path(path).stem`) → **Task 4**
7. **Commit identity + gh:** every commit must be `s10023 <ngkhaijian@gmail.com>` (`git config --local user.email`). Every `gh` call passes `--repo s10023/buibui-wifey-wall-street-bot`. Branch off `origin/main` each task: `git fetch origin main && git checkout -b <branch> origin/main`.
8. **`make db-update` is NOT needed for any task.** Default-off `LiveParityConfig()` ⇒ `run_backtest` output is byte-identical ⇒ regression goldens (`tests/fixtures/golden_*.json`) do not move. Each task's final test step MUST prove this (default-off byte-identical test). If a golden moves, the gate is leaking into the default path — fix before committing.

---

## File Structure

<!-- markdownlint-disable MD060 -->

| File | Responsibility | Created in |
|------|----------------|-----------|
| `analytics/backtest/live_parity_config.py` | `LiveParityConfig` frozen dataclass + `is_on()` | Task 0 |
| `analytics/backtest/engine.py` | `_df_to_events` / `_events_to_df` adapters; per-gate adapter fns; `run_backtest(*, live_parity=...)` wiring + apply-order | Tasks 0–5 |
| `analytics/backtest_config.py` | `BacktestSweepConfig.live_parity` + `.bias` + `.live_strategy_params` + `.config_name`; `[backtest.live_parity]` loader | Tasks 0,1,3,4 |
| `analytics/backtest_runner.py` | thread `live_parity` into 4 `run_backtest` call sites; regime/HTF-slope/ratings caches; conflict-resolver cross-strategy pooling | Tasks 0,1,2,4 |
| `analytics/signal/gates.py` | (unchanged behaviour) `_apply_conflict_resolver` lifted out of scanner | Task 3 |
| `analytics/signal/scanner.py` | refactor inline conflict-resolution to call `_apply_conflict_resolver` | Task 3 |
| `cli/backtest.py` | `--live-parity` + `--with-/--without-<gate>` flags; `_resolve_live_parity` | Task 0 |
| `tests/test_live_parity_config.py` | foundation: dataclass / loader / CLI / adapters | Task 0 |
| `tests/test_live_parity_regime_gate.py` | regime replay-parity | Task 1 |
| `tests/test_live_parity_direction_filter_gate.py` / `_htf_ema_gate.py` | PR-3 gates | Task 2 |
| `tests/test_live_parity_adr_gate.py` / `tests/test_signal_gates_conflict_resolver.py` | PR-4 ADR + resolver lift | Task 3 |
| `tests/test_live_parity_conflict_resolver.py` | PR-4b runner pooling | Task 4 |
| `tests/test_live_parity_cooldown.py` | PR-5 cooldown | Task 5 |

<!-- markdownlint-enable MD060 -->

---

## Task 0: Foundation — LiveParityConfig + CLI/TOML wiring + DF↔event adapters

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show 3f2769e` (PR #387). Zero gate logic — pure scaffold. Default `LiveParityConfig()` is a no-op.

**Files:**

- Create: `analytics/backtest/live_parity_config.py`
- Modify: `analytics/backtest/engine.py` (imports + adapters + `run_backtest` signature/log)
- Modify: `analytics/backtest_config.py` (`BacktestSweepConfig.live_parity` field + loader)
- Modify: `analytics/backtest_runner.py` (4 call sites + import)
- Modify: `cli/backtest.py` (flags + resolver)
- Test: `tests/test_live_parity_config.py`

- [ ] **Step 1: Branch off origin/main**

```bash
git -C /home/kng/repo/buibui-wifey-wall-street-bot fetch origin main
git checkout -b feat/t6-live-parity-foundation origin/main
git config --local user.email   # MUST print ngkhaijian@gmail.com
```

- [ ] **Step 2: Verify `SignalEvent` has the fields the adapter needs**

The `_df_to_events` adapter constructs `SignalEvent(...)` with: `symbol, timeframe, strategy, direction, reason, open_time, price, sl_price, context, low_volume, volume_spike, tp_price`. Confirm wifey's dataclass carries all of these:

Run: `grep -nE "symbol|timeframe|strategy|direction|reason|open_time|price|sl_price|context|low_volume|volume_spike|tp_price" analytics/signal/types.py`
Expected: every field present (wifey inherited `signal/types.py` from parent Phase 2). If any field name differs, adjust the adapter's `record.get(...)` keys to match — this is the only place field drift would bite.

- [ ] **Step 3: Create `analytics/backtest/live_parity_config.py`**

```python
"""T6 live-parity gate toggles for run_backtest().

Each flag defaults to False so passing a default-constructed `LiveParityConfig()`
(or `None`) keeps the engine's current behaviour. Set `enabled=True` to flip
every individual flag on at once; per-gate flags remain effective on top of the
master switch so callers can compose `--live-parity --without-cooldown`.

PR-1 lands the dataclass + plumbing only. Per-gate logic ports ship in PRs 2-5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveParityConfig:
    """Toggle live-only gates inside run_backtest().

    `enabled` is the master switch. `is_on(gate)` returns True when the master
    switch OR the specific gate field is set. Cooldown bars per timeframe are
    optional and fall back to the engine's baked-in defaults when None.
    """

    enabled: bool = False
    regime: bool = False
    direction_filter: bool = False
    f8_htf_ema: bool = False
    adr_bias: bool = False
    conflict_resolver: bool = False
    cooldown: bool = False
    cooldown_bars_per_tf: dict[str, int] | None = None

    def is_on(self, gate: str) -> bool:
        """Return True iff the named gate field is set.

        Note: `enabled` is a *resolver-time* convenience — the CLI/TOML resolver
        expands it into per-gate True values *before* the engine sees the
        config, so an explicit `--without-<gate>` can still cleanly disable
        one gate while the master switch stays on (the acceptance contract).
        """
        return bool(getattr(self, gate))
```

- [ ] **Step 4: Write the failing tests** (`tests/test_live_parity_config.py`)

Port the parent test file as-is, then reconcile symbols/TFs per §Global Reconciliation. Pull it: `git -C /home/kng/repo/buibui-moon-trader-bot show 3f2769e:tests/test_live_parity_config.py`. It has 6 test classes: dataclass defaults / `is_on` / master / `replace` / `cooldown_bars`; TOML loader (missing block / parse / extends / invalid types); CLI flags (no-flags / master / with-single / without-negates-master / with-then-without / inherits-base / cli-wins); engine adapters (roundtrip / empty / filtered subset); `run_backtest` no-op when default; `BacktestSweepConfig.live_parity` default. Reconcile any `BTCUSDT`→`AAPL`, `15m`/`1h`→`4h`/`1d`, crypto strategy → wifey strategy. Keep the adapter roundtrip test's columns aligned with wifey's signals frame.

- [ ] **Step 5: Run tests to verify they fail**

Run: `poetry run pytest tests/test_live_parity_config.py -q`
Expected: FAIL — `ImportError` / `AttributeError` (`live_parity` not on `BacktestSweepConfig`, CLI flags absent, adapters undefined).

- [ ] **Step 6: Apply the engine.py changes**

Apply the `analytics/backtest/engine.py` hunk from `3f2769e` verbatim. Concretely:

- Add `from __future__ import annotations` as the first import line.
- Add `import logging`, add `TYPE_CHECKING` to the `typing` import, add `from analytics.backtest.live_parity_config import LiveParityConfig`, add `if TYPE_CHECKING: from analytics.signal.types import SignalEvent`, add `logger = logging.getLogger(__name__)`.
- Un-quote the `_compute_atr14` numpy annotations (now legal under `from __future__`).
- Add module constant `_LIVE_PARITY_GATE_ORDER = ("regime","direction_filter","f8_htf_ema","adr_bias","conflict_resolver","cooldown")`.
- Add `_df_to_events(signals, symbol, timeframe, strategy) -> list[SignalEvent]` and `_events_to_df(events, original_df) -> pd.DataFrame` exactly as in the diff (deferred local `from analytics.signal.types import SignalEvent` inside `_df_to_events` to avoid the import cycle).
- In `run_backtest`, insert `*, live_parity: LiveParityConfig | None = None` after `tp_r_short: float | None = None`, extend the docstring, and add the gate-mask log block at the top of the body (logs `live_parity: regime=on ...` when any gate is on).

The full hunk is in the diff already captured — match it line-for-line. wifey's `run_backtest` signature ends with `tp_r_long` / `tp_r_short`, identical to parent, so the insertion point matches exactly.

- [ ] **Step 7: Apply the backtest_config.py changes**

Apply the `analytics/backtest_config.py` hunk from `3f2769e`: add `from analytics.backtest.live_parity_config import LiveParityConfig`; add `live_parity: LiveParityConfig = field(default_factory=LiveParityConfig)` to `BacktestSweepConfig`; add the `[backtest.live_parity]` parse block in `load_backtest_config` (validates dict, parses `cooldown_bars` sub-table, builds `LiveParityConfig(...)`) and pass `live_parity=live_parity_cfg` in the return. Match the diff verbatim.

- [ ] **Step 8: Apply the backtest_runner.py changes**

Apply the `analytics/backtest_runner.py` hunk from `3f2769e`: add `from __future__ import annotations`; add `if TYPE_CHECKING: from analytics.backtest.live_parity_config import LiveParityConfig`; add `live_parity=cfg.live_parity` after the `volume_spike_boost_short=...` kwarg at the **three sweep call sites** (`_collect_sweep_results` ~line 210, the tp_r sweep ~line 326, the ATR sweep ~line 369) and `live_parity=live_parity` in `run_backtest_cmd`'s `run_backtest(...)` call (~line 468); add `live_parity: LiveParityConfig | None = None` param to `run_backtest_cmd`'s signature (~line 419). Wifey's call sites have the same `volume_spike_boost_short` anchor, so the hunks apply cleanly.

- [ ] **Step 9: Apply the cli/backtest.py changes**

Apply the `cli/backtest.py` hunk from `3f2769e`: add `from dataclasses import replace`, `from typing import Any`, `from analytics.backtest.live_parity_config import LiveParityConfig`; add `_LIVE_PARITY_GATES` tuple + `_resolve_live_parity(args, base)`; in the sweep branch add `cfg.live_parity = _resolve_live_parity(args, cfg.live_parity)` before `run_backtest_sweep(cfg)`; in the single-combo branch add `live_parity=_resolve_live_parity(args, LiveParityConfig())` to the `run_backtest_cmd(...)` call; in `add_backtest_subparser` add the `--live-parity` master flag + the `--with-<gate>`/`--without-<gate>` loop before `set_defaults`. Match the diff verbatim (wifey's `run_backtest_sweep(cfg)` at line 95, `run_backtest_cmd` at 103, `set_defaults` at 300 align with parent).

- [ ] **Step 10: Run tests to verify they pass**

Run: `poetry run pytest tests/test_live_parity_config.py -q`
Expected: PASS (all foundation tests).

- [ ] **Step 11: Full quality gates**

Run: `make lint-py && make typecheck && make test`
Expected: ruff clean, mypy strict clean, full suite green. Pay attention to `test_regression` — it MUST stay green with goldens unmodified (default-off no-op proof). If a circular import surfaces (`engine` → `signal.types` → `signal/__init__` → `backtest_lib`), confirm the `TYPE_CHECKING` + deferred-local-import treatment from Step 6 is in place.

- [ ] **Step 12: Acceptance check (equity)**

Run: `PYTHONPATH=. poetry run python wifey.py backtest --symbols AAPL --timeframes 4h --strategies bos --live-parity --without-cooldown --since 2024-01-01`
Expected: log line `live_parity: regime=on direction_filter=on f8_htf_ema=on adr_bias=on conflict_resolver=on cooldown=off`. (Gates are still no-ops at this PR — the log is the only observable.)

- [ ] **Step 13: Commit**

```bash
git add analytics/backtest/live_parity_config.py analytics/backtest/engine.py \
        analytics/backtest_config.py analytics/backtest_runner.py cli/backtest.py \
        tests/test_live_parity_config.py
git commit -m "feat(backtest): T6 live-parity foundation — LiveParityConfig + CLI/TOML wiring

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 14: Docs + PR**

Update `CLAUDE.md` `backtest/` package listing (add `live_parity_config.py` + the `run_backtest` keyword-only param + adapters), `.claude/context/analytics.md` (`run_backtest` signature), and `README.md` (a "Live-parity options (T6)" subsection covering the 13 flags + the `[backtest.live_parity]` TOML block). Run `make lint-md`. Then `gh pr create --repo s10023/buibui-wifey-wall-street-bot --base main`. Run `/post-branch` before reporting the URL.

---

## Task 1: Port the regime gate (PR-2)

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show cc25833` (PR #388). Read it in full first — this task establishes the **replay-parity pattern** every later gate reuses.

**Design call (already decided by parent, keep it):** each historical signal is evaluated against the regime active at *that signal's* `open_time` (per-signal lookup), NOT a single "now" snapshot — otherwise the backtest measures "what would today's regime do to old signals", defeating the audit purpose.

**Files:**

- Modify: `analytics/backtest/engine.py` — add `_resolve_regime_at(signal_time_ms, regime_series)` (mirrors live `iloc[-2]` via `np.searchsorted(side="right")-1` then `-1`; returns `None` for warmup) and `_apply_regime_gate_to_signals(signals, symbol, tf, strategy, bias_cfg, regime_series)` (groups events by resolved regime, calls live `_apply_regime_gate` once per regime group with a synthetic `regime_cache={symbol: regime}`). Add `bias_cfg: BiasConfig | None = None` + `regime_series: pd.Series | None = None` to `run_backtest`; run the gate after the PR-1 log line, guarded on `live_parity.is_on("regime") and bias_cfg and bias_cfg.regime_enabled`.
- Modify: `analytics/backtest_config.py` — add `bias: BiasConfig | None = None` to `BacktestSweepConfig`; `load_backtest_config` sets it from `load_signal_config(path).bias` (lazy import inside the function, same pattern as `_day_filter_to_weekdays`).
- Modify: `analytics/backtest_runner.py` — add `_build_regime_series_by_symbol(conn, cfg, symbols, start_ms, end_ms)` (pre-classifies `bias.regime_htf_tf`, default `4h`, per symbol once per sweep via `analytics.regime`); thread `bias_cfg` + `regime_series` into all sweep call sites + `run_backtest_cmd`.
- Test: `tests/test_live_parity_regime_gate.py`

**Reuses (verbatim, no reimplementation):** `analytics/signal/gates.py::_apply_regime_gate` (gates.py:226) + `analytics/regime.py` classifier.

**Reconciliation:** acceptance uses an equity strategy with a per-strategy regime override. Parent used `bos = ["high_vol","range"]` forcing hard mode — keep `bos`, it exists in wifey. Use `--symbols AAPL --timeframes 4h`. The `extends` single-level caveat (§5) applies to any overlay TOML.

- [ ] **Step 1:** Branch: `git fetch origin main && git checkout -b feat/t6-regime-gate origin/main`. Read `git -C /home/kng/repo/buibui-moon-trader-bot show cc25833` end-to-end.
- [ ] **Step 2:** Port `tests/test_live_parity_regime_gate.py` from `cc25833` (3 classes: `TestResolveRegimeAt`, `TestApplyRegimeGateToSignals`, `TestRunBacktestRegimeGate`). Reconcile symbols/TFs/strategies. Use `regime_per_strategy`-style overrides, not type strings, to stay registry-independent.
- [ ] **Step 3:** Run them → FAIL (helpers undefined).
- [ ] **Step 4:** Apply the `backtest_config.py` `bias` field + loader change from the diff.
- [ ] **Step 5:** Apply the `engine.py` helpers + `run_backtest` wiring from the diff.
- [ ] **Step 6:** Apply the `backtest_runner.py` `_build_regime_series_by_symbol` + threading from the diff.
- [ ] **Step 7:** Run `tests/test_live_parity_regime_gate.py` → PASS.
- [ ] **Step 8:** `make lint-py && make typecheck && make test`. Regression goldens MUST be byte-identical (default-off proof). Confirm the suite's new test count = baseline + ~16.
- [ ] **Step 9:** Acceptance: run `bos` on `AAPL 4h` with a hard-mode regime override, baseline vs `--with-regime`; confirm trades drop where signals land in excluded regimes. Record the trade-count delta in the PR body.
- [ ] **Step 10:** Commit (`feat(backtest): T6 PR-2 — port live regime gate into run_backtest`), docs walk (CLAUDE.md engine listing + `.claude/context/analytics.md` signature + README live-parity section: mark `--with-regime` wired), `gh pr create --repo ...`, `/post-branch`.

---

## Task 2: Port direction_filter + F8 HTF-EMA gates (PR-3)

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show d931ef6` (PR #389).

**Files:**

- Modify: `analytics/backtest/engine.py` — add a direction_filter adapter (pure per-event flag check, no HTF time-series needed) and an HTF-EMA adapter (same replay-parity shape as regime, but with an EMA-slope cache keyed by `open_time` instead of a regime series). Wire both into `run_backtest` apply-order **after** regime, guarded on their respective `is_on(...)` + `bias_cfg` flags (`direction_filter_enabled`, `htf_ema_enabled`).
- Modify: `analytics/backtest_config.py` — surface any direction_filter / htf_ema config already on `bias` (added Task 1); confirm `BiasConfig.htf_ema_*` + `direction_filter_*` fields are reachable (they are — see `signal_config.py:249-261`).
- Modify: `analytics/backtest_runner.py` — add `_build_htf_slope_by_symbol(...)` analogous to the regime-series builder; thread it through sweep call sites.
- Test: `tests/test_live_parity_direction_filter_gate.py` + `tests/test_live_parity_htf_ema_gate.py`

**Reuses verbatim:** `_apply_direction_filter_gate` (gates.py:163), `_apply_htf_ema_gate` (gates.py:97).

**Reconciliation:** the HTF-EMA anchor TF in wifey defaults to `4h` (`htf_ema_default_tf`, signal_config.py:251). Acceptance uses an equity strategy with `htf_ema_per_strategy` configured.

- [ ] **Step 1:** Branch `feat/t6-direction-htf-gates` off origin/main; read `d931ef6`.
- [ ] **Step 2:** Port both test files from `d931ef6`, reconciled. → run → FAIL.
- [ ] **Step 3:** Apply engine adapters + `run_backtest` wiring (direction_filter then htf_ema, after regime).
- [ ] **Step 4:** Apply `_build_htf_slope_by_symbol` + runner threading.
- [ ] **Step 5:** Both test files PASS.
- [ ] **Step 6:** `make lint-py && make typecheck && make test`; goldens byte-identical.
- [ ] **Step 7:** Acceptance (equity) baseline vs `--with-direction-filter` and vs `--with-f8-htf-ema`; record deltas.
- [ ] **Step 8:** Commit, docs walk (mark both flags wired), PR, `/post-branch`.

---

## Task 3: Port ADR bias gate + lift the conflict resolver (PR-4)

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show ab47e91` (PR #393). Note the parent **split**: this PR ships ADR alone + *lifts* the conflict-resolver helper out of the scanner (no engine wiring yet); the resolver's *application* is Task 4.

**Files:**

- Modify: `analytics/backtest/engine.py` — add `_apply_adr_bias_gate_to_signals(signals, ohlcv, symbol, tf, strategy, bias_cfg, strategy_params)`. Engine runs per-strategy, so exemption only varies by **direction**: compute `exempt_long = _is_adr_exempt(strategy_params, strategy, "long")` / `exempt_short` similarly; both exempt → no-op; neither → `_filter_signals_by_adr` on the whole frame; else split by direction, filter only the non-exempt slice, concat, `sort_values("open_time").reset_index(drop=True)`. Wire after HTF-EMA, guarded on `is_on("adr_bias") and bias_cfg and bias_cfg.adr_suppress_threshold is not None`.
- Modify: `analytics/backtest_config.py` — add `live_strategy_params: dict[str, StrategyOverride]` (loaded from `load_signal_config(path).strategy_params`) so the engine can resolve per-strategy `adr_exempt`. Add `BacktestSweepConfig.is_adr_exempt(strategy)` helper if not present.
- Modify: `analytics/backtest_runner.py` — the legacy strategy-wide ADR pre-filter gains a fourth guard `and not cfg.live_parity.is_on("adr_bias")` so the engine takes over (no double-filter) when the flag is on.
- Modify: `analytics/signal/gates.py` — **lift** `_apply_conflict_resolver(events, symbol, tf, *, confidence_resolver: Callable[[SignalEvent], float] | None = None)` out of `scanner.py` (the cross-strategy direction-conflict resolution block). Default resolver reads `event.confidence`. (Use `from collections.abc import Callable` — repo ruff py313/UP035 rejects `typing.Callable`.)
- Modify: `analytics/signal/scanner.py` — replace the ~45 inline conflict-resolution lines with a single `_apply_conflict_resolver(events, symbol, tf)` call. Behaviour preserved (tests verify parity).
- Test: `tests/test_live_parity_adr_gate.py` (ADR adapter + run_backtest integration) + `tests/test_signal_gates_conflict_resolver.py` (resolver-lift parity).

**Reuses verbatim:** `_filter_signals_by_adr` (gates.py:16), `_is_adr_exempt` (gates.py:87).

**Reconciliation:** wifey's `bos` has `adr_exempt = true` in production → acceptance with `bos` should be byte-identical between baseline and `--with-adr-bias` (engine path ≡ legacy pre-filter for exempt strategy). Use a non-exempt strategy (e.g. `engulfing`) to see drops. Parent's `_chasing_ohlcv()` test fixture (single 24h day, 100→110 move) ports directly — equity OHLCV has the same shape.

- [ ] **Step 1:** Branch `feat/t6-adr-conflict-lift`; read `ab47e91`.
- [ ] **Step 2:** Port both test files, reconciled. → FAIL.
- [ ] **Step 3:** Lift `_apply_conflict_resolver` into `gates.py`; refactor `scanner.py` to call it. Run `tests/test_signal_gates_conflict_resolver.py` → PASS (parity preserved). Run existing scanner tests → still green.
- [ ] **Step 4:** Add `live_strategy_params` + `is_adr_exempt` to `backtest_config.py`.
- [ ] **Step 5:** Add `_apply_adr_bias_gate_to_signals` + `run_backtest` wiring; add the runner-skip guard.
- [ ] **Step 6:** `tests/test_live_parity_adr_gate.py` → PASS.
- [ ] **Step 7:** `make lint-py && make typecheck && make test`; goldens byte-identical.
- [ ] **Step 8:** Acceptance: `bos` (exempt) baseline ≡ `--with-adr-bias` byte-identical; `engulfing` baseline vs `--with-adr-bias` shows chasing-direction drops. Optionally a temp overlay with `adr_exempt_long = true` to prove per-direction exemption the legacy runner can't express. Delete temp overlay.
- [ ] **Step 9:** Commit, docs walk (`--with-adr-bias` wired; `--with-conflict-resolver` accepted-but-no-op-until-PR-4b), PR, `/post-branch`.

---

## Task 4: Port the conflict resolver via runner cross-strategy pooling (PR-4b)

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show 995f13e` (PR #394). The resolver operates on the **cross-strategy** event pool for a `(symbol, tf)`, but `run_backtest` is per-strategy — so application happens at the **runner**, not the engine.

**Files:**

- Modify: `analytics/backtest_runner.py` — restructure `_collect_sweep_results` into three phases: (1) **detect** → populate `signals_map: dict[(sym,tf,strat), (ohlcv, signals, sec)]` (day-filter + legacy ADR pre-filter applied); (2) **resolve conflicts** via cross-strategy pooling; (3) **backtest + save**. Default-off must stay byte-identical: the dict preserves `itertools.product` insertion order so phase-3 iteration matches the legacy single loop. Add `_build_confidence_ratings_map(conn, cfg)` → `{(strategy, tf, direction): avg_r}` or `None` when gate off, keyed on `cfg.config_name`; loads directional + `'combined'` rows in one query. Add `_resolve_conflicts_for_signals_map(signals_map, ratings_map)` (groups by `(symbol,tf)`, pools events by `open_time` via `_df_to_events`, calls `_apply_conflict_resolver(..., confidence_resolver=lambda e: ratings.get((e.strategy,e.timeframe,e.direction),0.0) or ratings.get((e.strategy,e.timeframe,"combined"),0.0))`, redistributes survivors via `_events_to_df`, mutates the map in place; single-strategy cells short-circuit). Build regime/HTF-slope/ratings caches once in `run_backtest_sweep` outer scope and thread via kw-only params.
- Modify: `analytics/backtest_config.py` — add `config_name: str = ""`; `load_backtest_config` sets it to `Path(path).stem`.
- Modify: `analytics/signal/gates.py` — widen `_apply_conflict_resolver`'s `confidence_resolver` return type from `int` to `float` (continuous avg_r); change log format `%d`→`%g`. (Int callers stay compatible.)
- Test: `tests/test_live_parity_conflict_resolver.py`

**Reuses:** `_apply_conflict_resolver` (created Task 3), `_df_to_events` / `_events_to_df` (Task 0).

**Reconciliation:** ratings come from `confidence_ratings` keyed by `config_name` = TOML stem (`signal_watch`, `signal_watch_weekdays`). DuckDB `REAL` is float32 → tests comparing avg_r need `pytest.approx`. Acceptance uses ≥2 strategies that fire opposing directions on the same equity candle (e.g. `bos` + `engulfing` + `pin_bar` on `AAPL 4h`).

- [ ] **Step 1:** Branch `feat/t6-conflict-resolver`; read `995f13e`.
- [ ] **Step 2:** Port `tests/test_live_parity_conflict_resolver.py` (builder + resolver groups), reconciled, `pytest.approx` for avg_r. → FAIL.
- [ ] **Step 3:** Add `config_name` to `backtest_config.py`.
- [ ] **Step 4:** Widen `_apply_conflict_resolver` resolver type in `gates.py`; existing resolver-lift tests stay green.
- [ ] **Step 5:** Restructure `_collect_sweep_results` into 3 phases + add the two helpers + outer-scope cache build. **Verify default-off byte-identical first** (the riskiest change in the series — phase-split must preserve order).
- [ ] **Step 6:** `tests/test_live_parity_conflict_resolver.py` → PASS.
- [ ] **Step 7:** `make lint-py && make typecheck && make test`; goldens byte-identical (critical here — the runner restructure is the most likely place to leak).
- [ ] **Step 8:** Acceptance: baseline vs `--with-conflict-resolver` on the 3-strategy equity set; confirm lower-rated strategies drop on opposing same-candle conflicts; look for the `Conflict: ... SHORT wins ...` + `loaded N rating(s) for config '<stem>'` log lines.
- [ ] **Step 9:** Commit, docs walk (`--with-conflict-resolver` wired; note `make db-update` may shift ratings-dependent output but default path unchanged), PR, `/post-branch`.

---

## Task 5: Port the cooldown gate (PR-5) — closes the series

**Parent source:** `git -C /home/kng/repo/buibui-moon-trader-bot show 77ebb25` (PR #395).

**Files:**

- Modify: `analytics/backtest/engine.py` — add `_CooldownState` dataclass (mutable `last_fire_by_key: dict[tuple[str,str,str,str], int]` keyed by `(symbol,tf,strategy,direction)`); add module constant `_DEFAULT_COOLDOWN_BARS_PER_TF` **reconciled to equity TFs → `{"4h": 2, "1d": 1, "1wk": 1}`**; add `_resolve_cooldown_bars(timeframe, live_parity)` (override map → baked default → `1`); add `_apply_cooldown_gate_to_signals(signals, symbol, tf, strategy, live_parity, state)` (sort by `open_time`, walk rows, drop if `open_time < last_fire + cooldown_bars × tf_ms` else stamp state; uses `parse_timeframe_secs` from `analytics/signal/_common.py`; `cooldown_bars <= 0` → no-op). In `run_backtest`, instantiate a fresh `_CooldownState()` per call (state scoped per call → two identical back-to-back calls byte-equal), run the adapter **last**, after ADR, before `BacktestResult` construction.
- Test: `tests/test_live_parity_cooldown.py`

**Reuses:** cooldown *semantics* mirror `signals/cooldown_store.py` (the live `CooldownStore.is_new_candle` watermark), but the engine uses its own per-call `_CooldownState` (no shared mutable state across `run_backtest` calls). `parse_timeframe_secs` already exists in `analytics/signal/_common.py`.

**Reconciliation — the cooldown TF map is the single most important fork delta in this task.** Parent's `{"15m":4,"1h":3,"4h":2,"1d":1}` has no 1wk and includes intraday TFs wifey doesn't trade. Use `{"4h": 2, "1d": 1, "1wk": 1}`. The `_resolve_cooldown_bars` tests must assert against these equity values, not parent's.

- [ ] **Step 1:** Branch `feat/t6-cooldown`; read `77ebb25`.
- [ ] **Step 2:** Port `tests/test_live_parity_cooldown.py` (`_resolve_cooldown_bars` / `_apply_cooldown_gate_to_signals` / `run_backtest` integration). **Rewrite the `_resolve_cooldown_bars` assertions for the equity map** (`4h`→2, `1d`→1, `1wk`→1, unknown→1). → run → FAIL.
- [ ] **Step 3:** Add `_CooldownState`, `_DEFAULT_COOLDOWN_BARS_PER_TF` (equity), `_resolve_cooldown_bars`, `_apply_cooldown_gate_to_signals`; wire into `run_backtest` (fresh state per call, applied last).
- [ ] **Step 4:** `tests/test_live_parity_cooldown.py` → PASS.
- [ ] **Step 5:** `make lint-py && make typecheck && make test`; goldens byte-identical (test both `live_parity=None` AND `live_parity.cooldown=False`).
- [ ] **Step 6:** Acceptance: `engulfing`+`pin_bar` on `AAPL`/`MSTR` `4h` baseline vs `--with-cooldown`; denser-firing strategy drops more (within the 2-bar 4h window). Confirm `live_parity: ... cooldown=on` log.
- [ ] **Step 7:** Commit (`feat(backtest): T6 PR-5 — port live cooldown via per-call _CooldownState ledger`), docs walk (`--with-cooldown` wired; **note in CLAUDE.md that the cooldown default map is equity 4h/1d/1wk, not parent's intraday map**), PR, `/post-branch`.

- [ ] **Step 8: Series wrap-up.** With all 5 gates wired, update MEMORY.md Current State: T6 live-parity series complete; the WFO filter-divergence debt (`project_wfo_filter_divergence.md`) now has its principled fix — `run_backtest --live-parity` replays the live gate stack. Note follow-on: re-run the WFO sweeps under `--live-parity` to get filter-accurate `tp_r` (supersedes the raw-signal-count caveat). Cross-link `[[wfo-filter-divergence]]`.

---

## Self-Review

**Spec coverage** — all 6 parent PRs mapped to a task: #387→T0, #388→T1, #389→T2, #393→T3, #394→T4, #395→T5. ✓
**Dependency order** — foundation first; regime establishes the replay-parity pattern; direction/HTF reuse it; ADR adds `live_strategy_params` + lifts the resolver helper; PR-4b adds `config_name` + applies the resolver via runner pooling; cooldown last (per-call state). ✓
**Reconciliation completeness** — equity TFs (cooldown map), tickers, registry diff, `volume_spike_boost` still-present, single-level `extends`, three missing `BacktestSweepConfig` fields (added in T1/T3/T4), commit identity + gh repo flag — all stated once in §Global Reconciliation and referenced per task. ✓
**Default-off invariant** — every task proves regression goldens byte-identical; `make db-update` explicitly NOT needed. The riskiest leak point (T4 runner phase-split) is flagged with a "verify byte-identical first" step. ✓
**Stale-hash guard** — plan cites parent `main` squash hashes (`3f2769e`/`cc25833`/`d931ef6`/`ab47e91`/`995f13e`/`77ebb25`) and explicitly warns off the stale branch hashes in MEMORY.md. ✓
**Type/name consistency** — `_df_to_events`/`_events_to_df` defined T0 and reused T4; `_apply_conflict_resolver` created T3 (int resolver) and widened to float T4; live fns referenced by their real wifey line numbers. ✓

**Port-plan note (intentional, not a placeholder):** Tasks 1–5 instruct the engineer to apply each parent commit as the literal code source (`git -C ... show <hash>`) plus the explicit per-task reconciliation deltas, rather than re-transcribing ~3k lines of parent diff inline. This is the correct granularity for a faithful port of a shared-ancestor codebase — the value is the reconciliation map, and the source is version-controlled and exact. Task 0 (the scaffold that needs the most wifey-specific wiring) is fully coded inline.
