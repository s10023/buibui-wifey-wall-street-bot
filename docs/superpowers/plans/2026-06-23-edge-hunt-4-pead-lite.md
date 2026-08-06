# Edge-Hunt #4 — PEAD-lite Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a post-earnings-announcement-drift (PEAD-lite) sleeve that trades a free-data seasonal-random-walk earnings surprise (SUE) over the S&P-500 universe as an overlapping 60-day drift book, scored on a pre-registered net-of-cost gate with an equity-beta guardrail — additive and read-only on the signal/backtest path so the regression goldens stay byte-identical. The one genuinely new piece of infrastructure is **EDGAR earnings ingestion** (a client + a new `earnings_facts` table); everything downstream is read-only.

**Architecture:** A new self-contained package `analytics/pead/` mirroring the `forecast/` / `xsmom/` / `lowvol/` / `xasset/` split (`signals.py` pure SUE+book, `replay.py` DB-only, `report.py` gate, `__init__.py` re-exports), plus `utils/edgar_client.py` (stdlib-`urllib` EDGAR fetch/parse), `analytics/store/earnings.py` (the new table's upsert/get), a one-shot backfill, and a read-only `tools/pead_audit.py`. It reuses `forecast.vol` (inverse-vol sizing), `forecast.replay.load_daily_inputs` (1d closes), `forecast.report.evaluate` / `G2Report` (DSR/PBO/boot-CI/MinTRL), `xsmom.diagnostics.beta_attribution` (the β guardrail), `analytics.trading_calendar` (the next-session entry anchor), and the Phase-0.4 cost surface. Nothing on the detector / backtest / signal path imports any of this, so the goldens cannot move.

**Tech Stack:** Python 3.11, numpy, pandas, duckdb (in-memory for tests), stdlib `urllib` (EDGAR), pytest, ruff, mypy strict. Spec: `docs/superpowers/specs/2026-06-23-edge-hunt-4-pead-lite-design.md`.

---

## Setup (before Task 1)

This plan ships alongside the spec as a **docs-only PR** (branch
`docs/edge-hunt-4-pead-lite`). Execution happens later on a fresh feature branch:

- [ ] After the docs PR merges, branch off `main`:
  `git checkout main && git pull && git checkout -b feat/pead-lite-sleeve`
- [ ] Confirm the baseline is green: `make test` passes (1727 / 3 skip) and
  `make test-regression` PASSES both configs. These are the byte-identical
  goldens the sleeve must not move.
- [ ] Verify the per-repo git identity before the first commit:
  `git config --local user.email` must print `ngkhaijian@gmail.com`.

## File structure

- Create `utils/edgar_client.py` — pure EDGAR fetch/parse (Task 1).
- Create `analytics/store/earnings.py` — `earnings_facts` upsert/get (Task 2).
- Modify `analytics/store/schema.py` — add `earnings_facts` to `init_schema` + migration list (Task 2).
- Modify `analytics/data_store.py` — re-export the new earnings store helpers (Task 2).
- Create `tools/pead_backfill.py` — one-shot EDGAR → `earnings_facts` ingestion (Task 3).
- Modify `Makefile` — `wifey-pead-backfill` + `wifey-pead-audit` targets + `.PHONY` (Task 3, Task 8).
- Create `analytics/pead/signals.py` — pure SUE + overlapping-cohort book (Task 4).
- Create `analytics/pead/replay.py` — the only DB-touching audit module; 2×2 + SPY (Task 5).
- Create `analytics/pead/report.py` — `PeadGridReport` + `evaluate_pead_grid` gate (Task 6).
- Create `analytics/pead/__init__.py` — eager re-exports (Task 7).
- Create `tools/pead_audit.py` — read-only verdict CLI (Task 8).
- Create tests: `tests/test_edgar_client.py` (Task 1), `tests/test_earnings_store.py` (Task 2), `tests/test_pead_signals.py` (Task 4), `tests/test_pead_replay.py` (Task 5), `tests/test_pead_report.py` (Task 6).
- Create JSON fixtures: `tests/fixtures/edgar/aapl_companyfacts_trimmed.json`, `tests/fixtures/edgar/aapl_submissions_trimmed.json`, `tests/fixtures/edgar/company_tickers_trimmed.json` (Task 1).
- Create `docs/audits/2026-06-23-edge-hunt-4-pead-lite.md` — verdict note (Task 10).

The 2×2 grid keys are fixed: `broad_ls` (**gated cell**), `broad_long`, `mega_ls`, `mega_long`.

---

### Task 1: EDGAR client — fetch + parse (pure, fixture-tested)

**Files:**

- Create: `utils/edgar_client.py`
- Test: `tests/test_edgar_client.py`
- Fixtures: trimmed AAPL `companyfacts` + `submissions` + `company_tickers` JSON.

Fetching is thin; the **value and the risk are in the pure parse functions**, which
are fed committed JSON fixtures (never the network) per house test rules. Build the
fixtures by trimming a live pull to ~6 quarters of EPS facts + a handful of 8-K /
10-Q submission rows (do this once during implementation, then commit them).

- [ ] **Step 1: Write the failing tests** (`tests/test_edgar_client.py`)

```python
import json
from pathlib import Path

from utils.edgar_client import (
    EpsFact,
    parse_eps_facts,
    parse_announce_dates,
    ticker_to_cik,
)

_FIX = Path(__file__).parent / "fixtures" / "edgar"


def _load(name: str) -> dict:
    return json.loads((_FIX / name).read_text())


def test_ticker_to_cik_zero_pads_to_10() -> None:
    tickers = _load("company_tickers_trimmed.json")
    assert ticker_to_cik(tickers, "AAPL") == "0000320193"


def test_parse_eps_facts_returns_quarterly_only() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    # all returned facts are ~one fiscal quarter long
    for f in facts:
        span = (f.period_end - f.period_start).days
        assert 80 <= span <= 100 or f.fp == "Q4"  # Q4 derived, no native span
    # fiscal labels present and de-duplicated by (fy, fp)
    keys = [(f.fy, f.fp) for f in facts]
    assert len(keys) == len(set(keys))


def test_parse_eps_facts_keeps_earliest_filed_on_restatement() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    # the fixture seeds one (fy, fp) twice with two filed dates;
    # the surviving record must carry the EARLIER filed date.
    target = next(f for f in facts if (f.fy, f.fp) == (2023, "Q1"))
    assert target.filed == "2023-02-03"  # the original, not the amendment


def test_parse_announce_dates_prefers_8k_item_202() -> None:
    subs = _load("aapl_submissions_trimmed.json")
    dates = parse_announce_dates(subs)
    # returns 8-K item-2.02 filing dates as a sorted list of ISO strings
    assert "2023-02-02" in dates
    assert dates == sorted(dates)
```

- [ ] **Step 2: Run to verify it fails** — `poetry run pytest tests/test_edgar_client.py -v` → `ModuleNotFoundError: utils.edgar_client`.

- [ ] **Step 3: Minimal implementation** (`utils/edgar_client.py`)

```python
"""Free EDGAR earnings ingestion for edge-hunt #4 (PEAD-lite).

Pure fetch + parse over the SEC's public JSON APIs (no key; a descriptive
User-Agent and ≤10 req/s throttle per SEC fair-access rules). Mirrors
utils/yfinance_client.py: no module-level side effects, network isolated to the
fetch_* helpers, all parsing pure and fixture-testable.
"""
from __future__ import annotations

import time
import json
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

# Contact moved to $EDGAR_CONTACT_EMAIL on 2026-08-06 — see utils/edgar_client.py
_DEFAULT_CONTACT = "https://github.com/s10023/buibui-wifey-wall-street-bot"
_MIN_INTERVAL = 0.12  # ~8 req/s, under SEC's 10 req/s ceiling
_last_call = 0.0


@dataclass(frozen=True)
class EpsFact:
    fy: int
    fp: str            # "Q1" | "Q2" | "Q3" | "Q4"
    period_start: date
    period_end: date
    eps_diluted: float
    filed: str         # ISO date the value was first filed
    accn: str


def _iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _get_json(url: str) -> dict:
    global _last_call
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    _last_call = time.monotonic()
    return data


# ---- network shims (not unit-tested; integration-only) -------------------
def fetch_company_tickers() -> dict:
    return _get_json("https://www.sec.gov/files/company_tickers.json")


def fetch_company_facts(cik: str) -> dict:
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")


def fetch_submissions(cik: str) -> dict:
    return _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")


# ---- pure parsers (fixture-tested) ---------------------------------------
def ticker_to_cik(company_tickers: dict, ticker: str) -> str | None:
    for row in company_tickers.values():
        if row.get("ticker", "").upper() == ticker.upper():
            return f"{int(row['cik_str']):010d}"
    return None


def parse_eps_facts(company_facts: dict) -> list[EpsFact]:
    """Quarterly diluted EPS, originally-filed-only, Q4 derived from FY−ΣQ1..Q3."""
    gaap = company_facts.get("facts", {}).get("us-gaap", {})
    units = gaap.get("EarningsPerShareDiluted", {}).get("units", {})
    raw = next(iter(units.values()), [])  # USD/shares
    # 1. keep 10-Q/10-K, parse dates, classify quarter vs full-year by span
    rows = []
    for f in raw:
        if f.get("form") not in ("10-Q", "10-K"):
            continue
        if not (f.get("start") and f.get("end") and f.get("filed")):
            continue
        s, e = _iso(f["start"]), _iso(f["end"])
        rows.append((s, e, (e - s).days, float(f["val"]), f["filed"],
                     f.get("fy"), f.get("fp"), f["accn"]))
    # 2. earliest-filed dedup per (fy, fp); split native-quarter vs FY rows
    best: dict[tuple, tuple] = {}
    fy_rows: dict[int, tuple] = {}
    for s, e, span, val, filed, fy, fp, accn in rows:
        if fy is None:
            continue
        if 80 <= span <= 100 and fp in ("Q1", "Q2", "Q3"):
            key = (fy, fp)
            if key not in best or filed < best[key][4]:
                best[key] = (s, e, span, val, filed, fy, fp, accn)
        elif span >= 350 and fp == "FY":
            if fy not in fy_rows or filed < fy_rows[fy][4]:
                fy_rows[fy] = (s, e, span, val, filed, fy, "FY", accn)
    facts = [EpsFact(fy, fp, s, e, val, filed, accn)
             for (s, e, span, val, filed, fy, fp, accn) in best.values()]
    # 3. derive Q4 = FY − (Q1+Q2+Q3) when all four present
    for fy, fyrow in fy_rows.items():
        q = {fp: best.get((fy, fp)) for fp in ("Q1", "Q2", "Q3")}
        if all(q.values()):
            q4_val = fyrow[3] - sum(v[3] for v in q.values())
            facts.append(EpsFact(fy, "Q4", q["Q3"][1], fyrow[1], q4_val,
                                 fyrow[4], fyrow[7]))
    return sorted(facts, key=lambda f: (f.fy, f.fp))


def parse_announce_dates(submissions: dict) -> list[str]:
    """8-K item-2.02 filing dates (ISO), sorted — the announcement anchors."""
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    items = recent.get("items", [])
    dates = recent.get("filingDate", [])
    out = []
    for form, item, d in zip(forms, items, dates):
        if form == "8-K" and "2.02" in (item or ""):
            out.append(d)
    return sorted(out)
```

- [ ] **Step 4: Build + commit the trimmed fixtures.** Pull live once
  (`fetch_company_facts("0000320193")` etc.), trim to ~6 quarters + seed one
  `(2023, "Q1")` restatement pair (earlier + later `filed`) and one 8-K
  item-2.02 row dated `2023-02-02`, write to `tests/fixtures/edgar/`.

- [ ] **Step 5: Run to GREEN** — `poetry run pytest tests/test_edgar_client.py -v`.

- [ ] **Step 6: Commit** — `feat(pead): EDGAR client + earnings parse (edge-hunt #4 task 1)`.

---

### Task 2: `earnings_facts` table + store module

**Files:**

- Create: `analytics/store/earnings.py`
- Modify: `analytics/store/schema.py` (CREATE TABLE + migration list), `analytics/data_store.py` (re-export)
- Test: `tests/test_earnings_store.py`

`earnings_facts` is a brand-new table touched by nothing legacy, so it goes in
both `init_schema`'s CREATE TABLE **and** the migration list with no
positional-INSERT hazard (the hazard that forces migration-list-only columns
elsewhere does not apply to a fresh table). Use the sealed `_upsert`
register/unregister convention from `store/_common.py` — **never** implicit
replacement scan.

- [ ] **Step 1: Failing test** (`tests/test_earnings_store.py`)

```python
import duckdb
from analytics.store.schema import init_schema
from analytics.store.earnings import upsert_earnings_facts, get_earnings_facts


def test_upsert_then_get_roundtrip() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    rows = [
        {"symbol": "AAPL", "cik": "0000320193", "fy": 2023, "fp": "Q1",
         "period_end": "2022-12-31", "eps_diluted": 1.88,
         "announce_date": "2023-02-02", "filed_date": "2023-02-03",
         "accn": "x", "source": "8k"},
    ]
    upsert_earnings_facts(conn, rows)
    got = get_earnings_facts(conn, symbols=["AAPL"])
    assert len(got) == 1
    assert got.iloc[0]["fp"] == "Q1"
    # idempotent upsert on PK (symbol, fy, fp)
    upsert_earnings_facts(conn, rows)
    assert len(get_earnings_facts(conn, symbols=["AAPL"])) == 1
```

- [ ] **Step 2: Verify fails** (no `earnings_facts` table / module).

- [ ] **Step 3: Implementation.** In `schema.py` add to `init_schema` and the
  migration list:

```sql
CREATE TABLE IF NOT EXISTS earnings_facts (
    symbol       VARCHAR NOT NULL,
    cik          VARCHAR,
    fy           INTEGER NOT NULL,
    fp           VARCHAR NOT NULL,
    period_end   DATE,
    eps_diluted  DOUBLE,
    announce_date DATE,
    filed_date   DATE,
    accn         VARCHAR,
    source       VARCHAR,
    PRIMARY KEY (symbol, fy, fp)
);
```

`analytics/store/earnings.py` — `upsert_earnings_facts(conn, rows)` via the sealed
`_upsert` (register a DataFrame, `INSERT OR REPLACE`, unregister in finally);
`get_earnings_facts(conn, symbols=None)` → DataFrame filtered by symbol. Re-export
both from `analytics/data_store.py`.

- [ ] **Step 4: GREEN** — `poetry run pytest tests/test_earnings_store.py -v`.

- [ ] **Step 5:** Confirm `make test-regression` still PASSES (a fresh table
  must not move goldens). **Commit.**

---

### Task 3: One-shot backfill (`tools/pead_backfill.py`) + Makefile

**Files:**

- Create: `tools/pead_backfill.py`
- Modify: `Makefile`

Per the spec's open question, the backfill stays a `tools/` one-shot (like the
xasset backfill effectively was), not a `wifey` subcommand — it touches a new
client + table and is run once.

