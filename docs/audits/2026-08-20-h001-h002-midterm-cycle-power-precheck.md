# H-001 / H-002: the midterm-cycle rows are BLOCKED on power, and no backfill reaches them

**Date:** 2026-08-20
**Verdict:** **BLOCKED — both rows, permanently for H-001 and effectively for H-002.** The
effects run in the claimed direction in every cell measured, and every one sits **below** its
own minimum detectable effect. Detecting the *observed* H-001 effect on the statistic the claim
actually names (back-half drawdown) needs **307 midterm years — 1,227 years of market history**
against the 96 that exist. H-002 needs **346 midterm years (1,385 years)**; gold's usable panel
is **6 midterm years** and cannot grow. Neither is a data-availability block: `^GSPC` is already
at its yfinance floor and a `GC=F` backfill triples the bars while moving the verdict not at all.
**Scope:** pricing only. No study was run, no sleeve built, no parameter chosen.

## Why this ran

PR #237 removed the blocker these two rows were waiting on — `^GSPC` went from 4,896 daily bars
(2007 →) to **24,774 (1927-12-30 →)** once `data_sync.backfill` stopped truncating at 5,000.
The handoff queued H-001/H-002 as "unblocked" and attached a warning: *price the unit and the
detectable effect BEFORE the n*, because H-021 had just died on exactly that axis. This is that
pricing step. It is the second use of the discipline `/research-distil`'s G3 gate exists for,
and the first where the answer was available before any code was written.

## The unit, and why the tracked tool could not price it

The unit is a **calendar year**, classified midterm (`year % 4 == 2`) or not. That is the whole
reason these rows are hard: the sample grows at one observation per year and four years per
midterm year, so no amount of intraday depth adds a single unit.

⚠ **`tools/distil_power.py` does not cover this shape and was not used.** Its `--units` choices
are `per_trade | per_alert | per_book_day`, and its model prices a **Sharpe against the DSR
gate** — the right instrument for a sleeve, the wrong one for a calendar-conditioning claim that
has no book, no trades and no Sharpe. Passing one of the three existing units would have been
precisely the defect its own docstring exists to prevent: *"a figure that looks portable and
silently changes meaning with the panel."* The minimum detectable effect below is therefore the
standard two-sample formula, `MDE = (z_0.975 + z_0.80) · sd · sqrt(1/n₁ + 1/n₂)`, at 80% power
and two-sided α = 0.05, with `sd` **measured from the panel** rather than assumed.

## H-001 — US equities, midterm-year phase pattern

The claim: a shallow early-year correction, a rally to new highs, then a **larger 10–20% drop in
the back half**, leaving the market weak into year-end. Cited instances 2010, 2014, 2018, 2022.

`^GSPC` `1d`, complete calendar years only (2026 is excluded — 157 bars, and its back half has
not happened). **24 complete midterm years against 74 non-midterm**, 1928–2025.

| panel | statistic | n mid / non | sd | MDE (80%) | observed | |
| --- | --- | --- | --- | --- | --- | --- |
| 1928–2025 | back-half max DD | 24 / 74 | 9.67pp | 6.36pp | −1.78pp | below MDE |
| 1928–2025 | full-year max DD | 24 / 74 | 11.47pp | 7.55pp | −4.87pp | below MDE |
| 1928–2025 | day-of-year of peak | 24 / 74 | 134.2d | 88.3d | −43.3d | below MDE |
| 1946–2025 | back-half max DD | 20 / 60 | 7.09pp | 5.13pp | −3.08pp | below MDE |
| 1946–2025 | full-year max DD | 20 / 60 | 8.64pp | 6.25pp | −5.46pp | below MDE |
| 1946–2025 | day-of-year of peak | 20 / 60 | 131.6d | 95.2d | −53.6d | below MDE |
| 1980–2025 | back-half max DD | 11 / 35 | 8.05pp | 7.80pp | −2.80pp | below MDE |
| 1980–2025 | full-year max DD | 11 / 35 | 9.76pp | 9.45pp | −3.42pp | below MDE |
| 1980–2025 | day-of-year of peak | 11 / 35 | 121.8d | 117.9d | −54.2d | below MDE |

**Every cell carries the claimed sign** — midterm years draw down more and peak earlier — and
**not one is detectable.** ⚠ **That 9-for-9 is not nine pieces of evidence**: the three panels
are nested (1980 ⊂ 1946 ⊂ 1928), so the agreement is close to one observation reported three
times, and the three statistics are functions of the same 98 price paths.

Years needed to detect the effect that was actually observed:

