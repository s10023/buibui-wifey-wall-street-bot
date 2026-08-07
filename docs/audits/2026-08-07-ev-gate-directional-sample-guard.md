# The live EV gate's sample-size guard counted both directions and tested one

Date: 2026-08-07 · Branch: `fix/ev-gate-directional-sample-guard`
Scripts: `docs/plans/scripts/ev_gate_min_trades_diff.py`

## Summary

The queued task was "`min_trades` is a live decision, not a deferred one" — re-derive
whether the live EV gate's sample-size ladder is defensible. The re-derivation found the
queued framing anchored on a number that belongs to a different surface, and the sweep it
prescribed then showed that **`min_trades` is not the binding defect at all**.

The gate compared the **combined** closed-trade count against `min_trades` and then tested
a **directional** `avg_r`. The guard did not guard the quantity being decided, so a long
verdict could rest entirely on short trades. No value of `min_trades` fixes that.

Fixed by counting the population the statistic is computed from. The gate is now a
module-level, importable `passes_ev_gate` in `analytics/signal/gates.py`; it was previously
a closure inside `run_scan_cycle` and therefore unreachable from any test.

## Finding 1 — the queued framing cited the wrong ladder

The handoff said: *"#143's §10 SE table still applies: `bos` per-trade sd **1.652** ⇒ SE
**0.52R at n=10** … a cell at −0.4R on n=10 is ~1 SE from the threshold."*

There are **three** `min_trades` ladders, and `n=10` is not the live gate's threshold on any
timeframe:

| Surface | Reads | global | 4h | 1d | 1wk |
| --- | --- | --- | --- | --- | --- |
| **Live EV gate** (`signal_config.py`) | `[backtest]` block, base `:125–134` | 12 | **5** | **2** | **1** |
| Sweep / research (`backtest_config.py`) | top-level keys, base `:23,30–32` | 20 | 10 | 5 | 2 |
| Recalibrate / stars (`recalibrate_lib.py:211,252`) | Python defaults | combined 10 / directional 5 | — | — | — |

`config/strategy_params.toml:123` states the split outright: *"signal_config.py reads these
from `[backtest]`; backtest_config.py reads `min_trades_*tf`"*. Both live configs inherit
via `extends` and neither overrides. The arithmetic in the handoff is right
(1.652/√10 = 0.522); the *surface* is wrong. At the live thresholds the same sd gives SE
**0.739R** at n=5 and **1.168R** at n=2 — and `bos` is 1d-only on `signal_watch` since #143,
so the cell the handoff worried about is governed by `min_trades_1d = 2`.

## Finding 2 — raising `min_trades` weakens the gate

The gate **fails open** below the threshold (`return True`), so a larger sample-size guard
means *more* abstention and *fewer* blocks. Measured over the declared 365d window:

| | bypass @1 | @current | @10 | @20 |
| --- | --- | --- | --- | --- |
| `signal_watch` 4h (cur **5**) | 9% | 22% | 43% | 89% |
| `signal_watch` 1d (cur **2**) | 12% | 12% | 57% | 90% |
| `weekdays` 1wk (cur **1**) | 28% | 28% | 97% | 100% |

At `min_trades = 20` on `1wk` the gate is fully disabled — 234 bypass, **0 pass, 0 block**.
"Raise it for statistical safety" would have switched the gate off silently.

## Finding 3 — the guard counted the wrong population (fixed)

`scanner.py:752` tested `len(result.closed_trades)` — both directions — against
`min_trades`, then `:757` evaluated `result.long_avg_r` or `result.short_avg_r`.

| | blocked legs | `n_dir` < `min_trades` | `n_dir` = 1 | `n_dir` ≤ 4 |
| --- | --- | --- | --- | --- |
| `signal_watch` | 260 | **53 (20%)** | 19 (7%) | 108 (42%) |
| `weekdays` | 429 | **45 (10%)** | 68 (16%) | 191 (45%) |

