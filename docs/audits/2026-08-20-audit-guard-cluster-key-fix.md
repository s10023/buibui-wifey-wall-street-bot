# The cluster unit becomes a required, fail-closed input to `audit_guard`

**Date:** 2026-08-20
**Verdict:** **FIXED** — verdicts are now priced on the **session day** rather than the trade, on
**both** legs. `[bias.regime]`'s sole blocking cell does not survive, so the replay now reports
**FLIP justified**. ⚠ **That is the removal of an obstacle, not the arrival of evidence** — see
Bounds.
**Subject:** `analytics/audit_guard.py`, new `analytics/research_guards/cluster.py`, and both
consumers (`analytics/warning_audit.py`, `tools/regime_gate_replay.py`)
**Status:** code changed, tests added. **No config changed** — `[bias.regime].mode` was ruled
2026-08-20 to stay as it stands; see Decisions.
**Implements:** decision 1 of `2026-08-20-audit-guard-cross-sectional-clustering.md`.

## What changed

`AuditCell` gains a **required** `cluster_key`, one entry per `supp_r` row, positioned **before**
the defaulted `kept_r` so mypy refuses every call site that omits it. It did: nine sites, which is
the point — the previous shape offered no channel through which a caller *could* declare the unit.

Both legs now use it, because the verdict needs both and they failed differently:

| leg | before | after |
| --- | --- | --- |
| effect size | `block_bootstrap_ci` over trades | `cluster_bootstrap_ci` resampling whole days |
| significance | `t = sr·√n_trades` | `t = sr·√n_eff`, `n_eff = n / DEFF` |

`DEFF = 1 + (m̄−1)·ICC` from a one-way random-effects ICC, matching the analytic route the prior
audit validated against resampling (correlation 0.80).

⚠ **A mismatched key length FAILS CLOSED to `INSUFFICIENT`** and leaves the Holm family, rather
than falling back to per-trade resampling. An unmeasurable panel and an uncorrelated one must not
both read as a deflator of 1.0 — the fail-open shape this repo closed in `tools/distil_power.py`.

`evaluate_audit_cells` also **loses its `boot_method` parameter**. It selected between the
stationary and circular block bootstraps, and the cluster bootstrap has no such choice to make —
leaving it in place would have been a knob that silently did nothing. No caller passed it.

⚠ **The deflator can only shrink.** `icc` is clamped to `[0, 1]` and `DEFF` to `≥ 1`, so a
negative sample ICC — routine on small panels — never manufactures *more* independence than the
raw trade count.

## Measurement — `tools/regime_gate_replay.py`

The four suppressed cells, re-run against the live DB with the shipped defaults:

