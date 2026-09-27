---
name: research-distil
description: >
  Distil books, GitHub repos and papers into AT MOST THREE power-priced,
  pre-registered hypotheses per run, routed into the existing thesis-inbox /
  mechanics-backlog intake behind ONE human review gate. Four gates reject on
  novelty (a filed no-edge verdict), new-information (a re-slice of held
  price/volume is 8-for-8 dead), power (must RUN tools/distil_power.py, never
  estimate) and cost (net of the ADV-bucketed drag). "Unreachable, do not
  build" is a SUCCESSFUL output. Invoke when the user says "/research-distil",
  points at a book-to-skill slug, names a repo or paper to mine, asks "what
  should we test from this book", or wants research turned into a testable
  hypothesis.
---

# Research distil

Turn a distilled source into a small number of hypotheses this system can actually resolve — and say
so plainly when it cannot resolve any.

## Why this throttles

`docs/plans/thesis-inbox.md` opens with the sentence that decides this skill's entire shape:

> **Capture is not a commitment to test.** The free-data edge arc is CONCLUDED.

Trial count dominates the power bar far more than sample size does: widening the trial family costs
far more reach than growing n ever buys back. So the obvious version of this skill is actively
harmful — reading three books and emitting forty hypotheses does not accelerate research, it inflates
the trial family until every cell is unreachable, including ones that would have passed on their own.

The job is to throttle, not to amplify. A run that reads a 600-page book and emits one hypothesis has
done its job. A run that emits zero, with reasons, has also done its job.

## Flow

1. **Invoke** — `/research-distil <source> [<source> ...]`. A source is a book-to-skill slug
   (`~/.claude-personal/skills/<slug>`), a repo (local path or `owner/name`), a paper (URL or local
   PDF), or a free-text claim.
2. **Extract** — dispatch one `sonnet` subagent per source with the inline rubric below. It returns
   candidate claims only, applies no gates, issues no verdicts. Book and repo bytes never enter the
   main context. Only two subagents run at once — pipeline more than two sources by hand in pairs.
3. **Gate** — the main thread applies G1 → G4 in order, cheapest rejection first, running
   `tools/distil_power.py` for every G3 (drag-shaped mechanics rows excepted — see G3). Before
   accepting an abstract-only source, hunt the full text: a paywalled paper is often available as a
   dissertation chapter, working paper, or repository deposit, and can reveal a constraint the
   abstract omits. File abstract-only only after that hunt fails, and record the failed hunt in the
   row so the next session does not repeat it.
4. **Review** — present one consolidated digest for the whole batch: survivors and rejections, each
   with its citation. Write nothing before the operator approves.
5. **Route** — on approval only.

## Inline extraction rubric

Paste this verbatim into each extraction subagent. It must be self-contained: the subagent does not
read the SoT, memory, or this file.

> You are extracting candidate claims from one source for a trading-research intake. Return claims
> only — no verdicts, no rankings, no recommendations, and no judgement about whether a claim is good.
>
> A claim qualifies only if it is **falsifiable against price, volume, or an external data series**.
> Discard advice about discipline, psychology, position-sizing philosophy, or anything that cannot be
> tested on a data series.
>
> Return a JSON array. Each element:
>
> - `claim` — one sentence, in the author's own terms.
> - `mechanism` — why the author says it works. `null` if none is given. A claim with no mechanism is
>   still valid; say so rather than inventing one.
> - `data_required` — the series needed to test it, named concretely (e.g. "daily OHLCV per symbol",
>   "monthly non-farm payrolls", "GICS sector per member"). "Market data" is not an answer.
> - `author_effect_size` — any number the author states (Sharpe, win rate, annual return, t-stat),
>   verbatim with its units. `null` if none.
> - `citation` — chapter/section/page, or file and line for a repo.
>
> At most 12 claims per source. Prefer the specific over the general: "12-month momentum, skipping
> the most recent month, top decile" is a claim; "trends persist" is not.
>
> Return bare JSON with no prose and no code fence. If you cannot comply exactly, return the JSON
> anyway — malformed output is tolerated at the consuming end, missing output is not.

## The four gates

Applied on the main thread. A claim must survive all four. Every rejection is written down with its
citation — a rejection is a result, not a discard.

### G1 — Novelty

Reject anything already answered. This gate does the most work, because a trading book will suggest
most of these. Authority: `CLAUDE.md` § "Sleeve verdicts" and the SoT's § "Deliberately NOT queued".

