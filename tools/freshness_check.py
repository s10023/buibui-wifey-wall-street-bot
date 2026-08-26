"""Observed-state freshness probe for the two scheduled surfaces that alert only on failure.

`wifey-signal-watch`, `wifey-backup` and `wifey-backup-offsite` all alert through
`OnFailure=wifey-alert@%N.service`. That channel cannot distinguish a timer with
nothing to report from a timer that **stopped firing**, so the only honest check
is to read observed state and date it. `make backup-check` does that for the
backup tier; this does it for the two the backup probe cannot see:

- **signal** — has a scan actually run, and when did one last DISPATCH? These are
  two questions and only the first is gradeable. ⚠ **The watermark is a DISPATCH
  oracle, not a RUN oracle** — it advances when a candle is consumed by an alert,
  and dispatch is intermittent BY DESIGN: `day_filter = tue_thu` suppresses on
  the bar's open weekday, so a Mon run (Fri bars) and a Tue run (Mon bars) can
  never alert. Measured 2026-08-26, the newest watermark sat 4 sessions back
  with run evidence on TWO OF THE THREE intervening sessions (`fired_at_ms`
  rows on 08-24 and 08-25; none on 08-21, which is not evidence of no run —
  a scan that detects nothing new writes nothing).
  Grading it against the timer's daily cadence therefore prints STALE on a
  healthy system, which is why it is reported **dated but ungraded**.
  Run-liveness is answered instead by the ohlcv leg: a `go-live` run's first act
  is a watchlist sync, so fresh watchlist bars ARE the evidence a run happened,
  and that quantity does have a declared cadence.
  ⚠ Skipping a run day DESTROYS that day's alerts rather than deferring them
  (the next catch-up consumes the watermark without dispatching), so a late
  detection is not recoverable by running twice tomorrow.
- **ohlcv** — is what a study would read fresh? Ported from parent #681/#698
  (`tools/ohlcv_freshness.py`), whose motivating measurement was 22 of 25
  universe symbols frozen for eleven weeks with nothing broken and nothing
  watching. Wifey measured the same shape on 2026-08-26: the 13-symbol watchlist
  ran to the previous session while the bulk of the 505-member research universe
  had not moved since 2026-06-18.

Three properties decide whether a probe of this kind is worth anything, and all
three are why this is a module rather than a line of SQL in a caller.

- **Age is only meaningful in SESSIONS, never in wall-clock.** This is wifey's
  one hard divergence from the parent, which computes `(now - newest) / bar_ms`
  against a 24h tape. On an RTH tape that is wrong in the direction that reds
  everything forever: `4h` is 2 bars/day rather than 6, so a healthy series two
  sessions old reads as ~12 bars behind, and every Monday adds a phantom
  weekend. Age here is NYSE sessions elapsed (`analytics.trading_calendar`)
  multiplied by `cost_model.BARS_PER_DAY` — the single shared table, imported
  rather than forked.
- **The newest stored bar is normally IN PROGRESS**, and a pre-open scan reads
  the PREVIOUS session's bars by design. A healthy series therefore always
  trails, which is what `BASE_TOLERANCE_SESSIONS` absorbs; a probe that reads
  "not yet closed" as stale is mute within a week.
- **Staleness is meaningless without a declared cadence.** Nothing schedules the
  505-member universe, so "10 weeks behind" is not a fault there — it is the
  absence of a refresher, which is a different finding and is reported as one.
  Grading it against a cadence it does not have would print ~490 findings every
  run, and a leg that is never green stops being read.

⚠ **ADVISORY, and it must never enter `make test`, `make sanity-checks` or CI.**
Both legs read machine-local single-copy state (`analytics.db`, the gitignored
`signal_state.json` and `config/stocks.json`) that no clone has, so CI would
report a permanent fault. The pure functions below are unit-tested; only the
reading is machine-local. `--exit-nonzero` opts in for a human who wants a shell
condition.

Usage::

    make freshness-check
    poetry run python tools/freshness_check.py --exit-nonzero   # shell condition
    poetry run python tools/freshness_check.py --leg signal     # one leg only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from analytics.backtest.cost_model import BARS_PER_DAY

# --- Declared cadence -------------------------------------------------------
#
# `wifey-signal-watch.timer` is `OnCalendar=Mon..Fri *-*-* 08:30:00 UTC`, one
# fire per trading day, and it runs `make go-live CATCH_UP=1`. That covers the
# `config/stocks.json` watchlist on the timeframes the live scan reads. Nothing
# schedules anything else: the 505-member universe refreshes only through a hand
# -run `make wifey-universe-backfill`, and the pundit ledger's underlyings only
# through `make wifey-pundit-sync`. Both absences are deliberate, so they are
# reported as "unscheduled" rather than graded.
SCHEDULED_TIMEFRAMES: frozenset[str] = frozenset({"4h", "1d"})

# Sessions between two scheduled refreshes. One fire per trading day, so a
# correctly-running series is refreshed every session.
SCHEDULED_GAP_SESSIONS: float = 1.0

# The floor every graded series keeps, in SESSIONS. Two sessions absorbs the
# in-progress bar plus the pre-open lag: the 08:30 UTC scan runs before the bell,
# so the newest CLOSED daily bar it can see belongs to the previous session. A
# tighter floor reds every healthy series every morning.
BASE_TOLERANCE_SESSIONS: float = 2.0

# ⚠ There is deliberately NO signal-side tolerance constant. The watermark dates
# the last DISPATCH, whose cadence is a consequence of `day_filter` rather than a
# schedule, so no constant here would have an external referent — and a
# hand-picked one would red a healthy system. See `evaluate_signal`.

DEFAULT_DB = Path("analytics.db")
DEFAULT_STATE = Path("signal_state.json")
DEFAULT_STOCKS = Path("config/stocks.json")

# Scans `ohlcv`, the view consumers actually read. A fresh tail written somewhere
# the view does not surface is not freshness — it is a series that still looks
# frozen to everything downstream.
COVERAGE_SQL = """
SELECT symbol, timeframe, max(open_time)
FROM ohlcv
GROUP BY 1, 2
"""

SessionsFn = Callable[[date, date], Sequence[date]]


# --- Pure core --------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """The newest bar held for one ``(symbol, timeframe)``."""

    symbol: str
    timeframe: str
    newest_open_time: int


@dataclass(frozen=True)
class Graded:
    """A graded series.

    ``age_bars`` is None when the timeframe has no known bars-per-day — reported
    rather than dropped, so "we do not know how to check this" can never render
    the same as "this is fresh".
    """

    symbol: str
    timeframe: str
    newest_session: date
    age_sessions: int
    age_bars: float | None

    @property
    def measurable(self) -> bool:
        return self.age_bars is not None


@dataclass(frozen=True)
class OhlcvReport:
    """Both tiers of the ohlcv leg, kept apart because they answer different questions."""

    stale: list[Graded]
    scheduled_total: int
    unscheduled_total: int
    unscheduled_oldest: date | None
    unscheduled_newest: date | None

    @property
    def ok(self) -> bool:
        """True when every SCHEDULED series is within tolerance.

        The unscheduled tier deliberately cannot make this false: nothing
        refreshes it, so it is an absence to report, not a fault to fail on.
        """
        return not self.stale


@dataclass(frozen=True)
class SignalReport:
    """The newest dispatch watermark, dated and UNGRADED.

    ``newest_session`` is None when no watermark was read, which is the one
    finding this leg can make: a scan has never run here, or the state file is
    unreadable. Everything else it reports is context for the ohlcv leg's
    verdict, not a verdict of its own — see the module docstring.
    """

    newest_session: date | None
    age_sessions: int | None
    wife_newest_session: date | None
    watermark_count: int
    wife_count: int
    source: Path

    @property
    def ok(self) -> bool:
        """False only when nothing could be read.

        ⚠ Deliberately NOT a staleness verdict. An old watermark on a healthy
        system is the normal state between dispatch days, so grading it here
        would make `--exit-nonzero` fire on most weekdays.
        """
        return self.newest_session is not None


def session_date(open_time_ms: int) -> date:
    """America/New_York session date for a UTC epoch-ms ``open_time``.

    Delegates to the one definition in ``analytics.data_quality`` rather than
    re-deriving it: daily and weekly bars stamp midnight-ET-in-UTC (04:00/05:00
    by DST) and ``4h`` bars are 13:30/17:30 UTC RTH bins, so a naive UTC floor
    happens to agree today and would stop agreeing the moment either changes.
    """
    from analytics.data_quality import _et_date

    return _et_date(open_time_ms)


def sessions_elapsed(newest: date, now: date, sessions_fn: SessionsFn) -> int:
    """NYSE sessions between ``newest`` and ``now``, exclusive of ``newest``'s own.

    Zero when ``newest`` is the latest session, and never negative — a bar
    stamped in the future is a different defect and must not read as freshness
    with room to spare.
    """
    if now <= newest:
        return 0
    return max(0, len(sessions_fn(newest, now)) - 1)


def tolerance_sessions_for(timeframe: str) -> float | None:
    """Sessions a correctly-refreshed ``timeframe`` may trail before it is stale.

    ``base + gap``: the base absorbs the in-progress bar and the pre-open lag,
    the second term one whole refresh cycle. Returns None for a timeframe nothing
    schedules, so a caller reports "no declared cadence" rather than silently
    applying a tight default to a series it does not know the cadence of.
    """
    if timeframe not in SCHEDULED_TIMEFRAMES:
        return None
    return BASE_TOLERANCE_SESSIONS + SCHEDULED_GAP_SESSIONS


def grade(series: Series, *, now: date, sessions_fn: SessionsFn) -> Graded:
    """Date one series and convert its age to bars via the shared bars-per-day table."""
    newest = session_date(series.newest_open_time)
    elapsed = sessions_elapsed(newest, now, sessions_fn)
    per_day = BARS_PER_DAY.get(series.timeframe)
    age_bars = None if per_day is None else elapsed * per_day
    return Graded(series.symbol, series.timeframe, newest, elapsed, age_bars)


def evaluate_ohlcv(
    rows: Iterable[Series],
    *,
    now: date,
    scheduled_symbols: frozenset[str],
    sessions_fn: SessionsFn,
) -> OhlcvReport:
    """Split rows into the graded scheduled tier and the summarised unscheduled one.

    A series is scheduled only when BOTH its symbol is on the watchlist the timer
    syncs and its timeframe is one the live scan reads. `1wk` is unscheduled for
    every symbol, watchlist included — the scan does not read it, so a watchlist
    name's weekly bars go stale exactly like the universe's.

    An empty ``scheduled_symbols`` (an unreadable or absent watchlist) puts every
    series in the unscheduled tier. That degrades toward "nothing is graded",
    which reports an absence, rather than toward "everything is graded against a
    cadence it does not have", which would report ~500 phantom faults.
    """
    stale: list[Graded] = []
    scheduled_total = 0
    unscheduled: list[date] = []

    for series in rows:
        is_scheduled = (
            series.symbol in scheduled_symbols
            and series.timeframe in SCHEDULED_TIMEFRAMES
        )
        graded = grade(series, now=now, sessions_fn=sessions_fn)
        if not is_scheduled:
            unscheduled.append(graded.newest_session)
            continue
        scheduled_total += 1
        limit = tolerance_sessions_for(series.timeframe)
        if limit is None or not graded.measurable:
            # Scheduled but unmeasurable: report it. An unknown bars-per-day is
            # the more urgent finding, since nothing can say how far behind it is.
            stale.append(graded)
            continue
        if graded.age_sessions > limit:
            stale.append(graded)

    # Unmeasurable first, then furthest behind: an unmeasurable series is the
    # more urgent finding.
    stale.sort(key=lambda g: (g.measurable, -g.age_sessions))
    return OhlcvReport(
        stale=stale,
        scheduled_total=scheduled_total,
        unscheduled_total=len(unscheduled),
        unscheduled_oldest=min(unscheduled) if unscheduled else None,
        unscheduled_newest=max(unscheduled) if unscheduled else None,
    )


def evaluate_signal(
    watermarks: dict[str, int],
    *,
    now: date,
    sessions_fn: SessionsFn,
    source: Path = DEFAULT_STATE,
) -> SignalReport:
    """Date the newest watermark.

    Reads the watermark's VALUE — a candle ``open_time`` — never the state file's
    mtime, which moves for a restore or an editor and fails in the direction that
    reports fresher than reality.

    The two families are dated separately because they mean different things:
    backfill marks the primary alone, while a real send marks both, so the
    ``:wife`` mark is the only oracle for an alert having actually gone out. A
    primary mark ahead of the ``:wife`` mark is the normal resting state, not a
    fault.
    """
    if not watermarks:
        return SignalReport(None, None, None, 0, 0, source)
    wife_marks = [v for k, v in watermarks.items() if k.endswith(":wife")]
    newest = session_date(max(watermarks.values()))
    return SignalReport(
        newest_session=newest,
        age_sessions=sessions_elapsed(newest, now, sessions_fn),
        wife_newest_session=session_date(max(wife_marks)) if wife_marks else None,
        watermark_count=len(watermarks),
        wife_count=len(wife_marks),
        source=source,
    )


# --- Machine-local reading --------------------------------------------------


def read_watermarks(path: Path) -> dict[str, int]:
    """Watermarks from ``signal_state.json``; ``{}`` when absent or unreadable.

    Every unreadable state degrades to empty, which `evaluate_signal` reports as
    a finding rather than as freshness.
    """
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    marks = raw.get("watermarks")
    if not isinstance(marks, dict):
        return {}
    return {str(k): int(v) for k, v in marks.items() if isinstance(v, (int, float))}


def read_scheduled_symbols(path: Path = DEFAULT_STOCKS) -> frozenset[str]:
    """The watchlist the daily timer syncs; empty when absent or unreadable.

    Reads the file directly rather than through ``load_stocks_config``, which
    raises on a missing file: this is a probe, and it must report an absent
    watchlist rather than crash on one.
    """
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return frozenset()
    if isinstance(raw, list):
        return frozenset(str(s) for s in raw)
    if isinstance(raw, dict):
        # `universe_policy` is a config block, not a symbol.
        return frozenset(str(k) for k in raw if k != "universe_policy")
    return frozenset()


def read_series(db_path: Path) -> list[Series] | None:
    """Newest bar per ``(symbol, timeframe)``; None when the DB cannot be read."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover - duckdb is a runtime dep
        return None
    if not db_path.exists():
        return None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        # A writer holds the lock, or the file is not a database. Either way this
        # is a probe: report that it could not read, never a fault in the data.
        return None
    try:
        rows = conn.execute(COVERAGE_SQL).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    return [Series(str(sym), str(tf), int(newest)) for sym, tf, newest in rows]


