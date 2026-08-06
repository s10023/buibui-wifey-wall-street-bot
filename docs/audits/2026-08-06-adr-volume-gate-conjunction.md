# ADR gate × `volume_suppress`: two filters that cancel each other out

**Date:** 2026-08-06
**Trigger:** `/post-branch` follow-up to #140 — deciding the 5 dead cells `make check-dead-surfaces` surfaced.
**Verdict:** **CONFIRMED config defect**, not a detector defect. Four strategies across both live
configs were declared-but-voided. Fixed here; guarded at the config-load boundary.

## 1. What was asked, and what was actually true

The queued task read: *"`doji` is near-inert everywhere — 3 signals (tue_thu/4h) and 9
(weekdays/4h) across years and 13 symbols. That is a detector question (is its trigger condition
too strict?)."*

The premise was wrong, and it was the fifth consecutive handoff whose stated mechanism did not
survive re-derivation (see `feedback_rederive_handoff_mechanism`). `detect_doji` is not strict at
all. Run directly over the 13 live symbols it fires:

| timeframe | raw detector fires |
| --- | --- |
| `4h` | 508 |
| `1d` | **1,247** |
| `1wk` | 207 |

Across the whole 522-symbol DB the joint condition (body ≤ 0.1 × range, then next body ≥ 0.6 ×
range) fires **38,628** times on `1d` alone — ~3.4% of bars, almost exactly the product of its two
stage probabilities (10.4% × 32.4%), so the two legs are near-independent and neither is rare.

The detector was never the problem. Everything downstream of it was.

## 2. The mechanism

Two filters sit between the detector and a trade, and they select for **opposite bars**.

- **The ADR gate** (`_filter_signals_by_adr`, `[bias] adr_suppress_threshold = 0.80`) drops a
  signal when its bar has already consumed ≥ 80% of the trailing 14-day average daily range.
  It keeps **quiet, small-range** bars.
- **`volume_suppress`** (`_is_low_volume`, `analytics/backtest/gates.py`) drops a signal whose bar
  has < 1.5 × its trailing 20-bar mean volume. It keeps **high-volume** bars.

Range and volume are strongly positively correlated in equities. Measured over the 13 live symbols:

| | `1d` | `4h` |
| --- | --- | --- |
| corr(consumed_ratio, volume_ratio) | **+0.613** | **+0.673** |
| P(pass ADR) | 0.370 | 0.426 |
| P(pass volume) | 0.098 | 0.141 |
| P(pass both), if independent | 0.036 | 0.060 |
| **P(pass both), actual** | **0.0046** | **0.0062** |
| P(volume \| passed ADR) | 0.012 | 0.015 |

Conditioning on passing the ADR gate cuts the odds of passing the volume gate by **8×**. Each gate
alone is survivable — ADR-only leaves `doji × 1d` with 170 signals, volume-only leaves 93. Their
conjunction leaves **zero**.

Neither gate is individually wrong. The defect is that nothing ever checked them *together*.

## 3. Blast radius

`doji` was merely the loudest case. Every strategy carrying a `volume_suppress*` flag without
`adr_exempt = true` was affected. Cost-inclusive (Phase 0.4 model), committed `tp_r`/ATR values.

**Measurement basis — read before quoting these numbers.** The tables below come from a harness
that replays `detector → filter_signals_by_day → _filter_signals_by_adr → run_backtest` over the
**full** OHLCV history of the 13 live symbols. That is the chain in
`analytics/signal/bt_cache.py::_compute_backtest`, and it was validated against the real CLI on the
cell that mattered (`doji × 1d` = 0 either way) before being trusted anywhere else. It deliberately
does **not** apply the live-parity gate stack (regime, F8 HTF-EMA, cooldown, conflict resolver) and
does not use the configs' `since` window, because the question here was "which filter zeroes this
surface", not "what does production total". Production `backtest_runs` figures after the fix are
correspondingly smaller — e.g. `doji × 1d` shows 12 (tue_thu) and 17 (weekdays) signals in the
committed window, against 0 before. Use these tables for the **direction and mechanism**; use
`backtest_runs` for production totals.

### `config/signal_watch.toml` — the live config that dispatches Telegram alerts

| strategy | tf | n (shipped) | avg R (shipped) | n (fixed) | avg R (fixed) | total R (fixed) |
| --- | --- | --- | --- | --- | --- | --- |
| `doji` | `1d` | **0** | — | 169 | +0.3191 | +53.92 |
| `doji` | `4h` | 2 | +0.9809 | 104 | +0.1542 | +16.04 |
| `orb` | `4h` | **1** | +0.9981 | 256 | +0.1700 | +43.53 |
| `engulfing` | `1d` | 135 | **−0.2728** | 271 | **+0.0419** | +11.36 |
| `engulfing` | `4h` | 145 | +0.1601 | 266 | +0.1600 | +42.57 |