| strategy | regime | n | days | DEFF | avg_r | CI | adj_p | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bos` | high_vol | 655 | 114 | 3.80 | −0.3852 | [−0.642, −0.106] | 0.001 | **ENABLE** |
| `bos` | trend | 897 | 161 | 3.05 | −0.3540 | [−0.522, −0.161] | 0.000 | **ENABLE** |
| `ema` | high_vol | 172 | **25** | **5.90** | +0.5497 | [−0.330, +1.450] | 0.276 | INSUFFICIENT |
| `ema` | range | 110 | **17** | 4.96 | +0.3291 | [−0.587, +1.565] | 0.484 | INSUFFICIENT |

**`ema/high_vol` was the single cell blocking the flip.** Its shipped CI was `[+0.085, +1.023]` — a
hair over the +0.05 bar — and it is now `[−0.330, +1.450]`, straddling zero at adj-p 0.276. n=172
was never 172 draws: it is **25 days at ~7 trades each**, and the design effect says it takes ~5.9
trades to buy one independent observation.

**Independent agreement.** The prior audit *predicted* this cell's day-clustered CI as
`[−0.287, +1.440]` from a separate implementation; this run measures `[−0.330, +1.450]`. Two
implementations, same answer.

⚠ **The correction does not erase strong findings, and that is the check that it is not simply a
width knob.** Both `bos` cells keep `ENABLE` at adj-p 0.001 / 0.000 on 114 and 161 days. A fix that
turned everything INSUFFICIENT would be indistinguishable from breaking the tool.

## Measurement — `analytics/warning_audit.py`: the prediction held

The prior audit predicted this consumer would not move, because pooling across strategies
decorrelates the day (it measured a width ratio of 1.208 there against ~1.9 for single-strategy
cells). Confirmed: **11 INSUFFICIENT + 1 SUPPRESS-CANDIDATE (`w5_wick_rejection` / long)**, the
same shape filed on 2026-08-13.

The deflation is present but small, and the two routes agree here as well. On that one surviving
cell the CI went **[−0.475, −0.144] → [−0.493, −0.126]** (width **×1.11**) and Holm-adjusted p
**0.002 → 0.012**; its measured design effect is **1.30**, and `√1.30 = 1.14` — the analytic
prediction of that same width ratio. For comparison `w1_marubozu` sits at DEFF 1.42–1.44. Against
the single-strategy cells' 3.05–5.90 in the table above, this is the cell-cut effect made visible.

Both tools now print `days` and `DEFF` beside `n`. That is the point of carrying them into
`WarningVerdict` even where they barely move: a reader who re-cuts these cells per strategy needs
to *see* the deflator jump rather than infer it.

⚠ **That is an indicative comparison, not a controlled one** — the clean cohort moved 3,168 →
3,190 rows, so the DB has drifted since the filed run and some of the difference is data, not
method. The verdict is unchanged either way, which is the claim being made.

⚠ **This is a fact about the CELL CUT, not about the tool.** Re-cutting the warning audit per
strategy would re-open the same error, exactly as the prior audit warned.

## Bounds on the claim

⚠ **Widening a CI removes evidence; it never supplies the opposite conclusion.** "FLIP justified"
means *no cell blocks the flip*, not *the flip is right*. The tool says so itself in the same run:
the kept-cell audit returns **MAPPING UNTESTED**, with both kept cells INSUFFICIENT
(`bos/range` [−0.200, +0.598], `ema/trend` [−0.187, +0.666]). Nothing here argues the regime
mapping is correct — only that the evidence once cited against changing it does not hold.

**The day is the unit for US RTH equities specifically.** `session_day_keys` floors a millisecond
stamp to the UTC day, which *is* the session day here: RTH spans 13:30–20:00 UTC under EDT and
14:30–21:00 UTC under EST, both inside one UTC date. ⚠ **Do not port that to a 24h tape**, where
the UTC day is an arbitrary cut through a continuous session.

**What is still not absorbed.** The cluster unit is one dimension. Trades correlated by *sector* or
by *symbol across days* are still treated as independent, and no single key can express two
crossed dependence structures at once.

## Decisions

1. **`[bias.regime].mode` — RULED 2026-08-20 (operator): LEAVE AS IT STANDS.** No config change.
   The reasoning is the Bounds section above, and it is the whole point of the ruling: the
   collapsed cell removes the evidence **against** the current setting and supplies none **for**
   changing it. The same run reports **MAPPING UNTESTED** with both kept cells `INSUFFICIENT`
   (`bos/range` [−0.200, +0.598], `ema/trend` [−0.187, +0.666]). ⚠ **"FLIP justified" is the
   tool reporting that nothing blocks the flip — it is not a recommendation to take it**, and
   acting on it would be reading a removed obstacle as an argument.
   **Re-open only on new evidence**, not on a re-read of this run: the honest blocker is that no
   kept cell clears the bar in either direction, so the question needs ledger growth or a different
   cell cut before it can be answered rather than guessed.
2. **Whether `bos` should trade at all** — untouched here, but the panel that question needs got
   stronger: both its suppressed cells survive the day-clustered correction as reliable losers
   (`high_vol` n=655 on 114 days, adj-p 0.001; `trend` n=897 on 161 days, p 0.000), so that half is
   no longer resting on an inflated t-stat.

## Reproduction

```bash
PYTHONPATH=. poetry run python tools/regime_gate_replay.py
make wifey-warning-value-audit
poetry run pytest tests/test_audit_guard.py tests/test_research_guards_cluster.py -q
```
