# Phase 0.1 Universe-As-Of Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every backtest self-describing about its symbol universe — an explicit `universe_policy` (scope + as-of semantics + survivorship caveat) validated in config, stamped onto every saved `backtest_runs` row, and printed alongside headline metrics in CLI and web UI output.

**Architecture:** A frozen `UniversePolicy` dataclass + validation + loader live in `utils/config_validation.py` (already the home of `load_stocks_config`, imported by every consumer — no new module, no import cycles). `stocks.json` gains an optional reserved top-level `universe_policy` key which `load_stocks_config` strips before returning symbols (all `.keys()` call sites stay untouched). When the block is absent, `DEFAULT_UNIVERSE_POLICY` honestly describes today's behaviour (hand-picked survivors → forward-looking selection bias). `backtest_runs` gains a nullable `universe_policy` TEXT column (JSON of the policy) via the existing ALTER-TABLE migration list; all four `upsert_backtest_run` call sites stamp it. CLI runners print `policy.describe(n_symbols)`; the web UI fetches `GET /api/universe-policy` and renders the caveat on the Backtest page.

**Tech Stack:** Python 3.11, dataclasses, duckdb, FastAPI + pydantic, Svelte 5 (runes), pytest. mypy strict, ruff.

---

## Context

- Spec: `docs/superpowers/specs/2026-06-05-phase0-correctness-foundation-design.md` §4.1. **Chosen scope = bound-the-universe + state-the-bound**; free PIT constituent scraping is an optional follow-on, NOT in the DoD.
- DoD: (1) no code path uses an implicit "today's tickers" set without an attached, printed policy; (2) every saved backtest run records its universe policy; (3) a one-paragraph survivorship bound renders in backtest output. **Goldens unmoved** — this is metadata/printing, not P&L; `make test-regression` must pass with no regen.
- Branch: `feat/phase0-universe-policy`. Git identity must be `ngkhaijian@gmail.com` (check `git config --local user.email`). All `gh` commands pass `--repo s10023/buibui-wifey-wall-street-bot`.
- `config/stocks.json` is gitignored — schema change ships in `config/stocks.json.example` + validation code. The block is **optional** (absent → default policy), so the user's local `stocks.json` keeps working unmodified.
- `upsert_backtest_run` call sites (all 4 get stamped): `analytics/backtest_runner.py:556` (sweep save), `analytics/backtest_runner.py:920` (single-run save), `web/api/routers/backtest.py:163` (web run endpoint), `analytics/signal/scanner.py:1184` (live passive accumulation).
- Implicit-watchlist resolution sites (all get a printed/logged policy): `analytics_runner._resolve_symbols`, `backtest_runner.run_backtest_sweep:591`, `run_combo_backtest_cmd:1052`, `run_cross_tf_combo_backtest_cmd:1293`.
- **Positional INSERT constraint:** `upsert_backtest_run` uses `INSERT OR REPLACE INTO backtest_runs SELECT <cols> FROM df` with NO target column list — the SELECT order must match the table's physical column order. Therefore the new column is added ONLY to the schema migration list (appends after `recovery_factor` on both fresh and existing DBs — identical physical order), NOT to the `CREATE TABLE` body (which would put it *before* the migration-added columns on fresh DBs and break positional alignment). The SELECT gains `universe_policy` last, in the same commit.
- `universe_policy` is deliberately **excluded from `_backtest_run_id`** — it's metadata, doesn't change P&L, and including it would orphan every existing run row.
- Default `as_of="today"`: the 13-name watchlist was hand-picked at fork time (2026-05-14) using knowledge of which names are mega-caps *today*, so relative to the backtested history the selection is forward-looking. `"fixed"` is for a future universe frozen at a date predating the sample.
- `tests/conftest.py::web_client` uses a `MagicMock` DuckDB conn — web tests assert stamping via monkeypatched `upsert_backtest_run` kwargs, not DB reads.
- Combo / cross-TF results go to `backtest_combos` tables, not `backtest_runs` — they get the *printed* policy only (spec names the column on `backtest_runs` specifically).

## File Structure