- [ ] **Step 1:** For each universe CIK: `fetch_company_facts` → `parse_eps_facts`;
  `fetch_submissions` → `parse_announce_dates`; match each EPS fact to the earliest
  8-K item-2.02 date strictly after `period_end` and on/before the 10-Q `filed`
  (fallback: the `filed` date, `source="10q"`); build rows; `upsert_earnings_facts`.
  Resolve ticker→CIK once via `fetch_company_tickers`. Polite throttle is in the
  client. Print a coverage summary (names with EPS, names missing CIK, fallback
  fraction).
- [ ] **Step 2:** Make `build_rows(eps_facts, announce_dates, symbol, cik)` the
  **pure testable unit** (the matching logic), network kept in `main()`. Add a
  small unit test for the 8-K-vs-fallback match in `tests/test_edgar_client.py`
  or a new `tests/test_pead_backfill.py`.
- [ ] **Step 3:** Makefile:

```make
wifey-pead-backfill:  ## Edge-hunt #4: ingest EDGAR earnings facts (one-shot)
    PYTHONPATH=. poetry run python tools/pead_backfill.py

.PHONY: wifey-pead-backfill
```

(The recipe line must be a real **tab** in the Makefile, shown as spaces here to
satisfy markdownlint.)

- [ ] **Step 4:** Lint/type/commit. (No live run yet — that is Task 10.)

