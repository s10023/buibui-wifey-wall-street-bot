# Exit MFE/MAE Diagnostic — Equity Port (#433) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the parent's `analytics/exits/mfe_mae.py` MFE/MAE excursion diagnostic (parent PR #433) into the wifey fork as an additive read-only package, then run it over the live equity ledger to answer the exit spec §2 go/no-go question: **is the live bot's −R / expiry leak exit-fixable or entry-broken?**

**Architecture:** A pure-function package `analytics/exits/` (this PR ships only the §2 diagnostic; the §3–§5 policy/replay/A/B layer is the deferred follow-up #437). `compute_excursions(conn)` walks each resolved `signal_alert_outcomes` row's held OHLCV window and records max-favorable / max-adverse excursion in R units under conservative anti-bias intrabar conventions; `aggregate_cohorts(...)` rolls those up into the spec's 4-pattern verdict grid. A read-only CLI (`tools/exit_audit.py`, diagnose mode only) prints the cohort tables over the real DB. Additive read-only — no DB writes, no schema change, no detector change → regression goldens stay byte-identical.

**Tech Stack:** Python 3.11, duckdb, pandas, numpy, pytest. Poetry-managed. ruff + mypy-strict gate.

---

## Background & key facts (read before starting)

- **Parent source of truth** (already-merged, port-from-here):
  `/home/kng/repo/buibui-moon-trader-bot/analytics/exits/mfe_mae.py` (PR `4e6cd04`),
  `…/tools/exit_audit.py`, `…/tests/test_mfe_mae.py`.
- **This PR is #433 only.** The parent's `policies.py` / `replay.py` / `audit.py`
  (the §3–§5 exit-policy A/B, PR #437) are a **separate follow-up** and are NOT
  in scope here. The §437 A/B headline depends on the parent's `portfolio/`
  paper-book package, which **wifey does not have** — that substitution
  (per-trade R Sharpe instead of portfolio Sharpe) is deferred to the #437 PR.
- **Two source adaptations vs the parent (everything else is verbatim):**
  1. `mfe_mae.py` import: parent does `from analytics.data_store import get_ohlcv`.
     Wifey's `data_store` shim does **not** re-export `get_ohlcv`; the wifey idiom
     (see `analytics/forecast/replay.py:20`, `analytics/xsmom/replay.py`) is
     `from analytics.store.market_data import get_ohlcv`. Use the wifey idiom.
  2. `tests/test_mfe_mae.py` fixtures: the parent inserts an OHLCV row with a
     `taker_buy_volume` column. Wifey's `ohlcv` table **dropped that column in
     T4** (`analytics/store/schema.py` — columns are
     `symbol, timeframe, open_time, open, high, low, close, volume`). Drop
     `taker_buy_volume` from the fixture dicts. Symbol/TF strings stay verbatim
     (`BTCUSDT` / `ETHUSDT`, `1h` / `4h`) — they are synthetic in-memory inserts,
     and keeping them verbatim minimises future `/sync-parent` diff noise.
- **The wifey ledger is young.** `signal_alert_outcomes` holds ~48 total rows,
  **22 resolved** (`win`/`loss`/`expired`), the rest still `open` — the diagnostic
  runs and scores them all, but 22 is far below any usable cohort floor, so the
  real-ledger verdict (Task 4) is expected to be INCONCLUSIVE / instrument-ready
  rather than a statistical exit-fixable call. (Confirms MEMORY's "live ledger
  young" note. An earlier scratch query of "2,697 resolved" was a mistake — that
  count was the **parent** repo's crypto ledger, not wifey's.)
- **Verified wifey import surface** (used by this plan):
  - `from analytics.store.market_data import get_ohlcv`
    — `get_ohlcv(conn, symbol, timeframe, start, end) -> pd.DataFrame`, columns
    `symbol, timeframe, open_time, open, high, low, close, volume`, inclusive
    `[start, end]` in Unix ms, ordered by `open_time`.
  - `from analytics.store import init_schema, upsert_signal_outcome, DEFAULT_DB_PATH`
    — all three are exported from `analytics.store.__init__`.
  - `upsert_signal_outcome(conn, row: dict)` accepts the same dict keys the parent
    test uses (`signal_id, symbol, tf, strategy, direction, fired_at_ms,
    candle_ts_ms, entry_price, sl_price, tp_price, rr_ratio, confidence_at_fire,
    tags`).
- **Spec / verdict grid:** the parent's `docs/redesign/2026-06-05-exit-improvement-spec.md`
  §2 is the 4-pattern interpretation table. It does NOT exist in the wifey repo;
  the CLI footer and the audit doc reproduce its key rows inline (Task 4) so wifey
  is self-contained.

## File structure (what each file owns)

- **Create** `analytics/exits/__init__.py` — package public surface. This PR
  exports only the §2 diagnostic trio (`EXCURSION_COLUMNS`, `aggregate_cohorts`,
  `compute_excursions`). #437 will extend it with the policy/replay exports.
- **Create** `analytics/exits/mfe_mae.py` — the per-alert MFE/MAE excursion study.
  Two public functions (`compute_excursions`, `aggregate_cohorts`) + one private
  helper (`_excursion_for_row`) + `EXCURSION_COLUMNS`. Pure over a DuckDB conn /
  DataFrames; no clock or network I/O.
- **Create** `tools/exit_audit.py` — read-only diagnose-mode CLI. Prints coverage,
  the overall cohort roll-up, the per-(strategy, tf, direction) table, and the
  inline verdict-grid footer. No `--replay` (that ships with #437).
- **Create** `tests/test_mfe_mae.py` — full test port (conventions, robustness,
  aggregation), `taker_buy_volume` dropped from fixtures.
- **Modify** `Makefile` — add the `wifey-exit-audit` target + `.PHONY` entry,
  mirroring `wifey-forecast-audit` / `wifey-xsmom-audit`.
- **Create** `docs/audits/2026-06-20-exit-mfe-mae-diagnostic-equity.md` — the
  verdict note written from the real-ledger run (Task 4).
- Doc-surface edits (CLAUDE.md / `.claude/context/analytics.md` / README /
  MEMORY) are handled in the post-branch sweep (Task 6), not inline.

---

### Task 1: Package skeleton + `compute_excursions`

**Files:**

- Create: `analytics/exits/__init__.py`
- Create: `analytics/exits/mfe_mae.py`
- Test: `tests/test_mfe_mae.py`

- [ ] **Step 1: Write the failing tests (conventions + robustness)**

Create `tests/test_mfe_mae.py` with the shared fixture helpers and the first two
test classes. Notes: (a) `taker_buy_volume` is intentionally absent from the
OHLCV fixture (wifey's `ohlcv` table has no such column); (b) Task 1 imports
only `compute_excursions` — `aggregate_cohorts` is added in Task 2 so its tests
stay red-first.

```python
"""Tests for analytics.exits.mfe_mae (exit spec §2 MFE/MAE diagnostic).

Covers the conservative intrabar conventions per cohort (loss excludes the
exit bar's favorable extreme; win clamps post-TP overshoot; expired counts
every in-window bar), short-direction sign handling, the zero floor,
zero-risk / missing-OHLCV row skips, (symbol, tf) batching, and the cohort
aggregation (reach fractions + min_n gate).

Equity port of the parent's tests/test_mfe_mae.py (PR #433): the only fixture
change is dropping `taker_buy_volume` (not a column in the wifey ohlcv table).
"""

import duckdb
import pandas as pd
import pytest

from analytics.exits import compute_excursions
from analytics.store import init_schema, upsert_signal_outcome

_HOUR = 3_600_000


def _insert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    tf: str,
    rows: list[dict[str, int | float]],
) -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": r["open_time"],
                "open": r.get("open", r["close"]),
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": 1.0,
            }
            for r in rows
        ]
    )
    conn.register("_o", df)
    conn.execute("INSERT INTO ohlcv SELECT * FROM _o")
    conn.unregister("_o")


def _insert_resolved(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_id: str,
    outcome: str,
    filled_at_ms: int,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    strategy: str = "fvg",
    direction: str = "long",
    candle_ts_ms: int = 0,
    entry: float = 100.0,
    sl: float = 95.0,
    tp: float = 110.0,
    rr: float = 2.0,
    outcome_r: float = 0.0,
) -> None:
    upsert_signal_outcome(
        conn,
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "tf": tf,
            "strategy": strategy,
            "direction": direction,
            "fired_at_ms": candle_ts_ms,
            "candle_ts_ms": candle_ts_ms,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "rr_ratio": rr,
            "confidence_at_fire": 3,
            "tags": "",
        },
    )
    conn.execute(
        "UPDATE signal_alert_outcomes "
        "SET outcome = ?, outcome_r = ?, outcome_filled_at_ms = ? "
        "WHERE signal_id = ?",
        [outcome, outcome_r, filled_at_ms, signal_id],
    )


class TestExcursionConventions:
    def test_long_loss_excludes_exit_bar_favorable_extreme(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # risk = 5. Bar 1: fav 0.8R. Bar 2 (SL exit): high would be 2.4R but
        # must NOT count; low 94 gaps past the stop -> MAE 1.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 103.0},
                {"open_time": 2 * _HOUR, "high": 112.0, "low": 94.0, "close": 96.0},
            ],
        )
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=2 * _HOUR)
        exc = compute_excursions(conn)
        assert len(exc) == 1
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(0.8)
        assert row["mae_r"] == pytest.approx(1.2)
        assert row["bars_held"] == 2

    def test_long_win_clamps_exit_bar_overshoot(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Bar 1: fav 0.6R, adv 0.2R. Bar 2 (TP exit): high 118 = 3.6R
        # overshoot -> clamped to rr 2.0; its adv 0.8R counts.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 99.0, "close": 102.0},
                {"open_time": 2 * _HOUR, "high": 118.0, "low": 96.0, "close": 115.0},
            ],
        )
        _insert_resolved(conn, signal_id="s1", outcome="win", filled_at_ms=2 * _HOUR)
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(2.0)
        assert row["mae_r"] == pytest.approx(0.8)

    def test_short_direction_signs(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Short, entry 100, sl 105 (risk 5). Bar 1: low 96 -> fav 0.8R,
        # high 103 -> adv 0.6R. Bar 2: low 94 -> fav 1.2R, high 101 -> 0.2R.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 103.0, "low": 96.0, "close": 98.0},
                {"open_time": 2 * _HOUR, "high": 101.0, "low": 94.0, "close": 95.0},
            ],
        )
        _insert_resolved(
            conn,
            signal_id="s1",
            outcome="expired",
            filled_at_ms=2 * _HOUR,
            direction="short",
            sl=105.0,
            tp=90.0,
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.2)
        assert row["mae_r"] == pytest.approx(0.6)

    def test_expired_counts_all_bars_both_extremes(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Peak fav on bar 2 (high 107 -> 1.4R), worst adv on bar 3
        # (low 96 -> 0.8R) — the LAST bar still counts for expired.
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [
                {"open_time": _HOUR, "high": 102.0, "low": 99.0, "close": 101.0},
                {"open_time": 2 * _HOUR, "high": 107.0, "low": 100.0, "close": 105.0},
                {"open_time": 3 * _HOUR, "high": 104.0, "low": 96.0, "close": 97.0},
            ],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="expired", filled_at_ms=3 * _HOUR
        )
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == pytest.approx(1.4)
        assert row["mae_r"] == pytest.approx(0.8)
        assert row["bars_held"] == 3

    def test_mfe_floors_at_zero_on_first_bar_stopout(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        # Single-bar loss: no prior bars -> MFE 0.0 (never favorable).
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 99.0, "low": 94.0, "close": 95.0}],
        )
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR)
        exc = compute_excursions(conn)
        row = exc.iloc[0]
        assert row["mfe_r"] == 0.0
        assert row["mae_r"] == pytest.approx(1.2)


class TestComputeExcursionsRobustness:
    def test_zero_risk_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        _insert_resolved(
            conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR, sl=100.0
        )
        assert compute_excursions(conn).empty

    def test_missing_ohlcv_row_skipped(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_resolved(conn, signal_id="s1", outcome="loss", filled_at_ms=_HOUR)
        assert compute_excursions(conn).empty

    def test_open_rows_excluded(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 101.0, "low": 99.0, "close": 100.0}],
        )
        upsert_signal_outcome(
            conn,
            {
                "signal_id": "s-open",
                "symbol": "BTCUSDT",
                "tf": "1h",
                "strategy": "fvg",
                "direction": "long",
                "fired_at_ms": 0,
                "candle_ts_ms": 0,
                "entry_price": 100.0,
                "sl_price": 95.0,
                "tp_price": 110.0,
                "rr_ratio": 2.0,
                "confidence_at_fire": 3,
                "tags": "",
            },
        )
        assert compute_excursions(conn).empty

    def test_batches_multiple_symbol_tf_groups(self) -> None:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        _insert_ohlcv(
            conn,
            "BTCUSDT",
            "1h",
            [{"open_time": _HOUR, "high": 104.0, "low": 98.0, "close": 99.0}],
        )
        _insert_ohlcv(
            conn,
            "ETHUSDT",
            "4h",
            [{"open_time": 4 * _HOUR, "high": 12.0, "low": 9.7, "close": 11.0}],
        )
        _insert_resolved(conn, signal_id="b1", outcome="expired", filled_at_ms=_HOUR)
        _insert_resolved(
            conn,
            signal_id="e1",
            outcome="expired",
            filled_at_ms=4 * _HOUR,
            symbol="ETHUSDT",
            tf="4h",
            entry=10.0,
            sl=9.5,
            tp=11.0,
        )
        exc = compute_excursions(conn)
        assert set(exc["symbol"]) == {"BTCUSDT", "ETHUSDT"}
        eth = exc[exc["symbol"] == "ETHUSDT"].iloc[0]
        assert eth["mfe_r"] == pytest.approx(4.0)  # (12-10)/0.5
        assert eth["mae_r"] == pytest.approx(0.6)  # (10-9.7)/0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_mfe_mae.py -q`
Expected: collection / import error — `ModuleNotFoundError: No module named 'analytics.exits'`.

- [ ] **Step 3: Create the package `__init__.py` (compute-only surface for now)**

Create `analytics/exits/__init__.py`. Task 1 exports only `EXCURSION_COLUMNS` +
`compute_excursions`; Task 2 adds `aggregate_cohorts`. This keeps the package
import resolvable at every stage.

```python
"""Exit-policy research package (exit spec 2026-06-05, parent PR #433).

This PR ships the §2 MFE/MAE excursion diagnostic (`mfe_mae.py`). The §3–§5
policy / replay / A/B layer (parent PR #437) is a deferred follow-up and will
extend these exports.
"""

from analytics.exits.mfe_mae import EXCURSION_COLUMNS, compute_excursions

__all__ = ["EXCURSION_COLUMNS", "compute_excursions"]
```

- [ ] **Step 4: Create `analytics/exits/mfe_mae.py` (`compute_excursions` half)**

Create `analytics/exits/mfe_mae.py`. This is the parent file verbatim **except**
the `get_ohlcv` import path (wifey idiom). `aggregate_cohorts` is added in Task 2
— do NOT include it yet (keeps Task 2's test red-first).

```python
"""Per-alert MFE/MAE excursion study over the live outcome ledger (exit spec §2).

For every resolved `signal_alert_outcomes` row (win / loss / expired), walk
the OHLCV bars the trade actually held — strictly after the signal candle
(`candle_ts_ms`) up to and including the exit bar (`outcome_filled_at_ms`,
as resolved by `analytics/signal/outcome_backfill.py`) — and record, in R
units (÷ |entry − sl|):

  - mfe_r: max favorable excursion (best unrealized R reached, floored at 0)
  - mae_r: max adverse excursion (worst unrealized R, positive magnitude,
    floored at 0)

Conservative intrabar conventions (anti-bias, exit spec §4):

  - loss exit bar: its favorable extreme does NOT count toward MFE — no way
    to know the favorable wick printed before the stop touch (adverse-first,
    mirrors `_scan_forward`'s same-bar tie rule).
  - win exit bar: MFE clamps to max(prior-bar MFE, rr_ratio) — post-TP
    overshoot is not credited; the exit bar's adverse extreme DOES count
    toward MAE (assume it printed before TP).
  - expired: both extremes of every in-window bar count.

Excursions are GROSS of costs — price-path geometry for exit design; net
realized PnL (fee/slippage) already lives in `outcome_r`.

Pure functions over a DuckDB conn / DataFrames; no clock or network I/O.
"""

import duckdb
import numpy as np
import pandas as pd

from analytics.store.market_data import get_ohlcv

EXCURSION_COLUMNS = [
    "signal_id",
    "symbol",
    "tf",
    "strategy",
    "direction",
    "outcome",
    "outcome_r",
    "rr_ratio",
    "mfe_r",
    "mae_r",
    "bars_held",
]


def _excursion_for_row(
    window: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    sl_price: float,
    rr_ratio: float,
    outcome: str,
) -> tuple[float, float] | None:
    """(mfe_r, mae_r) for one resolved alert over its held window.

    `window` holds the bars strictly after the signal candle up to and
    including the exit bar, in time order. Returns None when the window is
    empty or risk is zero (excursions in R are undefined).
    """
    if window.empty:
        return None
    risk = abs(entry - sl_price)
    if risk <= 0.0:
        return None
    high = window["high"].to_numpy(dtype=np.float64)
    low = window["low"].to_numpy(dtype=np.float64)
    if direction == "long":
        fav = (high - entry) / risk
        adv = (entry - low) / risk
    else:
        fav = (entry - low) / risk
        adv = (high - entry) / risk

    prior_fav = float(fav[:-1].max()) if len(fav) > 1 else 0.0
    if outcome == "loss":
        mfe = prior_fav
    elif outcome == "win":
        mfe = max(prior_fav, float(rr_ratio))
    else:  # expired — no intrabar exit event; every extreme was reachable
        mfe = float(fav.max())
    mae = float(adv.max())
    return max(mfe, 0.0), max(mae, 0.0)


def compute_excursions(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per-alert MFE/MAE rows for every resolved ledger row (EXCURSION_COLUMNS).

    Groups rows by (symbol, tf) so each group's OHLCV is fetched once — the
    same batching shape as `backfill_outcomes`. Rows with zero risk, an empty
    held window, or missing OHLCV are dropped; the caller can diff
    len(result) against the resolved count for coverage.
    """
    rows = conn.execute(
        "SELECT signal_id, symbol, tf, strategy, direction, candle_ts_ms, "
        "entry_price, sl_price, rr_ratio, outcome, outcome_r, "
        "outcome_filled_at_ms "
        "FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired') "
        "AND candle_ts_ms IS NOT NULL "
        "AND entry_price IS NOT NULL "
        "AND sl_price IS NOT NULL "
        "AND rr_ratio IS NOT NULL "
        "AND outcome_filled_at_ms IS NOT NULL"
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=EXCURSION_COLUMNS)

    by_group: dict[tuple[str, str], list[tuple]] = {}
    for r in rows:
        by_group.setdefault((str(r[1]), str(r[2])), []).append(r)

    out: list[dict[str, object]] = []
    for (symbol, tf), grp in by_group.items():
        start = min(int(r[5]) for r in grp)
        end = max(int(r[11]) for r in grp)
        bars = get_ohlcv(conn, symbol, tf, start, end)
        if bars.empty:
            continue
        open_time = bars["open_time"].to_numpy(dtype=np.int64)
        for (
            signal_id,
            _sym,
            _tf,
            strategy,
            direction,
            candle_ts_ms,
            entry_price,
            sl_price,
            rr_ratio,
            outcome,
            outcome_r,
            filled_at_ms,
        ) in grp:
            lo_i = int(np.searchsorted(open_time, int(candle_ts_ms), side="right"))
            hi_i = int(np.searchsorted(open_time, int(filled_at_ms), side="right"))
            exc = _excursion_for_row(
                bars.iloc[lo_i:hi_i],
                direction=str(direction),
                entry=float(entry_price),
                sl_price=float(sl_price),
                rr_ratio=float(rr_ratio),
                outcome=str(outcome),
            )
            if exc is None:
                continue
            mfe_r, mae_r = exc
            out.append(
                {
                    "signal_id": str(signal_id),
                    "symbol": symbol,
                    "tf": tf,
                    "strategy": str(strategy),
                    "direction": str(direction),
                    "outcome": str(outcome),
                    "outcome_r": float(outcome_r)
                    if outcome_r is not None
                    else float("nan"),
                    "rr_ratio": float(rr_ratio),
                    "mfe_r": mfe_r,
                    "mae_r": mae_r,
                    "bars_held": hi_i - lo_i,
                }
            )
    return pd.DataFrame(out, columns=EXCURSION_COLUMNS)
```

- [ ] **Step 5: Run the two test classes to verify they pass**

Run: `poetry run pytest tests/test_mfe_mae.py -q`
Expected: 9 passed. (Task 1's test file contains only `TestExcursionConventions`
and `TestComputeExcursionsRobustness`, and imports only `compute_excursions`;
`TestAggregateCohorts` is added in Task 2.)

- [ ] **Step 6: Run lint + typecheck on the new files**

Run: `make lint-py && make typecheck`
Expected: both clean (ruff format/lint pass, mypy strict no errors).

- [ ] **Step 7: Commit**

```bash
git add analytics/exits/__init__.py analytics/exits/mfe_mae.py tests/test_mfe_mae.py
git commit -m "feat(exits): MFE/MAE excursion compute — equity port (#433, part 1)"
```

---

### Task 2: `aggregate_cohorts` (the §2 verdict-grid roll-up)

**Files:**

- Modify: `analytics/exits/mfe_mae.py` (append `aggregate_cohorts`)
- Modify: `analytics/exits/__init__.py` (export `aggregate_cohorts`)
- Test: `tests/test_mfe_mae.py` (add `TestAggregateCohorts`, add the import)

- [ ] **Step 1: Add the failing aggregation tests**

Append to `tests/test_mfe_mae.py` and update the top import line to
`from analytics.exits import aggregate_cohorts, compute_excursions`:

```python
class TestAggregateCohorts:
    def _exc_df(self) -> pd.DataFrame:
        rows = [
            # 4 expired in one cell: MFE 0.2 / 0.6 / 1.5 / 0.1
            ("e1", "expired", 0.2, 0.3),
            ("e2", "expired", 0.6, 0.5),
            ("e3", "expired", 1.5, 0.4),
            ("e4", "expired", 0.1, 1.1),
            # 1 loss in same cell (filtered out at min_n=2)
            ("l1", "loss", 0.8, 1.2),
        ]
        return pd.DataFrame(
            [
                {
                    "signal_id": sid,
                    "symbol": "BTCUSDT",
                    "tf": "1h",
                    "strategy": "fvg",
                    "direction": "long",
                    "outcome": outcome,
                    "outcome_r": -0.1,
                    "rr_ratio": 2.0,
                    "mfe_r": mfe,
                    "mae_r": mae,
                    "bars_held": 10,
                }
                for sid, outcome, mfe, mae in rows
            ]
        )

    def test_reach_fractions_and_min_n(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), min_n=2)
        assert len(agg) == 1
        row = agg.iloc[0]
        assert row["outcome"] == "expired"
        assert row["n"] == 4
        assert row["reach_05"] == pytest.approx(0.5)
        assert row["reach_10"] == pytest.approx(0.25)
        assert row["tp_r_p50"] == pytest.approx(2.0)

    def test_overall_rollup_groups_by_outcome_only(self) -> None:
        agg = aggregate_cohorts(self._exc_df(), by=(), min_n=1)
        assert set(agg["outcome"]) == {"expired", "loss"}
        assert "strategy" not in agg.columns

    def test_empty_input_returns_empty(self) -> None:
        assert aggregate_cohorts(pd.DataFrame(), min_n=1).empty
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_mfe_mae.py::TestAggregateCohorts -q`
Expected: FAIL — `ImportError: cannot import name 'aggregate_cohorts'` (it is not
yet defined/exported).

- [ ] **Step 3: Append `aggregate_cohorts` to `mfe_mae.py`**

Add at the end of `analytics/exits/mfe_mae.py`:

```python
def aggregate_cohorts(
    excursions: pd.DataFrame,
    *,
    by: tuple[str, ...] = ("strategy", "tf", "direction"),
    min_n: int = 30,
) -> pd.DataFrame:
    """Cohort-level MFE/MAE aggregation — the exit spec §2 table.

    Groups by (outcome, *by); pass by=() for the overall per-cohort roll-up.
    Columns map onto the spec's 4-pattern verdict grid: reach_05 / reach_10
    are the share of the cohort whose MFE hit ≥0.5R / ≥1.0R, and tp_r_p50 is
    the target those trades were asked to reach. Cells below min_n are
    dropped (diagnostic n-floor).
    """
    if excursions.empty:
        return pd.DataFrame()
    keys = ["outcome", *by]
    enriched = excursions.assign(
        reach_05=(excursions["mfe_r"] >= 0.5).astype(float),
        reach_10=(excursions["mfe_r"] >= 1.0).astype(float),
    )
    agg = (
        enriched.groupby(keys)
        .agg(
            n=("mfe_r", "size"),
            mfe_mean=("mfe_r", "mean"),
            mfe_p50=("mfe_r", "median"),
            mae_mean=("mae_r", "mean"),
            mae_p50=("mae_r", "median"),
            reach_05=("reach_05", "mean"),
            reach_10=("reach_10", "mean"),
            tp_r_p50=("rr_ratio", "median"),
            bars_held_p50=("bars_held", "median"),
            outcome_r_mean=("outcome_r", "mean"),
        )
        .reset_index()
    )
    agg = agg[agg["n"] >= min_n]
    return agg.sort_values(keys).reset_index(drop=True)
```

- [ ] **Step 4: Export `aggregate_cohorts` from the package**

Replace `analytics/exits/__init__.py` with the full diagnostic surface:

```python
"""Exit-policy research package (exit spec 2026-06-05, parent PR #433).

This PR ships the §2 MFE/MAE excursion diagnostic (`mfe_mae.py`). The §3–§5
policy / replay / A/B layer (parent PR #437) is a deferred follow-up and will
extend these exports.
"""

from analytics.exits.mfe_mae import (
    EXCURSION_COLUMNS,
    aggregate_cohorts,
    compute_excursions,
)

__all__ = [
    "EXCURSION_COLUMNS",
    "aggregate_cohorts",
    "compute_excursions",
]
```

- [ ] **Step 5: Run the full test file to verify all pass**

Run: `poetry run pytest tests/test_mfe_mae.py -q`
Expected: 12 passed.

- [ ] **Step 6: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add analytics/exits/mfe_mae.py analytics/exits/__init__.py tests/test_mfe_mae.py
git commit -m "feat(exits): MFE/MAE cohort aggregation — verdict-grid roll-up (#433, part 2)"
```

---

### Task 3: Diagnose-mode CLI + Makefile target

**Files:**

- Create: `tools/exit_audit.py`
- Modify: `Makefile`

- [ ] **Step 1: Create `tools/exit_audit.py` (diagnose mode only)**

This is the parent's `tools/exit_audit.py` with the `--replay` path and its
`portfolio` / `analytics.exits.audit` imports removed (those ship with #437).
The verdict-grid footer reproduces the spec §2 interpretation inline so wifey is
self-contained.

```python
"""MFE/MAE diagnostic over the live alert ledger (exit spec §2) — diagnose mode.

Answers "is the expiry / −R leak exit-fixable or entry-broken?" by reporting,
per cohort (win / loss / expired) and per (strategy, tf, direction) cell:
mean/median MFE_R and MAE_R, the share of trades whose MFE reached
>=0.5R / >=1.0R, the median tp_r they were asked to reach, and median bars
held. Read the tables against the 4-pattern verdict grid printed in the footer.

Read-only — no writes, no schema changes. The exit-policy A/B (spec §3–§5,
parent PR #437) ships as a follow-up and adds a `--replay` mode here.

Usage::

    PYTHONPATH=. poetry run python tools/exit_audit.py
    PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 20
    PYTHONPATH=. poetry run python tools/exit_audit.py --csv /tmp/excursions.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import pandas as pd

from analytics.exits import aggregate_cohorts, compute_excursions
from analytics.store import DEFAULT_DB_PATH


def _print_df(title: str, df: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--min-n",
        type=int,
        default=30,
        help="hide cohort×cell rows with fewer than this many trades",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="optional path to dump the per-alert excursion rows",
    )
    args = parser.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    print(f"DB: {args.db}")

    excursions = compute_excursions(con)
    resolved_row = con.execute(
        "SELECT count(*) FROM signal_alert_outcomes "
        "WHERE outcome IN ('win', 'loss', 'expired')"
    ).fetchone()
    resolved = int(resolved_row[0]) if resolved_row else 0
    print(
        f"Coverage: {len(excursions)} of {resolved} resolved alerts scored "
        f"({resolved - len(excursions)} skipped: zero-risk or missing OHLCV)"
    )
    if excursions.empty:
        print("(nothing to report)")
        return

    _print_df(
        "Cohort roll-up (all cells)", aggregate_cohorts(excursions, by=(), min_n=1)
    )
    _print_df(
        f"Cohort × (strategy, tf, direction) — min_n={args.min_n}",
        aggregate_cohorts(excursions, min_n=args.min_n),
    )
    print(
        "\nVerdict grid (exit spec §2):\n"
        "  expired reach_10 high + tp_r_p50 higher  => TP unreachably far: "
        "lower tp_r / add partial at 1R (exit fix).\n"
        "  expired reach_05 low                     => weak entries, not exits: "
        "prune/down-weight; don't paper over with exit tuning.\n"
        "  loss mfe high before SL                  => went +1R then reversed: "
        "breakeven / trail candidate (exit fix).\n"
        "  loss mfe low, fast mae                   => just wrong: exit can't "
        "help; entry/SL issue."
    )

    if args.csv is not None:
        excursions.to_csv(args.csv, index=False)
        print(f"\nPer-alert excursions written to {args.csv}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the CLI imports and runs `--help`**

Run: `PYTHONPATH=. poetry run python tools/exit_audit.py --help`
Expected: argparse help text prints (no import error), listing `--db`,
`--min-n`, `--csv`.

- [ ] **Step 3: Add the Makefile target**

In `Makefile`, add `wifey-exit-audit` to the `.PHONY` line (alongside
`wifey-forecast-audit wifey-xsmom-audit`) and add the target next to the other
two audit targets (after the `wifey-xsmom-audit` recipe at ~line 132):

```makefile
wifey-exit-audit:
    @echo "🚪 Exit MFE/MAE diagnostic over the live alert ledger (spec §2)..."
    @PYTHONPATH=. poetry run python tools/exit_audit.py $(ARGS)
```

> **Note for the engineer:** the recipe lines above must be indented with a
> literal **TAB**, not spaces (Makefile syntax). The `.PHONY` edit: add
> `wifey-exit-audit` (space-separated) immediately after `wifey-xsmom-audit` in
> the existing `.PHONY:` list at the top of the Makefile.

- [ ] **Step 4: Verify the make target dispatches**

Run: `make wifey-exit-audit ARGS=--help`
Expected: the emoji echo line, then the argparse help text.

- [ ] **Step 5: Lint + typecheck (tools/ is in scope)**

Run: `make lint-py && make typecheck`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add tools/exit_audit.py Makefile
git commit -m "feat(exits): exit_audit diagnose-mode CLI + make wifey-exit-audit (#433)"
```

---

### Task 4: Full gate + real-ledger run → verdict doc

**Files:**

- Create: `docs/audits/2026-06-20-exit-mfe-mae-diagnostic-equity.md`

- [ ] **Step 1: Full local gate**

Run: `make lint-py && make typecheck && make test`
Expected: ruff clean, mypy strict clean, full suite green (existing pass count
plus 12 new `test_mfe_mae.py` tests; 3 pre-existing skips).

- [ ] **Step 2: Confirm regression goldens are byte-identical**

Run: `make test-regression`
Expected: both configs PASS, **no golden movement** (this PR is additive
read-only — new package + new CLI + new test only; no detector / schema /
DB-write change touches the backtest pipeline). If a golden moves, STOP — that
means something non-additive slipped in; investigate before continuing.

- [ ] **Step 3: Run the diagnostic over the real ledger**

Run:

```bash
make wifey-exit-audit ARGS="--min-n 20 --csv /tmp/exit-excursions.csv"
```

Expected: a coverage line (`N of M resolved alerts scored`, where M is the
current resolved count — ~22 on the young ledger), the overall cohort roll-up
table, the per-(strategy, tf, direction) table (likely empty at `min_n=20` while
the ledger is thin), and the verdict footer. Capture the full stdout — it is the
raw material for Step 4.

> If coverage is surprisingly low (e.g. `0 of N`), check that the local
> `analytics.db` is the live one (`ls -la analytics.db`) and that resolved rows
> carry non-NULL `candle_ts_ms` / `entry_price` / `sl_price` / `rr_ratio` /
> `outcome_filled_at_ms`. A low score with a high resolved count points at
> missing OHLCV coverage for the alerts' (symbol, tf), not a code bug.

- [ ] **Step 4: Write the verdict note**

Create `docs/audits/2026-06-20-exit-mfe-mae-diagnostic-equity.md` capturing:

- one-line headline verdict against the §2 grid (exit-fixable vs entry-broken
  vs mixed — or INCONCLUSIVE if n is too thin), keyed off the **expired**
  cohort's `reach_05` / `reach_10` / `tp_r_p50` and the **loss** cohort's
  `mfe_mean`;
- the overall cohort roll-up table (paste from Step 3);
- the per-cell highlights (the cells with the clearest exit-fixable or
  entry-broken signature);
- coverage (`N scored of M`) and the data caveat (live ledger young, equity
  4h/1d/1wk, costs gross);
- an explicit **recommendation on #437**: does the diagnostic justify building
  the exit-policy A/B (clear exit-fixable cohorts), or does it say "entries are
  the problem, don't tune exits" (→ backlog #437)?

This is CI-linted markdown (`docs/audits/` is in the lint set) — hand-format to
the full markdownlint ruleset (MD060 table pipes, MD010 no hard tabs in fenced
blocks, etc.).

- [ ] **Step 5: Lint the audit doc**

Run: `make lint-md`
Expected: clean (and remember CI is stricter than local — eyeball tables for
MD060 / MD013 if unsure).

- [ ] **Step 6: Commit**

```bash
git add docs/audits/2026-06-20-exit-mfe-mae-diagnostic-equity.md
git commit -m "docs(exits): MFE/MAE diagnostic equity verdict + #437 recommendation (#433)"
```

---

### Task 5: PR + post-branch docs sweep

**Files:** (decided by the `/post-branch` behaviour gate — likely)

- Modify: `CLAUDE.md` (Project Structure: `analytics/exits/` entry + `tools/exit_audit.py` entry)
- Modify: `.claude/context/analytics.md` (new `## exits/` section)
- Modify: `README.md` (the `wifey-exit-audit` make target, if README lists audit targets)
- Modify: `MEMORY.md` (Current State + correct the stale "live ledger n≈0" note)

- [ ] **Step 1: Push the branch**

```bash
git push -u origin HEAD
```

(Uses the `git@github.com-personal:` SSH alias configured for this repo.)

- [ ] **Step 2: Create the PR (s10023 account must be active)**

```bash
gh auth switch --user s10023
gh pr create --repo s10023/buibui-wifey-wall-street-bot \
  --title "feat(exits): MFE/MAE diagnostic — equity port (#433)" \
  --body-file /tmp/pr-exit-mfe-mae.md
gh auth switch --user KhaiJianNgFRG
```

(Write the PR body with the `/pr-summary` skill first → `/tmp/pr-<branch>.md`.
The work account `KhaiJianNgFRG` is NOT a collaborator, hence the
switch-create-restore.)

- [ ] **Step 3: Run `/post-branch`**

Invoke the `post-branch` skill immediately after the PR is created, before
reporting the URL. It diffs the branch's behaviour against the doc surfaces and
proposes targeted edits. Confirm each edit before writing. Key edits expected:
add the `analytics/exits/` bullet to CLAUDE.md Project Structure (note: §2
diagnostic only, #437 deferred), the new `## exits/` section in
`.claude/context/analytics.md`, the make target in README, and the MEMORY
Current State update (the "live ledger young" note stays accurate — it is ~22
resolved, and the verdict turns on that thinness).

- [ ] **Step 4: Rewrite the handoff prompt**

Overwrite `docs/plans/next-conversation-prompt.md` with the next task: either
**execute #437** (if the verdict says exits are fixable) or **bump the
parent-sync pointer `2698cfd` → `e3b2a05` and backlog #437** (if the verdict
says entries are the problem). Include the verdict headline so the next session
starts informed.

---

## Self-Review notes

- **Spec coverage:** parent PR #433 = `mfe_mae.py` (`compute_excursions` Task 1 +
  `aggregate_cohorts` Task 2), `tools/exit_audit.py` diagnose mode (Task 3),
  `test_mfe_mae.py` (Tasks 1–2). The §3–§5 replay/policy/A/B layer (#437) is
  explicitly OUT of scope and tracked as the follow-up. The real-ledger run +
  verdict (Task 4) is the actual deliverable that gates #437.
- **No portfolio dependency:** this PR touches nothing in the parent's
  `portfolio/` package (which wifey lacks). That dependency only enters with the
  #437 A/B and is handled there via the per-trade-R-Sharpe substitution.
- **Type consistency:** `compute_excursions(conn) -> pd.DataFrame`,
  `aggregate_cohorts(excursions, *, by=(...), min_n=...) -> pd.DataFrame`,
  `_excursion_for_row(...) -> tuple[float, float] | None`, `EXCURSION_COLUMNS:
  list[str]` — names and signatures are consistent across Tasks 1–3 and match
  the parent verbatim.
- **Additive read-only invariant:** asserted in Task 4 Step 2 (goldens
  byte-identical). If that check fails the PR is not actually additive — stop.
