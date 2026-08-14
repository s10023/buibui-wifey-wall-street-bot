# Exit-Policy Replay A/B v1 (2026-08-14)

**Verdict: BOUNDED — exit management is worth at most +0.368R per trade on this
book, and that ceiling buys a book whose own mean R still does not clear zero.
Every arm beats the `fixed` baseline on a paired CI clear of zero, the whole
effect is the TIME lever (the composite is WORSE than the time-stop alone), and
the paired result survives a multiplicity bar. But no arm's own mean R clears
zero once days rather than alerts are the unit, and the best point on the swept
time-stop grid fails that same bar. So this closes a direction rather than
opening one: exits are not where the missing equity edge is. The re-run trigger
is ledger growth, not a code change.**

Port of parent PR #437 (`fbf6607`). Step 1 is
`docs/audits/2026-08-14-mfe-timing.md`.

**Reframed 2026-08-14b** under the standing rule that every audit answers the
edge question. v1 led with the paired uplift and buried what that certifies;
three things it lacked are now in it — the arm-level significance table
(§Arm-level), the swept-maximum correction (§Multiplicity), and the clustering
that bounds effective n (§Effective n). One of its tables was **mislabelled**
and its "mechanism is decay" section is rewritten (§Mechanism); nothing in the
headline result changed.

## How this was produced

- Engine: `analytics/exits/{policies,replay}.py`, copied verbatim from upstream
  (no `portfolio/` dependency); verdict layer `analytics/exits/audit.py`.
- Command: `make wifey-exit-replay` (= `tools/exit_audit.py --replay`), read-only.
- Sensitivity + attribution: `docs/plans/scripts/exit_time_stop_sensitivity.py`.
- Significance, multiplicity, clustering, costs, and the corrected open-position
  curve: `docs/plans/scripts/exit_ab_arm_significance.py`, which calls the
  production `resolve_ledger_under_policy` / `replay_exits` / `implied_tp_r` /
  `block_bootstrap_ci` rather than restating their arithmetic, and asserts its
  own re-implemented fetch loop reproduces production `fixed` on all 267 rows
  before printing anything (PASS).
- Population: the same **267** resolved scoreable alerts, `4h` 177 / `1d` 90.

**Metric substitution.** Upstream judged on portfolio Sharpe from its P1
`PaperBook`. This fork has no `portfolio/`, so the headline is per-trade R Sharpe
(`stats_overfit.sharpe_ratio`, **not** annualized) and the decisive leg is a
paired block-bootstrap CI on the per-alert R uplift. DSR is a stamp;
`passes_sleeve_gate` is deliberately not called. The full reasoning, including why
both of its legs would be wrong here, is in `audit.py`'s module docstring.

**Positive control: the `fixed` arm reproduces the live resolver's label on 267 of
267 rows.** That is what makes the other three arms interpretable.

## Results

| policy | n | Sharpe_R | avg_r | win% | expiry% | hold | DSR | ΔSharpe | uplift_R | 95% CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fixed | 267 | −0.113 | −0.176 | 16.9 | 9.0 | 7.49 | 0.000 | — | — | — |
| time_only | 267 | +0.110 | +0.141 | 8.6 | 53.9 | 2.92 | 0.465 | +0.223 | **+0.317** | [+0.173, +0.468] |
| be_partial | 267 | +0.001 | +0.001 | 14.2 | 7.1 | 5.99 | 0.030 | +0.113 | **+0.176** | [+0.060, +0.291] |
| composite | 267 | +0.123 | +0.121 | 8.2 | 46.4 | 2.84 | 0.545 | +0.236 | **+0.297** | [+0.153, +0.443] |

`time_only` = time-stop at the p90 floor, no locks. `be_partial` = breakeven-at-1R
plus partial-50%-at-1R over the full window. `composite` = both.

**The decomposition is the finding.** `time_only` (+0.317R) beats the full
`composite` (+0.297R). Adding the lock levers to a time-stop makes the result
worse, because the partial caps the winners the time-stop had already spared.
`be_partial` alone captures barely half the available uplift.

