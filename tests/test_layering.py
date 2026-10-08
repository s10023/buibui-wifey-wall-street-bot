"""Layering / boundary contract tests.

Rule 1: ``analytics/*`` MUST NOT import from ``signals/*``.
Rule 2: ``signals/*`` MAY import from ``analytics/*`` (one direction only).
Rule 3: ``analytics.indicators_lib`` was removed in strat-3 — no file may import
        from it. Strategies, registries, and shared helpers live in
        ``analytics.strategies``.

Module-level allowlist for ``analytics/* → signals/*`` (each is a deliberate
domain-ownership boundary, not a violation):

* ``signals.cooldown_store`` — scanner uses ``CooldownStore`` for two-layer
  dedup state. ``signals/`` owns dedup.
* ``signals.registry`` — scanner uses ``SIGNAL_REGISTRY`` to filter and dispatch
  detectors. ``signals/`` owns the dispatch registry.
* ``signals.alert_formatter`` — scanner calls ``format_confluence_alert`` to
  build the Telegram message before dispatch. ``signals/`` owns alerting.

Even when an analytics file imports from one of the allowlisted modules, the
type dataclasses (``SignalEvent`` / ``StatsContext`` / ``ConfluenceData``) MUST
come from ``analytics.signal.types`` — the second test enforces that.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent

_ALLOWED_SIGNALS_IMPORTS_FROM_ANALYTICS: frozenset[str] = frozenset(
    {
        "signals.cooldown_store",
        "signals.registry",
        "signals.alert_formatter",
    }
)

_TYPES_OWNED_BY_ANALYTICS: frozenset[str] = frozenset(
    {"ConfluenceData", "SignalEvent", "StatsContext"}
)


def _iter_py_files(root: str) -> list[pathlib.Path]:
    base = REPO_ROOT / root
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_from(path: pathlib.Path, prefix: str) -> list[str]:
    """Return module names this file imports that start with ``prefix``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(prefix)
        ):
            hits.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix):
                    hits.append(alias.name)
    return hits


def _from_imports(path: pathlib.Path, prefix: str) -> list[tuple[str, list[str]]]:
    """Return ``(module, [names])`` for each ``from <prefix>... import ...`` line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith(prefix)
        ):
            out.append((node.module, [alias.name for alias in node.names]))
    return out


def test_analytics_does_not_import_from_signals() -> None:
    """``analytics/*`` MUST NOT import from ``signals/*`` (allowlist excepted)."""
    violations: list[tuple[str, list[str]]] = []
    for path in _iter_py_files("analytics"):
        hits = [
            mod
            for mod in _imports_from(path, "signals")
            if mod not in _ALLOWED_SIGNALS_IMPORTS_FROM_ANALYTICS
        ]
        if hits:
            violations.append((str(path.relative_to(REPO_ROOT)), hits))
    assert not violations, (
        "Layering violation: analytics/* imported from signals/*:\n"
        + "\n".join(f"  {p}: {hits}" for p, hits in violations)
    )


def test_signal_types_come_from_analytics() -> None:
    """``SignalEvent`` / ``StatsContext`` / ``ConfluenceData`` MUST be imported
    from ``analytics.signal.types`` (or its package re-export), never from
    ``signals.*``. This is the contract signal-1 ratifies.
    """
    violations: list[tuple[str, str, list[str]]] = []
    for path in _iter_py_files("analytics"):
        for module, names in _from_imports(path, "signals"):
            offending = [n for n in names if n in _TYPES_OWNED_BY_ANALYTICS]
            if offending:
                violations.append((str(path.relative_to(REPO_ROOT)), module, offending))
    assert not violations, (
        "Type ownership violation: analytics/* imported analytics-owned types from signals/*:\n"
        + "\n".join(f"  {p}: from {m} import {n}" for p, m, n in violations)
    )


def test_layering_test_covers_known_files() -> None:
    """Sanity: the walk picks up a non-trivial number of analytics files."""
    files = _iter_py_files("analytics")
    assert len(files) >= 10, (
        f"Layering test only walked {len(files)} files in analytics/ — likely a bug."
    )


_INDICATORS_LIB_SCAN_ROOTS: tuple[str, ...] = (
    "analytics",
    "cli",
    "monitor",
    "scripts",
    "signals",
    "tests",
    "trade",
    "utils",
    "web",
)


def test_no_imports_of_removed_indicators_lib() -> None:
    """``analytics.indicators_lib`` was removed in strat-3 — no file may import
    from it. Strategies, registries, and shared helpers all live under
    ``analytics.strategies``.
    """
    violations: list[str] = []
    for root in _INDICATORS_LIB_SCAN_ROOTS:
        if not (REPO_ROOT / root).exists():
            continue
        for path in _iter_py_files(root):
            if any(
                mod == "analytics.indicators_lib"
                or mod.startswith("analytics.indicators_lib.")
                for mod in _imports_from(path, "analytics.indicators_lib")
            ):
                violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, (
        "analytics.indicators_lib was removed in strat-3 — these files still "
        "import from it (use analytics.strategies instead):\n"
        + "\n".join(f"  {p}" for p in violations)
    )