### `config/signal_watch_weekdays.toml`

| strategy | tf | n (shipped) | avg R (shipped) | n (fixed) | avg R (fixed) | total R (fixed) |
| --- | --- | --- | --- | --- | --- | --- |
| `bos` | `1d` | 86 | **−0.0330** | 1328 | **+0.0714** | +94.83 |
| `bos` | `4h` | 47 | −0.3291 | 580 | −0.0199 | −11.53 |
| `bos` | `1wk` | 14 | +0.4224 | 167 | +0.0990 | +16.53 |
| `doji` | `1d` | **0** | — | 284 | +0.5078 | +144.20 |
| `doji` | `4h` | 5 | −1.0208 | 200 | +0.0279 | +5.59 |
| `doji` | `1wk` | **0** | — | 55 | +0.1555 | +8.55 |
| `engulfing` | `1d` | 224 | **−0.4626** | 434 | **+0.0909** | +39.46 |
| `engulfing` | `1wk` | 52 | −0.4441 | 103 | +0.1510 | +15.55 |
| `orb` | `4h` | **1** | +0.9981 | 394 | +0.2312 | +91.11 |

Two things matter more than the totals:

1. **`orb × 4h` is a live cell that has been running on one trade.** It was never on the
   dead-cell list because it was not *quite* zero.
2. **On `engulfing × 1d` and `bos × 1d` the conjunction inverted the sign.** It does not merely
   thin a sample; it selects a biased subsample whose measured expectancy is the opposite of the
   population's. Any decision taken on those numbers was taken on an artifact.

## 4. Provenance — how it got here

Both flags predate the fork's equity re-target, and their own comments say so:

- `doji`: `volume_suppress = true  # A14b crypto finding; T14 AAPL volume split not re-tested`
- `orb`: `volume_suppress = true  # A14b: normal-vol wins Δ=+0.33R`
- `bos`: `volume_suppress = true  # A14b: normal-vol wins Δ=+0.11R`

A14b was measured on crypto, before the ADR gate existed. `bos` survived only because it was
*separately* given `adr_exempt = true` — and that exemption lived in `signal_watch.toml` alone,
so `signal_watch_weekdays.toml` never inherited it and `bos` was voided there. That asymmetry is
its own finding: a flag whose correctness depends on a second flag was allowed to drift apart from
it across two configs.

## 5. Why nothing caught it

This is the #140 defect class on a new axis — *a declared surface nothing executes and nothing
asserts about* — with a twist worth recording.

There **was** a test over these exact flags,
`TestEffectiveVolumeSuppress::test_signal_watch_toml_volume_suppress_flags`, and it passed
throughout. It asserted `effective_volume_suppress("doji") is True` — that the flag **parsed**. It
could not, by construction, notice that the parsed flag left nothing alive. A test written beside a
config asserts "the value I typed is the value I read back"; it never asks "does this configuration
produce any output at all".