**This corrects the direction implied by the n=264 diagnostic.** That audit found
43.9% of losses reached ≥1R before stopping and read it as a breakeven/trail
candidate. The replay says the breakeven/trail reading is the *weaker* half:
across all 141 `4h` losses, `be_partial` recovers a mean **+0.606R** and a plain
time-stop that never arms a stop at all recovers **+0.705R**. **A diagnostic that
shows a lever COULD work does not rank it against the levers it did not
measure.**

### Arm-level: the CI above is PAIRED and cannot say an arm makes money

The `95% CI` column tests `arm − fixed`. A paired CI clear of zero certifies
**"A beats B"** and is silent on **"A is profitable"** — two different questions,
and the second is the one an edge claim needs. Each arm's own mean R against
zero, same `block_bootstrap_ci` as the paired leg, plus a CI that resamples
**ET session days** instead of alerts:

| arm | n | avg_r | sd | t | boot 95% CI | day-clustered CI |
| --- | --- | --- | --- | --- | --- | --- |
| fixed | 267 | −0.176 | 1.557 | −1.84 | [−0.351, +0.006] | [−0.373, +0.062] |
| time_only | 267 | +0.141 | 1.277 | +1.80 | [−0.027, +0.318] | [−0.084, +0.397] |
| be_partial | 267 | +0.001 | 1.098 | +0.01 | [−0.127, +0.132] | [−0.174, +0.176] |
| composite | 267 | +0.121 | 0.989 | +2.01 | [+0.003, +0.246] | [−0.042, +0.298] |

**Not one arm's own mean clears zero on the clustered CI**, and only `composite`
clears it on the iid one (`[+0.003, +0.246]`, i.e. by 0.003R). The baseline's
**negative** expectancy is equally unproven — `fixed` sits at t = −1.84 with a CI
that contains zero — so "a real improvement on a losing book" overstates both
halves: the book is not measurably losing and the arms are not measurably
winning. What is measurable is the *difference*, which is why the paired leg is
the only one v1 should have led with.

The paired leg itself is robust to clustering, which is worth stating because it
is the half that survives:

| arm | uplift | t | iid CI | day-clustered CI |
| --- | --- | --- | --- | --- |
| time_only | +0.317 | +4.42 | [+0.173, +0.468] | [+0.156, +0.470] |
| be_partial | +0.176 | +3.02 | [+0.060, +0.291] | [+0.040, +0.299] |
| composite | +0.297 | +3.98 | [+0.153, +0.443] | [+0.133, +0.443] |

### Multiplicity: the sensitivity curve is a swept grid, not a point

Paired uplift and arm-level mean across the whole admissible time-stop range:

| ts (bars) | 1 | 2 | 3 | 4 | 6 | 8 | 10 | 14 | 20 | 30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paired uplift | +0.283 | +0.308 | +0.368 | +0.320 | +0.244 | +0.217 | +0.213 | +0.101 | +0.019 | 0 |
| paired t | 3.24 | 3.80 | 4.91 | 4.47 | 3.99 | 3.76 | 3.70 | 2.37 | 0.54 | — |
| arm avg_r | +0.108 | +0.132 | **+0.192** | +0.145 | +0.068 | +0.042 | +0.038 | −0.075 | −0.157 | −0.176 |
| arm t | 2.19 | 2.15 | **2.52** | 1.83 | 0.80 | 0.47 | 0.41 | −0.82 | −1.73 | −1.84 |

(`time_only`; the `composite` sweep is in the script and peaks lower on both
rows.) **Both maxima sit at ts=3 and they answer differently.** Ten time-stops ×
two policies is 20 trials, so the bar for a swept maximum is a two-sided
Bonferroni z of **2.81** at 10 trials or **3.02** at 20, not 1.96:

- best **paired** t = **+4.91** → clears both bars. The "an exit policy beats
  this baseline" claim survives multiplicity.