def latest_session(now: datetime, sessions_fn: SessionsFn) -> date:
    """The most recent NYSE session on or before ``now``.

    Looks back 10 calendar days, which clears any holiday run. Falls back to the
    plain date when the calendar returns nothing, so an out-of-range clock
    degrades to a slightly tighter grade rather than crashing the probe.
    """
    today = now.date()
    window = list(sessions_fn(today - timedelta(days=10), today))
    return window[-1] if window else today


# --- Rendering --------------------------------------------------------------


def render_signal(report: SignalReport) -> str:
    lines = ["  signal — last DISPATCH, from the watermarks it advanced (ungraded)"]
    if report.newest_session is None:
        lines.append(f"      ! NO WATERMARKS read from {report.source}")
        lines.append(
            "        A scan has never run here, or the state file is unreadable."
        )
        return "\n".join(lines)
    lines.append(
        f"      · primary  session {report.newest_session}"
        f" ({report.age_sessions} session(s) back), {report.watermark_count} marks"
    )
    wife = report.wife_newest_session
    lines.append(
        f"      · :wife    session {wife if wife else '—'}"
        f", {report.wife_count} marks — the only oracle for an alert going OUT"
    )
    lines.append(
        "        ⚠ NOT graded: dispatch cadence follows `day_filter`, not the"
        " timer, so a Mon"
    )
    lines.append(
        "        or Tue run can never alert and an old mark is the normal resting"
        " state."
    )
    lines.append(
        "        Run-liveness is the ohlcv leg's scheduled tier below — a run"
        " syncs the watchlist."
    )
    return "\n".join(lines)