| cell | have | need | in years of history |
| --- | --- | --- | --- |
| **back-half max DD, 1928–2025** *(the claim's own statistic)* | 24 | **306.8** | **1,227** |
| back-half max DD, 1946–2025 | 20 | 55.5 | 222 |
| full-year max DD, 1946–2025 | 20 | 26.2 | 105 |
| day-of-year of peak, 1946–2025 | 20 | 63.1 | 252 |

⚠ **The one nearly-powered cell is nearly-powered because it discards the claim.** Full-year
maximum drawdown at 26.2 needed against 20 available is the only figure within reach — and it
drops the back-half timing, which is the entire content of H-001. A midterm year being a worse
year is not the phase pattern; the phase pattern is *when* inside the year. This is the same
shape as H-021, where the surviving leg only survived by being a different question.

### The encoding is a free parameter, and it widens the trial family

The measurement reproduces the pundit's own instances where his reading is unambiguous, which is
what makes the encoding trustworthy: 2018 measures a back-half drawdown of **−19.78%** with the
peak on **day 263 (Sep 20)** against his stated "topped Sep, −20%". Exact.

⚠ **Under the natural encoding — the year's highest close — only 1 of his 4 flagship instances
tops in the back half.**

| year | peak day-of-year | back-half max DD | his claim |
| --- | --- | --- | --- |
| 2010 | 363 (Dec 29) | −7.14% | "~17%" — the 2010 correction was **Apr–Jul**, not the back half |
| 2014 | 363 (Dec 29) | −7.40% | "topped Sep, −10%" — direction right, magnitude ~¾ of it |
| 2018 | **263 (Sep 20)** | **−19.78%** | "topped Sep, −20%" — exact |
| 2022 | 3 (Jan 3) | −16.91% | "topped mid-Aug, −19%" — mid-Aug was a **bear-market rally high** |

Reading 2010 and 2022 as instances requires "the top" to mean a *local* top chosen after the
fact. That is a free parameter, and a free parameter makes the power problem **worse**: it
multiplies the trial family the same result must survive, on a design that cannot clear a single
pre-registered comparison.

## H-002 — gold, midterm-year seasonal weakness

The claim: gold shows the same midterm-year weakness, bottoming Jun–Oct — with the batch-3
refinement that it bottoms on **day 187 of the year on average**. The pundit volunteers the base
rate's own limit: *"seasonality works about 70% of the time."*

**The instrument is `GC=F`, not GLD** — the inbox row's own correction, because Cowen quotes
spot throughout. Two separate depths matter here and they are not the same number:

- **In `analytics.db`: 2,168 bars, 2018-01-02 → 2026-08-16 — 2 complete midterm years.** That is
  the universe backfill floor (477 of 505 symbols share a 2018-01-02 first bar), not a data limit.
- **Available from yfinance: 6,516 bars, 2000-08-30 →** — 3× the history, and **6** complete
  midterm years (2002, 2006, 2010, 2014, 2018, 2022) against 19 non-midterm.

Priced at the **deeper, not-yet-ingested** panel, so the verdict cannot be blamed on the backfill:

| statistic | n mid / non | sd | MDE (80%) | observed | need n_mid | in years |
| --- | --- | --- | --- | --- | --- | --- |
| trough day-of-year | 6 / 19 | 120.4d | 157.9d | +45.2d | 73.3 | 293 |
| max drawdown | 6 / 19 | 6.7pp | 8.7pp | −1.2pp | 346.2 | **1,385** |

### Postscript — `GC=F` was deepened, and the ingested panel is thinner still

The backfill ran the same day (2,168 → **6,076** bars, 2000-08-30 → 2026-08-19), paging in two
writes, which is #237's fix working on a second symbol. **441 rows quarantined**, and they are
not spread evenly: 2003–2007 land at 161–196 bars a year against ~250, gold's patchy early
electronic-session era. So the **ingested** panel carries **5** complete midterm years, not the 6
yfinance advertises — 2006 comes in at 166 bars. The verdict above is priced at the more
generous 6 and therefore still holds *a fortiori*; the honest figure for anyone re-running it
against `analytics.db` is 5.

The directional read is mildly favourable — midterm-year troughs land on **day 145** on average
against **day 100** for other years, later as claimed, though 145 is well short of the stated 187
— and the MDE is **157.9 days on a 365-day axis**. A design that can only reject a shift of
nearly half a year cannot speak to a seasonal window at all.

## What this closes, and what it does not

**H-001 is BLOCKED, and it is the permanent kind.** H-021's residual premise was blocked because
*there are no more years*; H-001 is blocked by a factor of **12×** on its own named statistic.
`^GSPC` is already at its yfinance floor, so no ingestion changes this, and it is not
reopenable by a longer wait — four more midterm years take sixteen years and move `need` by
nothing that matters.

**H-002 is BLOCKED on power, but it sits on a data gap worth closing for an unrelated reason.**
`GC=F` is a **pundit-ledger symbol** that `make go-live` does not cover, and the DB holds 2018 →
where 2000 → is free. Deepening it does not rescue H-002; it makes the ledger's gold rows
scoreable against three times the history. **That is a separate, cheap, non-research action —
not a step toward this hypothesis.**

**Neither row is EXCLUDED.** No claim was refuted; the design was shown to be blind. Under
`audit_guard.CellVerdict.powered_null` the distinction is load-bearing and it lands on the
BLOCKED side both times: an untested cell establishes nothing, and a non-significant result here
would be INSUFFICIENT rather than "no effect".

## The transferable part

**Price the unit before the n, and price it against the statistic the claim actually names.**
Every cheaper statistic here was better powered *and* less faithful, and the gap between the two
is where a hypothesis gets quietly replaced by a testable neighbour that answers nothing. The
cost of learning this was an afternoon against H-021's session-arc, which is the second
consecutive time the pre-check paid for itself — and the first time it did so **before** any
data was ingested or any code was written.

⚠ **A repeated claim from one author does not add an observation.** H-001 has now been
corroborated in three consecutive ingest batches — restated in **11 of 14 videos** by the
inbox row's own counts (4 of 5, 4 of 5, 3 of 4) — and the sample was 24 midterm years
throughout. The corroboration count grew; `n` did not. The inbox
column that records "CORROBORATED AGAIN" measures the pundit's confidence, never the evidence.
