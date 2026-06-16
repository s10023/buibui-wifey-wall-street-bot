# N3 PR2 — Calendar-Aware Sessions / Gap Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a US-equity (NYSE) trading calendar and use it to detect *missing trading sessions*
in ingested OHLCV — closing the "calendar-aware gap detection is intentionally out of scope" gap
left in `analytics/data_quality.py` by the crypto-parent's 24/7 assumptions.

**Architecture:** Isolate the new `exchange_calendars` runtime dependency behind one thin module
(`analytics/trading_calendar.py`) that exposes NYSE session dates. The gap-detection *math* lives as
a **pure** function in `analytics/data_quality.py` (no calendar import — keeps the module's "no
network, no side effects" guarantee), consuming a precomputed session-date list. Both stored bars and
the calendar are normalised to **NYSE session dates in America/New_York** rather than matched on
epoch-ms, so the logic is robust to the DST shift in daily/weekly `open_time` (midnight-ET expressed
in UTC moves 04:00↔05:00) and to the DST-naive 13:30-UTC 4h synthesis anchor. `data_sync.backfill`
calls the calendar-backed bridge and logs gaps **warn-only** (never quarantines — a missing session
is absent data, not a corrupt row).

**Tech Stack:** Python 3.13, `exchange_calendars` (new runtime dep — NYSE/`XNYS` calendar), pandas
(tz conversion via `tzdata`, already a dep), DuckDB, pytest + unittest.mock, Poetry, ruff, mypy
(strict).

---

## Scope & decisions (read before starting)

This is **PR 2 of the N3 track**. PR 1 (committed breadth universe + coverage report) already merged
(`fd990e1`). This PR is independently shippable and testable.

**24/7-assumption audit (done up front — informs scope):** A grep of the live/ingest code for
cadence/staleness assumptions found that the crypto-parent's 24/7 model has *already* been converted
everywhere it mattered:

- `analytics/signal/scanner.py:167` — forming-bar drop uses `now < open_time + tf_ms`; a daily bar
  over a weekend is still correctly treated as *closed*, no naive "expected next bar by now" staleness
  check that would misfire on weekends/holidays. **No change needed.**
- `analytics/data_fetcher.py:_resample_to_4h` — 4h bars are already anchored to the 13:30-UTC RTH
  session open (not the crypto 00:00-UTC anchor). **No change needed** (this PR consumes the anchor,
  does not alter it).
- `analytics/strategies/orb_breakout.py` — ORB anchor already shifted off the 00:00-UTC daily anchor.
  **No change needed.**

The **one** place that explicitly punted on a trading calendar is `analytics/data_quality.py`
(module docstring: *"Calendar-aware gap detection is intentionally out of scope … without a trading
calendar"*). That is the sole concrete target of this PR. The audit's other finding — that the
ingest/scan paths are already calendar-correct — is documented in the PR description, not changed.

**Out of scope (deferred, stated explicitly):**

- **Intraday 4h slot-level gaps** (e.g. "session D had only 1 of its 2 expected 4h bars"). 4h gap
  detection here is **day-level**: a trading day with *zero* 4h bars is a gap; a partial-day is not
  flagged. Slot-level intraday integrity (half-days like the day after Thanksgiving closing 13:00 ET,
  the DST-naive anchor) is fiddly and low-value; deferred.
- **RTH minute-bar / LULD-halt / auction modelling** — gap-map §2.1 market-structure facts, post-G2.
- **Quarantining** missing sessions — gaps are warn-only by design (absent data ≠ corrupt row).
- **Earnings / event calendar** — deferred post-G2 per the N3 roadmap.

**Key facts confirmed against the codebase:**

- OHLCV table `ohlcv`; `open_time` is BIGINT epoch ms, UTC. Daily/weekly bars are **midnight-ET**
  expressed in UTC (so `open_time` is 04:00 or 05:00 UTC depending on DST). 4h bars are DST-naive
  13:30-UTC-anchored RTH bins, ~2 per session day. **Both map cleanly to an America/New_York session
  date** — that is the normalisation key this PR uses.
- `analytics/data_quality.py` is pure (no DB/network/side-effects); `check_ohlcv` returns
  `DataQualityReport` (positional-index findings); `quarantine` splits into (clean, dropped).
  `data_sync.backfill` calls `check_ohlcv` → `quarantine` → `upsert_ohlcv`, all warn-only logging.
- `tests/test_data_sync.py` patches `analytics.data_sync.fetch_bars`; `tests/test_data_quality.py`
  builds frames with a `_frame([...])` helper. Follow both styles.
- mypy is strict (`disallow_untyped_defs`, `warn_return_any`, `warn_unused_ignores`); third-party libs
  without stubs get a `[[tool.mypy.overrides]] … ignore_missing_imports = true` block (see the
  existing `duckdb.*` / `uvicorn.*` overrides). `exchange_calendars` needs one.
- `exchange_calendars ^4.13.2` resolves cleanly against the current `pandas (>=3.0.3)` / Python 3.13
  pins (verified via `poetry add --dry-run`). It is the calendar engine that `pandas_market_calendars`
  itself wraps; depending on it directly is leaner.

## File structure

- **Modify** `pyproject.toml` — add `exchange-calendars` runtime dep + a mypy override block.
- **Create** `analytics/trading_calendar.py` — the *only* module importing `exchange_calendars`.
  Exposes `nyse_sessions(start, end)` and the `check_session_gaps(df, timeframe)` bridge.
- **Modify** `analytics/data_quality.py` — add the **pure** `SessionGapReport`, `detect_session_gaps`,
  and the `_et_date` / `_week_start` helpers; update the module docstring.
- **Modify** `analytics/data_sync.py` — call `check_session_gaps` in `backfill`, warn-only.
- **Create** `tests/test_trading_calendar.py` — calendar wrapper + bridge tests (real `XNYS`).
- **Modify** `tests/test_data_quality.py` — pure `detect_session_gaps` tests (hand-built sessions).
- **Modify** `tests/test_data_sync.py` — backfill gap-warning wiring test (stubbed bridge).
- **Modify** `CLAUDE.md` / `README.md` — document the new module, dep, and behaviour (Task 6).

---

## Task 1: Add the `exchange_calendars` dependency + mypy override

**Files:**

- Modify: `pyproject.toml`
- Modify: `poetry.lock` (via Poetry — never hand-edit)

- [ ] **Step 1: Add the runtime dependency via Poetry**

Run (do NOT hand-edit `poetry.lock`):

```bash
poetry add exchange_calendars
```

Expected: resolves to `^4.13.2`, installs `exchange-calendars`, `pyluach`, `korean-lunar-calendar`,
`toolz`. Poetry will also re-lock several already-constrained deps to their latest in-range versions
(idna, click, yarl, etc.) — that churn is within existing `pyproject` constraints and benign. After
it finishes, sanity-check the import:

```bash
poetry run python -c "import exchange_calendars as x; c=x.get_calendar('XNYS'); print(type(c).__name__)"
```

Expected: prints a calendar class name (e.g. `XNYSExchangeCalendar`) with no error.

- [ ] **Step 2: Add the mypy override**

`exchange_calendars` ships no type stubs. In `pyproject.toml`, add a new override block alongside the
existing `duckdb.*` / `uvicorn.*` blocks:

```toml
[[tool.mypy.overrides]]
module = "exchange_calendars.*"
ignore_missing_imports = true
```

- [ ] **Step 3: Verify the gate is still green**

Run:

```bash
poetry run mypy . 2>&1 | tail -5
poetry run pytest -q 2>&1 | tail -5
```

Expected: mypy clean; full suite unchanged (still ~1586 pass / 3 skip — no behaviour added yet).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "build(deps): add exchange_calendars for NYSE trading calendar (N3 PR2)"
```

---

## Task 2: `analytics/trading_calendar.py` — `nyse_sessions`

The single module that imports `exchange_calendars`. Returns NYSE session dates so the rest of the
codebase never touches the library directly.

**Files:**

- Create: `analytics/trading_calendar.py`
- Test: `tests/test_trading_calendar.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trading_calendar.py`:

```python
"""Tests for analytics/trading_calendar.py (real XNYS calendar)."""

from datetime import date

from analytics.trading_calendar import nyse_sessions


def test_excludes_weekends_and_holidays() -> None:
    sessions = set(nyse_sessions(date(2024, 1, 1), date(2024, 1, 31)))
    assert date(2024, 1, 1) not in sessions  # New Year's Day (holiday)
    assert date(2024, 1, 2) in sessions  # first trading day of 2024
    assert date(2024, 1, 6) not in sessions  # Saturday
    assert date(2024, 1, 7) not in sessions  # Sunday
    assert date(2024, 1, 15) not in sessions  # MLK Day (holiday)
    assert date(2024, 1, 16) in sessions  # trading day


def test_returns_sorted_ascending() -> None:
    sessions = nyse_sessions(date(2024, 7, 1), date(2024, 7, 12))
    assert sessions == sorted(sessions)
    assert date(2024, 7, 4) not in sessions  # Independence Day


def test_single_trading_day() -> None:
    assert nyse_sessions(date(2024, 7, 3), date(2024, 7, 3)) == [date(2024, 7, 3)]


def test_single_holiday_is_empty() -> None:
    assert nyse_sessions(date(2024, 7, 4), date(2024, 7, 4)) == []


def test_end_before_start_is_empty() -> None:
    assert nyse_sessions(date(2024, 7, 10), date(2024, 7, 1)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_trading_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: analytics.trading_calendar`.

- [ ] **Step 3: Write minimal implementation**

Create `analytics/trading_calendar.py`:

```python
"""NYSE trading-calendar wrapper (N3 PR2).

The ONLY module that imports ``exchange_calendars``. It exposes NYSE session
dates so the rest of the codebase can reason about trading sessions without a
direct dependency on the calendar library, and bridges the calendar to the pure
``analytics.data_quality.detect_session_gaps`` math.

No DB, no side effects beyond a process-lifetime cached calendar handle.
"""

from __future__ import annotations

import functools
from datetime import date
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from analytics.data_quality import SessionGapReport, detect_session_gaps

_CALENDAR_NAME = "XNYS"  # exchange_calendars' code for NYSE


@functools.lru_cache(maxsize=1)
def _calendar() -> Any:
    """Process-lifetime cached XNYS calendar handle (construction is non-trivial)."""
    return xcals.get_calendar(_CALENDAR_NAME)


def nyse_sessions(start: date, end: date) -> list[date]:
    """NYSE trading-session dates in ``[start, end]`` inclusive, sorted ascending.

    Excludes weekends and NYSE holidays. Returns ``[]`` when ``end < start``.
    """
    if end < start:
        return []
    sessions = _calendar().sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    result: list[date] = [ts.date() for ts in sessions]
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_trading_calendar.py -v`
Expected: PASS (5 tests).

> If `sessions_in_range` raises `AttributeError` (API drift across `exchange_calendars` majors), the
> v4 equivalent is `_calendar().sessions_in_range(...)`; alternatives are `.sessions` (a
> `DatetimeIndex` you can slice `[start:end]`) or `.schedule.loc[start:end].index`. Verify with
> `poetry run python -c "import exchange_calendars as x; print([m for m in dir(x.get_calendar('XNYS')) if 'session' in m])"`
> and use whichever returns a `DatetimeIndex` of session labels.

- [ ] **Step 5: Commit**

```bash
git add analytics/trading_calendar.py tests/test_trading_calendar.py
git commit -m "feat(calendar): nyse_sessions XNYS wrapper (N3 PR2)"
```

> Note: Task 2's `trading_calendar.py` is self-contained — it imports only `exchange_calendars`,
> `pandas`, and stdlib, and does **not** reference `SessionGapReport` / `detect_session_gaps` (those
> arrive with the Task 4 bridge). The full suite stays green after this commit.

---

## Task 3: Pure `detect_session_gaps` + `SessionGapReport` in `data_quality.py`

The calendar-free math core. Maps `open_time` epoch-ms to America/New_York session dates and diffs
the observed sessions against a caller-supplied expected-session list. Fully unit-testable with
hand-built session lists — **no `exchange_calendars` import here**.

**Files:**

- Modify: `analytics/data_quality.py`
- Test: `tests/test_data_quality.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_quality.py` (add the imports at the top:
`from datetime import date` and extend the existing `data_quality` import to include
`SessionGapReport, detect_session_gaps`):

```python
def _ot(d: date, hour_utc: int = 12) -> int:
    """Epoch ms for date ``d`` at ``hour_utc`` UTC.

    Noon UTC is the same America/New_York calendar date everywhere in CONUS
    year-round, so _et_date(_ot(d)) == d regardless of DST.
    """
    ts = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=hour_utc, tz="UTC")
    return int(ts.value // 1_000_000)


class TestDetectSessionGaps:
    def test_no_gap_daily(self) -> None:
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in days], "1d", days)
        assert isinstance(rep, SessionGapReport)
        assert rep.unit == "session"
        assert rep.has_gaps is False
        assert rep.missing == ()
        assert rep.n_present == 3
        assert rep.n_expected == 3

    def test_missing_daily_session(self) -> None:
        present = [date(2024, 1, 2), date(2024, 1, 4)]
        sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert rep.has_gaps is True
        assert rep.missing == (date(2024, 1, 3),)
        assert rep.n_missing == 1

    def test_4h_missing_whole_day(self) -> None:
        # two 4h bars on D0 and D2, none on D1 -> D1 is a missing session
        d0, d1, d2 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
        ots = [_ot(d0, 14), _ot(d0, 18), _ot(d2, 14), _ot(d2, 18)]
        rep = detect_session_gaps(ots, "4h", [d0, d1, d2])
        assert rep.unit == "session"
        assert rep.missing == (d1,)

    def test_weekly_missing_week(self) -> None:
        # weekly bars anchored on Mondays of W0 and W2; a session in W1 -> W1 missing
        w0 = date(2024, 1, 1)  # Monday
        w1_session = date(2024, 1, 9)  # Tue in week of Jan 8
        w2 = date(2024, 1, 15)  # Monday
        sessions = [w0, w1_session, w2]
        rep = detect_session_gaps([_ot(w0), _ot(w2)], "1wk", sessions)
        assert rep.unit == "week"
        assert rep.missing == (date(2024, 1, 8),)  # Monday of the skipped week

    def test_empty_is_no_gap(self) -> None:
        rep = detect_session_gaps([], "1d", [])
        assert rep.n_present == 0
        assert rep.has_gaps is False
        assert rep.missing == ()

    def test_single_bar_no_gap(self) -> None:
        d = date(2024, 1, 2)
        rep = detect_session_gaps([_ot(d)], "1d", [d])
        assert rep.has_gaps is False

    def test_expected_clamped_to_observed_range(self) -> None:
        # sessions list extends past the observed bars; only interior gaps count
        present = [date(2024, 1, 3), date(2024, 1, 4)]
        sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert rep.has_gaps is False  # Jan 2 and Jan 5 are outside [min,max] present

    def test_summary_mentions_counts(self) -> None:
        present = [date(2024, 1, 2), date(2024, 1, 4)]
        sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        rep = detect_session_gaps([_ot(d) for d in present], "1d", sessions)
        assert "1" in rep.summary()
        assert "gap" in rep.summary().lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_data_quality.py::TestDetectSessionGaps -v`
Expected: FAIL — `ImportError` (`SessionGapReport` / `detect_session_gaps` not defined).

- [ ] **Step 3: Write minimal implementation**

In `analytics/data_quality.py`, add `from collections.abc import Sequence` and
`from datetime import date, timedelta` to the imports. Append after the `quarantine` function:

```python
def _et_date(open_time_ms: int) -> date:
    """America/New_York session date for a UTC epoch-ms ``open_time``.

    Daily/weekly bars are midnight-ET-in-UTC (04:00/05:00 UTC by DST); 4h bars
    are 13:30-UTC RTH bins. All map to the ET calendar date of the session.
    """
    return (
        pd.Timestamp(open_time_ms, unit="ms", tz="UTC")
        .tz_convert("America/New_York")
        .date()
    )


def _week_start(d: date) -> date:
    """Monday of ``d``'s ISO week (weekly-bar anchor key)."""
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class SessionGapReport:
    """Missing trading sessions for one (symbol, timeframe) OHLCV series.

    ``unit`` is ``"week"`` for ``1wk`` (gaps reported as the Monday of a skipped
    trading week) and ``"session"`` otherwise (gaps reported as the missing
    trading-day dates). Warn-only — a gap is absent data, never quarantined.
    """

    timeframe: str
    unit: str
    n_present: int
    n_expected: int
    missing: tuple[date, ...]

    @property
    def n_missing(self) -> int:
        return len(self.missing)

    @property
    def has_gaps(self) -> bool:
        return bool(self.missing)

    def summary(self) -> str:
        if not self.missing:
            return f"no session gaps ({self.n_present} {self.unit}s present)"
        sample = ", ".join(d.isoformat() for d in self.missing[:5])
        more = "" if self.n_missing <= 5 else f", +{self.n_missing - 5} more"
        return (
            f"{self.n_missing} {self.unit} gap(s) "
            f"({self.n_present}/{self.n_expected} present): {sample}{more}"
        )


def detect_session_gaps(
    open_times: Sequence[int],
    timeframe: str,
    sessions: Sequence[date],
) -> SessionGapReport:
    """Find expected trading sessions absent from ``open_times`` (pure).

    ``sessions`` is the NYSE trading-date list spanning the observed range
    (supplied by the caller via ``analytics.trading_calendar.nyse_sessions`` —
    this function never touches a calendar library). For ``1wk`` the unit is the
    trading *week* (Monday anchor); otherwise the trading *day*. Expected
    sessions are clamped to the observed ``[min, max]`` bar range so only interior
    gaps are flagged.
    """
    is_weekly = timeframe == "1wk"
    unit = "week" if is_weekly else "session"

    if not open_times:
        return SessionGapReport(timeframe, unit, 0, 0, ())

    present_dates = sorted({_et_date(t) for t in open_times})
    lo, hi = present_dates[0], present_dates[-1]
    in_range = [s for s in sessions if lo <= s <= hi]

    if is_weekly:
        present_keys = {_week_start(d) for d in present_dates}
        expected_keys = sorted({_week_start(s) for s in in_range})
    else:
        present_keys = set(present_dates)
        expected_keys = sorted(set(in_range))

    missing = tuple(k for k in expected_keys if k not in present_keys)
    return SessionGapReport(
        timeframe=timeframe,
        unit=unit,
        n_present=len(present_keys),
        n_expected=len(expected_keys),
        missing=missing,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_data_quality.py::TestDetectSessionGaps -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/data_quality.py tests/test_data_quality.py
git commit -m "feat(data-quality): pure detect_session_gaps + SessionGapReport (N3 PR2)"
```

---

## Task 4: `check_session_gaps` bridge + wire into `data_sync.backfill`

**Files:**

- Modify: `analytics/trading_calendar.py`
- Modify: `analytics/data_sync.py`
- Test: `tests/test_trading_calendar.py`, `tests/test_data_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trading_calendar.py` (real-calendar integration for the bridge):

```python
import pandas as pd

from analytics.trading_calendar import check_session_gaps


def _ohlcv(open_times: list[int], timeframe: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "timeframe": timeframe,
                "open_time": t,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
            }
            for t in open_times
        ]
    )


