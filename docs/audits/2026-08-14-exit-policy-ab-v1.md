# Exit-Policy Replay A/B v1 (2026-08-14)

**Verdict: every exit arm beats the fixed baseline with a paired CI clear of zero,
and the whole effect is the TIME lever, not the lock levers — the composite is
WORSE than the time-stop alone. But the mechanism is signal decay, not exit
craft: the mean R of an open position peaks at bar 3 and falls monotonically to
the cap, and the median trade is at −1R by bar 8. This is a statement about how
long these signals stay good, not a deployable exit rule, and it is measured on a
book whose baseline expectancy is negative.**

Port of parent PR #437 (`fbf6607`). Step 1 is
`docs/audits/2026-08-14-mfe-timing.md`.

## How this was produced

- Engine: `analytics/exits/{policies,replay}.py`, copied verbatim from upstream
  (no `portfolio/` dependency); verdict layer `analytics/exits/audit.py`.
- Command: `make wifey-exit-replay` (= `tools/exit_audit.py --replay`), read-only.
- Sensitivity + attribution: `docs/plans/scripts/exit_time_stop_sensitivity.py`.
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

### Where the uplift comes from (`time_only`, by recorded cohort)

| recorded | tf | n | ΣΔR | mean ΔR |
| --- | --- | --- | --- | --- |
| loss | 4h | 141 | +99.44 | +0.705 |
| loss | 1d | 57 | +35.69 | +0.626 |
| win | 4h | 33 | −32.15 | −0.974 |
| win | 1d | 12 | −8.74 | −0.728 |
| expired | 1d | 21 | −7.09 | −0.338 |
| expired | 4h | 3 | −2.59 | −0.864 |

The cost to winners is real and large (−40.9R across 45 wins, ~1R each); it is
simply outweighed by 198 losses that had not yet reached their stop at bar 3–4.

### Sensitivity — the result is not an artifact of the chosen floor

Paired uplift vs `fixed` across the whole admissible time-stop range:

| ts (bars) | 1 | 2 | 3 | 4 | 6 | 8 | 10 | 14 | 20 | 30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| time_only uplift | +0.283 | +0.308 | +0.368 | +0.320 | +0.244 | +0.217 | +0.213 | +0.101 | +0.019 | 0 |
| t | 3.24 | 3.80 | 4.91 | 4.47 | 3.99 | 3.76 | 3.70 | 2.37 | 0.54 | — |

Positive with t > 2.3 everywhere below the cap, peaking at ts=3. The floor
(`4h` 4 / `1d` 3) is near but not at the optimum, and the conclusion does not
depend on it.

### The mechanism is decay, not exit skill

Mean R of an **open** position marked to market at bar k:

| k | 1 | 2 | 3 | 4 | 6 | 8 | 12 | 20 | 30 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mean R | +0.108 | +0.132 | **+0.192** | +0.145 | +0.068 | +0.042 | +0.000 | −0.157 | −0.176 |
| median R | +0.060 | +0.096 | +0.007 | −0.144 | −0.856 | −1.000 | −1.000 | −1.000 | −1.000 |

R-per-bar: `fixed` −0.0235, `time_only` +0.0483. The book is only favourable for
about three bars. **An exit policy that harvests that is arbitraging the holding
period, not managing the trade** — and the honest restatement is that these
signals have a ~3-bar half-life against a stop they mostly reach.

## Caveats — read before quoting any number above

1. **The baseline book is negative-expectancy** (avg_r −0.176, Sharpe −0.113).
   Turning −0.176 into +0.141 is a real improvement on a losing book and is not
   evidence of an edge. Nothing here clears any deploy bar, and no gate was
   applied that could have said otherwise.
2. **In-sample.** Both the +1R lock and the time-stop floor were derived from
   these 267 rows. The sensitivity curve shows the result is not a *point*
   artifact; it does not make it out-of-sample.
3. **Gross of costs.** R-space only. Turnover per trade is unchanged (one round
   trip either way), so this is not a turnover-cost story — but a 3-bar exit
   marks to the bar close, and slippage against a mark is unmodelled.
4. **The whole ledger is pre-#151** — it describes the narrow live gate, before
   the direction-counted sample guard and the significance test.
5. **45 wins are the entire positive tail.** The policy destroys ~1R of each.
   A cohort that small cannot support a per-edge assignment: **0 of 30 loss cells
   reach n=30**, which is why every arm here is one pooled global policy.
6. **`expired` is 9.0% here vs upstream's 39.4%.** Upstream's headline finding was
   that caps, not exits, dominated its portfolio; this fork has no book and no
   caps, so this measures the population effect upstream could not reach — a
   different question with the same engine, not a reproduction.

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

**Not fixed here.** `analytics/exits/audit.py::effective_tp_r` corrects it
**read-side, for the replay only**. Correcting the ledger itself means rewriting
`outcome_r` on historical rows and changing what `_scan_forward` credits — a live
path, a migration, and a decision about whether `rr_ratio` should store the
effective target at fire time instead. That is its own task.

## Next

The engine exists and is cheap to re-point. The levers upstream listed as
follow-ups — per-edge assignment, direction-prune, cost-netting, a time-stop
sweep — are all blocked here by cohort depth (0 of 30 loss cells reach n=30),
not by the engine. **The re-run trigger is ledger growth, not a code change.**
The ledger-credit defect above is the one actionable item, and it is a live-path
fix rather than research.