- best **arm-level** t = **+2.52** (avg_r +0.192R) → fails both. The "and the
  resulting book makes money" claim does not, at any point on the grid.

That pair is the audit's real result, and it is why the verdict is BOUNDED
rather than FOUND: **the ceiling on the lever is +0.368R of uplift, and the best
book that ceiling can buy is +0.192R per trade at t = 2.52 against a bar of
2.81.** The floor the arms actually ship (`4h` 4 / `1d` 3) is near but not at
that optimum, so the conclusion does not depend on the chosen value — it is
already the most favourable value on the grid that fails.

### Effective n: 267 alerts are not 267 draws

| quantity | value |
| --- | --- |
| alerts | 267 |
| distinct ET session days | **31** |
| symbols | 13 (correlated megacaps) |
| calendar span | 83 days (2026-05-20 → 2026-08-11) |
| alerts/day | mean 8.6, median 9, max 18 |
| SPY over the same window | +4.0% (57 `1d` bars) |

If the day is the independent unit, `sqrt(n)` falls 16.3 → 5.6, a **~2.93×** SE
inflation. Measured, the day-clustered paired CIs above barely move — so
clustering does **not** overturn the paired leg, and saying so is part of the
result. It does overturn every arm-level CI, and it is the reason the whole
exercise is one regime: 31 days inside a single +4.0% SPY stretch, on 13 names
that move together, in a long-tilted book.

### Costs are not the binding constraint

"Gross of costs" is a caveat only if costs could change the sign. They cannot,
because 1R is wide here — median **313 bps**, p25 200, p75 503, min 50:

| round trip | median cost in R | worst-case row |
| --- | --- | --- |
| 5 bps | 0.016R | 0.100R |
| 10 bps | 0.032R | 0.200R |
| 20 bps | 0.064R | 0.400R |

A 10bps round trip is **0.032R**, about a tenth of the +0.317R uplift, and
turnover per trade is unchanged (one round trip either way). Slippage against a
time-stop's mark-to-close remains unmodelled, but no plausible cost assumption
reaches the effect. **The constraint on this result is significance and regime,
not execution cost.**

### Mechanism: the marginal bar, not a decay curve

v1 printed a table headed *"mean R of an open position marked to market at bar
k"* and read it as a peak at bar 3 followed by monotone decay. **That table was
mislabelled.** It re-resolves the `time_only` **arm** at `time_stop = k`, so it
is the sensitivity curve shifted by the baseline — `arm_avg_r(k) = −0.176 +
paired_uplift(k)`, e.g. `+0.192 = −0.176 + 0.368` at k=3 — and it includes every
trade that had already hit SL or TP. It is the same measurement as the row above
it, presented twice as two findings.

The population it claimed to describe behaves in the **opposite** direction.
Positions genuinely still unresolved under `fixed` at bar k, marked at that
bar's close:

| k | 1 | 2 | 3 | 4 | 6 | 8 | 10 | 14 | 20 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| still open | 225 | 191 | 158 | 136 | 105 | 86 | 75 | 33 | 18 |
| share open | 84% | 72% | 59% | 51% | 39% | 32% | 28% | 12% | 7% |
| mean R | +0.283 | +0.384 | +0.489 | +0.589 | +0.588 | +0.787 | +0.914 | +0.952 | +0.951 |
| median R | +0.156 | +0.342 | +0.368 | +0.438 | +0.385 | +0.675 | +0.805 | +0.782 | +1.068 |

It rises monotonically, and the reason is survivorship: a stop removes losers
first, so the surviving cohort looks better every bar. **An open position that is
still alive at bar 10 is not a decaying asset — it is a winner.**

