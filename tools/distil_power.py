"""Price a candidate hypothesis before it is written into the inbox.

The G3 gate of ``/research-distil``. Prints the effect size the gate demands at
the declared ``n`` and trial family, beside the corpus best, so a claim is
*priced* rather than estimated.

Two things this exists to prevent, both with filed precedent in this repo:

* **A power claim the model did the arithmetic for.** The warning-value audit
  awarded COSMETIC on ``n >= min_n``, and its printed legend was wrong in the
  same direction as the code, so the output corroborated the defect and no
  review surface caught it. Under the honest predicate 0 of 11 cells survived
  (``docs/audits/2026-08-13-warning-value-audit.md``). Running one tracked tool
  is the only structural defence.
* **A number whose units are implied.** ``--units`` is mandatory and has no
  default. ``regime.py`` carried crypto bar counts across the fork, so its
  "90-day" ATR window really spanned ~270 sessions on ``4h`` — RTH is 2 bars a
  day, not 6 — and 12.02% of ``4h`` labels moved when it was corrected. A
  deflator quoted for one panel and reused on another is that same defect: a
  figure that looks portable and silently changes meaning with the panel.
* **A Sharpe on the wrong footing.** ``research_guards.psr`` forms
  ``z = (sr - sr_benchmark) * sqrt(n_obs - 1)``, so every Sharpe it touches is
  per-observation. The filed sleeve Sharpes are annualized, and feeding their
  variance (0.0652) and the annualized corpus best (0.41) beside ``n_obs`` in
  sessions priced H-023 and H-024 at 0.3047 "per book day" -- 4.84 annualized
  -- and printed REACHABLE; on one footing the bar is 0.83 annualized, 2x the
  corpus best. ``--sr-footing`` is therefore mandatory too: ``annual`` converts
  every Sharpe-valued input by ``--periods-per-year`` and prints both footings,
  and a ``per_obs`` declaration on ``per_book_day`` whose implied annual
  dispersion is implausible is refused. Retraction audit:
  ``docs/audits/2026-09-27-distil-power-units-retraction.md``.

The null-containment verdict is delegated to
:func:`analytics.audit_guard.powered_null`. The comparison is never restated here.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence

from analytics.audit_guard import powered_null
from analytics.research_guards import GATE_DSR, required_sharpe

Z_95 = 1.959963984540054

UNITS = ("per_trade", "per_alert", "per_book_day")

FOOTINGS = ("per_obs", "annual")

# A book day's annualization factor is fixed, so a per-obs declaration on that
# unit can be checked: sqrt(sr_variance * 252) is the trial family's annualized
# Sharpe dispersion. The nine filed sleeves span -0.47..+0.41, so a dispersion
# of 3 annualized is already ~10x anything measured here; the H-023 inputs
# implied 4.05. The other units have no fixed year, so they get no check.
BOOK_DAYS_PER_YEAR = 252.0
MAX_PLAUSIBLE_ANNUAL_SHARPE = 3.0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="distil_power",
        description="Price a hypothesis against the gate before designing it.",
    )
    parser.add_argument("--units", required=True, choices=UNITS)
    parser.add_argument("--sr-footing", required=True, choices=FOOTINGS)
    parser.add_argument("--periods-per-year", type=float, default=None)
    parser.add_argument("--n-obs", type=int, required=True)
    parser.add_argument("--n-trials", type=int, required=True)
    parser.add_argument("--sr-variance", type=float, required=True)
    parser.add_argument("--n-series", type=int, default=None)
    parser.add_argument("--n-eff", type=float, default=None)
    parser.add_argument("--sd", type=float, default=None)
    parser.add_argument("--bar", type=float, default=None)
    parser.add_argument("--corpus-best", type=float, default=None)
    parser.add_argument("--skew", type=float, default=0.0)
    parser.add_argument("--kurtosis", type=float, default=3.0)
    return parser.parse_args(argv)


def effective_n(n_obs: int, n_series: int | None, n_eff: float | None) -> int:
    """Deflate pooled observations by the correlation deflator.

    Pooling ``k`` correlated series carries the noise reduction of ``n_eff``
    independent ones, so the naive ``n`` overstates the information. Omitting
    the deflator on a pooled multi-symbol panel is a declared error rather than
    a default, which is why both flags must be supplied together.
    """
    if n_series is None and n_eff is None:
        return n_obs
    if n_series is None or n_eff is None:
        raise ValueError("--n-series and --n-eff must be supplied together")
    if n_series < 1 or n_eff <= 0.0:
        raise ValueError("--n-series must be >= 1 and --n-eff must be > 0")
    if n_eff > n_series:
        raise ValueError(
            f"n_eff ({n_eff}) cannot exceed n_series ({n_series}): the deflator "
            "counts effective independent series among n_series, so n_eff <= "
            "n_series always. Check the argument order."
        )
    return max(2, int(n_obs * n_eff / n_series))


def check_footing(args: argparse.Namespace) -> None:
    """Refuse a footing declaration the inputs contradict. Raises ``ValueError``."""
    ppy = args.periods_per_year
    if ppy is not None and ppy <= 0.0:
        raise ValueError("--periods-per-year must be > 0")
    if args.sr_footing == "annual":
        if ppy is None:
            raise ValueError(
                "--sr-footing annual needs --periods-per-year: PSR runs per "
                "observation, so the annualized inputs must be converted"
            )
        if args.sd is not None:
            raise ValueError(
                "--sd does not apply under --sr-footing annual: --corpus-best "
                "and --bar are annualized Sharpes there, not effect units"
            )
        return
    if args.units != "per_book_day":
        return
    implied = math.sqrt(max(args.sr_variance, 0.0) * BOOK_DAYS_PER_YEAR)
    if implied > MAX_PLAUSIBLE_ANNUAL_SHARPE:
        raise ValueError(
            f"--sr-variance {args.sr_variance} per book day implies a trial-family "
            f"Sharpe dispersion of {implied:.2f} annualized, which no filed sleeve "
            "approaches. If it is the variance of annualized Sharpes, pass "
            "--sr-footing annual --periods-per-year 252"
        )
    if args.corpus_best is not None and args.sd is not None and args.sd > 0.0:
        implied = abs(args.corpus_best / args.sd) * math.sqrt(BOOK_DAYS_PER_YEAR)
        if implied > MAX_PLAUSIBLE_ANNUAL_SHARPE:
            raise ValueError(
                f"--corpus-best {args.corpus_best} per book day implies an "
                f"annualized Sharpe of {implied:.2f}. If it is an annualized "
                "Sharpe, pass --sr-footing annual --periods-per-year 252"
            )


def price(args: argparse.Namespace) -> list[str]:
    """Build the report. Returns lines; printing is the caller's job."""
    check_footing(args)
    annual = args.sr_footing == "annual"
    # PSR is per observation. `scale` carries a per-observation Sharpe onto the
    # declared footing; under `annual` every Sharpe-valued input is divided back.
    scale = math.sqrt(args.periods_per_year) if annual else 1.0
    n_used = effective_n(args.n_obs, args.n_series, args.n_eff)
    sr = required_sharpe(
        n_used,
        n_trials=args.n_trials,
        sr_variance=args.sr_variance / scale**2,
        skew=args.skew,
        kurtosis=args.kurtosis,
    )
    reachable = not math.isinf(sr)

    out = [
        "distil_power - G3 power gate",
        f"  units             {args.units}",
    ]
    if annual:
        out.append(
            f"  Sharpe footing    annual, converted at {args.periods_per_year:g}"
            " periods/year (PSR runs per observation)"
        )
    else:
        out.append("  Sharpe footing    per_obs")
    out.append(f"  n_obs (declared)  {args.n_obs:,}")
    if n_used != args.n_obs:
        out.append(
            f"  effective n       {n_used:,}"
            f"  (deflated by n_eff {args.n_eff} / {args.n_series} series)"
        )
    else:
        out.append("  effective n       (no deflator applied)")
    family = (
        f"  trial family      {args.n_trials} trials, sr_variance {args.sr_variance}"
    )
    if annual:
        family += f" annual = {args.sr_variance / scale**2:.8f} per obs"
    out += [family, f"  gate target       DSR >= {GATE_DSR}", ""]

    if not reachable:
        out.append("  VERDICT           UNREACHABLE at any finite Sharpe")
        out.append(
            "                    Not underpowered - unreachable. More data of this"
        )
        out.append(
            "                    shape cannot fix it; the trial family must shrink."
        )
    elif annual:
        out.append(f"  required Sharpe   {sr * scale:.6f} annual  ({sr:.6f} per obs)")
        if args.corpus_best is not None:
            out.append(f"  corpus best       {args.corpus_best:+.4f} annual")
            if sr * scale > args.corpus_best:
                out.append(
                    "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
                )
            else:
                out.append("  VERDICT           REACHABLE")
        else:
            out.append("  VERDICT           REACHABLE")
    else:
        out.append(f"  required Sharpe   {sr:.6f}")
        if args.periods_per_year is not None:
            out.append(
                f"                    = {sr * math.sqrt(args.periods_per_year):.6f}"
                f" annualized at {args.periods_per_year:g} periods/year"
            )
        if args.sd is not None:
            unit_noun = args.units.replace("per_", "").replace("_", " ")
            out.append(
                f"  required effect   {sr * args.sd:+.4f} per {unit_noun}"
                f"  (sd {args.sd})"
            )
        if args.corpus_best is not None:
            out.append(f"  corpus best       {args.corpus_best:+.4f}")
            if args.sd is None:
                # `--corpus-best` is quoted in effect units, so comparing it to
                # the bar needs `--sd` to convert the required Sharpe into those
                # units. Without it there is no comparison to make, and a bare
                # REACHABLE (what a cleared bar prints) would render "did not
                # compare" as "passed" on a gate `/research-distil` mandates
                # running. Name the missing input instead.
                out.append("  VERDICT           REACHABLE, corpus best NOT COMPARED")
                out.append(
                    "                    --corpus-best is in effect units; without"
                )
                out.append(
                    "                    --sd there is nothing to compare it to."
                )
                out.append("                    Re-run with --sd to close the gate.")
            elif sr * args.sd > args.corpus_best:
                out.append(
                    "  VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
                )
            else:
                out.append("  VERDICT           REACHABLE")
        else:
            out.append("  VERDICT           REACHABLE")

    # Under `annual` the bar is an annualized Sharpe, so the half-width is a
    # unit-sd per-observation CI carried onto that footing.
    sd = 1.0 if annual else args.sd
    if args.bar is not None and sd is not None:
        half = Z_95 * sd / math.sqrt(n_used) * scale
        licensed = powered_null(-half, half, bar=args.bar)
        suffix = " annual" if annual else ""
        out += [
            "",
            f"  null bar          +/-{args.bar}{suffix}",
            f"  CI half-width     {half:.4f}{suffix}"
            "  (best case, point estimate exactly 0)",
            f"  powered null      {'LICENSABLE' if licensed else 'NOT LICENSABLE'}"
            f"  (analytics.audit_guard.powered_null)",
        ]
        if not licensed:
            out.append(
                "                    A null here would be INSUFFICIENT, never 'no effect'."
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lines = price(args)
    except ValueError as exc:
        print(f"distil_power: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
