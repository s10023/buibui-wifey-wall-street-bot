# Enabling live-parity on the ratings sweep — and why the conflict resolver must stay off

**Date:** 2026-08-07
**Scope:** `config/strategy_params.toml`
**Status:** five gates enabled; `conflict_resolver` deliberately left off (measured non-deterministic)

## Summary

The sweep behind `make db-update-backtest` — the only input to `confidence_ratings`
since #146 — had never run the live-parity gate stack, so the star ratings measured a
signal population the daemon does not trade. This enables five of the six gates.

The sixth, `conflict_resolver`, is **not** a preference left for later: switching it on
makes `make db-update` non-deterministic, which is unshippable. The measurement is below.

| Gate | State | Why |
| --- | --- | --- |
| `regime` | **on** | pure function of price + config |
| `direction_filter` | **on** | pure per-event flag check |
| `f8_htf_ema` | **on** | pure function of HTF slope |
| `adr_bias` | **on** | pure function of price (intraday-only since #142) |
| `cooldown` | **on** | pure function of signal ordering |
| `conflict_resolver` | **OFF** | **reads `confidence_ratings`** — closes a feedback loop |

## Background — how the gap survived ~2.5 months

T6 (2026-05-26, six PRs) wired all six live gates into `run_backtest` behind a
**default-off** `LiveParityConfig`. The default was correct for the port: plan §8 reads
"`make db-update` is NOT needed for any task. Default-off `LiveParityConfig()` ⇒
`run_backtest` output is byte-identical ⇒ regression goldens do not move." Nothing in the
plan assigned the *flip*, so it never happened.

- No config had ever declared `[backtest.live_parity]` (`git log -S live_parity -- config/`
  was empty).
- `make wifey-backtest` passes no `--live-parity`, and `_resolve_live_parity` returns the
  base config unchanged when neither the master switch nor a per-gate override is set.

Meanwhile the daemon **gates dispatch** with all six, and the research that set policy ran
gated via ad-hoc `--live-parity` — #143's `bos × 4h` retirement ("live-parity all gates on",
audit line 40) and the 2026-06-03 `tp_r` re-derives, whose TOML comments record raw n as
"inflated 4.3×" / "8.2×" / "9×". So every *measurement* was ungated and only *dispatch* was
gated, and the committed `tp_r` values were calibrated against a population the routine sweep
never produced.

**The block lives in `config/strategy_params.toml`, not behind a Makefile flag**, precisely
because a flag is how the two diverged unnoticed. Both live configs inherit it via
single-level `extends` (verified after the edit).

## The conflict-resolver feedback loop

`_build_confidence_ratings_map` loads `confidence_ratings.avg_r` and
`_resolve_conflicts_for_signals_map` uses it to pick the winning side of an opposing
same-candle conflict. So with the gate on:

> sweep reads ratings → drops the losing side → `backtest_runs` changes →
> `recalibrate` writes different ratings → next sweep resolves differently

Three consecutive full `backtest + recalibrate` passes, all six gates on:

| Comparison | rows differing (of 160) | star changes |
| --- | --- | --- |
| pass 1 → pass 2 | 108 | 18 |
| pass 2 → pass 3 | 66 | 8 |
| pass 1 → pass 3 | 88 | 10 |

Damping, but **not converged**, and individual cells were still oscillating at iteration 3 —
`pin_bar × 1d` long went 2★ +0.10 → **4★ +0.76** while `pin_bar × 1wk` long went 4★ → 2★, and
`eqh_eql × 4h` short swung 4★ +0.504 → 1★ −0.607 between passes 1 and 2. There is no argument
that the map is a contraction, and no unique fixed point is guaranteed.

`make db-update` must be deterministic or a genuine rating change is indistinguishable from a
re-run artifact — the exact silent-defect class this repo keeps finding. Hence the gate stays
off, and the reason is recorded in the TOML beside the flag rather than in a doc.

**The live path does not have this problem.** It reads ratings that were already written and
never feeds its own output back within a cycle. Only the sweep closes the loop.

## Result with five gates

**Deterministic.** Two consecutive full `backtest + recalibrate` passes: **0 of 160 rating
rows differ.**

| Config | closed trades (gates off → 5 on) | sweep grid |
| --- | --- | --- |
| `signal_watch` | 3371 → 2244 (**−33.4%**) | 312 = 24 × 13 |
| `signal_watch_weekdays` | 5884 → 3837 (**−34.8%**) | 468 = 36 × 13 |

So the five deterministic gates capture roughly four-fifths of the full parity effect
(−41.6% / −42.5% with all six).

**No cell lost its rating: 0 rows lost, 0 gained.** This is better than the pre-flip forecast,
which was measured with all six gates on and predicted two casualties (`eqh_eql × 1d` 26 → 9,
`hammer_hanging_man × 1wk` 20 → 0). Without the resolver the samples are larger and both stay
above `min_trades`. `make check-dead-surfaces` is clean in both directions for both configs.

**52 of 160 rating rows change stars**, including sign flips in both directions —
`trend_day × 4h` long 3★ +0.332 → **5★ +0.961**; `engulfing × 4h` short 1★ −0.137 → **3★
+0.257**; `pin_bar × 1wk` combined 3★ +0.325 → **1★ −0.102**; `eqh_eql × 1d` short (weekdays)
3★ +0.342 → **1★ −0.614**. Ratings now describe the population the daemon actually trades.

Regression goldens move by design, exactly as the Phase 0.4 cost-model flip did in #80.

## Transferable rules

- **A default that exists to protect a migration needs an owner for removing it.** T6's
  default-off was right for the port and wrong for the 2.5 months after; the plan proved
  byte-identical output at every step and never said who flips it. When a PR ships a
  capability default-off "to keep goldens stable", the flip is a follow-up task, not an
  implicit one.
- **A gate that reads a table the pipeline writes is not a gate, it is a fixed-point
  iteration.** Check the data-flow direction of every gate before enabling it in the
  producer of its own input. Five of these six were safe for a structural reason (pure
  functions of price/config), and that reason is what makes them safe — not that they
  measured well.
- **"It converges" needs three points, not two.** Passes 1→2 alone (108 rows) look like
  transient re-seeding. Only 2→3 (66) shows the decay rate, and only the per-cell swings
  show that decay in aggregate hides oscillation in individuals.
- **A fallout forecast is conditional on the configuration it was measured under.** The
  two predicted rating casualties did not materialise, because the forecast assumed six
  gates and five shipped. Re-measure after any change to the thing you forecast against.
- **Put the reason next to the flag.** `conflict_resolver = false` is indistinguishable from
  an oversight without the comment block explaining that it is load-bearing — and the next
  session's instinct will be to "finish the job" by flipping it on.