So the honest mechanism is about the **marginal bar over the whole book**, not
about a position's own trajectory: past ~3 bars, the next bar adds more stop-outs
than it adds gains, which is a statement about the unconditional expectation of
holding. The clean way to say what v1 was reaching for is that **67.8% of alerts
have resolved by bar 8 and 55.1% of the book is a full −1R stop-out by then, which
is why the median trade under an 8-bar stop is exactly −1.000R** — against a median
of −0.144R under a 4-bar stop. These signals mostly reach their stop, and they
reach it early. R-per-bar (`fixed` −0.0235 vs `time_only` +0.0483) points
the same way, though it is not independent evidence: both terms are
policy-determined.

**Either way the actionable reading is the holding period, not the exit craft** —
and a 3-bar time-stop and a 3-bar `max_hold_bars` are the same trade with
different paperwork. Which of the two (if either) to change is a user decision;
`max_hold_bars` changes are on the frozen list.

## Caveats — read before quoting any number above

1. **In-sample.** Both the +1R lock and the time-stop floor were derived from
   these 267 rows. The sensitivity curve shows the result is not a *point*
   artifact; §Multiplicity shows what that costs when the grid is priced in.
2. **The whole ledger is pre-#151** — it describes the narrow live gate, before
   the direction-counted sample guard and the significance test.
3. **45 wins are the entire positive tail.** `time_only` destroys ~1R of each
   (−40.9R across 45 wins), outweighed by 198 losses that had not yet reached
   their stop at bar 3–4. A cohort that small cannot support a per-edge
   assignment: **0 of 30 loss cells reach n=30**, which is why every arm here is
   one pooled global policy.
4. **`expired` is 9.0% here vs upstream's 39.4%.** Upstream's headline finding was
   that caps, not exits, dominated its portfolio; this fork has no book and no
   caps, so this measures the population effect upstream could not reach — a
   different question with the same engine, not a reproduction.

### Where the uplift comes from (`time_only`, by recorded cohort)

| recorded | tf | n | ΣΔR | mean ΔR |
| --- | --- | --- | --- | --- |
| loss | 4h | 141 | +99.44 | +0.705 |
| loss | 1d | 57 | +35.69 | +0.626 |
| win | 4h | 33 | −32.15 | −0.974 |
| win | 1d | 12 | −8.74 | −0.728 |
| expired | 1d | 21 | −7.09 | −0.338 |
| expired | 4h | 3 | −2.59 | −0.864 |

## Two defects this port found

Both were found by **running** the ported code and reading its output, not by reviewing it.

### 1. The forward-window fetch assumed a 24/7 tape (fixed here)

Upstream fetches a trade's forward window as `max(candle_ts) + (max_hold + 2) * tf_ms`,
which is exact when bars are contiguous. Equity RTH bars are not:

| tf | max_hold | real span p50 | p95 | max | upstream assumed | windows covered |
| --- | --- | --- | --- | --- | --- | --- |
| 4h | 30 | 132 | 150 | 161 | 32 | **0.0%** |
| 1d | 14 | 20 | 22 | 25 | 16 | **0.0%** |

Units are `tf_ms`; measured over all 13 ledger symbols (14,072 `4h` and 33,397 `1d`
windows). Reproduce:

```sql
SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ? ORDER BY open_time
-- then (open_time[i+max_hold] - open_time[i]) / tf_ms, per symbol
```

The truncation is silent — a would-be winner is marked to market at the last fetched bar
and labelled `expired` — and it **biases the A/B**, because a window that stops early
cannot affect a policy whose time-stop fires at bar 3 but does clip the long-held
baseline. It was invisible in review and obvious in the positive control, which sat at
**95.9%** rather than 100%: high enough to read as tie-break noise.

Fixed by fetching to `get_latest_open_time`, which removes the trap by construction rather
than re-tuning the literal. `TestForwardWindowSpansRthGaps` pins it with RTH-gapped
fixture bars and fails if the time-derived horizon returns (mutation-verified).

### 2. The live ledger over-credits R on a structural-TP win (NOT fixed here)