def _ot(d: date, hour_utc: int = 12) -> int:
    ts = pd.Timestamp(year=d.year, month=d.month, day=d.day, hour=hour_utc, tz="UTC")
    return int(ts.value // 1_000_000)


def test_check_session_gaps_flags_missing_trading_day() -> None:
    # Jan 2, 3, 4 2024 are all NYSE trading days; drop Jan 3 -> one gap
    df = _ohlcv([_ot(date(2024, 1, 2)), _ot(date(2024, 1, 4))], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is True
    assert date(2024, 1, 3) in rep.missing


def test_check_session_gaps_clean_contiguous_range() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    df = _ohlcv([_ot(d) for d in days], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is False


def test_check_session_gaps_ignores_weekend_absence() -> None:
    # Fri Jan 5 -> Mon Jan 8: the weekend is NOT a gap (no sessions Sat/Sun)
    df = _ohlcv([_ot(date(2024, 1, 5)), _ot(date(2024, 1, 8))], "1d")
    rep = check_session_gaps(df, "1d")
    assert rep.has_gaps is False


def test_check_session_gaps_empty_frame() -> None:
    rep = check_session_gaps(_ohlcv([], "1d"), "1d")
    assert rep.n_present == 0
    assert rep.has_gaps is False
```

Add to `tests/test_data_sync.py` (wiring — stub the bridge to isolate from the real calendar):

```python
from datetime import date

from analytics.data_quality import SessionGapReport


class TestBackfillSessionGapWarning:
    def test_backfill_warns_on_session_gap(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        gappy = SessionGapReport("1d", "session", 2, 3, (date(2024, 1, 3),))
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=gappy),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert any("session gap" in r.message.lower() for r in caplog.records)

    def test_backfill_silent_when_no_gaps(self, caplog: Any) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000], timeframe="1d")
        clean = SessionGapReport("1d", "session", 3, 3, ())
        with (
            patch("analytics.data_sync.fetch_bars", return_value=df),
            patch("analytics.data_sync.check_session_gaps", return_value=clean),
            caplog.at_level("WARNING"),
        ):
            backfill(conn, "AAPL", "1d", 0)
        assert not any("session gap" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_trading_calendar.py tests/test_data_sync.py -v`
Expected: FAIL — `check_session_gaps` not defined / not imported in `data_sync`.

- [ ] **Step 3: Write minimal implementation**

Append `check_session_gaps` to `analytics/trading_calendar.py`:

```python
def check_session_gaps(df: pd.DataFrame, timeframe: str) -> SessionGapReport:
    """Calendar-backed bridge: detect missing NYSE sessions in an OHLCV frame.

    Computes the observed ET session-date range, asks the NYSE calendar for the
    trading sessions spanning it, and diffs via the pure
    ``data_quality.detect_session_gaps``. Empty frame -> empty (no-gap) report.
    """
    if df.empty:
        return detect_session_gaps([], timeframe, [])
    open_times = [int(t) for t in df["open_time"].tolist()]
    lo = min(_session_date(t) for t in open_times)
    hi = max(_session_date(t) for t in open_times)
    sessions = nyse_sessions(lo, hi)
    return detect_session_gaps(open_times, timeframe, sessions)
```

To avoid importing the private `_et_date`, expose a public alias in `data_quality.py` and import it,
**or** reuse the existing private. Cleanest: import the helper explicitly. Add to the
`trading_calendar.py` import block:

```python
from analytics.data_quality import (
    SessionGapReport,
    detect_session_gaps,
)
from analytics.data_quality import _et_date as _session_date
```

(`_et_date` is an internal helper; aliasing it locally keeps `trading_calendar` from recomputing the
tz conversion and keeps one source of truth for the ET-date mapping.)

Wire into `analytics/data_sync.py`. Update the import block:

```python
from analytics.trading_calendar import check_session_gaps
```

In `backfill`, after the `quarantine` block and before/after `upsert_ohlcv` (place it right before the
final `upsert_ohlcv(conn, clean)` so the warn references the data being stored), add:

```python
    if len(clean) >= 2:
        gap_report = check_session_gaps(clean, timeframe)
        if gap_report.has_gaps:
            logging.warning(
                "session gap %s %s: %s", symbol, timeframe, gap_report.summary()
            )
```

The full `backfill` body after edits (for reference — do not duplicate the function):

```python
    report = check_ohlcv(df)
    if not report.is_clean:
        logging.warning("data-quality %s %s: %s", symbol, timeframe, report.summary())
    clean, dropped = quarantine(df, report)
    if clean.empty:
        logging.warning(
            "data-quality %s %s: all %d rows quarantined", symbol, timeframe, len(df)
        )
        return 0

    if len(clean) >= 2:
        gap_report = check_session_gaps(clean, timeframe)
        if gap_report.has_gaps:
            logging.warning(
                "session gap %s %s: %s", symbol, timeframe, gap_report.summary()
            )

    upsert_ohlcv(conn, clean)
    logging.info(
        "backfill %s %s: stored %d rows (%d quarantined)",
        symbol,
        timeframe,
        len(clean),
        len(dropped),
    )
    return len(clean)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_trading_calendar.py tests/test_data_sync.py -v`
Expected: PASS (all new tests; the existing `test_data_sync_has_no_funding_or_oi_imports` still
passes — `check_session_gaps` contains no funding/OI strings).

- [ ] **Step 5: Commit**

```bash
git add analytics/trading_calendar.py analytics/data_sync.py tests/test_trading_calendar.py tests/test_data_sync.py
git commit -m "feat(data-quality): calendar-aware session-gap warning in backfill (N3 PR2)"
```

---

## Task 5: Update the `data_quality.py` module docstring

The module header still says calendar-aware gap detection is out of scope. Update it to reflect that
the pure detector now exists (fed by `trading_calendar`), while `check_ohlcv` itself stays
calendar-free.

**Files:**

- Modify: `analytics/data_quality.py`

- [ ] **Step 1: Replace the out-of-scope paragraph**

In `analytics/data_quality.py`, replace the module-docstring paragraph that currently reads:

```text
Calendar-aware gap detection is intentionally out of scope: equity
weekends/holidays make naive cadence checks fire constantly without a trading
calendar. We flag only unambiguous timestamp anomalies (duplicates,
non-monotonic order — Task 2).
```

with:

```text
``check_ohlcv`` stays calendar-free (no library dependency, pure): it flags only
unambiguous timestamp anomalies (duplicates, non-monotonic order). Calendar-aware
*session-gap* detection (N3 PR2) lives in the pure ``detect_session_gaps`` /
``SessionGapReport`` below, fed an NYSE trading-date list by
``analytics.trading_calendar`` (the only module importing ``exchange_calendars``).
Missing sessions are warn-only — absent data, never quarantined.
```

- [ ] **Step 2: Verify nothing broke**

Run: `poetry run pytest tests/test_data_quality.py -q`
Expected: PASS (docstring-only change).

- [ ] **Step 3: Commit**

```bash
git add analytics/data_quality.py
git commit -m "docs(data-quality): note calendar-aware session-gap detection (N3 PR2)"
```

---

## Task 6: Full gate + docs

**Files:**

- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Run the full quality gate**

Run, in order:

```bash
make lint-py
make typecheck
make test
make lint-md
```

Expected: all green. Suite count rises by ~19 (Tasks 2–4). Regression goldens are **not** touched
(no detector / backtest / cost change — `detect_session_gaps` is ingest-side only and warn-only), so
`make test-regression` stays green untouched.

- [ ] **Step 2: Operational re-verify (optional but recommended)**

The session-gap warning now fires during backfill. Re-run a small universe backfill and eyeball the
logs for any *expected-but-surprising* gaps (a true gap is a yfinance hole; weekends/holidays must NOT
warn):

```bash
poetry run python wifey.py analytics backfill --universe --timeframes 1d --since 2024-01-01 2>&1 | grep -i "session gap" | head
```

Expected: few or no `session gap` lines on liquid mega-caps. Note any flagged symbols in the PR
description (do not silence the warning).

- [ ] **Step 3: Update `CLAUDE.md`**

In the `analytics/` module list, **add** a bullet for the new module (near `data_quality.py`):

```markdown
  - `trading_calendar.py` — NYSE (`XNYS`) trading-calendar wrapper (N3 PR2); the only module importing
    `exchange_calendars`. `nyse_sessions(start, end)` returns NYSE session dates; `check_session_gaps(df,
    timeframe)` bridges the calendar to the pure `data_quality.detect_session_gaps`. Wired into
    `data_sync.backfill` as a warn-only missing-session check.
```

**Update** the `data_quality.py` bullet — change its closing parenthetical from
"Calendar-aware gap detection intentionally out of scope (no trading-calendar dependency)." to:

```markdown
Calendar-aware *session-gap* detection added in N3 PR2: the pure `detect_session_gaps` /
`SessionGapReport` (maps `open_time` → America/New_York session date, diffs against an NYSE
trading-date list; `1wk` → trading-week unit, else trading-day) is fed by `analytics/trading_calendar.py`
and logged warn-only from `data_sync.backfill`. `check_ohlcv` itself stays calendar-free.
```

In the **Dependencies** section, add `exchange-calendars` to the runtime list:

```markdown
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O),
  `exchange-calendars` (NYSE trading calendar — N3 PR2)
```

- [ ] **Step 4: Update `README.md`**

Wherever data quality / backfill is described, add a one-line note that ingest now flags missing NYSE
trading sessions (weekends/holidays excluded) via the `exchange_calendars`-backed trading calendar.
If the README lists dependencies, add `exchange-calendars`.

- [ ] **Step 5: Final markdown lint + commit**

```bash
make lint-md
git add CLAUDE.md README.md
git commit -m "docs: document trading_calendar + session-gap detection (N3 PR2)"
```

---

## Self-review checklist (run before opening the PR)

- [ ] **Spec coverage** — N3 PR2 acceptance: (a) US-equity trading calendar added
  (`trading_calendar.py` + `exchange_calendars`, Task 1–2 ✓); (b) calendar-aware gap detection in the
  data-quality path (`detect_session_gaps` pure core + `check_session_gaps` bridge + `backfill`
  wiring, Tasks 3–4 ✓); (c) 24/7-assumption audit done — only `data_quality.py` punted; scanner / 4h
  synthesis / ORB already calendar-correct (documented in Scope ✓). 4h slot-level gaps + earnings
  explicitly deferred.
- [ ] **No placeholders** — every code block above is complete and runnable.
- [ ] **Type consistency** — `nyse_sessions(start, end) -> list[date]`;
  `detect_session_gaps(open_times, timeframe, sessions) -> SessionGapReport`;
  `SessionGapReport(timeframe, unit, n_present, n_expected, missing)` with `.n_missing` / `.has_gaps`
  / `.summary()`; `check_session_gaps(df, timeframe) -> SessionGapReport`; `_et_date(open_time_ms) ->
  date` (aliased as `_session_date` in `trading_calendar`); `_week_start(d) -> date`. Names match
  across tasks.
- [ ] **Default behaviour unchanged** — `check_ohlcv` / `quarantine` untouched; gap check is
  additive, warn-only, never quarantines; goldens unmoved. The live scanner and recalibration paths
  do not import `trading_calendar`.
- [ ] **Dependency isolated** — `import exchange_calendars` appears in exactly one module
  (`analytics/trading_calendar.py`). Confirm:
  `grep -rln "import exchange_calendars" --include="*.py" . | grep -v tests` → one path only.

---

## Roadmap — subsequent N3 work (NOT in this plan)

- **N3 PR3 — Universe expansion to ~S&P 100.** Add rows to `config/universe.json` + re-run
  `make wifey-universe-backfill SINCE=2018-01-01`. Pure config + data.
- **Deferred (post-G2)** — intraday 4h slot-level gap detection (half-days, DST-naive anchor);
  earnings/event calendar; RTH/auction/LULD market-structure modelling.
