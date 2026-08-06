# `bos` was never decaying, and its direction flag was never equity-derived

**Date:** 2026-08-06
**Branch:** `fix/bos-4h-retire-and-crypto-era-suppress-long`
**Predecessors:** [`2026-08-06-adr-gate-timeframe-degeneracy.md`](2026-08-06-adr-gate-timeframe-degeneracy.md)
(PR #142), [`2026-08-06-adr-volume-gate-conjunction.md`](2026-08-06-adr-volume-gate-conjunction.md) (PR #141)

## 1. What was queued, and what was actually true

The handoff carried this across two PRs as *"a decay call"*:

> `bos` is **positive over ~7 years and negative over the last 1**, and the production window is
> `days = 365`. So this is a *decay* call, not "always broken". Retiring on one year of data would
> be a mistake; so would leaving a 1★ strategy dispatching because nobody looked.

Neither half survives measurement, and the framing itself — *time* — is the wrong axis.

| handoff claim | measured | verdict |
| --- | --- | --- |
| positive over ~7 years | `1d` +0.047, boot 95% CI **[−0.068, +0.166]** | **straddles zero** |
| …and materially so | 12-strategy cohort median over the same window = **+0.037** | `bos` is the **median** |
| negative over the last 1 year | **depends entirely on the window anchor** (§3) | **ill-posed** |
| therefore *decaying* | early-vs-late Welch **p=0.445**, Spearman(year, meanR) **−0.033** | **no trend** |
| "a 1★ strategy dispatching" | **zero** live alerts ever; but 13/52 cells *do* pass the gate | **half right** |

That makes **seven-for-seven** on re-deriving a handoff's stated mechanism before acting on it.

The real finding is a different shape: `bos` has no time trend and no net edge on `1d`, one
genuinely and significantly negative cell (`4h`), and one genuinely positive cell that is pure beta.

## 2. Method

Everything below reuses `_collect_sweep_results` directly rather than a hand-replica, so the chain
(detect → day filter → ADR → conflict resolver → `run_backtest`) is the production runner **by
construction**. Runs are **competed** — all of a config's strategies are present — because the
live-parity conflict resolver pools signals across strategies per `(symbol, tf)` and drops `bos`
events that lose to a higher-rated strategy on the same candle. Running `bos` alone inflates its
sample by ~30% (`1d`: 969 solo vs **735** competed) and is not what production does.

Window `since 2018-01-02`, live-parity all gates on, 13-symbol watchlist, Phase 0.4 cost model.

## 3. The window anchor decides the sign — this is the trap in the queued claim

The same calendar year, measured two defensible ways, gives opposite signs:

| method | `bos × 1d` | n |
| --- | --- | --- |
| 365-day-**anchored** run (what production does) | **−0.111** | 81 |
| trailing-365-day **slice** of a full-history run | **+0.085** | 92 |

An anchored run hands the detector no warm-up history and truncates trades at the window edge —
and `bos × 1d` holds for a mean of **77 days**. So "negative over the last 1 year" is not a fact
about `bos`; it is a fact about how the window was cut. Reconciliation check: the anchored replica
returns −0.111 against the saved `backtest_runs` pooled value of −0.119 (which mixes four save
dates), so the replica is sound and the divergence is genuinely the anchor.

**Transferable rule:** before reporting a trailing-window number, state whether the detector saw
warm-up history. For a strategy whose holding period is a meaningful fraction of the window, the two
methods are not approximations of each other.

## 4. There is no decay

`bos × 1d`, by calendar year (competed):

| 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| −0.366 | +0.332 | +0.244 | +0.204 | −0.026 | +0.243 | −0.260 | +0.105 | −0.176 |

- early (2018–21) **+0.125** vs late (2022–26) **+0.045**
- Welch **t=+0.76, p=0.445**; bootstrap of the difference **[−0.126, +0.285]** — straddles zero
- Spearman(year, mean R) = **−0.033**

The series is noise around zero. Retiring `bos` *for decay* is unsupported — and so is keeping it
*for edge*.

## 5. The axis that is real: direction, not time

| cell | long | short |
| --- | --- | --- |
| `1d` | **+0.351** [+0.191, +0.520] n=417 | **−0.352** [−0.494, −0.198] n=318 |
| `4h` | −0.036 [−0.313, +0.243] n=144 | **−0.488** [−0.675, −0.279] n=172 |

Both `1d` legs are individually significant, opposite in sign, and **near-cancelling** — which is
exactly why the combined number sits at zero. The short leg loses on both timeframes.

**The `1d` long-leg positive is not an edge claim.** It is 13 megacap *survivors* across a 2018–2026
bull market — the same survivorship/beta confound that voided the `xsmom/residual.py` long-only
read (+0.88, rejected on identical grounds). §7 demonstrates the confound directly.

## 6. Multiplicity: five cells, two survivors

Per #142's standing rule, the five `(config × timeframe)` cells are corrected together. One-sample
test of mean R vs 0, Benjamini–Hochberg at FDR 0.05:

| config | tf | n | mean R | t | p | BH crit | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `signal_watch` | `4h` | 316 | **−0.282** | −3.24 | 0.0012 | 0.010 | **SURVIVES** |
| `weekdays` | `1wk` | 184 | **+0.441** | +3.08 | 0.0021 | 0.020 | **SURVIVES** |
| `weekdays` | `4h` | 495 | −0.143 | −1.93 | 0.0540 | 0.030 | not significant |
| `signal_watch` | `1d` | 735 | +0.047 | +0.79 | 0.4301 | 0.040 | not significant |
| `weekdays` | `1d` | 1224 | +0.025 | +0.55 | 0.5841 | 0.050 | not significant |

This is why the fix is **not** symmetric across the two configs, and applying it symmetrically would
have been wrong (§8).

## 7. The one positive cell is beta, and can be shown to be

`bos × 1wk` on `weekdays` is the only positive `bos` cell measured anywhere (+0.441, p=0.0021,
n=184). It fails every beta discriminator:

- **Direction.** long **+1.020** (n=130) vs short **−0.954** (n=54). 71% of trades are long and
  they carry all of it.
- **Per year.** The only substantially negative year is **2022** — and 2022 is the only year the
  book was mostly *short* (7/34 long, mean −0.913). 2021 is **33/33 long** at +0.448. Long in up
  years, short in the down year, losing in both directions relative to the trend.
- **Selection.** Index proxies +0.368 vs single names +0.454 — indistinguishable, so this is not
  name-picking. The per-symbol winners are simply the megacaps that ran hardest (MSFT +1.27,
  GOOGL +1.24, ORCL +0.99, NVDA +0.74).
- **It has also stopped firing**: n=3 in 2025 and n=2 in 2026.

So the cell is a long-biased beta harvest on survivors, not an edge. It is **left exactly as it
is** — promoting it would be selecting a cell to maximise avg R, which is the frozen sweep work.

## 8. What changed

**`config/signal_watch.toml` — `bos` restricted to `["1d"]`.** The `4h` cell is −0.282 with a 99%
bootstrap CI of [−0.447, −0.106], survives Benjamini–Hochberg at p=0.0012, and is the **worst of all
12 strategies on that timeframe** in both the full window and the trailing year. Its `1d` sibling is
kept: indistinguishable from zero and exactly cohort-median, so there is no evidence to retire it
*or* to defend it, and the freeze says leave it alone.

Note the "~7 years" framing never applied to `4h` at all — **4h history starts 2024-05-16** (~2.2
years). Only `1d` (2018, and 2007 for SPY/QQQ) and `1wk` (2018) have long history.

**`config/signal_watch_weekdays.toml` — deliberately UNCHANGED.** Its `4h` cell is −0.143 at
**p=0.054**, which fails BH at its 0.030 threshold, and its `1wk` cell is significantly *positive*.
Mirroring the `signal_watch` edit would have removed a cell the evidence does not condemn. Two live
configs are two populations; a finding on one is not a finding on the other.

This is a **correctness removal** — a measured effect whose CI excludes zero on the wrong side —
not a threshold sweep. Nothing was searched or maximised. The TA freeze is intact.

## 9. The flag that was never equity-derived

`[strategy_params.bos] suppress_long = true` is **removed**.

**Provenance.** It arrived 2026-05-13 in **parent PR #367**, justified by `tools/bos_routing_audit.py`
(**parent PR #365**, same day) citing *"long-side avg_r=−0.268R on n=34,767"* out of an n=72,643
trade population. This fork was cut **2026-05-14** — one day later — and frozen at parent commit
`635ed5a`. Both the tool and the flag therefore predate every equity bar this repo holds.

**The cited n cannot be equity data.** This repo's entire `backtest_trades` table holds **24,196
rows**, of which **1,152** are `bos`. The audit read 72,643. Those were Binance futures trades.

**Re-derived on equities, the claim inverts** — long is `bos`'s *better* leg on both timeframes
(§5), not its worse one.

**Why it was invisible.** #141 removed four crypto-era `volume_suppress` flags in the same family
and missed this one, because it greps as a *direction* flag rather than a *volume* flag. And it is
latent rather than loud: `[bias.direction_filter]` ships as `mode = "soft"`, which logs and keeps
(`analytics/signal/gates.py:311-336`), so a wrong flag costs nothing — until someone follows that
block's own instruction to *"observe ≥2 weeks before flipping to hard"*. That window passed roughly
three months ago. Flipping it would have suppressed `bos`'s better leg and left only the
significantly-negative short.

**Not replaced with `suppress_short = true`.** The mirror flag would be selecting a direction to
maximise avg R — frozen — and it would be selecting on the beta-confounded side of §5/§7. *Removing
a falsified flag is correctness; adding its mirror is not.*

**Enforcement.** `tests/test_signal_config.py::test_no_shipped_strategy_sets_direction_suppress`
is the direct sibling of #141's `test_no_shipped_strategy_sets_volume_suppress`: neither live config
may declare `suppress_long` / `suppress_short`. Re-adding one must break a test and be re-measured
on equity data first. Prose does not enforce.

## 10. Found on the way, NOT fixed here

> **CORRECTED 2026-08-06** (same day, next branch). Two claims below were measured on the
> **sweep** path, not the live one, and are wrong as written. The corrections are inline and
> struck through; the SE arithmetic and the direction of the concern are unaffected. Full
> measurement: `docs/audits/2026-08-06-live-ev-gate-window.md`.
>
> - ~~"365-day directional avg_r"~~ → the live gate ran a **90-day** window. `[backtest] days = 365`
>   never reached it (`signal_runner.py` omitted `days`, taking the 90-day signature default).
> - ~~"13 of 52 `bos` cells pass the gate"~~ → computed via `_collect_sweep_results` (competed,
>   live-parity gates on, 365d), **not** `bt_cache._compute_backtest` (single strategy, ~90d),
>   which is what the daemon actually calls. The real live figure is worse: at 90d the gate
>   **abstained entirely on 71%** of all direction-legs.
>
> Lesson: *parity by shared intent is not parity*. Verify a replica against observed live
> behaviour, never against a second replica.

**The live EV gate admits on n=2.** `analytics/signal/scanner.py:739-770` runs in `mode = "hard"`
and suppresses a `(symbol, tf, direction)` cell whose directional `avg_r < 0.0` — but only
once `closed_trades >= min_trades`, and `[backtest] min_trades_1d = 2`, `min_trades_4h = 5`.

`bos`'s per-trade R standard deviation is **1.652**. So:

| n | standard error |
| --- | --- |
| 2 | **1.168 R** |
| 3 | 0.954 R |
| 5 | 0.739 R |
| 10 | 0.523 R |

Cells that clear `min_trades` do so on n=2–11 — every one of them inside ~1 SE of zero. The gate is
not selecting on edge at these counts; it is selecting on noise, and it does so for **every**
strategy, not just `bos` (`trend_day`, the 3★ control, has sd **2.343** → SE 1.66R at n=2).

This also corrects a tempting misreading: `bos` has dispatched **zero** live alerts since the daemon
started 2026-06-04 while ten other strategies fired 260 — but that is a two-month small-sample fact,
**not** evidence that the gate structurally blocks it. It does not.

Deliberately out of scope: raising `min_trades` changes what the live daemon dispatches for every
strategy, so it needs its own branch and its own falsification.

### 10a. `confidence_ratings` keeps rating cells the configs no longer declare

Retiring `bos × 4h` did **not** remove its `confidence_ratings` row. `recalibrate` rebuilt it from
the historical `backtest_runs` rows, stamping a fresh `updated_at_ms` onto the unchanged stale value
(−0.3174). It has no notion of what the config currently declares.

This is pre-existing and wider than this PR. Counting rated cells against declared cells:

| config | rated | declared | **orphaned** |
| --- | --- | --- | --- |
| `signal_watch` | 27 | 22 | **5** |
| `signal_watch_weekdays` | 36 | 32 | **4** |

The orphans are `bos × 4h` (new, from this PR) plus `fib_golden_zone` and `liquidity_sweep` on
`4h`/`1d` in both configs — **both removed from the configs on 2026-05-21**, and their ratings have
been refreshed on every recalibrate for the ~2.5 months since.

This matters because ratings are a *displayed* surface: they feed the Backtest UI stars, the star
line on Telegram alerts, and `_build_confidence_ratings_map` (which logs *"loaded 77 rating(s)"*).
`fib_golden_zone × 4h` currently shows **3★ at +0.4688** — the second-highest-rated cell in the
entire `signal_watch` table, above every live strategy except `orb` — for a strategy that has not
been dispatchable since May.

This is the exact **inverse** of `check-dead-surfaces`: that tool finds *declared-but-dead* cells;
this is *rated-but-undeclared*. Neither `make db-update` nor `make check-dead-surfaces` reports it —
`db-update` ran to completion and printed "no unexpected dead cells" with all nine orphans present.

Not fixed here: pruning changes what the UI displays and what the conflict resolver loads, so it
needs its own falsification.

## 11. Transferable rules

1. **State the window anchor.** A trailing-window number without warm-up history is a different
   measurement, not an approximation — and for a long-hold strategy the two can differ in *sign*.
2. **"Positive over N years" needs a cohort baseline.** `bos` at +0.047 looks positive until you see
   the 12-strategy median is +0.037. Compare to the cohort, not to zero.
3. **Two live configs are two populations.** The same strategy × timeframe justified removal in one
   and did not in the other. Never mirror a config edit because the configs look similar.
4. **A significant positive cell still has to pass the beta test** — direction split, per-year, and
   index-vs-names. On survivor universes in a bull market, long-tilted books manufacture alpha.
5. **Crypto-era inheritance is a flag *class*, not a flag list.** #141 swept volume flags; this was
   a direction flag one grep away and survived. Sweep by *provenance date*, not by flag name.
6. **A soft-mode gate hides a wrong flag indefinitely.** Cost-free today is not cost-free — it is an
   armed trap carrying a written instruction to arm it further.