def render_ohlcv(report: OhlcvReport) -> str:
    lines = ["  ohlcv — freshness of what a study would actually read"]
    if report.scheduled_total == 0 and report.unscheduled_total == 0:
        lines.append("      ! NO SERIES read — the DB is absent, locked or empty.")
        return "\n".join(lines)

    if report.scheduled_total == 0:
        lines.append(
            "      ! NO SCHEDULED SERIES — the watchlist is absent or unreadable,"
        )
        lines.append(
            "        so nothing could be graded. Every series fell to the"
            " unscheduled tier below."
        )
    elif report.ok:
        lines.append(
            f"      + FRESH   all {report.scheduled_total} scheduled series within"
            f" {BASE_TOLERANCE_SESSIONS + SCHEDULED_GAP_SESSIONS:g} sessions"
        )
    else:
        lines.append(
            f"      ! STALE   {len(report.stale)} of {report.scheduled_total}"
            " scheduled series behind their refresh cadence"
        )
        for graded in report.stale[:10]:
            age = (
                "unmeasurable bars"
                if graded.age_bars is None
                else f"{graded.age_bars:g} bars"
            )
            lines.append(
                f"          {graded.symbol:<10} {graded.timeframe:<4}"
                f" last session {graded.newest_session}"
                f" ({graded.age_sessions} sessions, {age})"
            )
        if len(report.stale) > 10:
            lines.append(f"          … and {len(report.stale) - 10} more")

    if report.unscheduled_total:
        lines.append(
            f"      · {report.unscheduled_total} series have NO SCHEDULED REFRESHER"
            f" — last session {report.unscheduled_oldest} … {report.unscheduled_newest}"
        )
        lines.append(
            "        Not a fault: nothing refreshes the 505-member research"
            " universe or any `1wk`"
        )
        lines.append(
            "        series. ⚠ It IS a hazard for a pooled cross-section, which"
            " would mix these"
        )
        lines.append(
            "        with the fresh watchlist inside one query. Refresh with"
            " `make wifey-universe-backfill`"
        )
        lines.append("        / `make wifey-pundit-sync` before any breadth study.")
    return "\n".join(lines)