---

### Task 4: PEAD signals — SUE + overlapping-cohort book (pure)

**Files:**

- Create: `analytics/pead/signals.py`
- Test: `tests/test_pead_signals.py`

The causality heart. Everything is `.shift`-guarded and keyed on `announce_date`,
never `period_end`. **The perturbation test is mandatory and must be
non-vacuous** (RED without the guard).

- [ ] **Step 1: Failing tests** (`tests/test_pead_signals.py`)

```python
import numpy as np
import pandas as pd
from analytics.pead.signals import (
    seasonal_sue,
    sue_leverage,
)


def test_seasonal_sue_is_eps_minus_year_ago_over_std() -> None:
    # 8 quarters of a single name; hand-computed SUE on the latest
    eps = pd.DataFrame({
        "symbol": ["AAA"] * 8,
        "fy": [2021, 2021, 2021, 2021, 2022, 2022, 2022, 2022],
        "fp": ["Q1", "Q2", "Q3", "Q4"] * 2,
        "announce_date": pd.to_datetime([
            "2021-02-01", "2021-05-01", "2021-08-01", "2021-11-01",
            "2022-02-01", "2022-05-01", "2022-08-01", "2022-11-01"]),
        "eps_diluted": [1.0, 1.1, 1.2, 1.3, 1.2, 1.3, 1.5, 1.6],
    })
    sue = seasonal_sue(eps)
    # 2022-Q1 UE = 1.2 - 1.0 = 0.2; std over prior UEs only
    assert sue.loc[sue["announce_date"] == "2022-02-01", "ue"].iloc[0] == 0.2


def test_sue_uses_only_prior_quarters_no_lookahead() -> None:
    # injecting a FUTURE-dated, huge EPS must not change any earlier SUE
    base = _panel()
    sue_base = seasonal_sue(base)
    perturbed = base.copy()
    perturbed.loc[perturbed["announce_date"] == perturbed["announce_date"].max(),
                  "eps_diluted"] = 999.0
    sue_pert = seasonal_sue(perturbed)
    earlier = sue_base["announce_date"] < base["announce_date"].max()
    pd.testing.assert_series_equal(
        sue_base.loc[earlier, "sue"], sue_pert.loc[earlier, "sue"])


def test_long_only_clip_has_no_negative_weight() -> None:
    lev = sue_leverage(_sue_panel(), _closes(), long_only=True, window=60)
    assert (lev.fillna(0.0) >= -1e-12).all().all()


def test_long_short_is_dollar_neutral_each_day() -> None:
    lev = sue_leverage(_sue_panel(), _closes(), long_only=False, window=60)
    day_sums = lev.sum(axis=1).dropna()
    assert np.allclose(day_sums.values, 0.0, atol=1e-9)
```

