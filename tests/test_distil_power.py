"""Tests for `tools/distil_power.py` — the G3 gate's CLI."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "distil_power.py"
    spec = importlib.util.spec_from_file_location("distil_power", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["distil_power"] = mod
    spec.loader.exec_module(mod)
    return mod


distil_power = _load()


def test_units_is_mandatory() -> None:
    """An undeclared unit is how a portable-looking number changes meaning."""
    with pytest.raises(SystemExit):
        distil_power.main(
            ["--n-obs", "1000", "--n-trials", "16", "--sr-variance", "0.05"]
        )


def test_benign_family_is_reachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "20000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "REACHABLE" in out
    assert "UNREACHABLE" not in out


def test_hostile_family_is_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    """A tiny sample against a wide trial family cannot clear the gate at any effect size.

    n_obs=2 is the ONLY value that works here, and it was measured rather than
    reasoned: the PSR z-statistic is bounded above by ``sqrt(2*(n_obs-1))``, so
    against ``Z_GATE(0.95) = 1.6449`` n=2 gives z_max 1.4142 (unreachable) but
    n=3 gives 2.0000 and a finite required Sharpe of 4.6463. Do not raise this
    number to make the test "more realistic" — it stops testing anything.
    """
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "2",
            "--n-trials",
            "320",
            "--sr-variance",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "UNREACHABLE" in out


def test_correlation_deflator_raises_the_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pooling correlated series must make the bar HARDER, never easier."""
    base = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--sr-footing",
                "per_obs",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
            ]
        )
    )
    deflated = distil_power.price(
        distil_power.parse_args(
            [
                "--units",
                "per_alert",
                "--sr-footing",
                "per_obs",
                "--n-obs",
                "4000",
                "--n-trials",
                "16",
                "--sr-variance",
                "0.05",
                "--n-series",
                "25",
                "--n-eff",
                "2.92",
            ]
        )
    )
    # "effective n" appears in BOTH branches, so asserting on it tests nothing.
    # "deflated by" is printed only when the deflator actually applied.
    assert "deflated by" in "\n".join(deflated)
    assert "no deflator applied" in "\n".join(base)
    assert "\n".join(base) != "\n".join(deflated)

    # The label check above would still pass if the deflator made the bar
    # EASIER — the exact failure class this tool exists to prevent. Assert the
    # numeric direction too, so a magnitude regression fails this test.
    def _required_sharpe(lines: list[str]) -> float:
        (line,) = [ln for ln in lines if "required Sharpe" in ln]
        return float(line.split()[-1])

    assert _required_sharpe(deflated) > _required_sharpe(base)


def test_effect_size_reported_when_sd_given(capsys: pytest.CaptureFixture[str]) -> None:
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--sd",
            "1.2",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "required effect" in out
    assert "corpus best" in out


def test_null_containment_uses_the_owning_predicate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--bar must report a containment verdict, never an |delta| < MDE comparison."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "1",
            "--sr-variance",
            "0.0",
            "--sd",
            "1.0",
            "--bar",
            "0.05",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "powered null" in out.lower()


def test_effective_n_rejects_n_eff_exceeding_n_series() -> None:
    """A deflator cannot claim more independent series than series exist.

    Reviewer-found regression: reversed ``--n-series``/``--n-eff`` (3 series,
    25 "effective" ones) inflated n_used and LOWERED the required Sharpe — the
    bar got easier, the exact failure class this tool exists to prevent.
    """
    with pytest.raises(ValueError, match=r"n_eff .* cannot exceed n_series"):
        distil_power.effective_n(4000, 3, 25.0)


def test_reversed_deflator_args_rejected_via_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The exact reviewer-reported case: --n-series 3 --n-eff 25 must not silently price."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--n-series",
            "3",
            "--n-eff",
            "25",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "cannot exceed" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_single_flag_deflator_error_exits_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--n-series with no --n-eff (or vice versa) must exit 2, not raise a traceback."""
    code = distil_power.main(
        [
            "--units",
            "per_alert",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--n-series",
            "25",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "supplied together" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def _verdict_lines(out: str) -> list[str]:
    """Every VERDICT line, stripped — so a bare pass cannot hide behind a prefix."""
    return [ln.strip() for ln in out.splitlines() if ln.strip().startswith("VERDICT")]


def test_corpus_best_without_sd_never_reads_as_a_cleared_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--corpus-best` alone cannot be compared, and must not print a bare pass.

    Parent #692 (ST76). The comparison needs `--sd` to convert the required
    Sharpe into effect units; without it the comparison would fall through to the
    same bare ``VERDICT REACHABLE`` a genuine pass prints. `/research-distil`'s
    G3 gate mandates running this tool, so *did not compare* read as *passed*.
    """
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "corpus best" in out
    # The whole point: the un-comparable case is distinguishable from a pass.
    assert _verdict_lines(out) == [
        "VERDICT           REACHABLE, corpus best NOT COMPARED"
    ]
    assert "--sd" in out


