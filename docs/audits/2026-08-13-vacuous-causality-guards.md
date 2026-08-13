# Vacuous causality guards in the sleeve books

**Date:** 2026-08-13
**Verdict:** Two of three causality guards were vacuous — they passed with the causal shift they
guard deleted. Fixed, and each is now mutation-verified in both directions. The third already had
teeth. Ported from parent #569's de-vacuuming half, but **not** as a copy: upstream's positive
control would have been a false control here.
**Audit:** this file

## What was wrong

Three tests assert a lookahead-freedom property of the research sleeves by perturbing one bar and
checking that earlier sizing did not move:

| Test | Guards | Status before |
| --- | --- | --- |
| `tests/forecast/test_book_instrument.py::test_position_is_causal_no_lookahead` | `forecast = forecast.shift(1)` (`analytics/forecast/book.py:43`) | **VACUOUS** |
| `tests/xsmom/test_book.py::test_xs_leverage_is_causal_no_lookahead` (and its `dollar_neutral` sibling) | `demeaned.shift(1)` (`analytics/xsmom/book.py:72`) | **VACUOUS** |
| `tests/forecast/test_book_portfolio.py::test_governor_is_causal` | `std().shift(1)` on trailing vol (`analytics/forecast/book.py:115`) | already had teeth |

A "did NOT change" assertion is satisfied by two different worlds: the invariant holds, **or** the
perturbation never reached the code under test. Only the first is a test. Measured by deleting each
shift and re-running:

- the instrument guard **passed** with its shift deleted — it perturbed the **last** bar, so there
  was no `k+1` at which any effect could ever be observed;
- both xsmom guards **passed** with their shift deleted (mechanism below);
- the governor guard **failed**, so it was genuinely protecting its property.

`make check-orphan-tests` cannot see this class: every one of these tests calls its subject. The
failure is in what the assertion can distinguish, not in whether the unit is reached.

## The xsmom mechanism, which is the transferable part

The fixture perturbed `STRONG`, a monotone ramp. Its own docstring says STRONG/WEAK "saturate the
EWMAC cap (+/-20)" — so the forecast is **pinned at +20** and a 1.5× price bump cannot move it.
Measured delta on the guarded input at `k`: **exactly 0.0**.

Upstream's fix asserts the stimulus reached `leverage[k+1]`. **That assertion passes here whether
or not the shift exists**, because leverage has a *second* causal input — `ew_return_vol(close)` —
whose own shift carries the bump to `k+1` through the vol path. Measured on STRONG: delta at
`k+1` = **2.4e+02** while delta at `k` = **0.0**. A control there would have reported a live
stimulus while the guard stayed vacuous.

So the fix is two changes, not one:

1. perturb `FLAT` — the fixture's deliberately sub-cap random walk (forecast −3.30 at `k`), which
   moves leverage at `k` by **~5.7e+01** and therefore does catch the missing shift;
2. put the positive control **on the guarded channel** — the demeaned forecast at `k` — not on a
   downstream value reachable by another path.

## Incidental finding

`test_governor_is_causal` carried the comment *"k=100 is in an unclamped region (verified:
governor[100] ≈ 0.656)"*. `governor[100]` is **exactly 1.5**, i.e. sitting at `g_max`. The stated
verification had drifted and nothing re-checked it; a clamped base can mask a stimulus, which is
why that test now asserts propagation explicitly even though it already had teeth.

## Verification

`docs/plans/scripts/causality_mutation_matrix.sh` (gitignored, durable) runs the matrix:

- unmutated: all three pass;
- delete `forecast.shift(1)` → the instrument guard fails (newly);
- delete the governor's `std().shift(1)` → the governor guard fails (as before);
- delete `demeaned.shift(1)` → both xsmom guards fail (newly);
- production code restored byte-identical afterwards (`git diff --stat analytics/` empty).

Controls checked in the opposite direction too: pointing the xsmom bump back at `STRONG`, and
making the instrument bump a ×1.0 no-op, each fires the new assertion with its intended message.

## Rules

- **A positive control must observe the channel the guard protects.** One that fires through a
  different path proves the fixture is alive while the guard stays vacuous.
- **A cap silently turns a perturbation test into a no-op.** Check the perturbed input is not
  clipped before trusting a "did NOT change" assertion.
- **Vacuity is per-shift and must be measured, not inferred** from a missing control block — one
  of the three guards here already worked.
- **Re-measure a comment that states a verified value.** Two did not survive contact.