| File | Action | Responsibility |
| ---- | ------ | -------------- |
| `utils/config_validation.py` | Modify | `UniversePolicy` dataclass, `DEFAULT_UNIVERSE_POLICY`, `UNIVERSE_POLICY_KEY`, `validate_universe_policy`, reserved-key handling in `validate_stocks_config` / `load_stocks_config`, `load_universe_policy` |
| `analytics/store/schema.py` | Modify | `("universe_policy", "TEXT")` appended to the `backtest_runs` migration list |
| `analytics/store/backtest_runs.py` | Modify | `upsert_backtest_run(..., universe_policy: str \| None = None)` + row + SELECT column |
| `analytics/backtest_runner.py` | Modify | Load + print policy in `run_backtest_sweep` / `run_backtest_cmd` / combo / cross-TF cmds; thread `universe_policy` through `_collect_sweep_results` into the upsert |
| `analytics/analytics_runner.py` | Modify | Log policy summary when `_resolve_symbols` falls back to the watchlist |
| `analytics/signal/scanner.py` | Modify | Stamp policy on passive backtest-run persistence in `run_scan_cycle` |
| `web/api/models/active_config.py` | Modify | `UniversePolicyResponse` pydantic model |
| `web/api/routers/config.py` | Modify | `GET /api/universe-policy` endpoint |
| `web/api/routers/backtest.py` | Modify | Stamp policy in the run endpoint's upsert |
| `web/ui/src/api.ts` | Modify | `UniversePolicyResponse` interface + `getUniversePolicy` |
| `web/ui/src/pages/Backtest.svelte` | Modify | Render the survivorship caveat under the page header |
| `config/stocks.json.example` | Modify | Example `universe_policy` block |
| `tests/test_config_validation.py` | Modify | Validation + loader + dataclass tests |
| `tests/test_data_store.py` | Modify | Column persistence + NULL-default tests |
| `tests/test_universe_policy.py` | Create | Runner print test (capsys) + `_resolve_symbols` log test (caplog) |
| `tests/test_web_backtest.py` | Modify | Run-endpoint stamp test |
| `tests/test_web_universe_policy.py` | Create | `GET /api/universe-policy` endpoint test |
| `README.md` | Modify | Document the policy block + honesty surface |

---

### Task 1: Branch setup

- [ ] **Step 1: Create branch + verify identity**

```bash
cd /home/kng/repo/buibui-wifey-wall-street-bot
git checkout -b feat/phase0-universe-policy
git config --local user.email   # must print ngkhaijian@gmail.com
```

### Task 2: `UniversePolicy` type, validation, loaders

**Files:**

- Modify: `utils/config_validation.py`
- Test: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config_validation.py` — extend the imports at the top:

```python
import json
from pathlib import Path
from typing import Any

import pytest

from utils.config_validation import (
    DEFAULT_UNIVERSE_POLICY,
    UNIVERSE_POLICY_KEY,
    UniversePolicy,
    load_stocks_config,
    load_universe_policy,
    validate_coins_config,
    validate_stocks_config,
    validate_universe_policy,
)
```

Append the new test classes:

```python
_POLICY_BLOCK = {
    "scope": "liquid_large_cap",
    "as_of": "today",
    "survivorship_note": "Hand-picked mega-caps; survivorship bias unbounded.",
}


class TestValidateUniversePolicy:
    """Tests for validate_universe_policy()."""

    def test_valid_block(self) -> None:
        assert validate_universe_policy(_POLICY_BLOCK) is True

    def test_fixed_as_of(self) -> None:
        assert validate_universe_policy({**_POLICY_BLOCK, "as_of": "fixed"}) is True

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            validate_universe_policy("nope")

    def test_missing_field(self) -> None:
        block = {k: v for k, v in _POLICY_BLOCK.items() if k != "survivorship_note"}
        with pytest.raises(ValueError, match="missing required"):
            validate_universe_policy(block)

    def test_unknown_field(self) -> None:
        with pytest.raises(ValueError, match="unknown field"):
            validate_universe_policy({**_POLICY_BLOCK, "selected_at": "2026-05-14"})

    def test_bad_as_of(self) -> None:
        with pytest.raises(ValueError, match="as_of"):
            validate_universe_policy({**_POLICY_BLOCK, "as_of": "yesterday"})

    def test_empty_scope(self) -> None:
        with pytest.raises(ValueError, match="scope"):
            validate_universe_policy({**_POLICY_BLOCK, "scope": "  "})


