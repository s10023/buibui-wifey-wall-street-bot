# The live ledger was gross while the backtest was net

**Date:** 2026-08-19
**Verdict:** **BOUNDED** — a real asymmetry, corrected on one basis; it is worth −0.0141R per
resolved alert and changes no decision. The larger error it exposed is *shared* by both books.
**Scope:** `analytics/signal/outcome_backfill.py`, `analytics/store/schema.py`,
`analytics/store/signals.py`, `analytics/signal_runner.py`,
`tools/backfill_null_tp_outcomes.py`, `migrations/004_live_ledger_net_of_cost.py`

## The question

`run_backtest` has charged its trades since Phase 0.4. The live outcome resolver never charged
anything. Both wrote an R multiple into a column read as if it meant one thing, so:

> Is `signal_alert_outcomes.outcome_r` comparable to a backtest's `avg_r`, and if not, by how much
> and in which direction?

The direction is what makes this worth a fix rather than a footnote. An uncharged live book is
**flattered relative to its backtest**, which is the opposite of the assumption a reader brings
when a live book underperforms — the instinct is "reality is worse than the model", and here part
of the gap ran the other way.

## What was wrong

`backfill_outcomes` booked a loss at exactly `-1.0`, a win at `implied_tp_r`, and an expired row at
mark-to-market, with no fee, spread, impact or borrow anywhere in the module (`grep -c fee_pct` → 0).
The tell was hiding in plain sight: **all 218 losses were exactly `-1.0000R`**, which reads as
canonical rather than as uncharged.

## What was NOT the right fix — and this is the substantive finding

The queued item named the parent's `net_R` resolver (its #432) as the port. Copying it would have
been wrong, and the reason generalises.

The parent charges `2 * (fee_pct + slippage_pct) * entry / risk` plus funding — a flat crypto taker
fee. wifey's `engine.Trade.pnl_r` **ignores `fee_pct` entirely whenever a `CostModel` is set**
(`engine.py:126-128`), and `config/strategy_params.toml` sets `[backtest.cost_model] enabled = true`
in the shared base. So a verbatim port would have priced the live ledger on a flat fee the backtest
does not use.

> **A third cost basis does not fix a comparability gap; it adds one.** The requirement was never
> "charge something", it was "charge what the other book charges".

So `live_cost_r` mirrors `pnl_r`'s two branches exactly — `CostModel` when set (spread + impact +
borrow + commission, `fee_pct` ignored), flat per-leg `fee_pct` otherwise — and the resolver feeds
it the same causal trailing ADV/sigma window the engine builds, via the same
`build_cost_context`. The cost model consumes a structural `TradeLike` protocol rather than
`engine.Trade`, which is what let the live path be priced by the *same object* instead of by a
re-implementation.

## Two decisions that look like details

**One definition, two surfaces.** `live_cost_r` is shared by the resolver (charging at resolution)
and by migration 004 (charging retroactively). Had the migration priced rows its own way, the basis
boundary would have survived the migration whose entire purpose is removing it.

**NULL ≠ 0.0 in `outcome_cost_r`.** NULL means *unpriced* — no cost basis was configured — while
`0.0` would claim "priced, and it cost nothing". Collapsing them would also hide a row from
migration 004, whose only guard is `outcome_cost_r IS NULL`. The resolver leaves NULL when neither
a `CostModel` nor a non-zero `fee_pct` is supplied, which is also what keeps every pre-existing
test on its original gross expectations.

## Why the restatement shipped in the same PR

Fixing only the resolver splits the column at an arbitrary instant: gross before, net after. **A
boundary inside one column is worse than a uniformly wrong column**, because it silently breaks
every pooled statistic spanning it, and nothing in the schema records which side a row is on. The
parent's own port shipped without the restatement and carries exactly that. There is no era to
respect here — unlike migration 002 — because the resolver charged zero from the first row to the
last, so one rule covers every row and the guard is state (`outcome_cost_r IS NULL`) rather than a
date.

## Measured

Applied 2026-08-19 to `analytics.db` (backup: `~/backups/wifey/daily/2026-08-19`, plus
`analytics.db.bak`; the pre-existing 2026-08-14 `.bak` was preserved as `analytics.db.bak.2026-08-14`
rather than overwritten).

| | Before | After |
| --- | --- | --- |
| resolved rows | 292 | 292 |
| pooled `avg_r` | **−0.2050** (gross) | **−0.2192** (net) |
| loss avg | −1.0000 | −1.0144 |
| win avg | +2.8370 | +2.8220 |
| expired avg | +0.9870 | +0.9764 |
| rows on the gross basis | 292 | **0** |

Mean drag **0.0141R** per row (range 0.0015–0.0489), total **−4.1298R**. All 292 rows priced with a
real cost context; **0** fell back to the widest-bucket default and **0** were unpriceable. The 32
still-open rows are deliberately untouched — they are charged when they resolve.

Gross stays recoverable as `outcome_r + outcome_cost_r`, verified to reproduce −0.2050 exactly.

⚠ **CLAUDE.md's `−0.1752R` and `docs/system-overview.md` §4a predate this**; both are updated, and
the figure moved for two reasons at once — the ledger also grew from 267 to 292 resolved rows since
that number was taken. **−0.1752 → −0.2050 is sample growth; −0.2050 → −0.2192 is this change.**
Do not attribute the whole move to costs.

## The bigger error, which this does NOT fix

While verifying the claim that costs were "the smaller gap between the books", the frame turned out
to be wrong and worth correcting explicitly.

A gap **through** the stop still books exactly −1.0R here, because the resolver reads levels rather
than fills. **`engine.py:1116` sets `trade.exit_price = sl_price`, so the backtest does the same.**
That absence is therefore **shared**, and unlike uncharged costs it does **not** bias live against
backtest — it means both books overstate.

Measured on this ledger by re-filling each loss at its fill bar's open whenever that open was
already beyond the stop:

| | value |
| --- | --- |
| losses that gapped through | **46 of 218 (21.1%)** |
| mean realized R on losses | **−1.1374** vs the booked −1.0000 |
| spread over all 292 resolved rows | **−0.1026R** per row |
| this PR's cost charge, same basis | −0.0141R per row |

So the unmodelled fill assumption is **~7× the cost correction**. It is a shared modelling choice
rather than a defect, and changing it would move both books — which makes it a user call, not a fix
to slip into a correctness PR. Filing it rather than building it.

⚠ **The estimate itself is a bound, not a measurement of realized loss**: it assumes a stop order
fills at the next bar's open on a gap, and on `4h` an RTH "gap" includes the resample boundary.

## Verdict — **BOUNDED**

The asymmetry was real, is fixed, and is small. The live book was already net negative and is now
measured 0.0141R/alert more negative; nothing about the −0.7 gate, any sleeve verdict or any
dispatch decision moves. What the PR actually buys is that **live and backtest are on one basis**,
so future comparisons are honest — and the ledger no longer contains a basis boundary that would
have quietly corrupted any pooled statistic spanning it.

**Not claimed:** that the ledger is now *right*. It is now *comparable*. The gap-through term above
is larger than everything this PR changed.