(`_panel` / `_sue_panel` / `_closes` are small synthetic builders in the test
file.)

- [ ] **Step 2: Verify fails.**

- [ ] **Step 3: Implementation** (`analytics/pead/signals.py`) — key shapes:

```python
def seasonal_sue(eps: pd.DataFrame) -> pd.DataFrame:
    """Add ue = eps - eps[same fp, fy-1] and sue = ue / trailing-std(ue).

    Causal: the std at row q uses only UEs whose announce_date < q's. Rows with
    <4 prior UE observations get sue = NaN (warm-up). No future row affects an
    earlier sue.
    """
    # sort by (symbol, announce_date); per symbol+fp, ue = eps.diff() across years;
    # per symbol, expanding/rolling std of ue shifted by 1 announcement; sue = ue/std.


def sue_leverage(
    sue: pd.DataFrame,           # long: symbol, announce_date, sue
    closes: pd.DataFrame,        # wide: index=session date, cols=symbols
    *,
    long_only: bool,
    window: int = 60,            # pre-registered drift horizon (trading days)
    vol_target: float = 0.20,
) -> pd.DataFrame:
    """Wide daily leverage matrix from overlapping SUE cohorts.

    For each (symbol, announce_date): entry at the next NYSE session, target
    weight ∝ cross-sectional z(sue) at that announcement, inverse-vol scaled
    (forecast.vol, .shift(1)), held flat `window` sessions then 0. Overlapping
    cohorts sum. Then per day: clip≥0 (long_only) or demean to Σw=0 (L/S),
    inverse-vol already applied, finally scale to a causal portfolio vol target.
    """
```