| Closed | Verdict |
| --- | --- |
| Time-series trend / EWMAC on equities | **G2 FAIL** — portfolio Sharpe −0.05, negative even pre-cost, so a signal failure rather than a cost one |
| Cross-sectional momentum (12-1, decile) | **G3 FAIL** — −0.156 @2bps, negative at 0bps; `corr_to_trend` +0.62, so not a diversification win either |
| Residualised cross-sectional momentum | **FAIL** — committed cell +0.15 @2bps, DSR 0.44, boot_lo < 0 |
| Low-beta / betting-against-beta | **FAIL** — −0.069 @2bps, DSR ~0.03; realized-beta guardrail fired (β +3.9) |
| Cross-asset TSMOM on free ETF proxies | **FAIL (clean)** — +0.41 cost-free, never ≥ 0.7; PBO ~0.79. The β guardrail held, so the premium is simply too weak here |
| PEAD-lite / earnings drift | **FAIL** — +0.10 @2bps, DSR 0.20; β guardrail fired (governor saturation on sparse daily cohorts) |
| Gap proximity / gap-fill as a book | **EXCLUDED, direction REFUTED** — the magnet returns −0.460 cost-free, so gaps continue rather than revert; ~211× daily gross turnover |
| Velocity alternation (depth / duration) | **EXCLUDED as a null** — β guardrail fired, so read hedged −0.169 at alpha t −0.49; corr +0.499 / +0.546 to its own component controls |
| New boolean TA detectors; `tp_r` / gate / threshold sweeps | **FROZEN** — an inherited category verdict, and a policy rather than a measurement |
| Exit tuning as a P&L lever | **BOUNDED** — ceiling +0.368R of paired uplift, and it buys no measurably profitable book |

Rejection text must name the verdict document.

A guardrail firing means the construction failed its own neutrality precondition, so that cell is not
evidence about the underlying premise — a G1 rejection citing one of those rows must say so, or it
over-claims.

The TA freeze binds only the equity signal engine's boolean detector book at `4h`/`1d`/`1wk` — not a
regime classifier, a sector aggregate, or a claim on a bar this repo doesn't carry (H-014 was
reclassified out of it on this reading). Send a near-match to G2/G3 rather than over-applying G1.

Read a filed "no edge" as "no effect was found", never "an effect was ruled out". The warning-value
audit found that awarding the null on `n >= min_n` called 11 of 12 cells decided, while under the
honest predicate 0 of 11 survive at a half-width median 4.1× the bar — this doesn't reopen those
cells, it governs how the rejection is worded.

### G2 — New information

Does the claim need data we do not already hold?

- **A re-slice of held price / volume → REJECT**, citing the free-data arc's honest exit
  (`docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`). Eight sleeves have run on that
  substrate and every one came back non-positive.
- **Free-but-unheld data → PASS**, naming the acquisition cost and the exact series. The stack is
  yfinance-OHLCV-only, so "free" and "held" are not the same question: H-010's recession markers need
  FRED (`PAYEMS`, `ICSA`, `FEDFUNDS`), which costs a client rather than a subscription.
- **Paid data → PASS, tagged `DATA-BLOCKED`.** The data-cost policy is free-first and pays only when
  a gate needs it; delisted-ticker history is paywalled and the standing decision is to bound the
  claim instead of buying it.

New data is necessary, not sufficient. The cross-asset sleeve ran on genuinely new instruments, its
guardrail held, and it still failed cleanly at +0.41. Passing G2 buys a test, not an edge.

### G3 — Power

Run the tool. Paste the real output into the row. No estimate, no recollection, no arithmetic done by
the model.

```bash
PYTHONPATH=. poetry run python tools/distil_power.py \
  --units {per_trade|per_alert|per_book_day} \
  --sr-footing {per_obs|annual} [--periods-per-year P] \
  --n-obs N --n-trials K --sr-variance V \
  [--n-series S --n-eff E] [--sd SD] [--bar R] [--corpus-best C] \
  [--skew S] [--kurtosis K]
```

`PYTHONPATH=.` is required — the bare invocation fails with `ModuleNotFoundError`.

`--sr-footing` is mandatory. PSR runs per observation (`z = (sr − sr_benchmark)·√(n_obs−1)`), and
every sleeve Sharpe this repo files is annualized, so a sleeve-anchored run is
`--sr-footing annual --periods-per-year 252` with `n_obs` in sessions: the tool divides
`--sr-variance` by the periods, scales the bar back up, and prints both footings. Under `annual`,
`--corpus-best` and `--bar` are annualized Sharpes and `--sd` is refused. Use `per_obs` only when
every Sharpe-valued input is already per observation. On `per_book_day` the tool refuses a `per_obs`
declaration whose implied annual dispersion exceeds 3; on `per_trade` and `per_alert` nothing can
check it, so the footing is yours to get right. Mixing footings priced H-023 and H-024 at 0.3047 per
day, 4.84 annualized, and printed REACHABLE where the consistent bar is 0.83. Audit:
`docs/audits/2026-09-27-distil-power-units-retraction.md`.

