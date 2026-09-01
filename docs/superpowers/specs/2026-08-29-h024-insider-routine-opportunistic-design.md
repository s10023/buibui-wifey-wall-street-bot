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
