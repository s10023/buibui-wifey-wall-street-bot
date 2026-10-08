"""Tests for `tools/sanity_checks.py`.

Every check gets a **positive control** — an input that must produce a finding —
alongside a clean case. A check exercised only against the real tree proves
nothing while the tree is clean, which is the state it is supposed to be in.

The last test is the gate itself: it runs the whole sweep against this working
tree and requires zero findings. That is what makes this a check rather than a
script, per CLAUDE.md's *a self-check outside CI is not a check* — it fails the
suite, and therefore CI, the moment a doc surface drifts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from tools.child_env import python_child_env
from tools.sanity_checks import (
    CheckResult,
    Finding,
    check_cli_documented,
    check_config_strategies,
    check_context_coverage,
    check_fork_drift,
    check_missing_paths,
    check_parent_leakage,
    check_regression_surface,
    check_router_wiring,
    gather,
    is_path_like,
    makefile_targets,
    regression_filter_patterns,
    render,
    subcommand_names,
    surface_paths,
)

TARGETS = {"test", "lint-py"}
TIMEFRAMES = {"4h", "1d", "1wk"}
STRATEGIES = {"doji", "orb"}
SYMBOLS = {"AAPL", "MSFT"}


_UNSET: set[str] = set()


def _drift(text: str, symbols: set[str] | None = _UNSET) -> list[Finding]:
    """`symbols` defaults to the fixture; pass None to exercise the skip.

    A plain `symbols or SYMBOLS` would collapse an explicit None back to the
    fixture, which is how the skip test passed without ever taking the branch.
    """
    resolved = SYMBOLS if symbols is _UNSET else symbols
    return check_fork_drift(
        [("doc.md", text)], TARGETS, TIMEFRAMES, STRATEGIES, resolved
    )


class TestForkDrift:
    def test_clean_document_yields_nothing(self) -> None:
        text = "Run `make test` with `--interval 1d --strategy doji --symbol AAPL`."
        assert _drift(text) == []

    def test_catches_a_dead_make_target(self) -> None:
        assert _drift("Run `make buibui-backtest` first.") == [
            Finding("fork-drift", "doc.md: make-target=buibui-backtest")
        ]

    def test_catches_a_rejected_timeframe(self) -> None:
        assert _drift("pass --interval 15m here") == [
            Finding("fork-drift", "doc.md: timeframe=15m")
        ]

    def test_catches_a_removed_strategy(self) -> None:
        assert _drift("--strategy liquidity_sweep") == [
            Finding("fork-drift", "doc.md: strategy=liquidity_sweep")
        ]

    def test_catches_a_foreign_symbol(self) -> None:
        assert _drift("--symbol BTCUSDT") == [
            Finding("fork-drift", "doc.md: symbol=BTCUSDT")
        ]

    def test_bare_prose_is_not_a_make_target(self) -> None:
        """The reason the pattern requires a backtick, `$ ` or line start.

        Widening past `.claude/` made bare prose 6 of 7 hits.
        """
        assert _drift("This should make sense and make money.") == []

    def test_glob_and_placeholder_targets_are_ignored(self) -> None:
        assert _drift("`make wifey-` covers each subcommand") == []

    def test_template_placeholders_are_not_strategies(self) -> None:
        assert _drift("--strategy my_strategy") == []

    def test_symbol_leg_is_skippable(self) -> None:
        """`symbols=None` is how the leg degrades where the watchlist is absent."""
        assert _drift("--symbol BTCUSDT", symbols=None) == []
        # ...and the other legs still run.
        assert (
            len(
                check_fork_drift(
                    [("doc.md", "`make buibui-x` --symbol BTCUSDT")],
                    TARGETS,
                    TIMEFRAMES,
                    STRATEGIES,
                    None,
                )
            )
            == 1
        )


class TestParentLeakage:
    def test_catches_a_parent_artifact_in_a_skill(self) -> None:
        found = check_parent_leakage(
            [(".claude/skills/foo/SKILL.md", "run `make buibui-backtest`")]
        )
        assert len(found) == 1
        assert "make buibui-backtest" in found[0].detail

    def test_exempt_skill_is_silent(self) -> None:
        assert (
            check_parent_leakage(
                [(".claude/skills/sync-parent/SKILL.md", "cd buibui-moon-trader-bot")]
            )
            == []
        )

    def test_context_docs_are_exempt(self) -> None:
        assert (
            check_parent_leakage(
                [(".claude/context/tools.md", "the old coins.json path")]
            )
            == []
        )

    def test_scope_stops_at_dot_claude(self) -> None:
        """CLAUDE.md's fork-lineage paragraph is correct history, not drift.

        Widening this check to the top-level docs reproduces the prose-marker
        grep that was built, measured and rejected: it returns four hits that
        are all true statements about the fork's origin.
        """
        assert (
            check_parent_leakage(
                [
                    (
                        "CLAUDE.md",
                        "A fork of `s10023/buibui-moon-trader-bot`, frozen at 635ed5a",
                    )
                ]
            )
            == []
        )


class TestMissingPaths:
    def test_catches_a_path_that_is_simply_gone(self) -> None:
        found = check_missing_paths(
            [("doc.md", "see `analytics/ghost.py` for detail")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert found == [Finding("missing-paths", "doc.md: MISSING analytics/ghost.py")]

    def test_existing_path_is_clean(self) -> None:
        assert (
            check_missing_paths(
                [("doc.md", "`analytics/real.py`")],
                exists=lambda _p: True,
                ignored=lambda _p: False,
            )
            == []
        )

    def test_gitignored_path_is_clean(self) -> None:
        """The leg that makes the check CI-portable.

        `config/stocks.json` is absent in a clean checkout **by design**, so a
        hard-coded allowlist calibrated on a developer machine reports a false
        failure in CI. Asking git instead moves the answer to the only place
        that knows it.
        """
        assert (
            check_missing_paths(
                [("doc.md", "`config/stocks.json`")],
                exists=lambda _p: False,
                ignored=lambda _p: True,
            )
            == []
        )

    def test_allowlisted_path_is_clean(self) -> None:
        assert (
            check_missing_paths(
                [("doc.md", "`analytics/indicators_lib.py` was removed")],
                exists=lambda _p: False,
                ignored=lambda _p: False,
            )
            == []
        )

    def test_a_path_is_reported_once(self) -> None:
        found = check_missing_paths(
            [("a.md", "`tools/ghost.py`"), ("b.md", "`tools/ghost.py`")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert len(found) == 1


class TestContextCoverage:
    def test_catches_an_undocumented_package(self) -> None:
        assert check_context_coverage(
            ["analytics", "ghostpkg"], "analytics is here"
        ) == [Finding("context-coverage", "UNDOCUMENTED package: ghostpkg/")]

    def test_documented_package_is_clean(self) -> None:
        assert check_context_coverage(["analytics"], "the analytics layer") == []

    def test_exempt_package_is_clean(self) -> None:
        assert check_context_coverage(["trade", "deploy"], "") == []

    def test_match_is_word_bounded(self) -> None:
        """A substring hit would report a package documented by an unrelated word.

        A false-positive presence check is worse than none, because it reports
        covered.
        """
        assert check_context_coverage(["web"], "the cobweb module") == [
            Finding("context-coverage", "UNDOCUMENTED package: web/")
        ]


class TestRouterWiring:
    def test_all_three_lists_agreeing_is_clean(self) -> None:
        assert check_router_wiring(["a", "b"], ["a", "b"], ["a", "b"]) == []

    def test_catches_a_router_never_imported(self) -> None:
        found = check_router_wiring(["a", "b"], ["a"], ["a"])
        assert found == [
            Finding("router-wiring", "b: on disk, not imported by main.py")
        ]

    def test_catches_an_imported_router_never_registered(self) -> None:
        """The silent shape: it imports cleanly and serves 404s."""
        found = check_router_wiring(["a", "b"], ["a", "b"], ["a"])
        assert found == [Finding("router-wiring", "b: imported but never registered")]

    def test_catches_a_registration_with_no_module(self) -> None:
        found = check_router_wiring(["a"], ["a", "b"], ["a", "b"])
        assert any("no module on disk" in f.detail for f in found)


class TestConfigStrategies:
    def test_catches_a_key_naming_no_strategy(self) -> None:
        found = check_config_strategies(
            [("cfg.toml", {"strategy_params": {"doji": {}, "gone": {}}})], STRATEGIES
        )
        assert found == [
            Finding(
                "config-strategies",
                "cfg.toml: [strategy_params.gone] is not a strategy",
            )
        ]

    def test_real_keys_are_clean(self) -> None:
        assert (
            check_config_strategies(
                [("cfg.toml", {"strategy_params": {"doji": {}}})], STRATEGIES
            )
            == []
        )

    def test_config_without_the_table_is_clean(self) -> None:
        assert (
            check_config_strategies([("cfg.toml", {"backtest": {}})], STRATEGIES) == []
        )


class TestCliDocumented:
    def test_catches_an_undocumented_subcommand(self) -> None:
        assert check_cli_documented(["backtest", "digest"], "run backtest") == [
            Finding("cli-documented", "`wifey digest` is not mentioned in README.md")
        ]

    def test_documented_subcommands_are_clean(self) -> None:
        assert check_cli_documented(["backtest"], "the `wifey backtest` command") == []


class TestHelpers:
    def test_makefile_targets_reads_target_names(self) -> None:
        assert makefile_targets("test:\n\tpytest\nlint-py: fmt\n") == {
            "test",
            "lint-py",
        }

    def test_subcommand_names_reads_the_argparse_tree(self) -> None:
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="cmd")
        subs.add_parser("alpha")
        subs.add_parser("beta")
        assert subcommand_names(parser) == {"alpha", "beta"}

    def test_surface_paths_excludes_the_self_referential_skills(self) -> None:
        """Those two files quote the anti-patterns in order to hunt for them."""
        names = [str(p) for p in surface_paths()]
        assert not any(n.endswith("sanity-check/SKILL.md") for n in names)
        assert not any(n.endswith("post-branch/SKILL.md") for n in names)
        assert "CLAUDE.md" in names

    def test_render_counts_findings_but_not_notes(self) -> None:
        """A degraded leg must not make the sweep permanently red."""
        lines, total = render(
            [CheckResult("fork-drift", [], note="symbol leg skipped")]
        )
        assert total == 0
        assert any("note: symbol leg skipped" in line for line in lines)

    def test_render_counts_a_real_finding(self) -> None:
        _lines, total = render([CheckResult("x", [Finding("x", "bad")])])
        assert total == 1


@pytest.mark.skipif(
    not Path("CLAUDE.md").exists(), reason="not running from the repo root"
)
def test_working_tree_is_clean() -> None:
    """The gate: doc drift fails the suite, and therefore CI.

    This is the whole point of extracting the skill's shell blocks. A script
    that only runs when a human remembers eventually reports failure to nobody.
    """
    results = gather()
    findings = [f.detail for r in results for f in r.findings]
    assert findings == []


class TestPathLikeTail:
    """`module.symbol` is not a file path.

    A tail of `[A-Za-z0-9_/.]+` with no extension requirement would parse any
    dotted reference as a path. That was 3 of 3 `missing-paths` hits when this
    same code first ran against the sibling repo, whose docs use the notation.
    """

    def test_module_dot_symbol_is_not_a_path(self) -> None:
        found = check_missing_paths(
            [("doc.md", "see `analytics/xsmom/replay.replay_targets` for the target")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert found == []

    def test_a_real_missing_file_still_fires(self) -> None:
        """Positive control: the narrowing must not blind the check.

        Without this, `is_path_like` returning False for everything passes the
        test above and silences the whole leg.
        """
        found = check_missing_paths(
            [("doc.md", "see `analytics/ghost.py`")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert found == [Finding("missing-paths", "doc.md: MISSING analytics/ghost.py")]

    def test_directory_reference_has_no_dot_and_is_kept(self) -> None:
        found = check_missing_paths(
            [("doc.md", "see `analytics/backtest/`")],
            exists=lambda _p: False,
            ignored=lambda _p: False,
        )
        assert found == [
            Finding("missing-paths", "doc.md: MISSING analytics/backtest/")
        ]

    def test_is_path_like_unit(self) -> None:
        assert is_path_like("analytics/backtest_lib.py")
        assert is_path_like("config/strategy_params.toml")
        assert is_path_like("analytics/backtest/")
        assert is_path_like("analytics/strategies")
        assert not is_path_like("tools/multi_regime_power.required_sr")
        assert not is_path_like("analytics/xsmom/replay.replay_targets")


class TestSymbolMetavar:
    """An argparse metavar rode into the capture and could never be suppressed.

    `SYMBOL_RE`'s class admits `.`, so `--symbols SYM...` captured `SYM...`,
    which never equals the `SYM` in `PLACEHOLDER_SYMBOLS`. The finding was
    therefore permanent and un-actionable — the never-clean shape that trains
    dismissal of the whole sweep.
    """

    def test_metavar_resolves_to_its_placeholder(self) -> None:
        assert _drift("run `--symbols SYMBOL...` to pass many") == []

    def test_a_real_unknown_symbol_still_fires(self) -> None:
        """Positive control: `rstrip` must not swallow genuine drift."""
        assert _drift("run `--symbol DOGEUSDT` here") == [
            Finding("fork-drift", "doc.md: symbol=DOGEUSDT")
        ]

    def test_trailing_dash_is_also_stripped(self) -> None:
        assert _drift("run `--symbols SYMBOL-` now") == []


WORKFLOW = """jobs:
  lint-typecheck-test:
    steps:
      - uses: dorny/paths-filter@v4
        id: changes
        with:
          filters: |
            python:
              - 'analytics/**/*.py'
              - 'poetry.lock'
              - 'tests/test_regression.py'

  frontend-check:
    steps:
      - uses: dorny/paths-filter@v4
        with:
          filters: |
            frontend:
              - 'web/ui/**'