There is no single filed corpus best — derive the one your units call for, from two non-interchangeable
anchors: in per-alert R units, the exit A/B's **+0.368R** paired uplift ceiling (a difference between
arms, never a profitable book); in sleeve-Sharpe units, cross-asset `broad_ls` at **+0.41 cost-free
annualized**, against `GATE_SHARPE` = 0.7 annualized — both need `--sr-footing annual`.

`--units` is mandatory with no default — a wrong bar-count assumption (e.g. treating `4h` RTH as 6
bars/day instead of 2) silently mislabels part of the panel, the way `regime.py`'s inherited crypto
assumption did before its window was corrected. Never reuse a filed deflator on a different panel —
pass `--n-series`/`--n-eff` for the panel actually in play. They must be supplied together; one alone
is a declared error.

Worked example, live-ledger scope:

```text
distil_power - G3 power gate
  units             per_alert
  Sharpe footing    per_obs
  n_obs (declared)  267
  effective n       (no deflator applied)
  trial family      20 trials, sr_variance 0.25
  gate target       DSR >= 0.95

  required Sharpe   1.077128
  required effect   +1.0771 per alert  (sd 1.0)
  VERDICT           REACHABLE

  null bar          +/-0.05
  CI half-width     0.1199  (best case, point estimate exactly 0)
  powered null      NOT LICENSABLE  (analytics.audit_guard.powered_null)
                    A null here would be INSUFFICIENT, never 'no effect'.
```

That example runs undeflated, so the printed n is an upper bound: alerts across the 13 watchlist
symbols are correlated, so pooling them carries fewer independent series than it appears. Measure the
deflator with `make wifey-n-eff` before quoting a G3 pass on pooled data — for reference, the
13-symbol watchlist runs `n_eff` ≈1.80 at `1d` (inflation ≈2.69×) and the 505-member universe ≈2.96
(inflation ≈13.05×). `n_eff × t_deflator²` must recover `n_series`, except when the deflator clamped
at a negative `rho`, where it is 1.0 by design and the identity breaks.

Three outcomes:

- **REACHABLE**, bar below the corpus best → survives to G4.
- **REACHABLE but the bar exceeds the corpus best** → survives only if the claim's own
  `author_effect_size` exceeds the bar. Say which.
- **UNREACHABLE** → write it down and stop. A successful output: "this cannot be resolved at our n
  against that trial family" is exactly the finding that saves a multi-session build. It is not
  underpowered — more data of that shape cannot fix it, only a smaller trial family can.

A mechanics claim that describes a drag or capacity constraint rather than an effect has nothing for
G3 to power — there is no effect size to detect. Record `G3 N/A (drag, no effect size)` on the row
rather than skipping silently; G4 is where a drag is priced.

If `sr_variance` for the trial family is unknown, the reachability leg cannot run and the claim is
`INSUFFICIENT`, not a pass. Derive it fresh each run as the sample variance of this repo's filed
sleeve Sharpes — never default it or reuse a figure from a prior run — and pass it with
`--sr-footing annual`, because those Sharpes are annualized. A pass is conditional on that
input, so stress it: check whether the required-Sharpe bar still clears the corpus best at the
derived variance, and note on the row if the trial family had to be fixed in advance to keep the
claim reachable.

On the `per_obs` footing, `--corpus-best` needs `--sd` — the comparison converts the required Sharpe into effect units, so
without `--sd` there is nothing to compare and the tool refuses rather than printing a bare
`REACHABLE`. Passing it alone is not a corpus comparison.

That bar does not apply to the powered-null leg, and for a null-shaped claim the null leg is the
decisive one. `--bar`/`--sd` compute the CI half-width from `n` alone (`Z_95 * sd / sqrt(n)`), so
`powered_null` returns a verdict whether or not `sr_variance` is known — the two legs fail
independently. A claim whose falsifiable form is a null — "X is *not* accompanied by Y" — is
confirmed only by licensing a null, so read that leg first and do not report `INSUFFICIENT` on an
unknown `sr_variance` before you have. H-004 was settled entirely this way: NOT LICENSABLE at n=5
across every `sr_variance` in {0.10, 0.25, 0.50, 1.00}, which is a **BLOCKED** verdict the three
outcomes above have no slot for. Audit: `docs/audits/2026-08-18-h004-sector-rotation-power.md`.

Price the unit before the n. A panel study's naive observation count is usually the wrong unit by two
orders of magnitude, and the inflated one flips `powered_null` to LICENSABLE — it buys the confident
answer, not merely a wrong one. H-004's honest unit gave n=5 against 1,020 episode-days, half of
which were a single 2022 episode — the same defect as the exit A/B's alerts-vs-session-days
(`docs/audits/2026-08-14-exit-policy-ab-v1.md`). State the unit, then justify why its members are
independent, then read n.

