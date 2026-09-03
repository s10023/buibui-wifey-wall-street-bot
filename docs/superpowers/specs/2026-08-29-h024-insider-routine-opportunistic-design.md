# H-024 — routine vs opportunistic insider trades: build design + pre-registration

**Status: DESIGN, pre-registration FROZEN 2026-08-29 — no return has been looked at.**
Source row: `docs/plans/thesis-inbox.md` H-024 (filed 2026-08-27, sources re-read and verified
2026-08-29). Paper: Cohen, Malloy & Pomorski, *Decoding Inside Information* (NBER WP 16454;
figures verified against the WP full text). This is the first non-price sleeve in the repo: the
TA freeze does not bind (no boolean detector, no `DETECTOR_REGISTRY` touch), and none of the
eight closed sleeves overlaps it.

## The claim being tested

An insider is **routine** if she traded in the same calendar month in each of ≥3 consecutive
years, **opportunistic** otherwise; opportunistic trades carry information and routine trades do
not. WP headline (1986–2007 panel): opportunistic L/S 82 bps/mo VW (t=2.15) vs routine −20 bps/mo
(t=0.57); Fama-MacBeth opportunistic buy +90 bps (t=4.64) vs routine buy +14 bps (t=0.81). The
effect size cannot be assumed to port (pre-SOX filing lag regime); this build is an
out-of-sample test on 2018→present, which is the point.

## Pre-registration (frozen — changing any line below re-opens the trial count)

**Panel.** The 505-member research universe (`config/universe.json`), `1d` bars,
2018-01-02 → run date. Form 4 history fetched from 2015-01-01 so classification at the start of
2018 has the three preceding years it requires.