`scanner.py:1042`
stores `rr_ratio = eff_alert_tp_r` (the *configured* `tp_r`), but
`alert_formatter` sets `tp_price` to a detector's **structural** TP when it has
one, falling back to `entry ± sl_dist × tp_r` only otherwise. `_scan_forward`
then walks `tp_price` and, on a hit, records `outcome_r = rr_ratio`.

So an alert whose TP was 2.0R away is credited 5.0R when it hits.

- **30 of 267** resolved rows have `tp_price` and `rr_ratio` disagreeing, by up
  to **3.0R**. 8 of them are wins.
- Total over-credit: **+13.50R**, i.e. **+0.051R per resolved alert**.
- **The live ledger's headline is therefore −0.1753R, not the −0.1247R currently
  quoted.** The arithmetic closes exactly: −0.1247 − 13.50/267 = −0.1753, which
  is the replayed `fixed` avg_r of −0.176.

Reproduce the whole thing in one query — the discriminator is that `implied_rr` is derived
from `tp_price` (what the resolver walks) and `rr_ratio` is what it credits:

```sql
SELECT signal_id, outcome, outcome_r, rr_ratio,
       CASE WHEN direction = 'long'
            THEN (tp_price - entry_price) / abs(entry_price - sl_price)
            ELSE (entry_price - tp_price) / abs(entry_price - sl_price) END AS implied_rr
FROM signal_alert_outcomes
WHERE outcome IN ('win', 'loss', 'expired')
  AND abs(implied_rr - rr_ratio) > 1e-6;      -- 30 rows; 8 with outcome='win'
```

This is the repo's declared-vs-effective family again (#142/#154): a column that
records what was *configured* is not a record of what *happened*, and the
stricter/nearer leg is the one nobody wrote down. It is also why the positive
control matters — at the declared target the `fixed` arm agreed with the ledger
on 263 of 267 rows, which is high enough to look like rounding noise and was not.

**Not fixed here** — `analytics/exits/audit.py::implied_tp_r` corrected it
**read-side, for the replay only**.

**FIXED 2026-08-14 (SoT N4).** The helper moved to
`analytics/signal/outcome_backfill.py` as the single definition — and was renamed
`implied_tp_r`, because `SignalWatchConfig.effective_tp_r` already means the
*declared* side (the configured `tp_r` after override resolution) and its output
is what arrives as `rr_ratio`. Sharing the name would have written
`effective_tp_r(rr_ratio=cfg.effective_tp_r(...))` into `scanner.py`. All three
surfaces now share it: the resolver credits the target it walked, the scanner
records that target at fire time, and this module imports it. `rr_ratio` answers
both questions the task left open — it stores the effective target — and
`migrations/003_outcome_r_implied_tp.py` rewrote the history.

Two figures above are worth restating with what the fix measured. The divergence
is **34 of 298** rows, not 30 of 267: the extra four were **still open**, so this
was mis-crediting future resolutions and not merely history. And the correction
runs **both** ways — a structural TP can sit further out than the declared target
as well as nearer — which is why the fix derives the value rather than clamping
it. The edge reading is unchanged: this moved the ledger's own headline to
**−0.1752R** but not the A/B, which already used the corrected target on every
arm.

## Next

**Nothing here deploys, and the bound is what closes the direction.** The levers
upstream listed as follow-ups — per-edge assignment, direction-prune,
cost-netting, a time-stop sweep — are all blocked by cohort depth (0 of 30 loss
cells reach n=30), not by the engine, which exists and is cheap to re-point.
**The re-run trigger is ledger growth, not a code change**; at roughly 3 alerts
per calendar day the binding scarcity is *days*, not alerts, so the honest
estimate is regime-limited rather than volume-limited.

Two things would move this from BOUNDED to answerable, and neither is exit work:
a book that is measurably positive before the exit is applied (so "A beats B"
becomes worth acting on), and a second regime (31 days inside one +4.0% SPY
stretch cannot separate an exit effect from a trend effect). **The successor
question — exit policy or a shorter `max_hold_bars` — is a user decision and the
second is on the frozen list.**
