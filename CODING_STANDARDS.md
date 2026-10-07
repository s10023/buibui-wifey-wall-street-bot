# Coding Standards

Read at **review** time, not during implementation. The standards reviewer in the
`mattpocock-skills:code-review` skill (and any other reviewer) checks a diff against this file.

Mechanical rules are enforced by tooling and are NOT review findings: `make lint-py` (ruff
format + lint), `make typecheck` (mypy strict), `make lint-md` and `make sanity-checks`. Skip
anything those catch.

## Where the judgement-call standards live

This file points rather than restates, so the rules have one home:

- `CLAUDE.md` → **Code style** and **Testing**: annotations, mock-client injection, in-memory
  DuckDB, no network in tests, positive controls for "did not change" assertions.
- `CLAUDE.md` → **Footguns**: storage and writers, gates and configuration, outcomes and fills,
  statistics, research ingest.
- `CLAUDE.md` → **Git conventions**.

## Review checklist this repo adds to the smell baseline

- **A restated constant is the defect.** A gate threshold, cost, fill rule or path written as a
  literal where an owning function or constant exists (`GATE_SHARPE` / `passes_sleeve_gate`,
  `DEFAULT_DB_PATH`, `implied_tp_r`, `live_cost_r`, `gap_fill_price`) is a finding even when the
  values agree today.
- **A write site opens through `connect_with_retry`.** A new `duckdb.connect` that writes, or an
  `except duckdb.IOException` not narrowed with `is_lock_conflict`, is a finding.
- **A guard needs a mutation case.** A new check, gate or hook without a test proving it can
  fail (teeth) and does not fire on clean input (specificity) is a finding.
- **A SKIP must not read as a PASS.** A check that degrades silently on missing input is a finding.
- **Causality.** A detector or study that reads bars after its decision bar is a finding;
  `tests/test_lookahead.py` covers registered detectors only.