**Classification (CMP's rule, verbatim mechanics).** At the start of each calendar year,
an insider is *classifiable* if she made ≥1 open-market trade in each of the three preceding
years; a classifiable insider is **routine** if some calendar month contains a trade of hers in
each of ≥3 consecutive preceding years, else **opportunistic**. Unclassifiable insiders are
excluded. Every subsequent trade that year inherits the trader's label. Identity is
`rptOwnerCik` from the Form 4 XML — a mechanical key, no name matching. Open-market purchases
and sales only (transaction codes P and S); option exercises and private transactions excluded,
per CMP.

**Formation.** Calendar-time, monthly. At each month-end, form portfolios from trades **filed**
during that month (filing date, not trade date — post-SOX the lag is ≤2 business days, and
filing date is the only outsider-observable clock; CMP used trade date with a median 3-day lag
and verified availability, so this is the implementable reading, not a deviation). Hold 1 month
(or 3, per trial), value-weighted by market cap at formation. Rebalance monthly; 3-month holds
run as overlapping thirds (Jegadeesh–Titman).

**Trial family — exactly 4, fixed in advance** (matches the filed `distil_power` run: bar
0.3047 at 4 trials, `sr_variance` 0.0652, book-day unit, n_obs 2,174 and growing):

| Trial | Book | Hold |
| --- | --- | --- |
| T1 (primary) | Opportunistic buys − opportunistic sells, VW | 1 month |
| T2 | Opportunistic buys only (long), VW — the wife-sleeve deployable form | 1 month |
| T3 | Opportunistic L/S, VW | 3 months |
| T4 | Opportunistic buys only (long), VW | 3 months |

Equal-weighted forms are **refused in advance**: the WP's EW book (180 bps/mo, t=6.07, 2.2× VW)
concentrates in exactly the thin tail the FRL-2025 capacity result deletes and the cost model's
widest bucket prices — see the mechanics-backlog row. Trade-date formation is refused
(lookahead). Any fifth cell is a new pre-registration, not a robustness check.

**Placebo (control, not a trial).** Every trial runs its routine-arm mirror. The filed reversal
observable binds: if the opportunistic and routine arms are statistically indistinguishable on
our panel (paired difference, session-day cluster keys, CI containing 0), the classification
carries no information here and the row closes.

**Gates per cell.** `passes_sleeve_gate` as shipped (net Sharpe ≥ `GATE_SHARPE = 0.7`, DSR,
PBO, `boot_lo`), DSR priced at the 4-trial family. L/S cells (T1/T3) carry the realized-beta
guardrail via `beta_attribution` (`analytics/xsmom/diagnostics.py`); a fired guardrail voids the
cell as evidence, per house rule. Long-only cells (T2/T4) are **not** market-neutral and the
velocity lesson applies (`long_only` +0.649 gross hedged to +0.004): a T2/T4 pass counts as
signal only if the beta-hedged alpha t-stat is positive — declared here, before any data.

**Costs.** Shared-base `CostModel` (half-spread buckets 20/8/3/1 bps by trailing 20-day dollar
ADV, <$5M → 20 bps), ADV computed from our own bars so no name falls to the unknown-ADV default.
Short-leg borrow: the WP states no borrow figure; the cost model's short-side treatment applies
and the gap is declared rather than guessed. Costs are MODELLED, not realised.

**Survivorship — direction differs by leg, declared in advance.** `delisted: 0` of 505 members:
delisted names' terminal losses are absent. That **flatters the buy legs** (a bought name that
went to zero is deleted) and **understates the sell legs** (an opportunistic sale before a
delisting is the signal's best outcome, also deleted). Therefore: a T2/T4 (long-only) PASS is an
**upper bound** and says so in its verdict; a sell-leg null is licensable in arithmetic and not
in substance. This is the mirror of H-023's one-directional case — do not copy that row's
"bias runs against" sentence here.

**Reversal observables.** (a) The filed one: arms indistinguishable → row closes. (b) Data
floor: if fewer than 80% of fetched Form 4 filings for universe members parse cleanly to
(`rptOwnerCik`, transaction date, code, shares, price), the panel is unmeasurable — fix the
parser or close the row; no return is looked at until (b) passes.

## Data pipeline

**What exists**: `utils/edgar_client.py` — `fetch_company_tickers`, `ticker_to_cik`,
`fetch_submissions` (per-CIK submissions JSON, which lists form type per accession), a UA
helper and a JSON getter. **What is added** (paths are *to be created*): an `analytics/insider/`
package — `fetch.py` (Form 4 XML fetcher: filter submissions to form `4`, fetch each accession's
primary XML; SEC fair-access ≤10 req/s; resumable, cache-to-DB, one-shot backfill on the
hours scale for an estimated 50–150k documents across 505 CIKs × 11 years), `classify.py`,
`book.py`, `report.py` — following the `pead/` / `xasset/` sleeve shape (replay → report →
gates), not the detector shape.

**Storage**: a new `insider_transactions` DuckDB table via `analytics/store/schema.py`'s
migration list. House rules apply: writes through `connect_with_retry`, `_upsert` with explicit
`register`/`unregister`, no positional INSERT without updating the arity guard, and grep
`tests/` for positional INSERTs before adding any column. Classification is derived state —
recompute from `insider_transactions`, do not store it as a second source of truth.

**Tests**: no network — the fetcher takes a `client` and tests pass `MagicMock`; parser and
classifier are fixture-driven with positive controls that *move* (a synthetic routine trader
classifies routine; perturbing one month breaks the streak and flips the label — the vacuity
rules from `docs/audits/2026-08-13-vacuous-causality-guards.md` apply).

## Build phases (each is a complete PR-sized unit; design is this doc)

1. **Fetch + parse + store** — fetcher, XML parser, `insider_transactions`, tests; run the
   backfill; report observable (b) coverage. Done = coverage number in hand, no return computed.
2. **Classification** — `classify.py` + tests; report cohort shape (WP: ~55% of trades from
   routine traders — a wildly different split is a parser smell, not a finding).
3. **Book + report + audit** — daily VW book returns net of costs, the 4 trials + placebos,
   gates, `beta_attribution`; audit doc closing FOUND / BOUNDED / EXCLUDED / BLOCKED with the
   verdict as prose under a Verdict heading; `make db-update` not touched (no detector surface).

Phase 3 must not start until phases 1–2 are merged and observable (b) has passed — the
pre-registration freezes at this document, and the first look at a return happens inside the
gated report, nowhere else.

## Amendment 1 (2026-09-01) — how observable (b) is SAMPLED

**Additive. It changes no frozen line, not the ≥80% floor, not the trial count, and not any
cell.** The frozen text says *"fewer than 80% of fetched Form 4 filings for universe members
parse cleanly"* — it never said which filings get fetched for the measurement, and the first
pilot's default answered that question badly enough to be worth pinning here.

**What went wrong.** `--limit 15` takes the HEAD of `config/universe.json`, which is grouped by
sector. Measured 2026-09-01, that is fifteen Information Technology mega-caps carrying **18,549**
Form 4 documents, CRM (4,175) and ACN (2,922) alone being ~38%. A coverage figure from that slice
supports the gate in neither direction: parse failures concentrate among small and older filers,
none of which the head contains, so a PASS is not evidence the universe clears the floor and a
FAIL is not evidence it does not. It is also the slowest slice in the universe — at the measured
**2.1 documents/second** (round-trip bound against `www.sec.gov/Archives`, not the 0.12s
throttle), the head-15 run needed ~147 minutes to produce an uninformative number. Stopped at 6
symbols; 12,019 rows across AAPL/ACN/ADBE/AMAT/AMD/AVGO are retained and valid.

**How it is sampled instead.** Observable (b) is measured on a **strided, per-symbol-capped**
draw: `--stride N` spreads symbols across the sector-grouped file, and
`--max-filings-per-symbol N` takes an evenly spaced subset of each symbol's filings. Both spread
rather than truncate, for the same reason at two levels — sectors for symbols, filing vintage for
documents. ⚠ **A head cap on filings would bias the observable OPTIMISTICALLY**: `collect_filings`
returns newest-first and recent filings are the most uniform, so taking the first N measures the
easy end of the range. That is worse than no cap, since the floor exists to catch precisely the
documents it would drop. Pinned by `tests/test_insider_backfill.py::TestSampleFilings`.

**Why this is the better estimator, not merely the cheaper one.** (b) is a proportion, so its
precision comes from the document count (~1,000 pins it to about ±2.5% at 95%) while its
*validity* comes from filer diversity. Depth per company buys neither. Hence
`--stride 10 --limit 50 --max-filings-per-symbol 40` ≈ 2,000 documents across 50 companies in
every sector (~16 min), against 10,183 documents from 15 companies (~81 min) for a strided run
with no cap.

**Full-universe cost, measured rather than assumed.** The Data pipeline section's "estimated
50–150k documents across 505 CIKs × 11 years" is the right order but low at the top end: the 15
heaviest names alone are 18,549. At 2.1 docs/sec a 150k run is ~20 hours and 300k is ~40 — a
deliberate overnight job, and SEC's rate limit is a floor no parallelism moves. Phase 3's full
backfill should be scheduled on that basis.

## Amendment 2 (2026-09-02) — observable (b), MEASURED: 95.2% PASS

Additive like Amendment 1, and outside the frozen block. Phase 1's acceptance observable is
answered, so **phase 2 is unblocked**.

```bash
make wifey-insider-backfill ARGS="--stride 10 --limit 50 --max-filings-per-symbol 40"
```

**`parse coverage 95.2% of fetched filings (338 carried no non-derivative transactions) —
phase-1 floor is 80%: PASS`**, from 1,960 filings fetched across 50 companies, yielding 3,525
transaction rows, 1 missing CIK and 0 errors. ⚠ **Record the command with the number** — a
different sample gives a different, non-comparable figure, which is the whole reason #278
existed.

**What the 4.8% shortfall is.** 94 of the 1,960 filings carried at least one parse failure, and
**every failure reason the run printed was `missing price`**. ⚠ That bounds the residual rather
than enumerating it: the printer emits `outcome.failures[:2]`, so a third reason of some other
kind on a heavily-failing filing would not appear. A `missing price` transaction is usually a
gift or an award, which carries no price by construction — so the shortfall is concentrated in
exactly the transaction types the routine/opportunistic split does not trade on. Worth
confirming against `transaction_code` in phase 2 rather than assuming it here.

**A filing that parses cleanly but is EMPTY still counts as covered**, by design: 338 of the
1,866 clean filings carried no non-derivative transactions at all. That is the correct reading
of "parsed cleanly" — a Form 4 reporting only derivative activity is not a parse failure — but
it means the observable is a *parser-health* figure and never a *data-yield* one. The yield
number is the 3,525 rows.

**Sample shape.** 50 companies at `--stride 10` over the sector-grouped universe, so every
sector is represented; this is the coverage sample the head-15 pilot could not be. It does not
supersede the banked head-15 data (12,019 rows, 6 symbols), which stays valid and stays
sector-concentrated. After both, the table holds **15,487 rows / 54 symbols / 1,060 insiders** —
the two draws overlap on 2 names.

**Runtime was not instrumented**, so the spec's 2.1 docs/sec and the 20–40h full-universe
estimate above are **unrevised**. The run finished inside Amendment 1's ~16 min prediction;
that is an observation, not a new throughput measurement.

## Amendment 3 (2026-09-03) — phase 2 BUILT; the cohort shape is NOT YET MEASURABLE

Additive, outside the frozen block: it changes no trial, no gate and no cell. Phase 2's code
deliverable is done — `analytics/insider/classify.py` (the CMP rule), 24 tests, and
`make wifey-insider-cohort`. Its *reporting* deliverable is answered in the negative, and that is
the finding.

**The phase-1 sample cannot support phase 2, and the reason generalises.** Amendment 1 chose
`--max-filings-per-symbol 40` because observable (b) is a proportion whose validity comes from
filer diversity and whose precision comes from document count — depth per company buys neither.
That was right for (b) and is **exactly wrong here**: the classifier needs ≥1 trade in each of
three consecutive years *per insider*, so thinning a company's filings thins every one of its
insiders' calendars. Measured on the stored table:

| Draw | Symbols | P/S rows | Classified | Routine | Median trade-days per insider |
| --- | --- | --- | --- | --- | --- |
| Uncapped (head-15 pilot) | 6 | 4,769 | **46.0%** | 1,622 | 6 |
| Capped (`--max-filings-per-symbol 40`) | 47 | 1,455 | **3.5%** | **0** | 1 |

**97.7% of every classified row in the table comes from 6 of the 53 symbols.** ⚠ **The cap is not
merely lossy, it is DIRECTIONALLY BIASED**: a thinned calendar cannot exhibit a same-month streak,
so every insider the capped draw does manage to classify falls to *opportunistic* — 0 routine
across 47 companies. A pooled split over both draws is therefore a statistic about the 6 uncapped
names wearing a 53-name label.

**Reproducing the table** — recorded with the numbers, for Amendment 2's reason: a different draw
gives a different, non-comparable figure.

```bash
make wifey-insider-cohort                                                  # pooled, 53 symbols
make wifey-insider-cohort ARGS="--symbols AAPL,ACN,ADBE,AMAT,AMD,AVGO"     # the uncapped draw
```

The capped row is the complement of those six names over the stored symbols; `--symbols` takes an
explicit list, so it is one more invocation rather than a new flag. Both rows read
`Classified rows` over the printed population, and `routine` off the same block.

⚠ **The transferable rule, and it is Amendment 1's own lesson recurring one phase later: a sample
designed for one observable is not a sample for another.** Both were defensible draws; neither is
reusable without re-deriving the sampling from the new statistic's unit. The unit changed from
*document* to *insider-year*, and nothing in the stored data announces that.

**The number that does exist, and what it is not.** On the 6 uncapped mega-caps, routine is
**74.0% of classified rows** against the WP's ~55%. Under the spec's smell test that is a wide gap,
but it is **not evidence of a parser fault**, and it is not evidence of the absence of one either.
The leading candidate is the era: 10b5-1 scheduled sell plans dominate large-cap insider *selling*
in this window and are routine by construction, **95.8% of stored P/S rows are sales**, and CMP's
1986–2007 panel predates their ubiquity. ⚠ **That is a hypothesis, not a measurement** — nothing
here tested it, and Form 4's 10b5-1 marker was not read. Do not quote 74% as a panel figure; it is
six technology mega-caps.

**Observability divergence — SETTLED, no amendment owed.** The frozen rule classifies from trade
dates at the start of the year, which can consult a December filing that was not public on
1 January. Re-running the identical rule against only filings public by that date moves **1 of
6,981 (insider, year) labels (0.0%)**. The frozen trade-date reading stands on measurement rather
than on convenience; `require_filed_by_year_start` stays in the classifier as the probe that
produced this, not as a live alternative.

**The 4.8% shortfall is now attributable, but not yet attributed.** `parse_form4`'s failure string
records the transaction code (`txn N [code G]: missing price`), so the next backfill run can settle
whether the shortfall is gifts and awards as Amendment 2 supposed. ⚠ **It is not settled now** — a
rejected row is never stored, so no query against `insider_transactions` can reach it, and the
existing run's strings predate the change. The claim stays bounded and unconfirmed.

**Consequence for phase 3.** The full uncapped backfill is a **hard prerequisite, not a scale-up**:
at 3.5% classified the current table cannot form a single monthly cross-section on the 505-name
universe. The 20–40h overnight estimate is unchanged (still unrevised by instrumentation), and it
must run **without** `--max-filings-per-symbol`.
