"""Guard: every positional INSERT must agree with its table's real column list.

Motivation (#151). ``put_backtest_cache`` writes
``INSERT OR REPLACE INTO backtest_cache VALUES (?,…)`` with a hand-counted
placeholder list, and ``schema.py`` states that constraint in prose only.
Adding ``r_long_sd`` / ``r_short_sd`` broke a pre-existing test's
``INSERT … SELECT`` — caught, but only by running the suite, and only because
that test happened to exist. Nothing mechanically tied the DDL to the INSERT.

Two things here differ from the obvious implementation, both deliberate:

1. **The truth side is the REAL schema, not parsed DDL text.** ``init_schema``
   runs against an in-memory DuckDB and the column list comes from
   ``information_schema.columns`` ordered by ``ordinal_position``. Four
   ``backtest_runs`` columns (``adr_suppress_threshold``, ``recovery_factor``,
   ``universe_policy``, ``cost_model``) exist ONLY in the migration list and
   never in ``CREATE TABLE``, so parsing the DDL would report a false failure on
   the very table this is meant to guard.

2. **The ``INSERT … SELECT`` shape is checked by NAME ORDER, not just count.**
   That form is positional against the table's column order, so a transposition
   of two same-typed columns is silent — a count check would pass it.

``test_every_insert_is_accounted_for`` closes the loop: an INSERT this parser
cannot judge must be named in an exemption set with a reason, so a new dynamic
INSERT fails loudly instead of silently escaping coverage.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from analytics.store.schema import init_schema

REPO_ROOT = Path(__file__).resolve().parent.parent

# Packages whose INSERTs are checked. `tests/` is excluded (fixtures build their
# own throwaway tables), and `migrations/` is excluded because a one-shot
# migration is a frozen snapshot of an older schema by design — see CLAUDE.md.
SCANNED_DIRS = ("analytics", "cli", "signals", "tools", "utils", "web")

# INSERTs built from an f-string: the table and/or column list are runtime
# values, so no static check can judge them. Listing the file is the check's
# honest coverage statement — a NEW dynamic INSERT must be added deliberately.
DYNAMIC_INSERT_FILES = {
    # _upsert(table, columns, df) — both are parameters, and the try/finally
    # register/unregister around it is load-bearing (see CLAUDE.md footguns).
    "analytics/store/_common.py",
}

# (file, table) pairs whose target table does not exist in the finished schema.
EXEMPT_TABLES = {
    # Transient migration table: created, filled, then renamed over
    # `confidence_ratings` inside init_schema, so it is absent afterwards.
    ("analytics/store/schema.py", "confidence_ratings_v2"),
}

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE)\s+)?INTO\s+(\w+)\s+(.*)",
    re.IGNORECASE,
)
_EXPLICIT_RE = re.compile(r"^\(([^)]*)\)\s*VALUES\s*\((.*?)\)", re.IGNORECASE)
_VALUES_RE = re.compile(r"^VALUES\s*\((.*?)\)", re.IGNORECASE)
_SELECT_RE = re.compile(r"^SELECT\s+(.*?)\s+FROM\s+\w+", re.IGNORECASE)


@dataclass(frozen=True)
class InsertStmt:
    """One INSERT statement recovered from Python source."""

    file: str
    table: str
    kind: str  # "select" | "values" | "explicit"
    columns: tuple[str, ...]  # names, for "select" / "explicit"
    placeholders: int  # count of '?', for "values" / "explicit"


def _schema_columns() -> dict[str, list[str]]:
    """Return {table: [column, ...]} in physical order from a real init_schema."""
    conn = duckdb.connect(":memory:")
    try:
        init_schema(conn)
        rows = conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(str(table), []).append(str(column))
    return out


def _python_files() -> list[Path]:
    files: list[Path] = []
    for name in SCANNED_DIRS:
        root = REPO_ROOT / name
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _sql_literals(tree: ast.Module) -> list[str]:
    """Every non-docstring string constant in the module, whitespace-normalised.

    Adjacent string literals are already joined by the Python parser, so a SQL
    statement split across a dozen source lines arrives here as one constant.
    Bare-expression strings (docstrings at any level) are skipped so prose that
    merely quotes an INSERT is not parsed as one.
    """
    docstring_ids = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        " ".join(node.value.split())
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_ids
    ]


def _split_columns(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _collect_inserts() -> tuple[list[InsertStmt], list[str]]:
    """Return (parsed INSERTs, files holding an f-string INSERT)."""
    found: list[InsertStmt] = []
    dynamic: list[str] = []
    for path in _python_files():
        rel = str(path.relative_to(REPO_ROOT))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and any(
                isinstance(v, ast.Constant)
                and isinstance(v.value, str)
                and "INSERT" in v.value.upper()
                and "INTO" in v.value.upper()
                for v in node.values
            ):
                dynamic.append(rel)
        for sql in _sql_literals(tree):
            match = _INSERT_RE.search(sql)
            if match is None:
                continue
            table, rest = match.group(1), match.group(2)
            explicit = _EXPLICIT_RE.match(rest)
            if explicit is not None:
                found.append(
                    InsertStmt(
                        rel,
                        table,
                        "explicit",
                        _split_columns(explicit.group(1)),
                        explicit.group(2).count("?"),
                    )
                )
                continue
            values = _VALUES_RE.match(rest)
            if values is not None:
                found.append(
                    InsertStmt(rel, table, "values", (), values.group(1).count("?"))
                )
                continue
            select = _SELECT_RE.match(rest)
            if select is not None:
                found.append(
                    InsertStmt(rel, table, "select", _split_columns(select.group(1)), 0)
                )
    return found, sorted(set(dynamic))


def _checkable(
    stmts: list[InsertStmt], schema: dict[str, list[str]]
) -> list[InsertStmt]:
    return [
        s for s in stmts if (s.file, s.table) not in EXEMPT_TABLES and s.table in schema
    ]


def test_insert_select_matches_column_order() -> None:
    """`INSERT … SELECT a, b, c FROM df` is positional — names AND order must match."""
    schema = _schema_columns()
    stmts, _ = _collect_inserts()
    failures = []
    for stmt in _checkable(stmts, schema):
        if stmt.kind != "select":
            continue
        expected = tuple(schema[stmt.table])
        if stmt.columns != expected:
            failures.append(
                f"{stmt.file}: INSERT INTO {stmt.table} SELECT list "
                f"({len(stmt.columns)} cols) does not match the table's "
                f"{len(expected)} columns in order.\n"
                f"  select: {list(stmt.columns)}\n"
                f"  schema: {list(expected)}"
            )
    assert not failures, "\n".join(failures)


def test_bare_values_insert_matches_column_count() -> None:
    """`INSERT INTO t VALUES (?,…)` must have one placeholder per column."""
    schema = _schema_columns()
    stmts, _ = _collect_inserts()
    failures = []
    for stmt in _checkable(stmts, schema):
        if stmt.kind != "values":
            continue
        expected = len(schema[stmt.table])
        if stmt.placeholders != expected:
            failures.append(
                f"{stmt.file}: INSERT INTO {stmt.table} VALUES has "
                f"{stmt.placeholders} placeholders but the table has "
                f"{expected} columns: {schema[stmt.table]}"
            )
    assert not failures, "\n".join(failures)


def test_explicit_column_insert_is_consistent() -> None:
    """`INSERT INTO t (a, b) VALUES (?, ?)` — arity matches and columns exist."""
    schema = _schema_columns()
    stmts, _ = _collect_inserts()
    failures = []
    for stmt in _checkable(stmts, schema):
        if stmt.kind != "explicit":
            continue
        if len(stmt.columns) != stmt.placeholders:
            failures.append(
                f"{stmt.file}: INSERT INTO {stmt.table} names "
                f"{len(stmt.columns)} columns but supplies {stmt.placeholders} "
                f"placeholders"
            )
        unknown = [c for c in stmt.columns if c not in schema[stmt.table]]
        if unknown:
            failures.append(
                f"{stmt.file}: INSERT INTO {stmt.table} names columns that do "
                f"not exist: {unknown}"
            )
    assert not failures, "\n".join(failures)


def test_every_insert_is_accounted_for() -> None:
    """No INSERT may silently escape the checks above.

    An unparsed or unknown-table INSERT must be listed in EXEMPT_TABLES /
    DYNAMIC_INSERT_FILES with a reason. This is what stops the guard from
    quietly shrinking to zero coverage as the codebase grows.
    """
    schema = _schema_columns()
    stmts, dynamic = _collect_inserts()

    unexplained_tables = sorted(
        {
            f"{s.file}: INSERT INTO {s.table}"
            for s in stmts
            if s.table not in schema and (s.file, s.table) not in EXEMPT_TABLES
        }
    )
    assert not unexplained_tables, (
        "INSERT targets a table that init_schema does not create; add it to "
        f"EXEMPT_TABLES with a reason if that is intended: {unexplained_tables}"
    )

    unexplained_dynamic = sorted(set(dynamic) - DYNAMIC_INSERT_FILES)
    assert not unexplained_dynamic, (
        "f-string INSERT cannot be checked statically; add the file to "
        f"DYNAMIC_INSERT_FILES with a reason: {unexplained_dynamic}"
    )

    # Guard the guard: if the parser silently stops matching, these drop to 0.
    checked = _checkable(stmts, schema)
    assert any(s.kind == "select" for s in checked)
    assert any(s.kind == "values" for s in checked)
    assert any(s.kind == "explicit" for s in checked)