"""


class TestRegressionSurface:
    """CLAUDE.md's test-regression trigger list vs CI's own paths filter.

    The doc list can diverge from the workflow in both directions, and only the
    narrowing one is harmful (measured 2026-08-26), so these tests assert the
    finding exists as much as they assert clean.
    """

    def test_reads_the_regression_block_not_the_frontend_one(self) -> None:
        """Two `filters:` blocks in one file; a positional read grabs the wrong one."""
        assert regression_filter_patterns(WORKFLOW) == [
            "analytics/**/*.py",
            "poetry.lock",
            "tests/test_regression.py",
        ]

    def test_catches_a_path_ci_fires_on_that_the_doc_omits(self) -> None:
        doc = (
            "required when the diff touches `poetry.lock` or `tests/test_regression.py`"
        )
        assert check_regression_surface(doc, WORKFLOW) == [
            Finding(
                "regression-surface",
                "CLAUDE.md does not name CI filter path: analytics/**/*.py",
            )
        ]

    def test_a_verbatim_mirror_is_clean(self) -> None:
        doc = "`analytics/**/*.py`, `poetry.lock`, `tests/test_regression.py`"
        assert check_regression_surface(doc, WORKFLOW) == []

    def test_a_paraphrase_does_not_count_as_naming_the_path(self) -> None:
        """`analytics/backtest/` for `analytics/**/*.py` is exactly the old defect."""
        doc = "`analytics/backtest/`, `poetry.lock`, `tests/test_regression.py`"
        assert len(check_regression_surface(doc, WORKFLOW)) == 1

    def test_a_missing_filter_is_a_finding_never_a_silent_pass(self) -> None:
        """If the workflow moves, the check must say so rather than grade nothing."""
        findings = check_regression_surface("anything", "jobs:\n  build:\n")
        assert len(findings) == 1
        assert "cannot see what it grades" in findings[0].detail

    def test_the_live_tree_mirrors_its_own_ci_filter(self) -> None:
        """The real check, against the real files — the reason the leg exists."""
        workflow = Path(".github/workflows/lint.yaml").read_text(encoding="utf-8")
        assert regression_filter_patterns(workflow), "no regression filter found"
        assert (
            check_regression_surface(
                Path("CLAUDE.md").read_text(encoding="utf-8"), workflow
            )
            == []
        )


class TestBareInvocationRunsEveryLeg:
    """The venv bootstrap, end to end — parent #742/#760, ported 2026-09-22.

    The unit tests in `test_venv_bootstrap.py` cannot cover this. They prove the
    swap fires; this proves the swap fixes the thing it was added for. Measured before
    the fix: a bare run exited **0** printing `0 finding(s)` with three of eight legs
    reading `SKIPPED  (project dependencies are not installed)` — the same words the
    legs that skip legitimately use, so the wrong interpreter was invisible in a report
    that looked healthy.

    The negative control is the load-bearing half. A pass here is satisfied by two
    worlds, the swap working or the probe never reaching a degraded run at all, and
    the second is the shape this repo removed four instances of on 2026-09-21. Setting
    the sentinel suppresses the swap, so the control must observe the degraded report;
    if it does not, the discriminator is broken and both cases are meaningless.
    """

    @staticmethod
    def _foreign_interpreter(repo: Path) -> str | None:
        """An interpreter that is not this repo's venv, or None if there is none.

        Derived from `sys.base_prefix`, deliberately not from `PATH`. Probing
        `shutil.which("python3")` makes the whole test skip: under `poetry run` the
        venv's own `Scripts`/`bin` is first on `PATH`, so every candidate resolves to
        the venv and the probe concludes there is nothing to swap from. It would read
        green-by-skip forever, in CI too: the dead-check shape, reproduced inside the
        test written to prove a dead check had been fixed.
        `sys.base_prefix` is the base installation whenever we are inside a venv, which
        is exactly the interpreter a human types by accident.
        """
        import os
        import subprocess

        venv = (repo / ".venv").resolve()
        base = Path(sys.base_prefix)
        version = sys.version_info
        candidates = (
            [base / "python.exe"]
            if os.name == "nt"
            else [
                base / "bin" / f"python{version.major}.{version.minor}",
                base / "bin" / "python3",
            ]
        )
        for candidate in candidates:
            if not candidate.exists():
                continue
            probe = subprocess.run(  # noqa: S603 - fixed argv, shell=False
                [str(candidate), "-c", "import sys; print(sys.prefix)"],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                env=python_child_env(),  # a prefix path can be non-ASCII
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                continue
            if Path(probe.stdout.strip()).resolve() != venv:
                return str(candidate)
        return None

    @staticmethod
    def _run_bare(repo: Path, interpreter: str, *, sentinel: str | None) -> str:
        import subprocess

        from tools.venv_bootstrap import SENTINEL

        # cp1252 would fail this tree on the read, not the swap
        env = python_child_env(drop=("PYTHONPATH",))
        env.pop(SENTINEL, None)
        if sentinel is not None:
            env[SENTINEL] = sentinel
        proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            [interpreter, str(repo / "tools" / "sanity_checks.py")],
            capture_output=True,
            text=True,
            cwd=str(repo),
            env=env,
            check=False,
            encoding="utf-8",
        )
        return proc.stdout

    def test_a_foreign_interpreter_still_runs_the_dependency_legs(self) -> None:
        from tools.venv_bootstrap import _venv_python

        repo = Path(__file__).resolve().parent.parent
        if not _venv_python(repo / ".venv").exists():
            pytest.skip("no in-project .venv on this host; nothing to swap into")
        interpreter = self._foreign_interpreter(repo)
        if interpreter is None:
            pytest.skip("every interpreter on PATH is already this repo's venv")

        degraded = self._run_bare(repo, interpreter, sentinel="1")
        swapped = self._run_bare(repo, interpreter, sentinel=None)

        assert "dependencies are not installed" in degraded, (
            "NEGATIVE CONTROL FAILED: the sentinel did not produce a degraded run, so "
            "this test cannot tell a working swap from one that never fired"
        )
        assert "dependencies are not installed" not in swapped
