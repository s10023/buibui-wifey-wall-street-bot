# `distil_power` units — the G3 bar priced two footings as one

**Date:** 2026-09-27
**Audit:** this file. Owner: SoT row N5.

## Verdict

BLOCKED — H-023 stays blocked at G3 on consistent units, and the 2026-08-27 REACHABLE is retracted.
The tool defect that produced it is fixed: `tools/distil_power.py` now requires a declared Sharpe
footing, converts annualized inputs, and refuses the H-023 inputs when they are declared per
observation. H-024's measured verdict stands, and H-004's does not depend on the defect.

## The defect

`research_guards.psr` forms `z = (sr − sr_benchmark)·√(n_obs − 1)`, so every Sharpe it touches is
per observation. `/research-distil`'s G3 recipe told the run to derive `--sr-variance` from "this
repo's filed sleeve Sharpes", which are all annualized, and named the annualized cross-asset
`broad_ls` +0.41 as the corpus best. H-023 and H-024 were priced that way: `n_obs` 2,174 NYSE
sessions, `sr_variance` 0.0652 (the sample variance of the eight annualized sleeve Sharpes), corpus
best 0.41.

The tool returned a per-day bar of **0.3047**, which is **4.84 annualized**, compared it with an
annualized 0.41, and printed REACHABLE. With every input on one footing, the bar is **0.83
annualized**, 2× the corpus best.

The trap was known before either run. The H1 precheck hit it on 2026-08-20 and caught it only
because the resulting bar (6.94 annualized) was absurd. That note lived in a gitignored one-shot
script and in `.claude/context/analytics.md`, and neither the tool nor the skill enforced it.
0.3047 was the "subtler mis-scaling" that note predicted would be believed.

## What it changed

| Hypothesis | Filed G3 | Consistent G3 | Consequence |
| --- | --- | --- | --- |
| H-023 Lazy Prices | REACHABLE, bar 0.3047 | bar 0.83 annualized, null floor ±0.67 | BLOCKED since the 2026-09-23 re-price; do not build the fetcher and parser |
| H-024 insider routine/opportunistic | REACHABLE, bar 0.3047 | same bar, 0.83 | Built on the wrong pass, measured EXCLUDED (`2026-09-20-h024-insider-phase3.md`); that verdict stands, and a consistent G3 would have predicted it |
| H-004 sector rotation | BLOCKED | unchanged | Rests on the powered-null leg at n=5 episodes, which uses no `sr_variance` |

The 2007 history lever for H-023 (n=4,943) lowers the DSR bar to 0.64. That leaves `GATE_SHARPE`
0.70 as the binding bar and moves the null floor to ±0.44. The realistic effect, the paper's weak
long leg against a corpus best of 0.41, still sits in the band that the panel can neither confirm
nor exclude.

H-023 unblocks only on (a) a PIT, survivorship-free universe, which is paid data, or (b) an
operator decision to accept a test that can confirm ≥ 0.70 and cannot exclude anything below
~0.44–0.67. Record in `docs/plans/thesis-inbox.md` § *G3 RE-PRICE — H-023*.

## The fix

- `--sr-footing {per_obs|annual}` is mandatory with no default, like `--units`. The recipe that
  priced 0.3047 no longer runs.
- `annual` requires `--periods-per-year`. It divides `--sr-variance` by the periods, reads
  `--corpus-best` and `--bar` as annualized Sharpes, refuses `--sd`, and prints the bar on both
  footings.
- On `per_book_day`, where the year is fixed at 252, a `per_obs` declaration is refused when
  `√(sr_variance·252)` or `|corpus_best/sd|·√252` exceeds 3. The H-023 inputs imply 4.05 and
  6.51, and the nine filed sleeves span −0.47 to +0.41.
- `tests/test_distil_power.py` pins the chain. The positive control reproduces 0.3047 (4.84
  annualized) from the mixed inputs. The annual footing returns 0.83 and a ±0.667 half-width, and
  the two footings agree to rounding. Each refusal has a passing twin with honest per-day inputs.
- `/research-distil`'s G3 section, `.claude/context/tools.md` and both 2026-08-29 specs now carry
  the footing. The specs' frozen pre-registrations keep their text with a dated correction below,
  because the trial family of 4 did not change.

## What the guard cannot see

- `per_trade` and `per_alert` have no fixed year, so nothing checks their footing. The skill says so.
- On `per_book_day`, an annualized variance below 3²/252 ≈ 0.0357, declared `per_obs`, passes the
  plausibility check. The mandatory declaration is the defence there, not the check.
- The effect-unit legs (`--sd` with `--corpus-best` or `--bar` on `per_obs`) remain the caller's
  arithmetic; the tool cannot know what an effect unit is.

## Reproduce

```bash
PYTHONPATH=. poetry run python tools/distil_power.py --units per_book_day \
  --sr-footing annual --periods-per-year 252 --n-obs 2174 --n-trials 4 \
  --sr-variance 0.0652 --corpus-best 0.41 --bar 0.70
```

Prints `required Sharpe 0.829175 annual (0.052233 per obs)`, `REACHABLE, but the bar EXCEEDS the
corpus best`, and a CI half-width of 0.6673. The same inputs declared `--sr-footing per_obs` exit 2.
