# H-021: the party-control calendar replicates exactly, the claim it carries does not

**Date:** 2026-08-19
**Verdict:** **EXCLUDED as filed** — the two quotable legs both fail. "Dem president + split
Congress is 100% positive" is **false on the price series** (5 of 6; 2011 is −0.003%), and a
6-for-6 bucket is a **13.5%** event under the null anyway (26.2% for *some* bucket of the six).
The "Rep president + Dem Congress is worst" leg reads p 0.072 / 0.041 as a *named* bucket and
**0.773 / 0.630** once compared against the distribution of the **minimum**, which is the
comparison actually made. The omnibus spread is p 0.59 / 0.36. ⚠ **The residual premise is
BLOCKED, not excluded**: to reject at 5% the max−min spread would have to exceed **21.9pp (iid)
/ 18.3pp (block)** against an observed 12.1pp, so this design is blind to any regime effect
smaller than roughly 18 annual percentage points.
**Scope:** measurement only — `^GSPC` `1d` OHLCV, 1953–2025 calendar-year price returns.
One code change came with it: `analytics/data_sync.py` now pages (below).

## The claim

Filed 2026-08-19 from [@benjaminjcowen, 2026-05-13](https://www.youtube.com/watch?v=9a-LA7tm8Mo&t=196s):
annual US-equity returns condition on **which party holds which branch**, not merely on position
in the 4-year cycle. Six buckets over 1953–2025, sample sizes read off his on-screen table.
The inbox row pre-registered the null it needed: *"shuffle the regime labels across years and ask
how often a 6-year bucket comes back 100% positive."*

## The data had to be built first — and the builder was truncating

`^GSPC` held **4,896** daily bars (2007-03-01 →) against a claim that starts in 1953. yfinance
serves **24,774** (1927-12-30 →), and the gap was not a data limit: `data_sync.backfill` was a
single `fetch_bars` call, and `fetch_bars` keeps the **first** `BARS_MAX_LIMIT = 5000` bars at or
after its start. A deep `--since` therefore stored 1927–1947 and silently left every later bar
missing — the failure mode reads exactly like a symbol with no recent history, and the run banner
says `Backfill complete`. `backfill` now pages until a short page arrives
(`tests/test_data_sync.py::TestBackfillPaging`, mutation-checked: two of its three legs fail
against the old single-call implementation). Post-fix: 24,774 rows, no year in 1953–2025 thin.

## Method

Calendar-year price return from the last print of each year. Presidency attributed to the
president inaugurated in the year; Congress by the 2-year session. **One debatable cell** — the
107th Senate, where Jeffords switched in June 2001 — is encoded D, and that is the cell that
reproduces his n=9 / n=10 split.

Five pre-registered statistics against two nulls, both of which move the **labels** and never the
return series: **N1** an iid shuffle (B=20,000, bucket sizes preserved) and **N2** a circular
shift of the label vector over all 72 non-trivial offsets (exact). N2 exists because party control
arrives in multi-year **blocks**, so an iid shuffle destroys the run structure and would over-reject
whenever a good decade sits inside one regime — the non-independence the inbox row flagged.
Here it did not bind: N2 is if anything *more* permissive than N1 on the one marginal leg.

## The replication is exact, which is what makes the rest usable

| Bucket | n | n (his) | mean | mean (his) | median | median (his) | %pos | %pos (his) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Dem sweep | 18 | 18 | 0.080 | 0.080 | 0.107 | 0.105 | 0.722 | 0.720 |
| Dem pres + split | 6 | 6 | 0.170 | 0.170 | 0.184 | 0.183 | **0.833** | **1.000** |
| Dem pres + Rep Congress | 8 | 8 | 0.163 | 0.162 | 0.199 | 0.197 | 0.750 | 0.750 |
| Rep sweep | 9 | 9 | 0.133 | 0.133 | 0.136 | 0.131 | 0.778 | — |
| Rep pres + split | 10 | 10 | 0.073 | 0.075 | 0.147 | 0.147 | 0.700 | — |
| Rep pres + Dem Congress | 22 | 22 | 0.049 | 0.049 | 0.040 | 0.040 | 0.682 | 0.680 |

All six sample sizes and every mean and median land on his table. **One cell does not**, and it is
the one he leads with: the n=6 bucket is 5 of 6 positive, not 6 of 6. Its years are
2011 **−0.0000**, 2012 +0.134, 2013 +0.296, 2014 +0.114, 2023 +0.242, 2024 +0.233 — the whole claim
rests on 2011, a **−0.003%** price year that a total-return series would score positive. Every
other `%pos` in his table matches the price series, so the table is not internally consistent about
which series it is on.

## The nulls

| Statistic | Observed | p (N1 iid) | p (N2 block) |
| --- | --- | --- | --- |
| S1 spread (max − min bucket mean) | 0.1208 | 0.593 | 0.356 |
| S2 the n=6 bucket is 100% positive | 0 (false) | 1.000 | 1.000 |
| S3 Rep-pres+Dem-Congress mean, as a **named** bucket | 0.0491 | 0.072 | 0.041 |
| S3m the **minimum** bucket mean (selection-corrected) | 0.0491 | 0.773 | 0.630 |
| S4 F-like between/within | 0.9823 | 0.436 | 0.206 |

**S3 against S3m is the entire finding.** The same number is marginal at 0.041 when you ask "is
*this* bucket low?" and 0.630 when you ask "is the *lowest of six* buckets this low?" — and the
second is the question, because the bucket was chosen for being worst. Nothing here survives it.

For the headline: under N1 a *named* 6-year bucket comes back 100% positive **13.5%** of the time
and *some* bucket of the six does **26.2%** of the time, at a 72.6% base rate of positive years.
Even had 2011 gone the other way, a 6-for-6 bucket is a one-in-four event with no effect present.

**His own outlier caveat does not touch the statistic.** Dropping 1954 (+45.0%) moves Rep sweep
+0.133 → +0.094 and leaves the spread at 0.1208 unchanged, because the extremes sit in two other
buckets. p_N1 0.578.

## Why this is EXCLUDED-as-filed and not a powered null

To reject at 5% the observed spread would have to clear **0.219 (N1) / 0.183 (N2)**; it is 0.121.
Per-bucket 95% CIs run ±6.7pp (n=18) to ±10.9pp (n=8). So the design cannot see a regime effect
below roughly 18 annual percentage points, and *no* plausible effect is that large — the claim's
own numbers span 12pp. **This is the `powered_null` distinction, and it lands on the BLOCKED side**:
the specific claims are refuted, the general premise is untested and untestable at annual
resolution with 73 observations partitioned six ways. More years is the only lever, and there are
no more years.

## What this does not establish

- **Nothing about H-001/H-002.** Their primitive is *position* in the 4-year cycle, a different
  variable, and the deep backfill unblocks them rather than answering them.
- **Nothing on a total-return basis.** We hold price only; a dividend series would move every
  `%pos` upward and would make the n=6 bucket 6 of 6 — which is the point, not a fix.
- **Nothing about the calendar itself**, which is now a reusable, exactly-validated artifact.

Reproduce: `PYTHONPATH=. poetry run python docs/plans/scripts/h021_party_regime_annual_returns.py`
(gitignored; seed 20260819).
