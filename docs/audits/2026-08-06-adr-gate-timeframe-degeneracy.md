# The ADR gate is undefined on every timeframe this bot scans

**Date:** 2026-08-06
**Branch:** `fix/adr-gate-degenerate-on-daily-timeframes`
**Predecessor:** [`2026-08-06-adr-volume-gate-conjunction.md`](2026-08-06-adr-volume-gate-conjunction.md) (PR #141)

## 1. What was queued, and what was actually true

The handoff queued this as *"the ADR gate is degenerate on `1d`"*, with the mechanism stated as
`consumed_ratio ≈ 1.0 by construction` and the scope stated as the daily surface.

Its **numbers** replicate exactly. Its **mechanism** and its **scope** are both wrong, and the real
finding is larger on both axes. That makes six-for-six on re-deriving a handoff's stated mechanism
before acting on it.

| handoff claim | measured | verdict |
| --- | --- | --- |
| `1d` median consumed 0.92 | **0.925** | replicates |
| gate drops 63% of daily bars | **64.6%** | replicates |
| `trend_day × 1d` 81.7% of signals | **81.7%** | replicates |
| `engulfing` 81.7% / `ema` 78.3% / `doji` 76.8% | identical | replicates |
| mechanism = "ratio ≈ 1.0 by construction" | ratio is *dispersed*: p25 0.71, p75 1.21 | **wrong** |
| scope = `1d` | **4h, 1d and 1wk** — every timeframe either config scans | **wrong** |

## 2. The mechanism

`_filter_signals_by_adr` (`analytics/signal/gates.py`) computes, per signal candle:

```text
consumed_ratio = (cumulative intraday range up to that candle) / (14-day ADR)
```

and drops the signal when `consumed_ratio >= threshold` **and** the signal is "chasing" — a long
when the day closed in the upper half of its range, a short when the lower.

Both halves need **more than one bar per calendar day** to mean anything. Neither has it.

### 2a. The ratio stops measuring exhaustion

The ratio is *not* pinned at 1.0. It is today's range divided by the mean of the trailing 14 days'
ranges — a quantity naturally centred near 1 but widely dispersed (`1d`: p25 0.71, p75 1.21). So the
gate does not become a constant, which would be obvious. It **silently changes meaning**: from an
*exhaustion* filter ("the move is already done") into a *high-range-day* filter ("today was wider
than usual"). It still looks like a working gate, and that is why it survived.

### 2b. The direction guard degenerates in the same step — and this is the sharper half

`move_up` is `close > midpoint of the cumulative range`. On a one-bar day that reduces to *"the bar
closed in the upper half of its own range"* — which is **the same quantity close-derived detectors
read their direction from**. `detect_trend_day` emits a long iff `c > o` with a large body and a
small lower wick; `detect_doji` emits a long iff its confirmation bar has `nxt_c > nxt_o` with a
large body. Such a bar closes in its upper half by definition.

So `chasing` is **true by construction**, and the guard meant to spare the counter-trend half spares
nothing:

| strategy × `1d` | share of above-threshold signals actually dropped | direction source |
| --- | --- | --- |
| `doji`, `ema`, `trend_day` | **100.0%** | the signal bar's own close |
| `eqh_eql` | 76.2% | structure + close |
| `bos` | 48.1% | prior swing structure |

`bos` and `eqh_eql` are the controls that prove the mechanism rather than merely illustrate it: their
direction comes from prior structure, not the signal bar, so the guard still functions for them.

This is PR #141's defect shape exactly — two components that each look sane, whose conjunction is
near-total because they read **correlated (here identical) quantities off the same bar**.

### 2c. Bars per calendar day — the precondition, measured

| timeframe | bars per calendar day | median consumed | bars ≥ 0.80 |
| --- | --- | --- | --- |
| `4h` | **2** (mode; 14,234 of 14,332 days) | 0.875 | 59.4% |
| `1d` | **1** | 0.925 | 64.6% |
| `1wk` | **1** | 0.919 | 62.9% |

On `4h`, US-equity RTH is 6.5h, so bar 0 spans 4 of those hours (62% of the session) and bar 1 is a
2.5h stub. Median consumed at the session's **first** bar is **0.82** — already above the 0.80
threshold. There is no "early in the day" on any timeframe this bot scans.

The gate is inherited from the parent's F8 bias layer, built for a 24h market where `4h` means **6**
bars per day and the ratio genuinely climbs 0.17 → 0.33 → … → 1.0 across the session. The threshold
of 0.80 is calibrated to that shape. It is the same crypto vintage as the `volume_suppress` flags
removed in #141.

## 3. Blast radius — signal attrition, all other gates off

| strategy | `4h` | `1d` | `1wk` |
| --- | --- | --- | --- |
| `bos` | 27.8% | 30.4% | 35.8% |
| `engulfing` | 58.9% | 81.7% | 80.1% |
| `orb` | 79.5% | — | — |
| `doji` | 60.4% | 76.8% | 72.9% |
| `eqh_eql` | 49.1% | 60.2% | — |
| `ema` | 68.2% | 78.3% | — |
| `trend_day` | 68.6% | 81.7% | 80.7% |

Signal-bar median consumed on `1d` is **1.09–1.16** against an all-bar median of 0.925 — the
detectors select for precisely the wide-range bars the gate rejects, which is why signal attrition
(77–82%) runs well above the bar-level base rate (64.6%).

## 4. Does the documented claim replicate?

Tested the only valid way. Gate-ON is a strict **subset** of gate-OFF, so an ON-vs-OFF A/B has
dependent arms and no test applies — the trap that nearly banked a wrong `bos` call in #141. Instead:
backtest the **raw** population with every gate off, then split those trades on the gate's own
predicate `(consumed >= 0.80 AND chasing)` and ask whether the dropped arm is actually worse.

17 cells. Uncorrected: 4 justified, **1 harmful**, 12 no effect.

| cell | ΔR (dropped − kept) | p | verdict |
| --- | --- | --- | --- |
| `trend_day × 1wk` | −0.490 | <0.001 | gate justified |
| `orb × 4h` | −0.278 | 0.007 | gate justified |
| `bos × 1wk` | **+0.553** | 0.024 | **gate harmful** |
| `engulfing × 4h` | −0.262 | 0.039 | gate justified |
| `doji × 1d` | −0.351 | 0.043 | gate justified |
| *12 others* | — | 0.061–0.802 | no effect |

17 tests at α=0.05 expect ~0.85 false positives on their own, so multiplicity is not optional. After
**Benjamini–Hochberg at FDR 0.05, exactly one of 17 survives** (`trend_day × 1wk`); Holm agrees.

And the lone survivor sits on `1wk` — the timeframe where "intraday range consumed" is *least*
meaningful, since the bar is a week. That is far better explained as a wide-range-week volatility
effect than as the exhaustion effect the gate documents.

**The gate discards 28–82% of every strategy's signals to buy an effect that survives correction in
1 cell out of 17.**

## 5. Live and backtest were never running the same gate

There are **two independent ADR implementations**, and they disagree:

| | live dispatch (`analytics/stats/adr.py`) | backtest (`analytics/signal/gates.py`) |
| --- | --- | --- |
| source bars | **`1h` only**, grouped by UTC date | the scan timeframe's own bars |
| "today" | newest date in a 35-day window — genuinely partial | the signal bar's own full calendar day |
| degenerate on `1d`/`1wk`? | **No** — always ≥ 2 bars/day | **Yes** |
| scope | per **symbol**, applied to every timeframe | per (symbol, timeframe) |

So live's gate is *not* degenerate: it computes a real intraday progression from 7 hourly bars. The
backtest gate is a re-implementation that silently means something else on `1d`/`1wk` — and that
backtest output drives star ratings, the live quality gate, and every `tp_r` calibration.

Making the backtest match live is **not available**: only **18 of 522** symbols have `1h` data, so an
intraday-derived ratio cannot be computed at breadth. The honest option is to stop applying a
*different* filter that merely shares a name.

## 6. Why nothing caught it

`tests/test_live_parity_adr_gate.py` asserts the adapter "must reuse live's `_filter_signals_by_adr`
verbatim" — but its fixture, `_chasing_ohlcv()`, builds *"a single 24h day"* of intraday bars. That
is the crypto shape. It is the one shape on which the gate works correctly, and it never occurs on
`1d` or `1wk` in production. The test exercised the mechanism on data whose defining property
production data does not have, so it passed for months while the gate meant something else.

Sibling of #141's `test_signal_watch_toml_volume_suppress_flags`, which asserted a flag *parsed*
without ever asserting it left anything alive. **Same lesson, new surface: a green test proves the
code does what the fixture describes, never that the fixture describes production.**

`make check-dead-surfaces` cannot see this either — it detects exact zeros, and every affected cell
still produces signals. A 77–82% silent haircut is invisible to it, exactly as `orb × 4h`'s n=1 was
in #141.

Nor could the regression goldens. `tests/test_regression.py` calls `run_backtest` with **no
`bias_cfg` and no `live_parity`**, so all five live-parity gates (regime, direction_filter, F8
HTF-EMA, ADR, cooldown) no-op inside the golden pipeline. The suite covers detectors, volume flags,
`tp_r` and the cost model — the entire bias stack is outside it. This is why the goldens are
**byte-identical across this fix** and moved for #141: `volume_suppress` *is* threaded into that call
and `adr_suppress_threshold` is not. Unchanged goldens here are the correct result, not a missing
refresh — but "regression suite green" carries no information about any bias gate.

Three independent guards, three different blind spots, one defect passing through all of them.

## 7. The fix

`adr_gate_applies(timeframe)` in `analytics/signal/gates.py` gates the whole filter: true only for
intraday timeframes, where a calendar day holds more than one bar. `1d` / `1wk` / `1mo` — and any
**unknown** timeframe — fall closed, because a gate that silently means something other than its
documentation is worse than one that is off.

`timeframe` is a **required** parameter of `_filter_signals_by_adr`, not a defaulted one, so mypy
strict forces all six call sites to state it (`bt_cache`, `engine` ×2, `backtest_runner`,
`param_sweep` ×2). A default would let a future caller silently re-acquire the degenerate behaviour —
the same silent-surface class this repo has been closing since #136.

`4h` is **unchanged**. A two-point progression does exist there, so the threshold is mis-scaled
rather than meaningless, and re-picking it would be frozen sweep work.

## 7b. Production impact, measured after `make db-update`

Sample restored on the `1d` / `1wk` surfaces (pre-fix values as recorded in #141's handoff):

| cell | signals before | signals after | rating after |
| --- | --- | --- | --- |
| `doji × 1d` tue_thu | 12 | **180** | 4★ |
| `doji × 1d` weekdays | 17 | **273** | 4★ |
| `doji × 1wk` weekdays | 4 | **57** | 2★ |

`make check-dead-surfaces` still reports every declared cell alive in both configs (23 and 32 cells).

And the predicted cost landed exactly where section 4 said it would: **`trend_day × 1wk` weekdays is
now −0.200 avg_r at 1★ on 408 trades.** That is the single cell whose gate survived
Benjamini–Hochberg, and removing it does hurt there. Recorded rather than engineered around.

## 8. What this is NOT

Not a sweep. Nothing was searched, and no threshold was tuned to maximise avg R. The action is
"stop applying a filter on the timeframes where the quantity it computes is definitionally not the
quantity it documents" — the same correctness/sweep line that made #141 shippable under the freeze.

The cost is stated plainly rather than engineered around: this **loses** `trend_day × 1wk`, the one
cell where the gate survives multiplicity correction. Carving that cell out as an exception would be
precisely the avg-R-maximising move the freeze forbids, so the loss is taken.

Not a claim that the gate is harmful. Only `bos × 1wk` measured significantly harmful, and it does
not survive correction. The claim is narrower and stronger: **on `1d` and `1wk` the gate cannot be
doing what it says**, so keeping it means keeping an unexamined filter that costs 30–82% of every
strategy's evidence.

## 9. Debt and follow-ups this creates

1. **`tp_r` debt, second instance.** Every `tp_r` / `atr_sl_multiplier` on `1d` and `1wk` was
   calibrated on the ~20% subsample the gate left behind. Same standing decision as #141: **flag as
   debt, keep the freeze**; re-derive only if the freeze lifts.
2. **Live still gates `1d`/`1wk` signals off today's partial intraday state.** `compute_adr` is
   per-symbol, so a weekly signal is judged against how far *today* has run. Coherent enough to leave
   alone here, but it is a judgment call nobody has made deliberately. Separate scoped review.
3. **`orb` remains a candidate for `adr_exempt = true`** (breakout, like `bos`) — carried from #141,
   still unmeasured.
4. **`backtest_runs.adr_suppress_threshold` records what the config declared, not what the gate
   did** — and this is **pre-existing, not introduced here**. All three `upsert_backtest_run` call
   sites (`backtest_runner.py:560`/`:936`, `scanner.py:1229`) store the config value flat, with no
   exempt or applicability check. Verified against the DB: every `bos` and `eqh_eql` run carries
   `0.8` even though **both strategies are `adr_exempt = true`** and the gate never touched them.
   (`.claude/context/analytics.md` asserted the opposite — that `_is_adr_exempt` "stores NULL" —
   until this branch corrected it.) After this fix, `1d`/`1wk` rows join that gap. The concrete
   consequence is `digest_lib.py`'s ADR on/off comparison, which selects `on_r.adr_suppress_threshold
   IS NOT NULL` against `off_r … IS NULL` and therefore counts gate-off runs on its "on" side,
   reporting a spurious ≈0 ADR impact. **Deliberately not fixed here:** storing NULL would move
   `_backtest_run_id` hashes and recalibrate matching, which needs its own falsification, and fixing
   only the timeframe half while leaving `adr_exempt` mislabelled would be worse than either.
5. **Upstream:** the parent shares this gate. On a 24h market the degeneracy does not bite at `4h`,
   but any parent config scanning `1d`/`1wk` has the identical defect, and `adr_gate_applies` is the
   portable fix. Raise on the next `/sync-parent`.
