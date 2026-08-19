# Regime gate: a pooled banner that inverted its own table

**Date:** 2026-08-19
**Subject:** `tools/regime_gate_replay.py`, the flip evidence for `config/strategy_params.toml
[bias.regime].mode`
**Verdict:** **EXCLUDED** — flipping `mode` to `hard` is ruled out on current evidence. One
suppressed cell is a reliable winner at Holm-adjusted p=0.001, and `mode` is a single global
switch, so no per-cell gain can be taken without also taking that loss.

## What was wrong

The tool's decision rule was an n-weighted `avg_r` pooled across strategy **and** regime:
suppressed `avg_r <= 0` with kept above it printed `FLIP justified`. That aggregate is dominated by
whichever cell carries the most trades, so the banner could contradict the per-cell table printed
directly above it — and on live data it did.

The compensation shipped in prose. `.claude/context/tools.md` carried **"Read the per-cell table,
never the banner"**, a standing instruction for every future reader to disregard the tool's own
output. That is the same shape as the six checker defects closed in #229: *when a check keeps being
wrong, the fix is to make it executable, not to write the caveat more emphatically.*

## What replaced it

Per-cell verdicts through `analytics/audit_guard.py::evaluate_audit_cells` — already the live path
behind the warning-value audit, so this reuses an audited implementation rather than adding a
second significance test. Each suppressed (strategy × regime) cell must clear a block-bootstrap CI
against ±`bar` **and** a Holm-adjusted p-value across the family of tested cells.

The cells are then combined under the constraint that **`mode` is one global switch**: a single
`DISABLE`/`CONCENTRATE` cell blocks the flip regardless of how many cells or how much trade volume
point the other way. An n-weighted mean cannot express a veto, which is the root of the original
defect. Pooled figures are still printed, labelled `DESCRIPTIVE — NOT decision-bearing`.

`kept_r` for a cell is that **strategy's** surviving book across all regimes. Suppression is a pure
function of (strategy, regime), so a cell is never part-suppressed and cannot supply its own
counterfactual.

## The evidence

Live DB, `config/strategy_params.toml`, 2026-08-19:

| strategy | regime | supp | n | avg_r | CI | adj p | verdict |
| --- | --- | --- | ---: | ---: | --- | ---: | --- |
| bos | trend | YES | 897 | −0.3540 | [−0.462, −0.246] | 0.000 | ENABLE |
| ema | high_vol | YES | 172 | **+0.5497** | **[+0.085, +1.023]** | **0.001** | **DISABLE** |
| ema | range | YES | 110 | +0.3291 | [−0.210, +0.966] | 0.117 | INSUFFICIENT |
| bos | high_vol | no | 655 | −0.3852 | — | — | not tested |
| bos | range | no | 341 | +0.1973 | — | — | not tested |
| ema | trend | no | 674 | +0.2367 | — | — | not tested |

Pooled: suppressed n=1179 avg_r −0.1585; kept n=1670 avg_r −0.0153. **That satisfies the old rule's
FLIP condition exactly** (suppressed ≤ 0, kept above it), so the correction is not a refinement —
it inverts the answer on the same data.

## Two corrections to filed claims

**"Three of six cells pointed the other way" overstates it.** Under a significance test **one**
does. `ema`/`range` (+0.3291, n=110) comes back `INSUFFICIENT` — its CI straddles zero at adj
p=0.117. The frame was right and the count was wrong, which is the pattern
`docs/audits/2026-08-19-correction-chain-and-silent-assurance.md` documents. The flip is blocked
either way, so nothing downstream moves.

**`INSUFFICIENT` here is not a null.** All three tested cells carry `powered_null = False`, so the
run has not ruled out an effect in `ema`/`range` — it cannot see one. A sample-size floor says a
test ran; only a CI sized against the bar licenses a negative claim.

## What this does NOT settle

`bos`/`high_vol` — avg_r **−0.3852** on n=655, the worst cell in the table — is a **kept** cell.
The tool never tests it, so `DO NOT FLIP` says nothing about it. That is the separate, still-open
refutation of the inherited crypto calibration: the `bos` override exists because a 2026-05-13
crypto audit found `high_vol` was bos's *best* regime, and on equities it is bos's *worst*.
Blocking the flip does not fix the mapping, and a reader who takes `DO NOT FLIP` as "the config is
fine" has drawn the wrong conclusion.

⚠ **Every figure here is in-sample** — 13 symbols, 2025-06-06 → 2026-08-11, one pass, no
out-of-sample split. This is evidence against flipping. It is not evidence for a replacement
mapping, and no parameter was changed.

## Test shape

`TestFlipVerdictCombinesCellsNotPools::test_one_blocking_cell_vetoes_a_dominant_losing_aggregate`
is the positive control. It asserts **both** halves: that the fixture satisfies the old pooled FLIP
condition, and that the verdict is still blocked. Without the first assertion it would pass against
a pooling implementation too, and so could not detect a revert — the "test that passed against both
implementations" failure from #229.

Mutation-checked: neutering the blocking branch fails that test and the render test; the mutation
was verified present on disk before the run and the revert verified byte-identical afterwards,
because a mutation harness that silently fails to apply reports a false green.
