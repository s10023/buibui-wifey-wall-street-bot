# Research-Guards Commit-Gate Wiring (N2 PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-ported `analytics/sweep_guard.evaluate_commit_gate`
into `run_param_sweep` so a WFO sweep returns a commit-gate verdict
(COMMIT / DO_NOT_COMMIT / INSUFFICIENT) alongside its rows, and surface that
verdict in the CLI sweep footer.

**Architecture:** Port the parent's `ParamSweepReport{rows, gate, n_grid}`
return shape and its three adapter helpers (`_recommended_row`,
`_row_to_trialperf`, `_compute_sweep_gate`) verbatim, adapting only to wifey's
`SweepRow` API (which already exposes `.overfit`, `.is_result`, `.oos_result`).
`run_param_sweep` changes its return type from `list[SweepRow]` to
`ParamSweepReport`; every caller unpacks `.rows`. `format_sweep_results` gains a
keyword-only `gate=` parameter and appends a `_fmt_gate` footer — additive, so
the existing Phase 0.3a/b "Overfit controls" display block is untouched.

**Tech Stack:** Python 3.11, pytest, ruff, mypy strict. Pure-stdlib/numpy guards
(no new deps). The commit-gate math already lives in `analytics/sweep_guard.py`
and `analytics/research_guards/` (shipped in PR #81).

---

## Scope

**In scope (this PR):**

1. `analytics/param_sweep.py` — `ParamSweepReport`, `_empty_report`,
   `_recommended_row`, `_row_to_trialperf`, `_compute_sweep_gate`, `_fmt_gate`;
   `run_param_sweep` returns the report; `format_sweep_results` gains `gate=`.
2. Caller updates: `cli/param.py`, the in-file `main()` in
   `analytics/param_sweep.py`, `tools/multi_symbol_wfo.py`, and the affected
   tests in `tests/test_param_sweep.py`.
3. `/param-sweep-apply` and `/wfo-sweep` skill docs — refuse to commit a
   `DO_NOT_COMMIT` verdict.

**Deferred (note in the PR body, do not implement here):**

- `audit_guard.evaluate_audit_cells` wiring. The parent wires it into
  `tools/gate_audit.py` / `tools/adr_threshold_audit.py`; **neither tool exists
  in wifey** (wifey's audit surface is `regime_threshold_sweep.py`,
  `direction_filter_replay.py`, `regime_gate_replay.py`,
  `bos_routing_audit.py`). There is no ±0.05R bar to replace, so this wiring has
  no home until a wifey audit tool needs it. TA sweeps are frozen, lowering
  urgency further.

**Why goldens stay byte-identical:** `test_regression._extract_metrics`
enumerates metric fields explicitly and never reads a gate/overfit field, and
the engine path is unchanged. The only moved output is CLI sweep text (not under
golden test).

---

## Reference (parent — `s10023/buibui-moon-trader-bot`)

Exact shapes to mirror (already read; reproduced in the tasks below):

- `analytics/param_sweep.py:226-286` — `ParamSweepReport`, `_empty_report`,
  `_recommended_row`, `_row_to_trialperf`, `_compute_sweep_gate`.
- `analytics/param_sweep.py:490-495` — `run_param_sweep` tail (gate over full
  grid, then top-N truncation, return report).
- `analytics/param_sweep.py:545-567` — `_fmt_gate`.
- `analytics/param_sweep.py:570-632` — `format_sweep_results` `gate=` kwarg +
  `lines.extend(_fmt_gate(gate))`.
- `cli/param.py:81-86` — `format_sweep_results(report.rows, …, gate=report.gate)`.
- `tests/test_param_sweep.py:232-284` — `_verdict` helper + `TestCommitGateWiring`.

---

## File Structure

- `analytics/param_sweep.py` (modify) — add the report dataclass + 4 helpers +
  `_fmt_gate`; change `run_param_sweep` return; extend `format_sweep_results`;
  fix the in-file `main()`. Imports `CommitGateVerdict`, `DECISION_INSUFFICIENT`,
  `TrialPerf`, `evaluate_commit_gate` from `analytics.sweep_guard`.
- `cli/param.py` (modify) — unpack `.rows`, pass `gate=`.
- `tools/multi_symbol_wfo.py` (modify) — unpack `.rows`.
- `tests/test_param_sweep.py` (modify) — add `_verdict` helper + gate-wiring
  tests; update the two `run_param_sweep` call sites to read `.rows`.
- `.claude/skills/param-sweep-apply/SKILL.md`,
  `.claude/skills/wfo-sweep/SKILL.md` (modify) — refuse `DO_NOT_COMMIT`.

---

## Task 1: Report dataclass and adapter helpers

**Files:**

- Modify: `analytics/param_sweep.py` (imports near top; new block after the
  `SweepRow` definition, around line 241)
- Test: `tests/test_param_sweep.py`

- [ ] **Step 1: Add the `_verdict` test helper and failing wiring tests**

In `tests/test_param_sweep.py`, extend the `analytics.param_sweep` import block
(lines 14-28) to add `ParamSweepReport`, `_recommended_row`, `_row_to_trialperf`,
`_compute_sweep_gate`, and add an import from `analytics.sweep_guard`:

```python
from analytics.sweep_guard import CommitGateVerdict
```

Add the `_verdict` helper near the other helpers (after `_make_sweep_row`,
~line 119):

```python
def _verdict(
    decision: str, reasons: list[str] | None = None
) -> CommitGateVerdict:
    return CommitGateVerdict(
        decision=decision,
        dsr=0.97,
        pbo=0.10,
        min_trl=30.0,
        n_obs=40,
        n_trials=99,
        reasons=reasons or [],
    )
```

Add a new test class (place it just before
`class TestFormatSweepResultsDirectional`):

```python
class TestCommitGateWiring:
    def test_row_to_trialperf_pools_is_and_oos(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long")], short_trades=[_loss("short")]
        )
        # add an IS trade so pooling is observable
        row.is_result.trades.append(_win("long", 2.0))
        tp = _row_to_trialperf(row)
        # 1 IS win + 1 OOS win + 1 OOS loss = 3 closed trades
        assert len(tp.returns) == 3
        assert len(tp.times) == len(tp.returns)
        assert tp.returns.count(-1.0) == 1  # the short loss

    def test_recommended_row_skips_overfit(self) -> None:
        overfit = _make_sweep_row(
            tp_r=1.0, overfit=True, long_trades=[_win("long")]
        )
        clean = _make_sweep_row(
            tp_r=2.0, overfit=False, long_trades=[_win("long")]
        )
        assert _recommended_row([overfit, clean]) is clean
        assert _recommended_row([overfit]) is None

    def test_compute_sweep_gate_insufficient_when_no_clean_row(self) -> None:
        overfit = _make_sweep_row(overfit=True, long_trades=[_win("long")])
        gate = _compute_sweep_gate([overfit], None, n_grid=1)
        assert gate.decision == "INSUFFICIENT"
        assert not gate.committable
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
poetry run pytest tests/test_param_sweep.py::TestCommitGateWiring -v
```

Expected: FAIL — `ImportError` for `_row_to_trialperf` / `_recommended_row` /
`_compute_sweep_gate` / `ParamSweepReport`.

- [ ] **Step 3: Add the imports to `analytics/param_sweep.py`**

Find the import region near the top of the file (where `OverfitStats` and other
`analytics.*` symbols are imported) and add:

```python
from analytics.sweep_guard import (
    CommitGateVerdict,
    DECISION_INSUFFICIENT,
    TrialPerf,
    evaluate_commit_gate,
)
```

- [ ] **Step 4: Add the report dataclass + helpers after `SweepRow`**

Insert immediately after the `_row_from_results` function (i.e. after the
`SweepRow` block, before `_dedup_trades`, ~line 271):

```python
# ---------------------------------------------------------------------------
# Commit gate (N2) — overfitting refusal for the chosen tp_r
# ---------------------------------------------------------------------------


@dataclass
class ParamSweepReport:
    """``run_param_sweep`` result: the (truncated) rows plus the commit gate.

    ``gate`` is computed over the **full** grid (``n_grid`` trials) before the
    top-N truncation, so the deflation reflects the true search size.
    """

    rows: list[SweepRow]
    gate: CommitGateVerdict
    n_grid: int


def _empty_report(reason: str) -> ParamSweepReport:
    return ParamSweepReport(
        rows=[],
        gate=CommitGateVerdict(
            DECISION_INSUFFICIENT, None, None, None, 0, 0, [reason]
        ),
        n_grid=0,
    )


def _recommended_row(rows: list[SweepRow]) -> SweepRow | None:
    """Top non-overfit config — the one the apply-skill would commit."""
    clean = [r for r in rows if not r.overfit]
    return clean[0] if clean else None


def _row_to_trialperf(row: SweepRow) -> TrialPerf:
    """Adapt a SweepRow into the gate's per-trial return series.

    Pools the IS + OOS closed trades (disjoint by timestamp) into a single
    chronological full-window series of (entry_time, R)."""
    trades = list(row.is_result.closed_trades) + list(
        row.oos_result.closed_trades
    )
    pairs = sorted((t.entry_time, t.pnl_r) for t in trades if t.pnl_r is not None)
    label = ", ".join(f"{k}={v}" for k, v in row.params.items())
    return TrialPerf(
        label=label,
        returns=[r for _, r in pairs],
        times=[ts for ts, _ in pairs],
    )


def _compute_sweep_gate(
    trial_rows: list[SweepRow], chosen_row: SweepRow | None, n_grid: int
) -> CommitGateVerdict:
    """Gate verdict: deflate ``chosen_row`` against the full ``trial_rows`` grid.

    ``chosen_row`` is the recommended (committable) config the apply-skill would
    write; ``trial_rows`` is the whole grid (for cross-trial variance + PBO)."""
    if chosen_row is None:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            0,
            len(trial_rows),
            ["no non-overfit config to evaluate"],
        )
    chosen = _row_to_trialperf(chosen_row)
    all_trials = [_row_to_trialperf(r) for r in trial_rows]
    return evaluate_commit_gate(chosen, all_trials, n_grid=n_grid)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run:

```bash
poetry run pytest tests/test_param_sweep.py::TestCommitGateWiring -v
```

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "feat(guards): add ParamSweepReport + sweep-gate adapters (N2 PR 2)"
```

---

## Task 2: `run_param_sweep` returns a report

**Files:**

- Modify: `analytics/param_sweep.py` (`run_param_sweep` signature/return ~521,
  early returns 552/578/603, tail 700-714; in-file `main()` 1298/1313)
- Test: `tests/test_param_sweep.py` (existing call sites 745/758, 787-794)

- [ ] **Step 1: Update the existing tests to read `.rows` (red)**

In `tests/test_param_sweep.py`:

`test_purged_cv_pools_train_and_test_folds` — change the call + assertions
(lines ~745-758) so the result is a report:

```python
        report = run_param_sweep(
            conn=MagicMock(),
            strategy="bos",
            symbol="AAPL",
            timeframe="1h",
            days=30,
            param_ranges=[ParamRange("tp_r", [1.0])],
            wfo_split=0.7,
            min_trades=2,
            fee_pct=0.0,
            top_n=5,
            cv=CvConfig(mode="purged", n_folds=5, embargo_bars=1),
        )
        rows = report.rows
        assert len(rows) == 1
        assert report.gate is not None
        assert report.n_grid == 1
```

`test_contiguous_cv_config_matches_cv_none` — unpack `.rows` (lines ~787-794):

```python
        legacy = run_param_sweep(conn=MagicMock(), **kwargs).rows
        contiguous = run_param_sweep(
            conn=MagicMock(), cv=CvConfig(mode="contiguous"), **kwargs
        ).rows
        assert [r.params for r in legacy] == [r.params for r in contiguous]
        assert [r.is_score for r in legacy] == [r.is_score for r in contiguous]
        assert [r.oos_score for r in legacy] == [r.oos_score for r in contiguous]
        assert [r.decay for r in legacy] == [r.decay for r in contiguous]
```

- [ ] **Step 2: Run to verify they fail**

Run:

```bash
poetry run pytest tests/test_param_sweep.py::TestPurgedCv -v
```

Expected: FAIL — `run_param_sweep` still returns a `list` (`.rows` /
`.gate` / `.n_grid` `AttributeError`). (Use whichever class names the two tests
live under; run the whole file if unsure.)

- [ ] **Step 3: Change the `run_param_sweep` return type + early returns**

Change the signature return annotation (line 521):

```python
) -> ParamSweepReport:
```

Replace each of the three early `return []` statements:

- Line ~552 (no OHLCV): `return _empty_report("no OHLCV data")`
- Line ~578 (detection failed): `return _empty_report("signal detection failed")`
- Line ~603 (CV split error): `return _empty_report(str(e))`

Replace the tail (lines ~705-714, the sort + `return rows[:top_n]`) with:

```python
    all_zero = all(r.is_score == 0.0 for r in rows)
    if all_zero:
        rows.sort(key=lambda r: r.is_avg_r or float("-inf"), reverse=True)
    else:
        rows.sort(key=lambda r: r.is_score, reverse=True)

    top_rows = rows[:top_n]
    # Gate the recommended (top non-overfit) row the apply-skill would commit,
    # deflated against the full grid (n combos) so a truncated top-N cannot
    # inflate DSR.
    gate = _compute_sweep_gate(rows, _recommended_row(top_rows), n_grid=len(grid))
    return ParamSweepReport(rows=top_rows, gate=gate, n_grid=len(grid))
```

Note: `n_grid` is the **full** grid size. Confirm the local variable holding the
param grid is named `grid` (it is iterated as `for p in grid` in the executor
block ~691); if the count is held elsewhere (e.g. `n`), use that instead so
`n_grid` equals the number of trials searched, not the truncated `len(rows)`.

- [ ] **Step 4: Update the in-file `main()` (lines ~1298, 1313)**

```python
        report = run_param_sweep(
            conn=conn,
            strategy=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            days=args.days,
            param_ranges=param_ranges,
            wfo_split=args.wfo_split,
            min_trades=min_trades,
            fee_pct=args.fee_pct,
            top_n=args.top_n,
        )
```

and the print line:

```python
    print(
        format_sweep_results(
            report.rows,
            args.strategy,
            args.symbol,
            args.timeframe,
            gate=report.gate,
        )
    )
```

- [ ] **Step 5: Run the updated tests to verify they pass**

Run:

```bash
poetry run pytest tests/test_param_sweep.py -v
```

Expected: PASS (the two updated tests + all existing tests; `format_sweep_results`
calls with no `gate=` still pass because the kwarg defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "feat(guards): run_param_sweep returns ParamSweepReport with gate (N2 PR 2)"
```

---

## Task 3: `_fmt_gate` footer in `format_sweep_results`

**Files:**

- Modify: `analytics/param_sweep.py` (`_fmt_gate` new; `format_sweep_results`
  ~764, append gate lines ~833)
- Test: `tests/test_param_sweep.py` (`TestCommitGateWiring`)

- [ ] **Step 1: Add failing format tests**

Add to `class TestCommitGateWiring`:

```python
    def test_format_renders_commit_pass(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results(
            [row], "fvg", "SPY", "4h", gate=_verdict("COMMIT")
        )
        assert "COMMIT-GATE: PASS" in out
        assert "DSR=0.97" in out

    def test_format_renders_do_not_commit(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results(
            [row],
            "fvg",
            "SPY",
            "4h",
            gate=_verdict("DO_NOT_COMMIT", ["DSR 0.40 < 0.95"]),
        )
        assert "DO-NOT-COMMIT" in out
        assert "DSR 0.40 < 0.95" in out

    def test_format_no_gate_is_backcompat(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results([row], "fvg", "SPY", "4h")
        assert "COMMIT-GATE" not in out
```

- [ ] **Step 2: Run to verify the first two fail**

Run:

```bash
poetry run pytest tests/test_param_sweep.py::TestCommitGateWiring -v
```

Expected: `test_format_no_gate_is_backcompat` PASSES already;
`test_format_renders_commit_pass` and `test_format_renders_do_not_commit` FAIL
(`format_sweep_results` has no `gate=` kwarg → `TypeError`).

- [ ] **Step 3: Add `_fmt_gate` above `format_sweep_results` (~line 763)**

```python
def _fmt_gate(gate: CommitGateVerdict) -> list[str]:
    """Render the N2 commit-gate verdict as printable lines."""
    dsr = "—" if gate.dsr is None else f"{gate.dsr:.2f}"
    pbo = "—" if gate.pbo is None else f"{gate.pbo:.2f}"
    if gate.min_trl is None:
        trl = "—"
    elif math.isinf(gate.min_trl):
        trl = "∞"
    else:
        trl = f"{math.ceil(gate.min_trl)}"
    metrics = (
        f"DSR={dsr}  PBO={pbo}  MinTRL={trl}  "
        f"n={gate.n_obs}  trials={gate.n_trials}"
    )
    if gate.decision == "COMMIT":
        return [f"\n  ✓ COMMIT-GATE: PASS   {metrics}"]
    if gate.decision == DECISION_INSUFFICIENT:
        why = "; ".join(gate.reasons) or "not enough data"
        return [
            f"\n  ⚠ COMMIT-GATE: INSUFFICIENT (do not commit)   {metrics}",
            f"      {why}",
        ]
    why = "; ".join(gate.reasons) or "failed overfitting gate"
    return [f"\n  ✗ COMMIT-GATE: DO-NOT-COMMIT   {metrics}", f"      {why}"]
```

- [ ] **Step 4: Add the `gate=` kwarg to `format_sweep_results`**

Change the signature (line ~764-770) to add a keyword-only `gate`:

```python
def format_sweep_results(
    rows: list[SweepRow],
    strategy: str,
    symbol: str,
    timeframe: str,
    current_toml: dict[str, Any] | None = None,
    *,
    gate: CommitGateVerdict | None = None,
) -> str:
```

In the `if clean:` recommendation branch, after the existing
`hint = _directional_split_hint(best)` / `if hint:` block (~line 833, the last
line before the `else:` branch), append:

```python
        if gate is not None:
            lines.extend(_fmt_gate(gate))
```

Leave the existing `stats = best.overfit_stats` "Overfit controls" block
(Phase 0.3a/b display) exactly as-is — the gate footer is additive.

- [ ] **Step 5: Run to verify all pass**

Run:

```bash
poetry run pytest tests/test_param_sweep.py::TestCommitGateWiring -v
```

Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add analytics/param_sweep.py tests/test_param_sweep.py
git commit -m "feat(guards): print commit-gate verdict in sweep footer (N2 PR 2)"
```

---

## Task 4: CLI and tool caller updates

**Files:**

- Modify: `cli/param.py` (~99-102)
- Modify: `tools/multi_symbol_wfo.py` (~678)

- [ ] **Step 1: Update `cli/param.py`**

The `_run(...)` call assigns to `rows`. Rename to `report`:

```python
            report = _run(
                conn=conn,
                strategy=args.strategy,
                symbol=args.symbol,
                timeframe=args.timeframe,
                days=args.days,
                param_ranges=param_ranges,
                wfo_split=args.wfo_split,
                min_trades=min_trades,
                fee_pct=args.fee_pct,
                top_n=args.top_n,
                adr_suppress_threshold=args.adr_suppress_threshold,
                since_ms=parse_since_to_ms(args.since) if args.since else None,
                day_filter=args.day_filter,
                atr_sl_multiplier=args.atr_sl_multiplier,
                atr_sl_floor=args.atr_sl_floor,
                cv=_cv_from_args(args),
            )
    finally:
        conn.close()

    print(
        format_sweep_results(
            report.rows,
            args.strategy,
            args.symbol,
            args.timeframe,
            gate=report.gate,
        )
    )
```

- [ ] **Step 2: Update `tools/multi_symbol_wfo.py`**

At the call site (~678) the result is used only for pooling rows. Append
`.rows`:

```python
        rows = run_param_sweep(
            conn=conn,
            strategy=strategy,
            symbol=sym,
            timeframe=tf,
            days=0,  # ignored when since_ms set
            param_ranges=[TP_R_RANGE],
            wfo_split=WFO_SPLIT,
            min_trades=min_trades,
            fee_pct=FEE_PCT,
            top_n=99,
            since_ms=since_ms,
            day_filter=day_filter,
            atr_sl_multiplier=atr_mult,
            atr_sl_floor=atr_floor,
            live_parity=lp_ctx.live_parity if lp_ctx is not None else None,
            bias_cfg=lp_ctx.bias_cfg if lp_ctx is not None else None,
            regime_series=(
                lp_ctx.regime_by_sym.get(sym)
                if lp_ctx is not None and lp_ctx.regime_by_sym is not None
                else None
            ),
            # … remaining kwargs unchanged …
        ).rows
```

Only the trailing `).rows` is new — keep every existing kwarg. Verify there is
no other `run_param_sweep(` call in the file.

- [ ] **Step 3: Verify both modules import cleanly**

Run:

```bash
poetry run python -c "import cli.param, tools.multi_symbol_wfo; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add cli/param.py tools/multi_symbol_wfo.py
git commit -m "fix(guards): unpack ParamSweepReport.rows at sweep call sites (N2 PR 2)"
```

---

## Task 5: Refuse `DO_NOT_COMMIT` in the apply skills

**Files:**

- Modify: `.claude/skills/param-sweep-apply/SKILL.md`
- Modify: `.claude/skills/wfo-sweep/SKILL.md`

- [ ] **Step 1: Read both skill files**

Run:

```bash
sed -n '1,40p' .claude/skills/param-sweep-apply/SKILL.md
sed -n '1,40p' .claude/skills/wfo-sweep/SKILL.md
```

Locate the decision-rule / "pick best tp_r" section in each.

- [ ] **Step 2: Add the gate-refusal rule to `param-sweep-apply`**

Insert a short rule (match the file's existing bullet/heading style) stating:
the sweep footer now prints a commit-gate verdict. If the recommended config's
gate reads **`✗ COMMIT-GATE: DO-NOT-COMMIT`**, do **not** write it to TOML —
report the verdict and its reason line instead. If it reads **`⚠ INSUFFICIENT`**,
treat the cell as not-yet-decidable (need more history / trades) and skip the
write. Only **`✓ PASS`** is committable. This gate is **additive** to the
existing OOS filter (drop `OVERFIT`, require positive OOS `avg_r`) — both must
hold.

- [ ] **Step 3: Add the same rule to `wfo-sweep`**

The `/wfo-sweep` chain auto-applies `tp_r`. Add a step before the "apply to TOML"
stage: if the sweep verdict is `DO-NOT-COMMIT` or `INSUFFICIENT`, halt the apply
for that cell and surface the verdict rather than committing.

- [ ] **Step 4: Lint the markdown**

Run:

```bash
make lint-md
```

Expected: no errors (note CI is stricter than local — hand-format to the full
default ruleset; `.claude` is excluded from the lint globs but keep it clean
anyway).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/param-sweep-apply/SKILL.md .claude/skills/wfo-sweep/SKILL.md
git commit -m "docs(guards): apply-skills refuse DO_NOT_COMMIT verdict (N2 PR 2)"
```

---

## Task 6: Full gate + lint + typecheck + regression

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run:

```bash
make test
```

Expected: all pass (prior baseline 1557 pass / 3 skip, now +~6 wiring tests; no
failures). If `test_regression` reports moved goldens, STOP — the gate must not
touch the engine path; investigate before regenerating.

- [ ] **Step 2: Lint + typecheck**

Run:

```bash
make lint-py
make typecheck
```

Expected: ruff clean; mypy strict clean. The new `ParamSweepReport` return type
and the keyword-only `gate=` kwarg are fully annotated.

- [ ] **Step 3: Smoke-test the CLI footer (optional, needs a populated DB)**

Run:

```bash
PYTHONPATH=. poetry run python wifey.py param-sweep \
  --strategy fvg --symbol SPY --timeframe 1d --days 365
```

Expected: the sweep table prints, followed by a `COMMIT-GATE:` line. (If the DB
is empty, expect the "No OHLCV data" path — that now returns an empty report and
`format_sweep_results([])` prints `No results.`, which is fine.)

- [ ] **Step 4: Commit any incidental fixups**

```bash
git add -A
git commit -m "test(guards): full-suite green for commit-gate wiring (N2 PR 2)"
```

---

## Self-Review Notes

- **Spec coverage:** report return (Task 2), adapters (Task 1), footer (Task 3),
  callers (Task 4), apply-skill refusal (Task 5), verification (Task 6).
  `evaluate_audit_cells` wiring intentionally deferred (no wifey audit tool to
  host it) — documented in Scope and to be repeated in the PR body.
- **Type consistency:** `ParamSweepReport.gate` is `CommitGateVerdict`;
  `_compute_sweep_gate` / `_empty_report` / `_fmt_gate` all use the same
  `CommitGateVerdict` from `analytics.sweep_guard`; `_row_to_trialperf` returns
  `TrialPerf` (same module). `n_grid` is the full grid size, not `len(rows)`.
- **Back-compat:** `format_sweep_results` keeps positional args; `gate` is
  keyword-only with a `None` default, so the ~6 existing `format_sweep_results([row], …)`
  test calls and any other caller stay valid.
- **Goldens:** untouched — gate/overfit fields are never read by
  `test_regression._extract_metrics`, and the engine path is unchanged.
