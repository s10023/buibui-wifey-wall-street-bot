# Symmetric gap fill — the mirror the first measurement missed

**Date:** 2026-08-20
**Verdict:** CORRECTNESS — no edge claim, and no sleeve verdict moves.
**Audit:** this file. Predecessor: `2026-08-19-gap-through-stop-measurement.md`.

## The question

The 2026-08-19 audit measured how much the books overstate a loss by assuming a stop
fills at its level when the bar opened through it, and left "whether to apply it" as a
user call. The call was made on 2026-08-20: apply it.

The question this audit had to answer first was not *whether* to apply it but *what*
to apply. Both books book **both** levels exactly — `engine.py`'s
`exit_price = sl_price` / `= tp_price`, and the resolver's flat `-1.0` /
`implied_tp_r`. The 2026-08-19 measurement covered only the adverse level. Pricing that
one alone does not remove a bias; it replaces one with its mirror.

## Finding 1 — the favourable tail is real, and it is the BIGGER of the two

Same ledger, same 292-row denominator, same replica discipline as the predecessor
(re-derived bar == stored `outcome_filled_at_ms` on **218/218** losses and **46/46**
wins):

| side | rate | per gapped trade | pooled, per resolved row |
| --- | --- | --- | --- |
| losses gapping through the stop | 46 / 218 = **21.1%** | −1.6512R vs −1.0000 booked | **−0.1026R** |
| wins gapping through the target | 12 / 46 = **26.1%** | +4.3249R vs +2.7083 booked | **+0.0664R** |
| net, symmetric | — | — | **−0.0362R** |

Wins gap through their target at a *higher* rate than losses gap through their stop.
That is the intuitive direction once stated: a favourable gap is exactly the event that
carries price past a level sitting further away than the stop.

⚠ **`n` is thin on the favourable side.** The rate rests on 46 wins, the magnitude on
**12** gapped ones. The rate is solid; the +1.62R mean overshoot is not, and nothing here
should be quoted as a precise expectation.

## Finding 2 — one-sided would have overstated the correction by ~65%

| model | pooled live `avg_r` |
| --- | --- |
| before | −0.2192 |
| adverse tail only (as filed 2026-08-19) | −0.3218 |
| **symmetric (shipped)** | **−0.2553** |

The one-sided number is not a rounding difference from the symmetric one — it is roughly
three times the true move. Shipping it would have made every sleeve look worse against a
**stop-free** benchmark, which is precisely the comparison the 2026-08-19 audit named as
the reason to do the work at all. The fix would have defeated its own justification.

**The transferable rule: measure both tails before pricing either.** A measurement that
covers one side of a symmetric mechanism is not a small version of the full measurement —
it points the wrong way.

## Finding 3 — the fetch window bug this migration walked into

Migration 005's first draft bounded its OHLCV fetch at
`latest_signal + (max_hold + 2) * tf_secs`. The replica check then reported **6 of 264**
rows unreproducible, where the audit's own scripts had reproduced 264/264.

The cause is this repo's own standing footgun: **a bar count is not a calendar span on an
RTH tape.** `4h` RTH is 2 bars/day, so `max_hold` bars spans `max_hold / 2` *days* and the
window was short by roughly a factor of six. Bounding the fetch at the table's own
`MAX(open_time)` took the replica check to **264/264**.

Worth recording because the failure was *quiet and conservative*: the migration skipped
the rows it could not reproduce, so the bug's symptom was six rows silently not restated —
a number small enough to read as a property of the data rather than of the query.

## What this does and does not buy

**Does:** one fill basis across both books, symmetric on both sides, shared through
`analytics/backtest/fills.py` so they cannot drift; a live ledger on one basis rather than
two split at an arbitrary instant.

**Does not:** change any sleeve verdict, any backtest-vs-live comparison, or any gate. The
absence was SHARED before the fix — live −0.1374R per loss against a matched-backtest
−0.0976R, 95% CI **[−0.1180, +0.0266]**, containing zero — and it stays neutral after it.
The eight sleeves are vol-targeted books, not stop-exited trades, so none of this reaches
them.

The forward-looking reason is unchanged from the predecessor: a stop-free external
benchmark carries no gap slip at all, so the shared bias stops being shared the moment SPY
buy-hold or a random-entry null is the reference.

## Reproducing this

```bash
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_stop_live.py  # adverse tail
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_tp_live.py    # favourable tail
PYTHONPATH=. poetry run python migrations/005_symmetric_gap_fill.py         # dry run
```

The migration's dry run is itself a reproduction: it prints the replica check, the split of
changed rows into gapped losses and gapped wins, and the before/after pooled `avg_r`.

## Still open

- **Slippage beyond the opening print** is still unmodelled, on both sides. It would make
  gapped losses worse and gapped wins better, i.e. it widens both tails rather than
  favouring either.
- **The favourable magnitude is thin at n=12.** Re-measure once the ledger grows; the
  *rate* is what this fix depends on, and that is the better-supported half.
- **The backtest book's own gap rates are unmeasured on the favourable side.** Only the
  adverse side was matched in the predecessor. Nothing here depends on it — the fix is
  applied to both books identically by construction — but a future comparison that wants
  to quote a backtest gap rate should measure it rather than assume this ledger's.