`make check-dead-surfaces` (#140) did find the three `doji` cells the same day — but classified them
as *bar scarcity*, because a zero-signal cell looks identical whatever zeroed it. It could not see
`orb × 4h` (n=1, not 0) or the two sign inversions at all.

## 6. The fix

- Removed `volume_suppress` from `doji` (both configs) and `orb`, and `volume_suppress_long` from
  `engulfing` (shared base). The measured columns above keep the ADR gate **on** — this is the
  minimal change, not a re-tune.
- Moved `bos`'s `adr_exempt = true` from `signal_watch.toml` into the shared
  `config/strategy_params.toml`, so both configs inherit it and the pairing cannot drift again.
- Added `voided_volume_gates()` to `analytics/signal_config.py` and enforced it in
  `load_signal_config`, mirroring #139's `dead_timeframes` guard: declaring `volume_suppress*`
  without `adr_exempt` while the ADR gate is active is now a hard `ValueError` at load.
  `load_backtest_config` calls `load_signal_config` internally, so the sweep path inherits it.
- **Falsified against the real pre-fix configs**: the guard flags `[engulfing, orb, doji]` on
  `signal_watch.toml` and `[bos, engulfing, orb, doji]` on `signal_watch_weekdays.toml` — exactly
  the set this audit derived independently.
- Dropped `eqh_eql × 1wk` and `ema × 1wk` from `signal_watch_weekdays.toml` (separate decision,
  genuine bar scarcity: zero under **both** day filters).

## 7. What this is NOT

**No edge is being claimed.** The positive columns above are 13 mega-cap US tech names over a
survivorship- and beta-confounded sample, with no research guards run — no DSR, no PBO, no
bootstrap. That is precisely the setup that produced six non-positive sleeves here. The defensible
claim is narrow and is about correctness only:

> A fork-inherited flag silently voided most of the live signal surface, and on two strategies
> inverted its measured sign.

Whether any of these cells carries a real edge is untested and stays untested while the TA book is
frozen.

## 8. Follow-up sweep — is anything else misconfigured?

Asked and answered mechanically rather than by inspection. Two sweeps were run over the whole
config surface after the fix.

**Gate attrition, all 16 strategies × both configs** (raw detector → day filter → ADR → volume).
`[backtest.live_parity]` is `enabled=False` in both configs, so this chain is *complete* for the
backtest path — there are no further gates hiding behind it.

- **No second conjunction exists.** Before the `bos` fix below, exactly one cell class exceeded 90%
  single-gate attrition: `bos`, whose volume flag alone killed 92.8% (4h) / 94.1% (1d) / 89.8% (1wk).
- That was **not** the pathology fixed above. `bos` is `adr_exempt`, so its ADR kill is 0% and the
  volume gate acted alone at roughly its own base rate (`P(pass volume)` ≈ 0.10–0.14) — severe
  selectivity, not a cancelling pair. It needed a different test, below.
- **A methodological caution worth keeping:** the first `bos` comparison was flag-ON vs flag-OFF,
  which is invalid — ON is a strict *subset* of OFF, so the arms are dependent and no test applies.
  Reading those two columns as "mixed evidence" nearly banked a wrong conclusion. Split the shared
  population on the gate's own predicate instead, and test it.
- **`bos`'s flag was also removed — but for a different reason, and only after a significance
  test.** A first pass compared flag-ON against flag-OFF and looked "genuinely mixed" (ON better
  per-trade on `1d` tue_thu and `1wk`, worse on `4h` and `1d` weekdays), which suggested the choice
  was a frozen sweep decision. **That framing was wrong**: ON is a *subset* of OFF, so the two arms
  are not independent, and the differences were never tested. The correct test splits the
  unsuppressed population on the gate's own `is_low_volume` predicate — i.e. does volume predict R
  at all?

  | cell | Δ (normal − low vol) | t | p | 95% CI of Δ |
  | --- | --- | --- | --- | --- |
  | `4h` tue_thu | **−0.133** | −0.51 | 0.610 | [−0.612, +0.398] |
  | `1d` tue_thu | +0.061 | 0.31 | 0.756 | [−0.316, +0.445] |
  | `4h` weekdays | **−0.299** | −1.58 | 0.113 | [−0.647, +0.090] |
  | `1d` weekdays | **−0.069** | −0.49 | 0.626 | [−0.337, +0.210] |
  | `1wk` weekdays | +0.521 | 1.27 | 0.203 | [−0.267, +1.316] |

  The evidence is not mixed, it is **absent**: every p ≥ 0.113, every 95% CI straddles zero, and the
  point estimate carries the **wrong sign on 3 of 5 cells** against the flag's own claim (*"A14b:
  normal-vol wins Δ=+0.11R"*, crypto-derived). The flag was discarding 90–94% of `bos`'s signals
  (1d: 1,210 → 71) to buy an effect measured at zero. **Removing a flag whose documented
  justification does not replicate is correctness, not a sweep** — nothing was searched or
  maximised, so the TA freeze is intact. This is the same reasoning #139 used to drop `1wk`
  (evidence accumulation), except the sample loss here is immediate and the compensation nil.

- **`bos`'s `adr_exempt` was KEPT**, tested the same way: also not distinguishable (p = 0.259–0.956),
  but it *preserves* sample (1d: n=1189 exempt vs n=831 gated) and its point estimates mildly favour
  exemption on `1d`/`1wk`. It sits on the opposite side of the evidence trade-off from
  `volume_suppress`, so keeping it is both the status quo and the sample-preserving choice.

- **Net result: all four crypto-era A14b volume flags are now gone**, and no strategy in either
  config sets `volume_suppress*`. That is the honest end state — none of the four replicated on
  equities — rather than "three removed for being catastrophic, one kept because its failure was
  merely expensive".

**Config-surface sweep, four silent-inertness classes.** The loaders do **not** validate unknown
TOML keys, so a typo or an obsolete key is ignored without a word — hence the mechanical check:

| class | result |
| --- | --- |
| A. keys no Python file reads | **none** — every key name resolves to code |
| B. per-TF keys for unscanned timeframes | one: `ema.atr_sl_multiplier_1wk` (a consequence of dropping `ema` to 4h/1d in this very PR) — annotated inert, not deleted |
| C. `[strategy_params]` blocks for unrun strategies | `marubozu` — registered in **both** registries yet absent from both configs' `strategies` list |
| D. flags inert given another flag | `engulfing.volume_spike_boost` — the engine reads `_boost` only inside `if _suppress`, so removing the suppress flag made it a no-op; annotated |

**Systemic observation, not acted on:** the ADR gate removes 77–83% of signals on `1d` for *every*
strategy (`trend_day` 81.7%, `engulfing` 81.7%, `ema` 78.3%, `doji` 76.8%), because on a daily
timeframe `consumed_ratio` is ~1.0 by construction (§9). That is a far larger effect on the daily
surface than any per-strategy flag, and it is uniform, so it is invisible in cross-strategy
comparisons.

## 9. Operational impact — the star ratings were never measured

The reason this mattered beyond a backtest table. Tracing the live dispatch path (verified, not
inferred):

1. `analytics/recalibrate_lib.py:51` writes ratings only `WHERE closed_trades > 0`. A voided cell
   therefore **can never earn a rating**, no matter how many times `make db-update` runs.
2. `analytics/signal/scanner.py:251-259` resolves an alert's stars as
   directional DB rating → combined DB rating → `STRATEGY_REGISTRY[s].get_confidence(tf)`.
3. `analytics/strategies/_base.py:57` makes that last fallback the spec's **editorial** value, or
   **3** when the timeframe is absent from the dict. No spec defines `1wk` — which is precisely
   why #139 observed a "hardcoded 3★" on weekly cells. The mechanism is this line.

So every voided cell dispatched on an *editorial guess* that no amount of backtesting could correct.
`doji × 1d`'s editorial value is **5 — the maximum** — for a cell that had produced zero signals in
its entire recorded history.

After the fix all four strategies carry measured, directional ratings for the first time:

| strategy | cell | stars now | avg_r | editorial fallback it used before |
| --- | --- | --- | --- | --- |
| `doji` | `1d` tue_thu | 5 (short 5, long 3) | +0.948 | 5 |
| `doji` | `4h` tue_thu | 3 (short 5, long 1) | +0.327 | 2 |
| `orb` | `4h` tue_thu | 4 (short 5, long 3) | +0.522 | 2 |
| `engulfing` | `1d` tue_thu | 3 (short 4, long 2) | +0.280 | 4 |
| `bos` | every cell | **1** | −0.094 … −1.031 | 1 (4h/1d), 3 (1wk) |

The `bos` row is the operationally important one: it is now correctly rated **1★ across every
cell**, on 105–423 trades per cell instead of the 7–29 it had while the volume flag was on. The
live quality gate can finally act on it. Note also `doji × 4h` splits hard by direction — short 5★,
long 1★ — which the strategy-wide editorial 2 could not express at all.

**This does not make any of them an edge** (§7 stands: 13 mega-caps, no research guards, and a
1-year production window). It means the ratings now describe something that was measured rather
than something that was assumed.

## 10. Debt this creates

Every `tp_r` and `atr_sl_multiplier` for `doji`, `orb`, `engulfing` and `bos` was calibrated
**under the conjunction**, on the surviving 2–8% subsample (`tp_r_4h = 4.5 # n=30`,
`tp_r_1d = 5.0 # raw n=99 → live n=13`, and so on). Those values are fitted to a population that
no longer exists. They are **deliberately not re-derived** — that is frozen TA-sweep work — and are
annotated as debt in both configs. The measured columns in §3 use these un-reoptimised values,
which makes them conservative rather than flattering.

Also left open, deliberately:

- `orb` is a breakout like `bos` and arguably qualifies for `adr_exempt = true`. That variant is
  unmeasured; the numbers here keep the ADR gate on. Follow-up, not a fix.
- `eqh_eql` still carries `adr_exempt = true` in `signal_watch.toml` only — the same config-local
  asymmetry that voided `bos`, on a strategy that has no volume flag today and so is currently
  harmless. It is a latent repeat of this defect if a volume flag is ever added.
- The ADR gate is **degenerate on `1d`**: "cumulative intraday range up to this candle" is the whole
  day's range when a calendar day holds exactly one bar, so `consumed_ratio` is ~1.0 by
  construction and the gate drops 63% of *all* daily bars (median ratio 0.92 against a 0.80
  threshold). It was designed for intraday use. Not touched here — it changes every strategy's
  behaviour — but it deserves its own review.
