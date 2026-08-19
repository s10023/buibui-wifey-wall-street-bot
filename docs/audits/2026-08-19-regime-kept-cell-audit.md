# The regime gate's blind spot: a kept cell nothing tested

**Date:** 2026-08-19
**Verdict:** **BOUNDED** — the config was wrong and is now right, and that buys no measurable book.
**Scope:** `tools/regime_gate_replay.py`, `config/strategy_params.toml [bias.regime.per_strategy]`

## The question

PR #231 gave the soft→hard flip decision a per-cell significance test and returned
`DO NOT FLIP`. It left a remainder that the handoff recorded but nothing could act on: `bos`/`high_vol` sat at
avg_r −0.3852 on n=655 — the worst cell in the tool's own printed table — and earned **no verdict at
all**, because it is a **kept** cell and `build_audit_cells` iterates only over suppressed ones.

The flip question and the mapping question are different, and only the first was ever asked:

| Question | Asks | Granularity |
| --- | --- | --- |
| Flip (`mode`) | should we start dropping the **suppressed** cells? | ONE global switch |
| Mapping (`enabled_regimes` / `per_strategy`) | is each **kept** cell one we should keep? | per (strategy, regime) |

A tool that only answers the first prints `DO NOT FLIP` and reads as a clean bill while a losing
cell it never examined sits in the table directly above the banner.

## What was wrong

`bos` carried a per-strategy override `["high_vol", "range"]` whose justification was written into
the config beside it: a 2026-05-13 routing audit at **n=72,643** ranking `trend` worst (−0.163),
`high_vol` **best** (+0.020), `range` middle (−0.048).

⚠ **That audit is crypto.** It landed as PR #366 on 2026-05-13 and
`git merge-base --is-ancestor 6f4f779 635ed5a` confirms it is an ancestor of the fork point, so its
n=72,643 is Binance trades on crypto regimes. On equities only the `trend` half ports; the
`high_vol` / `range` order is **inverted**:

| regime | crypto rank (n=72,643) | equity avg_r (n) | config before | config after |
| --- | --- | --- | --- | --- |
| `high_vol` | **best** +0.020 | **−0.3852** (655) | kept | **dropped** |
| `range` | middle −0.048 | **+0.1973** (341) | kept | kept |
| `trend` | worst −0.163 | −0.3540 (897) | suppressed | suppressed |

**The inherited value kept the losing regime and the tool could not see it.** This is the
crypto-era-inherited-flag class: swept by provenance date, never by flag name.

## Evidence

Kept cells now earn a verdict from the same `analytics/audit_guard.py::evaluate_audit_cells` path —
reused, not re-implemented, exactly as #231 reused it for the flip.

```text
KEPT-cell audit — would suppressing each cell HELP? (own Holm family, 3 cells)
strategy   regime         n    avg_r                 CI   adj_p   verdict
bos        high_vol     655  -0.3852   [-0.514, -0.245]   0.000   ENABLE
bos        range        341  +0.1973   [-0.045, +0.454]   0.040   INSUFFICIENT
ema        trend        674  +0.2367   [+0.033, +0.451]   0.002   INSUFFICIENT
```

Both gates must hold, which is why two significant cells are still `INSUFFICIENT`: their CIs do not
clear ±0.05R even though `ema`/`trend` reaches adj-p 0.002. A cell is not actionable because it is
significant; it is actionable when the effect is also big enough to be worth acting on.

**The sign survives every decomposition run.** The panel matters here because bos's dominant axis in
the crypto audit was *direction*, not regime — so a directional artifact was the live alternative
explanation, and it is ruled out:

| Split | Result |
| --- | --- |
| Direction | long −0.588 (301) · short −0.213 (354) — **both negative** |
| Timeframe | 4h −0.362 (397) · 1d −0.408 (253) · 1wk −1.037 (**n=5, ignore**) |
| Time halves | −0.137 → −0.637 |
| Calendar year | 2025 −0.100 · 2026 −0.590 |
| Symbols | 10 of 13 negative; top symbol 10.7% of n |
| Leave-one-symbol-out | −0.454 … −0.330 — sign never flips |

`bos`/`range`, by contrast, is positive in both directions (+0.185 / +0.217), so the regime axis is
the right axis and the equity ranking is direction-consistent.

## Limits — read before quoting any number here

⚠ **In-sample, one window**: 13 symbols, 2025-09-04 → 2026-08-06. There is no out-of-sample split
and none is available at this n.

⚠ **Only the SIGN is robust. The magnitude is not** — it nearly quintuples between the two halves
(−0.137 → −0.637), so −0.3852 is this window's estimate and not "the" effect. Any doc quoting it as
a stable figure is over-reading it.

⚠ **`range` is kept on absence of evidence, not evidence of absence.** Its CI [−0.045, +0.454] does
not clear the bar in either direction, so it is `INSUFFICIENT` — not a proven winner, and **not** a
powered null either.

⚠ **This is not evidence for a replacement mapping**, only against the one that was there. Nothing
here says `bos` should trade `range` *well*.

## What changed

- `tools/regime_gate_replay.py`: `build_kept_audit_cells` / `evaluate_kept_cells` /
  `mapping_verdict`, plus a `tested_as` column on the exported CSV.
- `config/strategy_params.toml`: `bos = ["high_vol", "range"]` → `bos = ["range"]`, and the comment
  that asserted the falsified crypto ranking as fact now records its provenance and its inversion.

Two design points, both deliberate:

**Two Holm families, never merged.** The flip family and the kept family answer different questions,
and merging them would enlarge the flip's denominator and silently restate #231's shipped verdict.
`TestKeptFamilyIsSeparate` pins it; a mutant merging them fails 6 tests.

**The mapping verdict does NOT veto.** `flip_verdict` applies one because `mode` is a single global
switch, so one reliable winner blocks everything. Each mapping entry is edited independently, so
here a winner must not cancel a loser — reusing the veto would hide a real finding whenever any
other cell won. That is the same class of error as pooling, reached from the opposite direction.

## Verdict — **BOUNDED**

The config was wrong in a way nothing could detect, and it is now right. **It buys no measurable
book.** `mode = "soft"` is log-only (`analytics/signal/gates.py:509`), so the change is
operationally inert today, and the hard flip that would activate it is still blocked by
`ema`/`high_vol`. The value delivered is a calibration repair and a permanently closed blind spot,
not P&L.

**Still open, and untouched here**: whether `bos` should trade at all — every regime available to it
is negative or unproven, and this audit deliberately did not ask that question.
