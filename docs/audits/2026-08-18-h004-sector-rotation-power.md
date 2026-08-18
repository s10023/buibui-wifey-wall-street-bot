# H-004 — risk-off sector rotation, G3 power price (2026-08-18)

**Verdict: BLOCKED — unreachable on power, and priced out BEFORE any sector
return was read. The claim's own falsifiable form is a NULL ("tech outflow
episodes are not accompanied by defensive relative strength"), so confirming it
means licensing a null, and `analytics.audit_guard.powered_null` refuses that at
this repo's n. The honest unit is the risk-off EPISODE, and the sector panel
holds 5 of them (peak-to-recovery, ≥10% on `^GSPC`, 2018-01-02 → 2026-08-14).
At n=5 the 95% CI half-width is 0.8765 sd, so a null could only be licensed
against a bar wider than 0.88 sd — a bar so permissive it would call almost any
rotation "no rotation". Licensing a ±0.25 sd null needs n≈62; ±0.50 sd needs
n≈16. Measured episode rate is 0.36/year, so those are ~172 and ~44 years of
history against the 8.6 the panel has. More data of this shape cannot fix it on
any horizon anyone will trade.**

Source claim: [@fenggemeigu, 2026-07-26](https://www.youtube.com/watch?v=5sv3Jjj1Gd0&t=280s)
(t=280s), captured 2026-08-05 as `docs/plans/thesis-inbox.md` row H-004.
Vision could not corroborate it — the frame showed an index chart with no sector
overlay, so it is a text-only assertion.

## What the claim says

> During a risk-off episode, capital leaving high-valuation tech/AI is **not**
> rotating into defensives, energy or industrials — it is parked, and returns to
> tech once sentiment stabilises.

Filed falsifiable form: *tech-sector outflow episodes are not accompanied by
defensive-sector relative strength.* Implied primitive: sector-level relative
strength conditioned on a risk-off episode.

## The pre-registration, and the line it did not cross

Recorded before any outcome was observed, and the run stopped at G3, so **no
sector return was ever read**. Establishing `n` reads only `^GSPC` closes, which
define the conditioning event, never the outcome.

| Field | Declared value |
| --- | --- |
| Conditioning event | Peak-to-recovery drawdown episode on `^GSPC` |
| Episode start | Running-max peak preceding a close ≥ `thr` below it |
| Episode end | Close regains that peak (or panel end, flagged UNRECOVERED) |
| Outcome | Defensive-minus-tech cumulative relative return over the episode |
| Panel | 2018-01-02 → 2026-08-14, the span on which all 505 members have `1d` |
| Timeframe | `1d` only |
| Unit of observation | The episode |

Trial family, enumerated rather than assumed: tech = IT or IT + Communication
Services (2) × defensives = Staples+Utilities or +Health Care (2) × `thr` ∈
{5, 10, 15, 20}% (4) × equal- or cap-weighted sector aggregate (2) = **32
trials**.

`1wk` is excluded as redundant with `1d` on an episode-conditioned study. `4h`
is excluded on a hard constraint: it reaches only 105 of 505 members and that
subset is size-tilted with sector coverage running 4–40%, so a 4h *sector*
aggregate is confounded by construction.

## The n, and why the naive count is an artifact

Thresholding raw drawdown fragments one bear market into many "episodes" as
price oscillates across the line — at ≥20% the 2022 bear alone splits into 12
of the 14 counted crossings. Peak-to-recovery counts it once:

| `thr` | Episodes | Episode-days | Share of panel |
| --- | --- | --- | --- |
| ≥5% | 13 | 1,370 | 63% |
| ≥10% | **5** | 1,020 | 47% |
| ≥15% | 4 | 874 | 40% |
| ≥20% | 2 | 639 | 30% |

The five at ≥10%: 2018-01-26 (−10.2%), 2018-09-20 (−19.8%), 2020-02-19
(−33.9%), 2022-01-03 (−25.4%), 2025-02-19 (−18.9%).

⚠ **The 2022 episode is 513 of the 1,020 episode-days — half the day-count is
one macro event.** Any statistic that treats days as independent is dominated by
a single observation.

## G3 — the power price

Real `tools/distil_power.py` output, `--n-trials 32 --sr-variance 0.25 --sd 1.0
--bar 0.25 --corpus-best 0.41`, units `per_book_day`:

```text
===== n=5   (>=10% peak-to-recovery episodes) =====
  required Sharpe   2.955121
  corpus best       +0.4100
  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best

  null bar          +/-0.25
  CI half-width     0.8765  (best case, point estimate exactly 0)
  powered null      NOT LICENSABLE  (analytics.audit_guard.powered_null)
                    A null here would be INSUFFICIENT, never 'no effect'.

===== n=13  (>=5% episodes, most permissive) =====
  required Sharpe   1.825175
  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best
  CI half-width     0.5436
  powered null      NOT LICENSABLE

===== n=1020 (episode-DAYS, undeflated upper bound) =====
  required Sharpe   1.115563
  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best
  CI half-width     0.0614
  powered null      LICENSABLE
```

The effect leg fails at **every** unit: required Sharpe 1.12–2.96 against a
corpus best of +0.41, the largest sleeve Sharpe any of the eight produced. It is
"REACHABLE" only arithmetically.

The reachability leg is insensitive to the `sr_variance` assumption. Across
{0.10, 0.25, 0.50, 1.00} the required Sharpe at n=5 runs 2.17 → 5.28, i.e. 5–13×
the corpus best throughout, and the powered-null leg does not depend on
`sr_variance` at all.

### ⚠ The unit choice, not the data, decides the verdict

The last two blocks differ **only** in whether the unit is the episode or the
episode-day. Counting days flips the powered null from NOT LICENSABLE to
LICENSABLE — that is, it converts "we cannot tell" into "no rotation", which is
the pundit's claim, on identical data. Half of that day count is 2022.

This is the same defect the exit A/B recorded, where no arm's mean R cleared
zero once the 31 ET session days rather than the 267 alerts were the unit
(`docs/audits/2026-08-14-exit-policy-ab-v1.md`), and the same one the
warning-value audit recorded when a sample-size floor stood in for power
(`docs/audits/2026-08-13-warning-value-audit.md`). **A null claim is exactly
where an inflated denominator is most dangerous, because it buys the confident
answer rather than a wrong one.**

## What would unblock it, and why nothing will

Smallest n at which a null becomes licensable, from the same tool:

| Null bar | n required | Years at the measured episode rate |
| --- | --- | --- |
| ±0.25 sd | 62 | ~172 |
| ±0.50 sd | 16 | ~44 |

The rate is measured, not assumed. Extending the `^GSPC` panel from 8.6 to 19.5
years (2007-03-01 → 2026-08-14, a 2.26× longer window) moves the ≥10% episode
count from **5 to 7** — +40%, not +126%, because the added years are mostly one
GFC episode. That is 0.36 episodes/year.

A maximal free-data backfill does not reach either bar:

- The sector panel is capped at 2018-01-02 because that was the constituent
  backfill `--since`, not because data is unavailable.
- Deep history to 2007-03-01 exists in the DB for index and macro ETF symbols
  only (`^GSPC`, `^NDX`, SPY, QQQ, TLT, GLD, SLV, and the `xasset/` set).
- **There are no sector ETFs in the DB.** The universe's four ETFs are DIA,
  IWM, QQQ, SPY — all broad index. XLK/XLP/XLU/XLV/XLE/XLI and siblings would
  have to be added, and they only list from December 1998, so the ceiling is a
  ~27-year panel ≈ 10 episodes. Still short of 16.

## The G2 conflict, stated rather than resolved quietly

`/research-distil` G2 rejects a re-slice of held price/volume outright, citing
the free-data arc's honest exit. H-004 needs no new data — the inbox row lists
that as a feasibility *strength*, and under G2 it is the disqualifier. This run
proceeded under the narrower reading that H-004 is a descriptive regime
diagnostic rather than a ninth sleeve, which is the framing the inbox row itself
uses, and under which `GATE_SHARPE = 0.7` never applies.

**It did not matter.** G3 blocks it independently of G2, on n alone. Recording
this so a future session does not reopen H-004 by arguing the G2 framing.

## Caveats

- **The corpus-best comparison mixes units.** `required_sharpe` returns a
  per-unit Sharpe at the declared n; +0.41 is `xasset/broad_ls` cost-free at
  sleeve scale. The skill prescribes it as the anchor and no better one exists
  here, but it is an order-of-magnitude check, not an identity.
- **`sr_variance` is a sensitivity grid, never a measurement.** It is unknown
  until the 32 trials run, and the skill is explicit that an unknown
  `sr_variance` makes a claim INSUFFICIENT rather than a pass. The verdict rests
  on the powered-null leg, which does not use it.
- **No deflator was applied** and none is needed: the episode unit is already
  the deflated one. The n=1020 row is the undeflated upper bound, shown to
  expose the trap, not to be quoted.
- **Survivorship is asymmetric and untested here.** `membership_as_of` is
  2026-06-16, so the panel is today's S&P 500 walked back to 2018, and tech's
  survivorship bias over that window is the largest of any sector. It would
  tilt *toward* the claim. It never became binding, because n did.
- **"Parked" is unobservable in OHLCV.** Cash and money-market balances leave no
  price trace, so even a powered result would under-determine the mechanism —
  absence of defensive strength is equally consistent with rotation into bonds,
  gold, ex-US or small caps. Any future attempt should test the observable and
  drop the flow story.
- Sector betas were never estimated. A powered version of this study would need
  a beta control, since defensives outperform tech in any drawdown by
  construction, and three cells here have already been contaminated that way.

## Edge-question verdict

**BLOCKED.** Not refuted, not a null, not bounded — untestable at this repo's n
against this trial family. The binding constraint is the number of independent
risk-off episodes, which is 5, and which grows at 0.36/year.

H-004 stays filed. Reopen only if the unit of observation changes to something
that recurs far more often than a macro drawdown — which would be a different
hypothesis, not this one.