### G4 — Cost

Restate the expected edge net of the modelled drag. The drag is bucketed by trailing dollar ADV, from
`analytics/backtest/cost_model.py`:

```text
half-spread   < $5M ADV -> 20 bps   $5M-$50M -> 8   $50M-$500M -> 3   > $500M -> 1
impact        impact_coef * sigma_daily * sqrt(notional / ADV), charged per leg
commission    0 bps (US retail)
unknown ADV   falls to the WIDEST bucket
```

Do not substitute a flat `2 * (fee + slip) * entry / risk` — against this bucket ladder it
undercharges the thin tail of the 505-name universe by 20× on spread alone, and it passes silently
rather than erroring. The gap is cross-sectional breadth, not turnover, so a turnover-based check
would miss it.

Anything surviving only gross is rejected. Because drag scales inversely with stop width, a
comparison between cells of differing stop width inherits a bias, not merely a level shift — say
which direction it runs.

Every surviving row repeats two standing caveats:

- **Costs are modelled, not realised.** Raw stays exactly −1.0 = declared risk, so no figure here
  expresses gap risk, and every number is an optimistic bound whose error runs one way.
- **The live ledger is net of costs on the backtest's own basis:** `outcome_r` is
  `gross − outcome_cost_r`, priced by `live_cost_r` in `analytics/signal/outcome_backfill.py`, which
  mirrors `engine.Trade.pnl_r` exactly. So live-versus-backtest is a legitimate comparison — the
  residual caveat is `outcome_cost_r IS NULL`, which means UNPRICED, never "cost nothing". Audit:
  `docs/audits/2026-08-19-live-ledger-net-of-cost.md`.

## Refusals

The skill must refuse to:

- Write any row whose G3 output was not pasted from a real tool run.
- Write more than three survivors per run. If more survive, rank by priced power, write the top
  three, and log every dropped item with its reason — a silent cap reads as "covered everything" when
  it did not.
- Re-open a G1 match without an explicit written operator override.
- Propose a new boolean detector, or any `tp_r` / gate / threshold sweep — both are frozen;
  `/wfo-sweep` remains the only trusted path for `tp_r`.
- Emit parameters, thresholds, detector code, or any code at all.

## Routing

On operator approval only.

| Claim type | Destination |
| --- | --- |
| Alpha / signal | `docs/plans/thesis-inbox.md` |
| Execution, cost, sizing, mechanics | `docs/plans/mechanics-backlog.md` |

Both are gitignored, matching the streams `/ingest-x`, `/ingest-video` and `/ingest-feed` already
write to. This skill creates no new pipeline — books and repos are a fourth source into the same
intake.

Each row carries: the claim, its source citation, all four gate results verbatim, the pasted
`distil_power.py` output, and a Decision Log naming the observable that would reverse it.

A pre-registered spec in `docs/superpowers/specs/` is generated only on promotion, when the operator
picks a row to run. Untested ideas do not enter the spec corpus: the spec-reconcile denominator is
the full corpus, and every spec costs a reviewer a look to confirm there is nothing to reconcile. Run
`make docs-index` after adding one.

## Guardrails

- **G3's bar is an under-estimate, not a conservative one.** The `n_eff` deflator corrects
  cross-symbol correlation only. Alerts also cluster in time within a symbol — multiple timeframes
  and strategies firing on one move — and nothing deflates that. So the effective n printed is an
  upper bound and every bar is smaller than the true one. Never present a G3 pass as having margin it
  did not measure.
- **A descriptive claim is inert without its null, and the null is agreed before the run.** Both
  re-opened edge hunts proved it: "90.3% of gaps fill within 60 sessions" is true, and a matched
  placebo fills 88.9%, leaving a gap-specific lift of +1.5pp. A claim that survives G1–G4 but has no
  stated null does not get promoted.
- `sr_variance` unknown ⇒ `INSUFFICIENT`. Never a pass, never a default.
- `distil_power.py` errors or is missing ⇒ abort the run. Do not degrade to an estimate. The tool
  existing and being run is the whole point of G3.
- A named book-skill that is not installed ⇒ name it and stop. Do not fall back to reading the raw
  PDF; that is `book-to-skill`'s job and costs 24×–51× more context.
- Malformed subagent JSON is tolerated at the consuming end — a system-prompt directive is not a
  parser, and subagents do not reliably comply with "bare JSON, no fence" even when stated explicitly.
- A repo source returns claims about mechanism (how execution, sizing or routing is done), which
  route to `mechanics-backlog.md`.
- Treat every extracted claim as a hypothesis, including the author's own numbers. A published Sharpe
  is a claim about the author's panel, not ours.
