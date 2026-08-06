# The live EV gate ran a 90-day window and abstained on 71% of the surface

**Date:** 2026-08-06 · **Branch:** `fix/live-ev-gate-window` · **Class:**
declared-but-unexecuted surface ([[project_silent_surface_enforcement]])

## 1. What was queued, and what is actually true

Queued as *"the live EV gate ABSTAINS on ~half the live surface"*, with the fix described as
*"probably one argument (`days=backtest_cfg.days` at `signal_runner.py:299`)"*.

The **defect is real and the measured bypass rates replicate exactly**. Two things in the framing
are wrong:

| Queued claim | Actual |
| --- | --- |
| `[backtest] days = 365` lives in `signal_watch.toml` | It lives in `config/strategy_params.toml:128`, the shared base both live configs inherit via `extends` |
| "~half the live surface" | Half is the *latest cycle*. Over all 512 cached cells it is **62% (4h) / 72% (1d)**; measured per direction-leg it is **71%** of the whole `signal_watch` surface |
| "`backtest_cfg.days` is read *nowhere* on this path" | It **is** read — at `scanner.py:1232`, where it is written into `backtest_runs.days`. It is recorded but not executed, which is worse than unread: the DB row **misstates its own window** |
| "probably one argument" | **Two sites must move together.** `days` alone widens nothing — see §3 |

## 2. Mechanism (re-derived, not quoted)

1. `analytics/signal_runner.py:299` calls `run_scan_cycle(...)` **without** `days`.
2. `analytics/signal/scanner.py:297` declares `days: int = 90`.
3. `scanner.py:350-360`: `start_ms = now_ms - days*86400_000` — the `backtest_cfg.since` branch is
   dead because `since` is commented out (`strategy_params.toml:129`).
4. `scanner.py:741-748` — `_passes_ev_gate` returns **`True`** ("not enough trades — noise") when
   `len(closed_trades) < effective_min_trades(tf)`. A narrow window therefore does not fail loudly;
   it silently converts a hard gate into a no-op.

`mode = "hard"`, `min_avg_r = 0.0`, `min_trades_4h = 5`, `min_trades_1d = 2`, `min_trades_1wk = 1`.

## 3. Why `days` alone cannot fix it

`scanner.py:388-392` prefers the pre-populated cache over its own read:

```python
ohlcv_map[_key] = (
    ohlcv_cache[_key] if ohlcv_cache and _key in ohlcv_cache
    else get_ohlcv(conn, symbol, tf, start_ms, now_ms)
)
```

`signal_runner.py:295-297` populates that cache for **every** `(symbol, tf)` before the call, using
`cache_start_ms`, which falls back to `_DEFAULT_BACKFILL_DAYS = 90`. So the `else` branch is never
taken in production, and passing `days=365` would have widened the *declared* window and the
`_backtest_run_id` hash while the DataFrame handed to `_compute_backtest` stayed **90 days**.

That would have produced a `backtest_runs` row claiming 365, a cache key claiming 365, and a gate
still judging on 90 — the same defect with better camouflage. **Both sites move together or neither.**

## 4. Proof from the DB, before any recomputation

`backtest_runs` rows written by the live scanner (`sweep_id IS NULL`) contradict themselves:

| `days` declared | source | rows | actual span `(data_end_ms - data_start_ms)` |
| --- | --- | --- | --- |
| 365 | sweep path | 3,117 | **365.0 d** |
| 365 | **live scanner** | **94** | **90.0 d** (min = median = max) |

Every live-written row states a 365-day window over 90 days of data. This is checkable without
running anything, and it is the cheapest possible detector for this defect class.

## 5. Falsification — the pass/bypass/block table at 90d vs 365d

Method: replicate the **live** path (`bt_cache._compute_backtest` + the scanner's own resolvers in
`analytics/signal/resolvers.py`), varying only the OHLCV window. Every declared `(strategy, tf)`
cell × 13 live symbols × both direction-legs. Script:
`scratchpad/ev_gate_window_diff.py`.

**`signal_watch`** — 22 cells, 572 direction-legs:

| window | bypass | pass | BLOCK |
| --- | --- | --- | --- |
| 90d (what runs today) | **405 (71%)** | 60 | 107 |
| 365d (what is declared) | 98 (17%) | 216 | 258 |

**`signal_watch_weekdays`** — 32 cells, 832 legs:

| window | bypass | pass | BLOCK |
| --- | --- | --- | --- |
| 90d | **512 (62%)** | 97 | 223 |
| 365d | 119 (14%) | 284 | 429 |

Per timeframe, median `n_closed` and bypass rate:

| config | tf | `min_trades` | 90d bypass | 90d med n | 365d bypass | 365d med n |
| --- | --- | --- | --- | --- | --- | --- |
| `signal_watch` | 4h | 5 | **85%** | 2 | 22% | 11 |
| `signal_watch` | 1d | 2 | 57% | 1 | 12% | 8 |
| weekdays | 4h | 5 | 63% | 3 | 9% | 20 |
| weekdays | 1d | 2 | 44% | 2 | 9% | 14 |
| weekdays | 1wk | 1 | **82%** | 0 | 28% | 3 |