class TestStocksConfigWithUniversePolicy:
    """The reserved universe_policy key inside stocks.json."""

    def test_policy_block_accepted(self) -> None:
        config = {UNIVERSE_POLICY_KEY: dict(_POLICY_BLOCK), "AAPL": {"sl_pct": 0.05}}
        assert validate_stocks_config(config) is True

    def test_invalid_policy_block_rejected(self) -> None:
        config = {UNIVERSE_POLICY_KEY: {"scope": "x"}, "AAPL": {"sl_pct": 0.05}}
        with pytest.raises(ValueError, match="universe_policy"):
            validate_stocks_config(config)

    def test_load_strips_policy_key(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}})
        )
        loaded = load_stocks_config(path)
        assert list(loaded.keys()) == ["AAPL"]


class TestLoadUniversePolicy:
    """Tests for load_universe_policy()."""

    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert load_universe_policy(tmp_path / "absent.json") == DEFAULT_UNIVERSE_POLICY

    def test_absent_block_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(json.dumps({"AAPL": {"sl_pct": 0.05}}))
        assert load_universe_policy(path) == DEFAULT_UNIVERSE_POLICY

    def test_block_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}})
        )
        policy = load_universe_policy(path)
        assert policy.scope == "liquid_large_cap"
        assert policy.as_of == "today"

    def test_invalid_block_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(json.dumps({UNIVERSE_POLICY_KEY: {"scope": "x"}}))
        with pytest.raises(ValueError):
            load_universe_policy(path)