Reuse `forecast.vol` for realized vol; `analytics.trading_calendar.nyse_sessions`
(or the closes index) for the next-session entry; the demean/clip mirror the
`xsmom` / `lowvol` patterns.

- [ ] **Step 4: GREEN.** All four tests pass, perturbation non-vacuous (delete the
  `.shift`/prior-only guard → `test_sue_uses_only_prior_quarters_no_lookahead`
  must FAIL). **Commit.**

---

### Task 5: Replay — DB-only 2×2 grid + SPY benchmark

**Files:**

- Create: `analytics/pead/replay.py`
- Test: `tests/test_pead_replay.py`

- [ ] **Step 1: Failing test** — seed an in-memory DuckDB with `init_schema`,
  deterministic random-walk 1d OHLCV for ~6 tickers + SPY, and a handful of
  `earnings_facts`; assert `replay_pead_grid(conn, cfg)` returns the four keys
  `{broad_ls, broad_long, mega_ls, mega_long}` each a result with an
  `equity_curve`, and `pead_market_return(conn, cfg)` is a non-empty Series.

```python
def test_grid_has_four_cells_and_spy_benchmark() -> None:
    conn = _seeded_conn()
    grid = replay_pead_grid(conn, _cfg())
    assert set(grid) == {"broad_ls", "broad_long", "mega_ls", "mega_long"}
    assert all(len(r.equity_curve) > 0 for r in grid.values())
    spy = pead_market_return(conn, _cfg())
    assert isinstance(spy, pd.Series) and len(spy) > 0
```

