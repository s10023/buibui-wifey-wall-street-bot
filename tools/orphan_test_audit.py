#!/usr/bin/env python3
"""Report test classes that NAME a unit but never CALL it.

Motivation (#150). ``tests/test_backtest_filter.py::TestEvGate`` held five tests
that never invoked the EV gate: the gate was ``def _passes_ev_gate`` nested
inside ``run_scan_cycle`` and therefore unimportable, so every test
re-implemented the comparison in its own body. One asserted the defect as the
expectation (``len(result.closed_trades) < cfg.effective_min_trades("4h")``) and
another reduced to ``assert None is None``. All five passed against any
implementation. ``/sanity-check`` and ``/post-branch`` passed over this
indefinitely: the file exists, the names are apt, and the suite is green.

**Two obvious detectors do not work**, both tried and discarded:

* *"Flag classes that never reference an importable symbol."* Misses this case
  entirely — those tests build a real ``BacktestFilterConfig`` and a real
  ``BacktestResult``. What they never touch is the subject their own name claims.
* *"Substring-match the class subject against every callable."* 95 findings on a
  clean tree, nearly all junk (``p_r`` "matches" ``directional_tp_r``). Matching
  is therefore token-based and directional: the callable's name must contain the
  subject's tokens **contiguously and in order**, so ``ev_gate`` matches
  ``_passes_ev_gate`` and ``p_r`` matches neither.

Two verdicts, both actionable:

``not-importable``
    The subject matches a NESTED function — a closure — and no module-level
    callable. The unit is unreachable from a test, so the tests can only
    re-implement it. This is the pre-#150 ``TestEvGate`` shape exactly, and it
    is why extraction is a prerequisite for a fix, not scope creep.

``not-called``
    A module-level callable matches, but no test in the class ever calls it.
    Ordinary drift: the unit was extracted or renamed and the tests kept
    exercising something else.

A class whose subject matches nothing at all is NOT reported — descriptive class
names (``TestWatermarkOnSend``) are legitimate and produced most of the noise in
the discarded formulation.

Advisory by default (exit 0). Pass ``--strict`` for a non-zero exit.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
FIRST_PARTY_DIRS = ("analytics", "cli", "signals", "tools", "utils", "web")

# Known false positives, keyed "<test file>::<class>". Each entry needs a reason:
# an unexplained allowlist is how a check decays into a no-op, and a check that
# always prints the same findings gets ignored, which is the same thing.
#
# Three FP shapes show up, all legitimate — the subject IS exercised, just not by
# a call this parser can see from inside the class body:
EXEMPT_CLASSES: dict[str, str] = {
    # (1) reached through a local test helper that wraps the subject
    "tests/test_pundit_score.py::TestScoreCall": "calls the local _score() wrapper",
    # (2) reached through the real CLI entry point, which dispatches to it
    "tests/test_x_route.py::TestCheckLevelsCli": "drives _check_levels_cli via main()",
    # (3) the subject is a PARAMETER name, not a callable — the matched
    #     candidates (_day_filter_to_weekdays, query_day_filter_ab) are unrelated
    "tests/test_signal_lib.py::TestDayFilter": "day_filter is a param of scan_symbol",
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class Callable_:
    tokens: tuple[str, ...]
    name: str
    module: str


@dataclass(frozen=True)
class Finding:
    verdict: str  # "not-importable" | "not-called"
    file: str
    lineno: int
    cls: str
    subject: str
    detail: str


def _tokens(name: str) -> tuple[str, ...]:
    snake = _CAMEL_BOUNDARY.sub("_", name).lower()
    return tuple(part for part in snake.split("_") if part)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """True when `needle` appears in `haystack` contiguously and in order."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def _collect_callables() -> tuple[list[Callable_], list[Callable_]]:
    """Return (module-level callables, nested/closure callables).

    Module-level = importable: top-level functions, classes, and class methods.
    Nested = defined inside a function body, therefore unreachable from a test.
    """
    top: list[Callable_] = []
    nested: list[Callable_] = []

    def walk_nested(node: ast.AST, rel: str) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                nested.append(Callable_(_tokens(child.name), child.name, rel))

    for dirname in FIRST_PARTY_DIRS:
        root = REPO_ROOT / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(REPO_ROOT))
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    top.append(Callable_(_tokens(node.name), node.name, rel))
                    for stmt in node.body:
                        walk_nested(stmt, rel)
                elif isinstance(node, ast.ClassDef):
                    top.append(Callable_(_tokens(node.name), node.name, rel))
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                            top.append(Callable_(_tokens(sub.name), sub.name, rel))
                            for stmt in sub.body:
                                walk_nested(stmt, rel)
    return top, nested


def _called_tokens(node: ast.ClassDef) -> list[tuple[str, ...]]:
    """Token tuples for every callable invoked anywhere in the class body."""
    out: list[tuple[str, ...]] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            out.append(_tokens(func.id))
        elif isinstance(func, ast.Attribute):
            out.append(_tokens(func.attr))
    return out


def audit() -> list[Finding]:
    top, nested = _collect_callables()
    findings: list[Finding] = []

    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.startswith("Test"):
                continue
            if f"{rel}::{node.name}" in EXEMPT_CLASSES:
                continue
            subject = _tokens(node.name)[1:]  # drop the leading "test"
            # A single-token subject ("TestConfig") is too vague to match on.
            if len(subject) < 2:
                continue

            top_hits = [c for c in top if _contains(c.tokens, subject)]
            if top_hits:
                if not any(_contains(t, subject) for t in _called_tokens(node)):
                    names = ", ".join(sorted({c.name for c in top_hits})[:4])
                    where = ", ".join(sorted({c.module for c in top_hits})[:3])
                    findings.append(
                        Finding(
                            "not-called",
                            rel,
                            node.lineno,
                            node.name,
                            "_".join(subject),
                            f"importable candidates exist ({names} in {where}) "
                            f"but no test in this class calls any of them",
                        )
                    )
                continue

            nested_hits = [c for c in nested if _contains(c.tokens, subject)]
            if nested_hits:
                names = ", ".join(sorted({c.name for c in nested_hits})[:4])
                where = ", ".join(sorted({c.module for c in nested_hits})[:3])
                findings.append(
                    Finding(
                        "not-importable",
                        rel,
                        node.lineno,
                        node.name,
                        "_".join(subject),
                        f"the unit exists only as a closure ({names} in {where}) "
                        f"— no test can call it, so these tests can only "
                        f"re-implement it; extract it first",
                    )
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when findings exist (default: advisory, exit 0)",
    )
    args = parser.parse_args()

    findings = audit()
    if not findings:
        print("✅ orphan-test audit: every Test* class calls the unit it names")
        return 0

    by_verdict: dict[str, list[Finding]] = {}
    for f in findings:
        by_verdict.setdefault(f.verdict, []).append(f)

    for verdict in ("not-importable", "not-called"):
        rows = by_verdict.get(verdict, [])
        if not rows:
            continue
        print(f"\n{verdict}  ({len(rows)})")
        print("-" * 72)
        for f in rows:
            print(f"  {f.file}:{f.lineno}  {f.cls}  (subject: {f.subject})")
            print(f"      {f.detail}")

    print(f"\n{len(findings)} finding(s). Advisory — review, do not auto-fix.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