class TestUniversePolicyType:
    """Tests for the UniversePolicy dataclass helpers."""

    def test_summary(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert policy.summary() == "liquid_large_cap (as_of=today)"

    def test_to_json_round_trip(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert json.loads(policy.to_json()) == _POLICY_BLOCK

    def test_describe_contains_note_and_count(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        text = policy.describe(13)
        assert "Universe: liquid_large_cap (as_of=today, 13 symbols)" in text
        assert _POLICY_BLOCK["survivorship_note"] in text

    def test_describe_without_count(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert "Universe: liquid_large_cap (as_of=today)" in policy.describe()

    def test_default_policy_describes_current_behaviour(self) -> None:
        assert DEFAULT_UNIVERSE_POLICY.scope == "liquid_large_cap"
        assert DEFAULT_UNIVERSE_POLICY.as_of == "today"
        assert "survivorship" in DEFAULT_UNIVERSE_POLICY.survivorship_note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_config_validation.py -v`
Expected: FAIL at import — `cannot import name 'UniversePolicy'`.

- [ ] **Step 3: Implement in `utils/config_validation.py`**

Extend the module imports:

```python
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
```

Add after the module constants:

```python
UNIVERSE_POLICY_KEY = "universe_policy"
_VALID_AS_OF = frozenset({"fixed", "today"})
_UNIVERSE_POLICY_FIELDS = frozenset({"scope", "as_of", "survivorship_note"})


@dataclass(frozen=True)
class UniversePolicy:
    """Explicit universe metadata — bounds selection/survivorship bias (Phase 0.1).

    as_of semantics: "today" = symbols picked with current knowledge (forward-
    looking over backtested history); "fixed" = list frozen at a date that
    predates the sample (point-in-time-ish).
    """

    scope: str
    as_of: str
    survivorship_note: str

    def summary(self) -> str:
        """One-line stamp, e.g. 'liquid_large_cap (as_of=today)'."""
        return f"{self.scope} (as_of={self.as_of})"

    def to_json(self) -> str:
        """Compact JSON for the backtest_runs.universe_policy column."""
        return json.dumps(
            {
                "scope": self.scope,
                "as_of": self.as_of,
                "survivorship_note": self.survivorship_note,
            },
            sort_keys=True,
        )

    def describe(self, n_symbols: int | None = None) -> str:
        """Multi-line honesty paragraph for CLI backtest output."""
        count = f", {n_symbols} symbols" if n_symbols is not None else ""
        return (
            f"Universe: {self.scope} (as_of={self.as_of}{count})\n"
            f"  ⚠ {self.survivorship_note}"
        )


DEFAULT_UNIVERSE_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note=(
        "Watchlist hand-picked at fork time (2026-05-14) from symbols that are "
        "liquid US mega-caps today — i.e. survivors. Backtests over earlier "
        "history carry forward-looking selection (survivorship) bias: names "
        "that crashed, shrank or delisted never enter the sample. The delisting "
        "bias is small for mega-caps (rare mid-sample delistings) but the "
        "selection bias is unbounded for broader universes — do not extrapolate "
        "these results beyond this watchlist."
    ),
)


def validate_universe_policy(block: Any) -> bool:
    """Validate a universe_policy block. Raises ValueError if invalid."""
    if not isinstance(block, dict):
        raise ValueError("universe_policy must be a dict")
    unknown = set(block) - _UNIVERSE_POLICY_FIELDS
    if unknown:
        raise ValueError(f"universe_policy: unknown field(s) {sorted(unknown)}")
    missing = _UNIVERSE_POLICY_FIELDS - set(block)
    if missing:
        raise ValueError(
            f"universe_policy: missing required field(s) {sorted(missing)}"
        )
    for field_name in ("scope", "survivorship_note"):
        value = block[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"universe_policy: {field_name} must be a non-empty string"
            )
    if block["as_of"] not in _VALID_AS_OF:
        raise ValueError(
            f"universe_policy: as_of must be one of {sorted(_VALID_AS_OF)}, "
            f"got {block['as_of']!r}"
        )
    return True
```

In `validate_stocks_config`, at the top of the per-symbol loop:

```python
    for symbol, params in config_dict.items():
        if symbol == UNIVERSE_POLICY_KEY:
            validate_universe_policy(params)
            continue
```

In `load_stocks_config`, strip the reserved key before returning:

```python
    validate_stocks_config(config)
    config.pop(UNIVERSE_POLICY_KEY, None)
    return config
```

Add the loader:

```python
def load_universe_policy(path: Path = _DEFAULT_STOCKS_PATH) -> UniversePolicy:
    """Return the universe_policy from stocks.json, or the honest default.

    Missing file or absent block → DEFAULT_UNIVERSE_POLICY (describes current
    behaviour). A present-but-invalid block raises ValueError, matching
    load_stocks_config's fail-loud validation.
    """
    if not path.exists():
        return DEFAULT_UNIVERSE_POLICY
    with path.open() as f:
        config: dict[str, Any] = json.load(f)
    block = config.get(UNIVERSE_POLICY_KEY)
    if block is None:
        return DEFAULT_UNIVERSE_POLICY
    validate_universe_policy(block)
    return UniversePolicy(
        scope=block["scope"],
        as_of=block["as_of"],
        survivorship_note=block["survivorship_note"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_config_validation.py -v`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add utils/config_validation.py tests/test_config_validation.py
git commit -m "feat(universe): UniversePolicy type + stocks.json universe_policy block validation"
```

### Task 3: `backtest_runs.universe_policy` column + upsert kwarg

**Files:**

- Modify: `analytics/store/schema.py` (migration list, ~line 113–127)
- Modify: `analytics/store/backtest_runs.py` (`upsert_backtest_run`)
- Test: `tests/test_data_store.py`

- [ ] **Step 1: Write the failing tests** — append to `TestUpsertBacktestRun`:

```python
    def test_universe_policy_persisted(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(
            conn,
            result,
            **_BT_PARAMS,
            universe_policy='{"scope": "liquid_large_cap"}',
        )
        row = _one(conn, "SELECT universe_policy FROM backtest_runs")
        assert row[0] == '{"scope": "liquid_large_cap"}'

    def test_universe_policy_defaults_null(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = _FakeResult("BTCUSDT", "4h", "bos")
        upsert_backtest_run(conn, result, **_BT_PARAMS)
        row = _one(conn, "SELECT universe_policy FROM backtest_runs")
        assert row[0] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_data_store.py::TestUpsertBacktestRun -v`
Expected: the two new tests FAIL (unexpected kwarg / missing column).

- [ ] **Step 3: Implement**

`analytics/store/schema.py` — append to the `backtest_runs` migration list (after `("volume_suppress", "BOOLEAN"),`):

```python
        ("universe_policy", "TEXT"),
```

Do **not** touch the `CREATE TABLE` body — physical column order must end with the migration-added columns on both fresh and existing DBs so the positional `INSERT … SELECT` in `upsert_backtest_run` stays aligned.

`analytics/store/backtest_runs.py` — `upsert_backtest_run` gains a trailing keyword param:

```python
    volume_suppress: bool | None = None,
    universe_policy: str | None = None,
) -> str:
```

Row dict gains (after `"volume_suppress": volume_suppress,`):

```python
        "universe_policy": universe_policy,
```

SELECT column list — change the tail from `"recovery_factor "` to:

```python
            "recovery_factor, universe_policy "
```

(`universe_policy` last — it is the last physical column on every DB.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_data_store.py -v`
Expected: PASS (all — including the pre-existing tail-column alignment regression test).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/store/schema.py analytics/store/backtest_runs.py tests/test_data_store.py
git commit -m "feat(universe): nullable universe_policy column on backtest_runs"
```

### Task 4: Stamp + print in `backtest_runner`; log in `analytics_runner`

**Files:**

- Modify: `analytics/backtest_runner.py`
- Modify: `analytics/analytics_runner.py`
- Test: `tests/test_universe_policy.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/test_universe_policy.py`:

```python
"""Phase 0.1 — universe policy printing/logging on the runner surfaces."""

import logging
from pathlib import Path

import duckdb
import pytest

from analytics.analytics_runner import _resolve_symbols
from analytics.backtest_config import BacktestSweepConfig
from analytics.backtest_runner import run_backtest_sweep
from analytics.data_store import init_schema
from utils.config_validation import UniversePolicy

_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note="Test caveat: survivors only.",
)


def test_sweep_prints_universe_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_backtest_sweep prints the policy paragraph with the symbol count."""
    db_path = tmp_path / "bt.db"
    conn = duckdb.connect(str(db_path))
    init_schema(conn)
    conn.close()
    monkeypatch.setattr(
        "analytics.backtest_runner.load_universe_policy", lambda: _POLICY
    )
    cfg = BacktestSweepConfig(symbols=["AAPL"], timeframes=["1d"], strategies=["bos"])
    run_backtest_sweep(cfg, db_path=db_path)
    out = capsys.readouterr().out
    assert "Universe: liquid_large_cap (as_of=today, 1 symbols)" in out
    assert "Test caveat: survivors only." in out


def test_resolve_symbols_logs_universe_policy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Implicit watchlist fallback logs the policy summary (DoD: no unstated set)."""
    monkeypatch.setattr(
        "analytics.analytics_runner.load_stocks_config",
        lambda: {"AAPL": {"sl_pct": 0.05}, "MSFT": {"sl_pct": 0.05}},
    )
    monkeypatch.setattr(
        "analytics.analytics_runner.load_universe_policy", lambda: _POLICY
    )
    with caplog.at_level(logging.INFO):
        resolved = _resolve_symbols(None)
    assert resolved == ["AAPL", "MSFT"]
    assert "liquid_large_cap (as_of=today)" in caplog.text


def test_resolve_symbols_explicit_list_skips_policy_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit symbol list is not an implicit universe — no policy log."""
    with caplog.at_level(logging.INFO):
        assert _resolve_symbols(["NVDA"]) == ["NVDA"]
    assert "Universe" not in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_universe_policy.py -v`
Expected: FAIL — no `load_universe_policy` attribute on either module / no Universe line printed.

- [ ] **Step 3: Implement**

`analytics/backtest_runner.py`:

1. Import (line 60): `from utils.config_validation import load_stocks_config, load_universe_policy`
2. `run_backtest_sweep` — after the `symbols` fallback (line ~591):

   ```python
       universe = load_universe_policy()
   ```

   After the header `if tp_sweep_mode / elif atr_sweep_mode / else` print block (line ~626):

   ```python
       print(universe.describe(len(symbols)))
   ```

3. Thread into the save path — `_collect_sweep_results` keyword-only params gain:

   ```python
       universe_policy: str | None = None,
   ```

   its `upsert_backtest_run(...)` call gains `universe_policy=universe_policy,` — and the call site in `run_backtest_sweep` (line ~759) gains `universe_policy=universe.to_json(),`.

4. `run_backtest_cmd` — after the `start_ms` computation (line ~820):

   ```python
       universe = load_universe_policy()
       print(universe.describe())
   ```

   and the save-path `upsert_backtest_run(...)` (line ~920) gains `universe_policy=universe.to_json(),`.

5. `run_combo_backtest_cmd` — directly after its `symbols` fallback (line ~1052):

   ```python
           print(load_universe_policy().describe(len(symbols)))
   ```

6. `run_cross_tf_combo_backtest_cmd` — same line directly after its `symbols` fallback (line ~1293).

`analytics/analytics_runner.py`:

```python
from utils.config_validation import load_stocks_config, load_universe_policy


def _resolve_symbols(symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    try:
        resolved = list(load_stocks_config().keys())
    except Exception as e:
        logging.error("Failed to load stocks config: %s", e)
        sys.exit(1)
    policy = load_universe_policy()
    logging.info("Universe: %s (%d symbols)", policy.summary(), len(resolved))
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_universe_policy.py tests/test_backtest_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/backtest_runner.py analytics/analytics_runner.py tests/test_universe_policy.py
git commit -m "feat(universe): print + stamp universe policy in backtest/analytics runners"
```

### Task 5: Stamp the scanner's passive backtest-run persistence

**Files:**

- Modify: `analytics/signal/scanner.py` (save loop, ~line 1179)

- [ ] **Step 1: Implement** (covered by Task 3's store tests + suite; no scan-cycle save harness exists)

Add import near the other `utils` imports in `analytics/signal/scanner.py`:

```python
from utils.config_validation import load_universe_policy
```

In `run_scan_cycle`, replace the save-loop opening:

```python
    if backtest_cfg and backtest_cfg.save_results and bt_to_save:
        try:
            universe_policy_json: str | None = load_universe_policy().to_json()
        except ValueError:
            universe_policy_json = None
        for (sym, tf, strategy), bt_result in bt_to_save.items():
```

and the `upsert_backtest_run(...)` call inside gains:

```python
                    universe_policy=universe_policy_json,
```

- [ ] **Step 2: Run the scanner-adjacent tests**

Run: `poetry run pytest tests/test_catch_up.py tests/test_watermark_on_send.py tests/test_signal_gates_conflict_resolver.py -v`
Expected: PASS (no behaviour change on the alert path).

- [ ] **Step 3: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add analytics/signal/scanner.py
git commit -m "feat(universe): stamp universe policy on scanner-persisted backtest runs"
```

### Task 6: Web API — `GET /api/universe-policy` + stamped run endpoint

**Files:**

- Modify: `web/api/models/active_config.py`, `web/api/routers/config.py`, `web/api/routers/backtest.py`
- Test: `tests/test_web_universe_policy.py` (create), `tests/test_web_backtest.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_universe_policy.py`:

```python
"""Tests for GET /api/universe-policy (Phase 0.1 honesty surface)."""

import pytest
from fastapi.testclient import TestClient

from utils.config_validation import UniversePolicy

_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note="Survivors only — do not extrapolate.",
)


def test_universe_policy_endpoint(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.api.routers.config.load_universe_policy", lambda: _POLICY
    )
    monkeypatch.setattr(
        "web.api.routers.config.load_stocks_config",
        lambda: {"AAPL": {"sl_pct": 0.05}, "MSFT": {"sl_pct": 0.05}},
    )
    resp = web_client.get("/api/universe-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "liquid_large_cap"
    assert data["as_of"] == "today"
    assert data["survivorship_note"] == "Survivors only — do not extrapolate."
    assert data["n_symbols"] == 2


def test_universe_policy_endpoint_without_stocks_config(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing stocks.json must not 500 — policy still served, count omitted."""
    monkeypatch.setattr(
        "web.api.routers.config.load_universe_policy", lambda: _POLICY
    )

    def _raise() -> dict[str, object]:
        raise FileNotFoundError("config/stocks.json not found")

    monkeypatch.setattr("web.api.routers.config.load_stocks_config", _raise)
    resp = web_client.get("/api/universe-policy")
    assert resp.status_code == 200
    assert resp.json()["n_symbols"] is None
```

Add to `tests/test_web_backtest.py` (reuses its `_make_ohlcv` / `_make_signals` helpers; add `from typing import Any` and `from utils.config_validation import UniversePolicy` to its imports):

```python
def test_backtest_run_stamps_universe_policy(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run endpoint passes the active universe policy into upsert_backtest_run."""
    monkeypatch.setattr(
        "web.api.routers.backtest.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.backtest.detect_signals_for_strategy",
        lambda *a, **kw: _make_signals(),
    )
    policy = UniversePolicy(
        scope="liquid_large_cap", as_of="today", survivorship_note="caveat"
    )
    monkeypatch.setattr(
        "web.api.routers.backtest.load_universe_policy", lambda: policy
    )
    captured: dict[str, Any] = {}

    def _fake_upsert(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "run123"

    monkeypatch.setattr("web.api.routers.backtest.upsert_backtest_run", _fake_upsert)
    monkeypatch.setattr(
        "web.api.routers.backtest.upsert_backtest_trades", lambda *a, **kw: None
    )
    resp = web_client.post(
        "/api/backtest",
        json={
            "symbol": "AAPL",
            "timeframe": "1h",
            "strategy": "fvg",
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
        },
    )
    assert resp.status_code == 200
    assert captured["universe_policy"] == policy.to_json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_web_universe_policy.py tests/test_web_backtest.py -v`
Expected: new tests FAIL (404 on the endpoint / no `load_universe_policy` attribute).

- [ ] **Step 3: Implement**

`web/api/models/active_config.py` — append:

```python
class UniversePolicyResponse(BaseModel):
    """GET /api/universe-policy — Phase 0.1 survivorship honesty surface."""

    scope: str
    as_of: str
    survivorship_note: str
    n_symbols: int | None = None
```

`web/api/routers/config.py` — extend imports:

```python
from utils.config_validation import load_stocks_config, load_universe_policy
from web.api.models.active_config import ActiveConfigResponse, UniversePolicyResponse
```

Append the endpoint:

```python
@router.get("/universe-policy")
def get_universe_policy() -> UniversePolicyResponse:
    """Return the active watchlist's universe policy + survivorship caveat."""
    policy = load_universe_policy()
    try:
        n_symbols: int | None = len(load_stocks_config())
    except Exception:
        n_symbols = None
    return UniversePolicyResponse(
        scope=policy.scope,
        as_of=policy.as_of,
        survivorship_note=policy.survivorship_note,
        n_symbols=n_symbols,
    )
```

`web/api/routers/backtest.py` — add `from utils.config_validation import load_universe_policy` and extend the `upsert_backtest_run(...)` call:

```python
        volume_suppress=None,
        universe_policy=load_universe_policy().to_json(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_web_universe_policy.py tests/test_web_backtest.py tests/test_web_error_handling.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint-py && make typecheck
git add web/api/models/active_config.py web/api/routers/config.py web/api/routers/backtest.py tests/test_web_universe_policy.py tests/test_web_backtest.py
git commit -m "feat(universe): GET /api/universe-policy + stamped web backtest runs"
```

### Task 7: UI caveat on the Backtest page

**Files:**

- Modify: `web/ui/src/api.ts`, `web/ui/src/pages/Backtest.svelte`

> Load `/frontend-design` (per CLAUDE.md) before editing the Svelte file; match the page's existing muted-text styling conventions.

- [ ] **Step 1: `api.ts`** — after `getActiveConfig`:

```ts
export interface UniversePolicyResponse {
  scope: string;
  as_of: string;
  survivorship_note: string;
  n_symbols: number | null;
}

export const getUniversePolicy = () =>
  apiFetch<UniversePolicyResponse>("/api/universe-policy");
```

- [ ] **Step 2: `Backtest.svelte`** — script additions (import alongside the existing `api` imports):

```ts
import { getUniversePolicy, type UniversePolicyResponse } from "../api";

let universePolicy = $state<UniversePolicyResponse | null>(null);
```

Extend the existing `onMount`:

```ts
onMount(() => {
  void loadRuns();
  getUniversePolicy()
    .then((p) => (universePolicy = p))
    .catch(() => {}); // caveat banner is best-effort — never block the page
});
```

Markup — directly after the closing `</div>` of `.page-header` (line ~379):

```svelte
{#if universePolicy}
  <p class="universe-caveat">
    Universe: {universePolicy.scope} (as_of={universePolicy.as_of}{universePolicy.n_symbols != null ? `, ${universePolicy.n_symbols} symbols` : ""})
    — {universePolicy.survivorship_note}
  </p>
{/if}
```

Style block — match the page's existing muted color tokens:

```css
.universe-caveat {
  margin: 0 0 12px;
  font-size: 0.78rem;
  line-height: 1.4;
  color: var(--text-muted);
  opacity: 0.85;
}
```

(If the page uses a different muted variable name, use that one — check the existing `<style>` section.)

- [ ] **Step 3: Build**

Run: `make web-build`
Expected: clean production build.

- [ ] **Step 4: Commit**

```bash
git add web/ui/src/api.ts web/ui/src/pages/Backtest.svelte
git commit -m "feat(universe): survivorship caveat banner on Backtest page"
```

### Task 8: Example config, docs, full verification

**Files:**

- Modify: `config/stocks.json.example`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: `config/stocks.json.example`** — add the block as the first key:

```json
{
  "universe_policy": {
    "scope": "liquid_large_cap",
    "as_of": "today",
    "survivorship_note": "Watchlist hand-picked 2026-05-14 from today's liquid US mega-caps (survivors). Backtests over earlier history carry forward-looking selection bias — small for mega-caps, unbounded for broader universes; do not extrapolate."
  },
  "AAPL": { "sl_pct": 0.05 },
  "MSFT": { "sl_pct": 0.05 },
  "GOOGL": { "sl_pct": 0.05 },
  "AMZN": { "sl_pct": 0.05 },
  "META": { "sl_pct": 0.05 },
  "ORCL": { "sl_pct": 0.05 },
  "ADBE": { "sl_pct": 0.05 },
  "NVDA": { "sl_pct": 0.06 },
  "AMD": { "sl_pct": 0.06 },
  "TSLA": { "sl_pct": 0.06 },
  "MSTR": { "sl_pct": 0.08 },
  "SPY": { "sl_pct": 0.03 },
  "QQQ": { "sl_pct": 0.03 }
}
```

- [ ] **Step 2: Docs** — README.md: document the optional `universe_policy` block in the stocks.json section + the printed caveat in backtest output + `GET /api/universe-policy`. CLAUDE.md: extend the `config/stocks.json` bullet (optional reserved `universe_policy` key, stripped by `load_stocks_config`, served by `load_universe_policy`, stamped on `backtest_runs.universe_policy`). Run `make lint-md`.

- [ ] **Step 3: Full gates**

```bash
make lint-py && make typecheck && make test
make test-regression   # MUST pass with NO golden regen — 0.1 is metadata only
make lint-md
```

- [ ] **Step 4: Commit + PR**

```bash
git add config/stocks.json.example README.md CLAUDE.md
git commit -m "docs(universe): universe_policy example block + README/CLAUDE.md"
git push -u origin feat/phase0-universe-policy   # remote: github.com-personal
gh pr create --repo s10023/buibui-wifey-wall-street-bot ...
```

Then `/pr-summary` + `/post-branch`.

## Self-review notes

- Spec coverage: config block + validation (Task 2), stamped runs incl. all 4 upsert call sites (Tasks 3–6), printed policy on every implicit-watchlist surface incl. combo/cross-TF and analytics_runner (Task 4), CLI + web honesty surface (Tasks 4, 6, 7), goldens unmoved (Task 8 gate). PIT scraping deliberately out (spec: optional follow-on).
- `load_stocks_config` mutating `config` via `.pop` is safe — the dict is function-local, freshly parsed from JSON.
- The sweep print lands before the table output; regression goldens compare extracted metric JSON, not stdout.