- [ ] **Step 2: Verify fails.**

- [ ] **Step 3: Implementation** — load 1d closes via
  `forecast.replay.load_daily_inputs` over `ResearchUniverse.stocks()` (broad) and
  the `config/universe_sp100_snapshot.json` ∩ active set (mega); load
  `earnings_facts` via `get_earnings_facts`; build the SUE panel
  (`seasonal_sue`); run `sue_leverage` per cell (broad/mega × long_only T/F) into
  per-instrument returns + cost (Phase-0.4 surface) → an aggregated book result
  with `equity_curve`. `pead_market_return` loads SPY **separately** so it never
  enters the universe demean.

- [ ] **Step 4: GREEN.** Confirm `make test-regression` PASSES. **Commit.**

---

### Task 6: Report — gate on `broad_ls`

**Files:**

- Create: `analytics/pead/report.py`
- Test: `tests/test_pead_report.py`

- [ ] **Step 1: Failing test** — feed `evaluate_pead_grid` a synthetic
  `dict[str, result]` + SPY series; assert `PeadGridReport` has
  `committed_key == "broad_ls"`, per-cell finite DSR/PBO/Sharpe, a finite realized
  β to SPY, and that `passed` / `deploy_grade` are booleans wired to
  `_GATE_SHARPE = 0.7` / `_DEPLOY_SHARPE = 1.0`.

```python
def test_gate_reads_broad_ls_only() -> None:
    rep = evaluate_pead_grid(_fake_grid(), _spy(), cfg=_cfg())
    assert rep.committed_key == "broad_ls"
    assert isinstance(rep.passed, bool)
    assert np.isfinite(rep.realized_beta)
```

- [ ] **Step 2: Verify fails.**

- [ ] **Step 3: Implementation** — `evaluate_pead_grid(grid, spy_ret, *, cfg)`
  calls `forecast.report.evaluate` per cell over the four-cell trial family
  (DSR/PBO/boot-CI/MinTRL, annualization from `cfg`), `beta_attribution` of
  `broad_ls` vs SPY, and applies the pre-registered gate
  (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0 ∧ n ≥ MinTRL ∧ Sharpe ≥ 0.7) to
  `broad_ls`. `deploy_grade` = Sharpe ≥ 1.0 ∧ `broad_long` Sharpe > 0.