def render(signal: SignalReport | None, ohlcv: OhlcvReport | None) -> str:
    out = ["freshness_check — did the scheduled work actually run?\n"]
    if signal is not None:
        out.append(render_signal(signal))
        out.append("")
    if ohlcv is not None:
        out.append(render_ohlcv(ohlcv))
        out.append("")
    out.append(
        "  ADVISORY — never gates CI, because both legs read machine-local"
        " single-copy state\n"
        "  no clone has. `OnFailure=` alerting cannot tell a quiet timer from a"
        " stopped one;\n"
        "  this dates observed state instead. Age is in NYSE SESSIONS, never"
        " wall-clock."
    )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Date the observed state of the scheduled signal and OHLCV surfaces."
    )
    ap.add_argument(
        "--leg",
        choices=("signal", "ohlcv", "both"),
        default="both",
        help="Which leg to run (default: both).",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to analytics.db.")
    ap.add_argument(
        "--state", type=Path, default=DEFAULT_STATE, help="Path to signal_state.json."
    )
    ap.add_argument(
        "--stocks", type=Path, default=DEFAULT_STOCKS, help="Path to the watchlist."
    )
    ap.add_argument(
        "--exit-nonzero",
        action="store_true",
        help="Exit 1 when any leg reports STALE, for use as a shell condition.",
    )
    args = ap.parse_args()

    from analytics.trading_calendar import nyse_sessions

    now = datetime.now(UTC)
    today = latest_session(now, nyse_sessions)

    signal = None
    if args.leg in ("signal", "both"):
        signal = evaluate_signal(
            read_watermarks(args.state),
            now=today,
            sessions_fn=nyse_sessions,
            source=args.state,
        )

    ohlcv = None
    if args.leg in ("ohlcv", "both"):
        series = read_series(args.db)
        ohlcv = evaluate_ohlcv(
            series or [],
            now=today,
            scheduled_symbols=read_scheduled_symbols(args.stocks),
            sessions_fn=nyse_sessions,
        )

    print(render(signal, ohlcv))

    if args.exit_nonzero:
        bad = (signal is not None and not signal.ok) or (
            ohlcv is not None and not ohlcv.ok
        )
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
