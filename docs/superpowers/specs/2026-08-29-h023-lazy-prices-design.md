# H-023 — Lazy Prices (10-K/10-Q textual change): build design + pre-registration

**Status: DESIGN, pre-registration FROZEN 2026-08-29 — no return has been looked at.**
Source row: `docs/plans/thesis-inbox.md` H-023 (filed 2026-08-27, sources re-read and verified
2026-08-29). Paper: Cohen, Malloy & Nguyen, *Lazy Prices* (NBER WP w25084; figures verified
against the WP full text). Second non-price sleeve; sibling design:
`2026-08-29-h024-insider-routine-opportunistic-design.md`, whose EDGAR fetch core (submissions
JSON per CIK, ≤10 req/s fair access, resumable cache-to-store) is shared infrastructure —
whichever sleeve builds second reuses the first's fetcher.

## The claim being tested

Year-over-year textual change in a firm's periodic filings predicts returns: long the
least-changed quintile, short the most-changed, entering the month after the filing's public
release, holding 3 months on a monthly rebalance. WP headline: L/S 18–45 bps/mo EW, up to
58 bps/mo VW (t=3.59); Item 1A (Risk Factors) changes alone reach 188 bps/mo (t=2.76,
Table VII-A). Effect measured 1995–2014; this build is an out-of-sample test on 2018→present.

⚠ **The re-read's material caveat binds this design**: Figure 7 shows the long side's alpha
reverting quickly to zero in event time — the persistence is in the short leg — and the long
leg's standalone EW 3-factor alpha is +18 bps/mo (t=2.66). The wife-sleeve deployable form
(long non-changers) is the WEAK half of the documented spread, and this spec says so rather
than powering it on the headline.

## Pre-registration (frozen — changing any line below re-opens the trial count)

**Panel.** The 505-member research universe, `1d` bars, 2018-01-02 → run date. Filings fetched
from 2017-01-01 so every 2018 filing has its year-ago counterpart for the similarity pair.