- [ ] **Step 4: GREEN. Commit.**

---

### Task 7: Package `__init__` re-exports

**Files:** Create `analytics/pead/__init__.py` — eager re-export the public
surface (`seasonal_sue`, `sue_leverage`, `replay_pead_grid`, `pead_market_return`,
`evaluate_pead_grid`, `PeadGridReport`), matching the sibling packages. Add an
import-smoke assertion to one test. Lint/type/**commit.**

---

### Task 8: Read-only audit CLI + Makefile

**Files:**

- Create: `tools/pead_audit.py`
- Modify: `Makefile`

- [ ] **Step 1:** `build_grid(conn, *, slippage_bps)` (the testable unit) wraps
  `replay_pead_grid` + `evaluate_pead_grid`. `main()` prints the 2×2 at
  **0 / 2 / 8 bps**, each cell's headline Sharpe + DSR/PBO/boot-CI/MinTRL, the
  realized equity-β to SPY, the 8-K-vs-fallback coverage fraction, the long-flat
  leg Sharpe, and **PASS / FAIL (+ deploy-grade flag) on `broad_ls`**.
  `--slippage-bps N` override, `--min-n`, optional `--csv`.
- [ ] **Step 2:** Makefile `wifey-pead-audit` target + `.PHONY`.
- [ ] **Step 3:** Add a tiny `build_grid` smoke test (seeded conn) to
  `tests/test_pead_replay.py` or a new file. Lint/type/**commit.**

---

### Task 9: Full gate — lint, types, suite, goldens byte-identical

- [ ] `make lint-py` (ruff format + lint) clean.
- [ ] `make typecheck` (mypy strict) clean — annotate every function, `-> None`
  on tests.
- [ ] `make test` green (1727 + the new tests, 3 skip).
- [ ] `make test-regression` **PASSES both configs — byte-identical.** If a golden
  moved, STOP: something imported the sleeve onto the signal/backtest path. Fix
  the import, do not regenerate goldens.
- [ ] `make lint-md` (+ hand-format to the full markdownlint default ruleset —
  CI is stricter than local; MD060 etc.).
- [ ] **Commit** any cleanup.

---

### Task 10: Backfill, run the audit, write the verdict

- [ ] `make wifey-pead-backfill` — ingest EDGAR earnings for the universe (one-shot;
  inspect the coverage summary: names with EPS, missing CIKs, 8-K-fallback %).
- [ ] `make wifey-pead-audit` — run the 2×2 at 0/2/8 bps.
- [ ] Write `docs/audits/2026-06-23-edge-hunt-4-pead-lite.md`: the headline
  `broad_ls` verdict (PASS/FAIL), the full 2×2 grid, the cost-sensitivity table,
  the realized equity-β guardrail reading, the 8-K-coverage diagnostic, the
  large-cap-efficiency caveat, and — **per pre-registration — NO retune-and-rerun**
  on a fail. A fail is recorded honestly and the roadmap advances to the
  **honest-exit decision** (all four free-data families exhausted).
- [ ] Update `MEMORY.md` Current State + the per-sleeve log in
  `[[binding-constraint-no-equity-edge]]`; rewrite
  `docs/plans/next-conversation-prompt.md`.
- [ ] `/post-branch` docs sweep (CLAUDE.md analytics/tools/config bullets, README,
  Makefile targets, `.claude/context/analytics.md`).

## After execution

- [ ] `/pr-summary`, open the feat PR with `--repo s10023/buibui-wifey-wall-street-bot`.
- [ ] On a fail, surface the **honest-exit decision** to the user explicitly: five
  price-factor sleeves + one event sleeve now fail clean → decide $29/mo Polygon
  (depth / breadth / estimates → analyst-SUE PEAD retest, PIT membership, small-cap
  universe, futures-grade TSMOM) vs. accept that a free-data US-investable edge
  needs paid event/flow data.