Worked example: `doji 1d GOOGL short` — `n_cmb=3`, `n_dir=1`, `avg_r=−1.011`, sd undefined.
The gate hard-blocked that leg on one trade.

**Why the ladder cannot fix it**: raising `min_trades` to 10 still admits an `n_dir=1` block
whenever the opposite direction carries 9+ trades. The defect is orthogonal to the value.

### This destroys observations, not just alerts

A blocked leg is dropped from `passing_events` at `scanner.py:777`; the outcome writer
(`upsert_signal_outcome`) runs downstream at `:1048`. So a blocked leg never reaches
`signal_alert_outcomes`. Under the current roadmap — option (d), *"bank correctness, let the
ledgers mature"*, chosen because it is *"the only free option that gains information"* — the
gate was deleting ledger rows using non-evidence.

### Fix and measured impact

The guard now counts `long_closed_trades` / `short_closed_trades` — the same population
`long_avg_r` / `short_avg_r` average over. Ladder unchanged. `min_avg_r` unchanged.

| Transition | `signal_watch` | `weekdays` |
| --- | --- | --- |
| BLOCK → bypass (**now dispatches**) | **53** (9.3% of legs) | **45** (5.4%) |
| pass → bypass (no dispatch change) | 24 | 20 |
| BLOCK → BLOCK | 207 | 384 |
| pass → pass | 190 | 264 |
| newly blocked | **0** | **0** |

Strictly permissive: nothing becomes newly blocked. Many freed legs sit at `avg_r ≈ −1.0`
on `n_dir` 3–4 — they look bad, and they are still below the config's **own declared** bar
of 5 for `4h`. The fix does not invent a policy; it enforces the one already declared.

## Finding 4 — the tests re-implemented the gate, and one encoded the bug

`_passes_ev_gate` was a closure inside `run_scan_cycle`, so no test could call it. Every
test in `TestEvGate` therefore copied the comparison into the test body:

- `test_insufficient_trades_passes` asserted
  `len(result.closed_trades) < cfg.effective_min_trades("4h")` — **the combined count, i.e.
  the defect, written down as the expectation.**
- `test_none_result_always_passes` reduced to `assert None is None`.
- The rest asserted on `result.long_avg_r` directly and never invoked the gate.

All five passed before and after the fix, and would pass against any implementation.

Extracted to `analytics/signal/gates.py::passes_ev_gate` (keyword-only `direction`,
`timeframe`, `backtest_cfg`, so mypy forces each call site to state them — the
`adr_gate_applies` precedent from #142). Rewritten tests call the real function.
Falsification: reverting only the two count expressions makes exactly the three new
regression tests fail and leaves the other 23 green.

`BacktestSnapshot` (the cached path in `bt_results`) exposes `long_closed_trades` /
`short_closed_trades` as `[None] * n_long` shims, so the fix is correct on that path too —
covered by `test_snapshot_counts_directionally_too`. mypy caught the union at the call site;
it was not found by reading.

## Transferable rules

1. **A sample-size guard must count the population its statistic is computed from.** A
   guard on a different population is not a weak guard, it is not a guard — and it fails
   silently, because the count it reports is real, just about something else.
2. **A gate that fails open inverts the meaning of "stricter".** Before tuning a threshold
   on a fail-open gate, check which direction of the knob disables it. Here the safest-
   sounding change (raise `min_trades`) reaches 100% bypass.
3. **A test that re-implements the code under test can never falsify it.** The tell is a
   test body containing the operator being tested. This is the sibling of the
   config-parse trap in CLAUDE.md: *a test that asserts a value PARSED cannot detect that
   the parsed value produces nothing.* Make the unit importable first; that is a
   prerequisite for the fix, not scope creep.
4. **Suppression that happens before the recorder destroys evidence, not just output.**
   Check where a filter sits relative to the persistence call before judging its cost.
5. **Re-derive which surface a quoted number belongs to.** Three ladders named `min_trades`
   exist here; the handoff's figure was the sweep's, applied to the live gate's question.