**Signal.** Year-over-year similarity between a filing and the same firm's filing of the same
type one year earlier (10-K↔prior 10-K; 10-Q↔same-quarter prior 10-Q). Primary measure:
**Sim_Cosine** on token counts (the WP's headline Table II measure); Sim_Jaccard is computed as
a diagnostic correlation check, never a trial. Quintiles cut on the prior year's distribution
of similarity across all stocks, per the paper. Entry in the month after the filing's public
release (EDGAR acceptance date — the outsider-observable clock), 3-month hold, monthly
rebalance, value-weighted.

**Trial family — exactly 4, fixed in advance** (matches the filed `distil_power` run: bar
0.3047 at 4 trials, `sr_variance` 0.0652, book-day unit):

| Trial | Scope | Book |
| --- | --- | --- |
| T1 (primary) | Full document (10-K + 10-Q) | Q5 − Q1 L/S, VW |
| T2 | Full document | Q5 long-only, VW — the wife-sleeve deployable form |
| T3 | Item 1A only (10-K; annual formation) | Q5 − Q1 L/S, VW |
| T4 | Item 1A only (10-K) | Q5 long-only, VW |

Declared expectation, stated before any data: T2/T4 are **not expected to clear
`GATE_SHARPE = 0.7`** at the measured long-leg effect size (~18 bps/mo); they are in the family
because deployment would select on them, and an honest family prices that look. A deployment
decision requires an L/S pass **plus** long-leg attribution showing the long side carries its
share — Figure 7's reversion makes the opposite the base case. Equal-weighted forms and any
fifth cell (other similarity measures, other holds) are refused in advance.

**Pre-registered null (mechanism check, not a gate).** No announcement-window return: the
spread accrues gradually over ~6 months and does not reverse (WP Figure 7). An
announcement-day jump means a different, already-priced phenomenon and voids the mechanism
claim even if a gate passes.

**Gates per cell.** `passes_sleeve_gate` at the 4-trial family; realized-beta guardrail via
`beta_attribution` on L/S cells; long-only cells require positive beta-hedged alpha t (the
velocity lesson, same as H-024's design). Statistical unit is the book day; any per-trade
cross-section carries session-day `cluster_key`s.

**Costs.** Shared-base `CostModel`, ADV from our own bars. Quintile monthly turnover is
6.6–8.7%/mo in the WP (Table III) — two orders below gapfill's 211×, so spread cost is not the
expected constraint. Short-leg borrow: the WP's own Table III shorting fees, 72–92 bps
annualized — modelled as a 92 bps/yr drag on the short leg (conservative end), declared here
because `CostModel` carries no borrow input.

**Survivorship — one-directional headline, two-sided fine print.** `delisted: 0` of 505: the
paper shows changers predicting bankruptcies (Appendix A-7), and those firms' terminal losses
are absent, so the short leg is understated and **the bias runs against the L/S hypothesis** —
an L/S null is licensable in arithmetic, not substance (as filed). The fine print: long-only
cells are still flattered (a delisted non-changer's loss is equally absent), so a T2/T4 pass is
an upper bound. Both sentences go in the verdict.

**Reversal observables.** (a) As filed: if the Item 1A parser succeeds on under 70% of
505 × 8 years of 10-Ks, T3/T4 are unmeasurable and the Item 1A half closes. (b) Degeneracy: if
the prior-year quintile cut fails to produce five strictly separated boundaries (mega-cap
filings may cluster near-identical), the cut is degenerate — report the similarity
distribution and close or re-scope before any return is looked at. (c) Coverage floor for the
full-document half: under 80% of expected filing-pairs resolved → fix the fetcher or close.

## Data pipeline

**Fetch**: per-CIK submissions JSON (exists: `utils/edgar_client.fetch_submissions`) filtered
to forms `10-K`/`10-Q`, then each accession's primary document from EDGAR Archives. Volume:
505 firms × ~4 filings/yr × ~9.7 years ≈ 20k documents, raw text on the GB scale — raw filings
cache to a **gitignored** on-disk store (they are refetchable, so the backup denylist does not
need them); DuckDB holds only extracted sections and scores.

**Parse**: strip HTML/exhibits to body text; Item 1A extraction by item-heading boundaries
(`ITEM 1A` → `ITEM 1B`/`ITEM 2`). Known hazards for phase 1 to handle, not discover: 10-Qs
carry risk factors in Part II and often as "no material changes" incorporation by reference —
those quarters produce no Item 1A pair by design and must be counted, not silently dropped
(observable (a) is measured on 10-Ks only for this reason); heading variants and HTML-mangled
headings are the main parse-failure class.

**Similarity + storage**: token-count cosine per pair, computed once into a
`filing_similarity` table (via `analytics/store/schema.py`'s migration list; house write rules
apply — `connect_with_retry`, explicit `register`/`unregister`, arity guard, grep `tests/` for
positional INSERTs). Raw text is not stored in DuckDB. Derived quintiles recompute from the
table — no second source of truth.

**Package**: `analytics/lazyprices/` — `fetch.py`, `parse.py`, `similarity.py`, `book.py`,
`report.py` — the `pead/`/`xasset` sleeve shape (paths *to be created*). Tests: no network,
fixture filings with known section boundaries, positive controls that *move* (perturbing a
token changes the score; breaking a heading fails the parse **loudly** into the coverage
count — the vacuity rules apply).

## Build phases (each a PR-sized unit; design is this doc)

1. **Fetch + parse + store** — fetcher (reuse H-024's core if it exists by then), section
   parser, `filing_similarity` schema, tests; run the backfill; report observables (a) and (c).
   Done = coverage numbers in hand, no similarity ranked, no return computed.
2. **Similarity + quintile construction** — scores, prior-year quintile cuts, degeneracy check
   (observable (b)); report the similarity distribution.
3. **Book + report + audit** — daily VW book returns net of costs (+ the declared borrow
   drag), the 4 trials, gates, `beta_attribution`, the Figure-7-shape mechanism check; audit
   doc closing FOUND / BOUNDED / EXCLUDED / BLOCKED with the verdict as prose under a Verdict
   heading.

Phase 3 must not start until phases 1–2 are merged and observables (a)–(c) have passed. The
first look at a return happens inside the gated report, nowhere else.