On both configs the median cell sits **below its own `min_trades` threshold** at 90 days. The gate's
median behaviour is abstention.

Transition matrix, `signal_watch` (90d verdict → 365d verdict):

| from → to | legs |
| --- | --- |
| bypass → **BLOCK** | **183** |
| bypass → pass | 124 |
| bypass → bypass | 98 |
| BLOCK → BLOCK | 60 |
| BLOCK → pass | 47 |
| pass → pass | 45 |
| pass → **BLOCK** | 15 |

**198 legs the gate currently lets through would be blocked** on the declared window; 47 currently
blocked would be released (a 90-day `avg_r < 0` on n≈2 is noise in the other direction). Weekdays:
281 newly blocked, 75 released.

*Caveat, stated honestly:* `n_closed` is **not** monotone in window length — the ADR and volume
filters use rolling statistics (14-day ADR, rolling volume mean), so a longer window changes which
signals survive filtering, not just how many exist. One weekdays leg goes BLOCK → bypass.

## 6. The five `bos × 1d` alerts that dispatched on 2026-08-05

`bos`'s first live dispatches ever, and the concrete reason this was queued first. What the gate
said, then and under the declared window:

| symbol | dir | 90d n | 90d avg_r | 90d verdict | 365d n | 365d avg_r | 365d verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ADBE | long | 0 | — | BYPASS | 7 | **−1.005** | **BLOCK** |
| NVDA | short | 1 | −1.006 | BYPASS | 7 | **−1.017** | **BLOCK** |
| AMD | short | 1 | — | BYPASS | 6 | **−1.008** | **BLOCK** |
| TSLA | short | 0 | — | BYPASS | 5 | **−1.018** | **BLOCK** |
| QQQ | short | 0 | — | BYPASS | 7 | +0.727 | pass |

**Four of five would have been blocked.** All four sit at avg_r ≈ **−1.0** — historically a full
stop-out on essentially every trade. That is precisely the case the hard gate exists to catch, and
it never evaluated them. The fifth (QQQ, +0.727) passes, so the widened gate discriminates rather
than blanket-suppressing.

The chain: #142 un-thinned `1d` → `bos × 1d` finally fires → the gate meant to catch *"a 1★ strategy
dispatching because nobody looked"* was inert on that cell.

## 7. What changed

`analytics/signal_runner.py` — one derived window, used by both surfaces:

```python
bt_days = backtest_cfg.days if backtest_cfg else _DEFAULT_BACKFILL_DAYS
backfill_start_ms = now_ms - bt_days * 24 * 3600 * 1000   # cache + new-symbol backfill
...
alerts = run_scan_cycle(..., days=bt_days, ...)
```

Tests (`tests/test_signal_runner.py::TestLiveBacktestWindowIsExecuted`) assert the **executed**
window on both surfaces plus the no-config fallback. All three fail against the pre-fix code —
verified by stashing the fix and re-running, not by inspection.

Asserting that the config *parsed* to 365 could never have caught this: it parsed correctly the
entire time. This is the `test_signal_watch_toml_volume_suppress_flags` failure mode from #141.

## 8. Out of scope, deliberately

- **`min_trades` is unchanged.** Median n rises to 8–20, which is better but still small; the SE
  table in the #143 audit §10 stands (sd 1.652 ⇒ SE 0.52R at n=10). Raising `min_trades` changes
  dispatch for every strategy and needs its own branch and falsification.
- **Every `backtest_cache` row is invalidated** — `_backtest_run_id` hashes `days`, so 90 → 365
  re-keys all 512 rows. Expected; they rebuild on the next cycle.
- **`backtest_runs.days` vs `adr_suppress_threshold`** — the *other* declared-not-executed column
  (#143 Task 3) is untouched here. The 94 self-contradicting rows in §4 stay in the DB as evidence;
  new rows will be consistent.
- No config value changed. The fix executes what the config already declared.

## 9. Transferable rules

1. **A declared config value that is also *recorded* is more dangerous than one that is ignored.**
   `backtest_runs.days` wrote 365 while the computation used 90, so the audit trail actively
   corroborated the wrong window. Cross-check a recorded parameter against a recorded *observable*
   (`data_end_ms - data_start_ms`) — that one join found this in a single query.
2. **When a value flows to a consumer through a cache, fixing the consumer's argument fixes
   nothing.** Find who populates the cache first. The precedence line
   (`cache[key] if key in cache else read(...)`) is where the real window is decided.
3. **A gate that fails open on insufficient data has no loud failure mode.** Its abstention rate is
   a first-class metric and should be measured, not assumed — 71% here.
4. **Verify a replica against observed live behaviour, never against a second replica** (the #143
   §10 correction). Two replicas agreeing means they share an assumption, not that either is right.
