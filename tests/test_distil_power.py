"""Tests for `tools/distil_power.py` — the G3 gate's CLI."""

from __future__ import annotations

import importlib.util
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
