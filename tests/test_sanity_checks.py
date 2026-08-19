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
from pathlib import Path

import pytest

from tools.sanity_checks import (
    CheckResult,
    Finding,
    check_cli_documented,
    check_config_strategies,
    check_context_coverage,
    check_fork_drift,
    check_missing_paths,
    check_parent_leakage,
    check_router_wiring,
    gather,
    is_path_like,
    makefile_targets,
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
    """`module.symbol` is not a file path, and used to be reported as one.

    The tail was `[A-Za-z0-9_/.]+` with no extension requirement, so any dotted
    reference parsed as a path. It was 3 of 3 `missing-paths` hits when this same
    code first ran against the sibling repo, whose docs use the notation.
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