def test_corpus_best_with_sd_still_reads_as_a_clean_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Positive control: the guard must not swallow a genuine comparison.

    Same family, same corpus best, `--sd` supplied — this one really does clear
    the bar, so it must still print the unqualified verdict. Without this the
    test above passes equally if the tool stopped ever emitting a clean pass.
    """
    code = distil_power.main(
        [
            "--units",
            "per_trade",
            "--sr-footing",
            "per_obs",
            "--n-obs",
            "4000",
            "--n-trials",
            "16",
            "--sr-variance",
            "0.05",
            "--sd",
            "1.2",
            "--corpus-best",
            "1.196",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert _verdict_lines(out) == ["VERDICT           REACHABLE"]
    assert "NOT COMPARED" not in out


# H-023's G3 inputs: 2,174 NYSE sessions, a 4-trial family, and the variance of
# the eight annualized sleeve Sharpes (0.0652) beside the annualized corpus best
# (0.41). Retraction audit: docs/audits/2026-09-27-distil-power-units-retraction.md.
_H023 = ["--units", "per_book_day", "--n-obs", "2174", "--n-trials", "4"]


def _sharpe_after(label: str, out: str) -> float:
    (line,) = [ln for ln in out.splitlines() if ln.strip().startswith(label)]
    return float(line.split()[2])


def test_footing_is_mandatory() -> None:
    """The recipe that priced H-023 at 0.3047 declared no footing; it must not run."""
    with pytest.raises(SystemExit):
        distil_power.main([*_H023, "--sr-variance", "0.0652"])


def test_mixed_footing_reproduces_the_filed_bar() -> None:
    """Positive control: the defect's arithmetic is exactly the filed 0.3047.

    Without this, the tests below would pass equally if the conversion were a
    no-op on some other path, because they never see the unconverted number.
    """
    bar = distil_power.required_sharpe(2174, n_trials=4, sr_variance=0.0652)
    assert bar == pytest.approx(0.3047, abs=5e-5)
    # ...and on the annual footing that per-day bar is 4.84, not a plausible 0.3.
    assert bar * math.sqrt(252) == pytest.approx(4.84, abs=5e-3)


def test_annual_footing_converts_to_the_repriced_bar(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = distil_power.main(
        [
            *_H023,
            "--sr-footing",
            "annual",
            "--periods-per-year",
            "252",
            "--sr-variance",
            "0.0652",
            "--corpus-best",
            "0.41",
            "--bar",
            "0.70",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert _sharpe_after("required Sharpe", out) == pytest.approx(0.83, abs=5e-3)
    assert _verdict_lines(out) == [
        "VERDICT           REACHABLE, but the bar EXCEEDS the corpus best"
    ]
    assert _sharpe_after("CI half-width", out) == pytest.approx(0.667, abs=1e-3)


def test_both_footings_price_the_same_bar(capsys: pytest.CaptureFixture[str]) -> None:
    """Declaring the footing must change the arithmetic, never the answer."""
    distil_power.main(
        [
            *_H023,
            "--sr-footing",
            "annual",
            "--periods-per-year",
            "252",
            "--sr-variance",
            "0.0652",
        ]
    )
    annual = _sharpe_after("required Sharpe", capsys.readouterr().out)
    distil_power.main(
        [*_H023, "--sr-footing", "per_obs", "--sr-variance", str(0.0652 / 252)]
    )
    per_obs = _sharpe_after("required Sharpe", capsys.readouterr().out)
    assert annual == pytest.approx(per_obs * math.sqrt(252), rel=1e-5)


def test_annualized_variance_declared_per_obs_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = distil_power.main(
        [*_H023, "--sr-footing", "per_obs", "--sr-variance", "0.0652"]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "--sr-footing annual --periods-per-year 252" in err


def test_annualized_corpus_best_declared_per_obs_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A plausible variance does not launder an annualized corpus best."""
    code = distil_power.main(
        [
            *_H023,
            "--sr-footing",
            "per_obs",
            "--sr-variance",
            "0.00025879",
            "--sd",
            "1.0",
            "--corpus-best",
            "0.41",
        ]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "--corpus-best 0.41" in err


def test_per_obs_book_day_inputs_still_price(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Positive control for the two refusals: honest per-day inputs pass the guard."""
    code = distil_power.main(
        [
            *_H023,
            "--sr-footing",
            "per_obs",
            "--sr-variance",
            "0.00025879",
            "--sd",
            "1.0",
            "--corpus-best",
            "0.025828",
        ]
    )
    assert code == 0
    assert "required Sharpe" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ([], "needs --periods-per-year"),
        (["--periods-per-year", "252", "--sd", "1.0"], "--sd does not apply"),
        (["--periods-per-year", "0"], "must be > 0"),
    ],
)
def test_annual_footing_declared_errors(
    extra: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = distil_power.main(
        [*_H023, "--sr-footing", "annual", "--sr-variance", "0.0652", *extra]
    )
    assert code == 2
    assert message in capsys.readouterr().err
