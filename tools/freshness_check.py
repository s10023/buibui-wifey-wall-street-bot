"""Observed-state freshness probe for the two scheduled surfaces that alert only on failure.

`wifey-signal-watch`, `wifey-backup` and `wifey-backup-offsite` all alert through
`OnFailure=wifey-alert@%N.service`. That channel cannot distinguish a timer with
nothing to report from a timer that **stopped firing**, so the only honest check
is to read observed state and date it. `make backup-check` does that for the
backup tier; this does it for the two the backup probe cannot see:

- **signal** — has a scan actually run, and when did one last dispatch? These are
  two questions and only the first is gradeable. **The watermark is a dispatch
  oracle, not a run oracle** — it advances when a candle is consumed by an alert,
  and dispatch is intermittent by design: `day_filter = tue_thu` suppresses on
  the bar's open weekday, so a Mon run (Fri bars) and a Tue run (Mon bars) can
  never alert. Measured 2026-08-26, the newest watermark sat 4 sessions back
  with run evidence on two of the three intervening sessions (`fired_at_ms`
  rows on 08-24 and 08-25; none on 08-21, which is not evidence of no run —
  a scan that detects nothing new writes nothing).
  Grading it against the timer's daily cadence therefore prints STALE on a
  healthy system, which is why it is reported **dated but ungraded**.
  Run-liveness is answered instead by the ohlcv leg: a `go-live` run's first act
  is a watchlist sync, so fresh watchlist bars ARE the evidence a run happened,
  and that quantity does have a declared cadence.
  Skipping a run day destroys that day's alerts rather than deferring them
  (the next catch-up consumes the watermark without dispatching), so a late
  detection is not recoverable by running twice tomorrow.
- **ohlcv** — is what a study would read fresh? The parent's
  `tools/ohlcv_freshness.py` motivated it with a measurement of 22 of 25
  universe symbols frozen for eleven weeks with nothing broken and nothing
  watching. Wifey measured the same shape on 2026-08-26: the 13-symbol watchlist
  ran to the previous session while the bulk of the 505-member research universe
  had not moved since 2026-06-18. It also lists **level breaks**: a series that
  resumed after a gap at a different level, which is how a provider serving the
  wrong security under a ticker shows in the stored tape (AVB, BNY `4h`; #469),
  and every `4h` slot missing from a session that holds another bar (#327).
  And it lists every `migrations/0*.py` this DB has no `schema_migrations` row
  for, since a purge that never ran leaves its bad rows in what a study reads
  (007 sat unapplied here until #445; #467).
  Network-free, and AMBER-only: it never makes the leg's ``ok`` false.

Three properties decide whether a probe of this kind is worth anything, and all
three are why this is a module rather than a line of SQL in a caller.

- **Age is only meaningful in sessions, never in wall-clock.** This is wifey's
  one hard divergence from the parent, which computes `(now - newest) / bar_ms`
  against a 24h tape. On an RTH tape that is wrong in the direction that reds
  everything forever: `4h` is 2 bars/day rather than 6, so a healthy series two
  sessions old reads as ~12 bars behind, and every Monday adds a phantom
  weekend. Age here is NYSE sessions elapsed (`analytics.trading_calendar`)
  multiplied by `cost_model.BARS_PER_DAY` — the single shared table, imported
  rather than forked.
- **The newest stored bar is normally in progress**, and a pre-open scan reads
  the previous session's bars by design. A healthy series therefore always
  trails, which is what `BASE_TOLERANCE_SESSIONS` absorbs; a probe that reads
  "not yet closed" as stale is mute within a week.
- **Staleness is meaningless without a declared cadence.** Nothing schedules the
  505-member universe, so "10 weeks behind" is not a fault there — it is the
  absence of a refresher, which is a different finding and is reported as one.
  Grading it against a cadence it does not have would print ~490 findings every
  run, and a leg that is never green stops being read.

**Advisory, and it must never enter `make test`, `make sanity-checks` or CI.**
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
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# A bare `python tools/<name>.py` puts tools/ on sys.path rather than the repo root,
# so the repo imports below died with ModuleNotFoundError and exit 1 (#436). The
# guarantee is `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.backtest.cost_model import BARS_PER_DAY
from analytics.data_quality import (
    LEVEL_BREAK_GAP_DAYS,
    _et_date,
    classify_level_break,
    detect_slot_gaps,
)
from analytics.store.schema_migrations import recorded_ids, script_ids
from tools import host_platform

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

# Sessions between two research-universe refreshes. `wifey-universe-sync.timer`
# is `OnCalendar=Sat *-*-* 10:00:00 UTC`, one fire a week = five sessions.
UNIVERSE_GAP_SESSIONS: float = 5.0

DEFAULT_UNIVERSE = Path("config/universe.json")

# The timer whose presence turns the universe from an absence into a graded tier.
UNIVERSE_TIMER = "wifey-universe-sync.timer"

#: Where `deploy/windows/install-tasks.ps1` registers this repo's tasks. Its default for
#: `-TaskPath` is the same string; `tests/test_windows_jobs.py` pins that they agree,
#: because a probe looking in the wrong folder returns "not enabled" rather than an
#: error and is therefore indistinguishable from a box that installed nothing.
WINDOWS_TASK_PATH = "\\wifey\\"

# The floor every graded series keeps, in SESSIONS. Two sessions absorbs the
# in-progress bar plus the pre-open lag: the 08:30 UTC scan runs before the bell,
# so the newest CLOSED daily bar it can see belongs to the previous session. A
# tighter floor reds every healthy series every morning.
BASE_TOLERANCE_SESSIONS: float = 2.0

# There is deliberately no signal-side tolerance constant. The watermark dates
# the last dispatch, whose cadence is a consequence of `day_filter` rather than a
# schedule, so no constant here would have an external referent — and a
# hand-picked one would red a healthy system. See `evaluate_signal`.

DEFAULT_DB = Path("analytics.db")
DEFAULT_STATE = Path("signal_state.json")
DEFAULT_STOCKS = Path("config/stocks.json")
DEFAULT_MIGRATIONS = Path("migrations")

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
class Cadence:
    """A declared refresh schedule: which series it covers and how often it runs.

    Two exist. The WATCHLIST cadence is `wifey-signal-watch.timer` refreshing
    `config/stocks.json` on the timeframes the live scan reads; the UNIVERSE
    cadence is `wifey-universe-sync.timer` refreshing the ACTIVE research members
    (502 of 505, measured 2026-09-02). Not all 505: the sync resolves `--universe`
    through `active_symbols()`, so a delisted member is refreshed by nothing --
    which is why `read_universe_symbols` excludes it rather than grading it
    against a cadence that does not cover it.

    A cadence is a claim that something runs, so it must be derived from
    observed state rather than asserted here. `resolve_cadences` takes the
    universe member set as an argument and callers pass an empty one whenever
    the timer is not enabled — see `universe_timer_enabled`. Asserting the
    schedule instead would grade ~1,100 series against a timer that may never
    have been installed, printing phantom faults on a healthy box.
    """

    name: str
    timeframes: frozenset[str]
    gap_sessions: float


WATCHLIST_CADENCE = Cadence("watchlist", SCHEDULED_TIMEFRAMES, SCHEDULED_GAP_SESSIONS)

# Covers `1wk` where the watchlist cadence does not. The live scan does not
# read `1wk`, so a watchlist name's weekly bars would go stale exactly like the
# universe's; `make wifey-universe-sync` fetches 4h/1d/1wk, so for a symbol in
# both sets the weekly series has a cadence through this one.
UNIVERSE_CADENCE = Cadence(
    "universe", frozenset({"4h", "1d", "1wk"}), UNIVERSE_GAP_SESSIONS
)


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
    # The cadence that graded it (`Cadence.name`); None until `evaluate_ohlcv` assigns
    # one. Readers treat None as the strict watchlist tier.
    cadence: str | None = None

    @property
    def measurable(self) -> bool:
        return self.age_bars is not None


@dataclass(frozen=True)
class LevelBreakRow:
    """A stored series that resumed after a gap at a different level (#469).

    Only the gapped kind is reported: it has no measured false positive on
    analytics.db, whereas an ungapped halving is usually a real move and needs
    the provider's quote to judge, which this network-free probe does not read.
    """

    symbol: str
    timeframe: str
    session: date
    prev_close: float
    close: float
    gap_days: float

    @property
    def ratio(self) -> float:
        return self.close / self.prev_close


@dataclass(frozen=True)
class OhlcvReport:
    """Both tiers of the ohlcv leg, kept apart because they answer different questions."""

    stale: list[Graded]
    scheduled_total: int
    unscheduled_total: int
    unscheduled_oldest: date | None
    unscheduled_newest: date | None
    # Whether the weekly universe timer was observed enabled. Distinguishes "the
    # universe is fresh" from "the universe was never graded", which the counts
    # alone cannot separate — the same trap as a SKIPPED CI job reading green.
    universe_scheduled: bool = False
    # Gapped level breaks anywhere in the stored history. Advisory: `ok` ignores
    # them, because a break is a data-identity question, not a staleness one.
    level_breaks: tuple[LevelBreakRow, ...] = ()
    # `migrations/0*.py` scripts with no `schema_migrations` row in this DB
    # (#467). None means the record was not read (DB absent or locked), which is
    # not the same claim as "every migration recorded". Advisory like the breaks:
    # a missed purge changes what a study reads, but it is not staleness.
    unrecorded_migrations: tuple[str, ...] | None = None
    # `(symbol, slot open_time ms)` for each `4h` slot missing from a session
    # that holds another bar (#327). Advisory like the breaks: absent data.
    slot_gaps: tuple[tuple[str, int], ...] = ()

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

        Deliberately not a staleness verdict. An old watermark on a healthy
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


def sessions_per_bar(timeframe: str) -> float | None:
    """Sessions one bar of ``timeframe`` spans; None when the table has no entry.

    The reciprocal of the shared `BARS_PER_DAY`, imported rather than forked so a
    bar-count constant cannot drift from the one the cost model uses.
    """
    per_day = BARS_PER_DAY.get(timeframe)
    if per_day is None or per_day <= 0:
        return None
    return 1.0 / per_day


def tolerance_sessions_for(timeframe: str, cadence: Cadence) -> float | None:
    """Sessions a correctly-refreshed ``timeframe`` may trail before it is stale.

    Three terms. The base absorbs the in-progress bar and the pre-open lag; the
    gap is one whole refresh cycle of the cadence that covers this series; the
    third is how much longer than a session this timeframe's own bar takes to
    close.

    That third term is why `1wk` can be graded at all. A weekly bar stamps on
    the week's Monday open and does not close until Friday, so a perfectly
    refreshed weekly series is routinely four sessions behind a daily one for
    reasons that have nothing to do with staleness. Grading it on the daily
    footing reds every weekly series forever — the same shape as the parent's
    wall-clock age, which this tool's session-based age exists to avoid. It is
    ``max(0, …)`` so 4h and 1d, whose bars close inside a session, keep exactly
    the tolerance they have without this term.

    ``cadence`` is required rather than defaulted so mypy forces every call site
    to state which schedule it is grading against: the same series has different
    tolerances under the daily and weekly timers, and a default would silently
    pick one.

    Returns None for a timeframe the cadence does not cover, or one absent from
    the bars-per-day table, so a caller reports "no declared cadence" rather than
    applying a tight default to a series whose cadence it does not know.
    """
    if timeframe not in cadence.timeframes:
        return None
    span = sessions_per_bar(timeframe)
    if span is None:
        return None
    return BASE_TOLERANCE_SESSIONS + cadence.gap_sessions + max(0.0, span - 1.0)


def resolve_cadence(
    symbol: str,
    timeframe: str,
    *,
    watchlist: frozenset[str],
    universe: frozenset[str],
) -> Cadence | None:
    """The cadence covering one series, or None when nothing schedules it.

    When both cover it, the tightest gap wins. A watchlist name is refreshed
    daily whether or not the weekly timer also touches it, so the tight bar is
    both achievable and the only one that would notice the daily timer stopping.
    Taking the loose one would let a watchlist series sit four sessions stale and
    still read FRESH.
    """
    covering = [
        cadence
        for cadence, members in (
            (WATCHLIST_CADENCE, watchlist),
            (UNIVERSE_CADENCE, universe),
        )
        if symbol in members and timeframe in cadence.timeframes
    ]
    if not covering:
        return None
    return min(covering, key=lambda cadence: cadence.gap_sessions)


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
    universe_symbols: frozenset[str] = frozenset(),
) -> OhlcvReport:
    """Split rows into the graded scheduled tier and the summarised unscheduled one.

    A series is scheduled when some declared cadence covers it — see
    `resolve_cadence`. Two do: the watchlist the daily signal timer syncs on the
    timeframes the live scan reads, and the ACTIVE research members the weekly
    universe timer syncs on 4h/1d/1wk (502 of 505; a delisted member is synced by
    nothing and so is not graded).

    ``universe_symbols`` defaults to empty, and that default is the safe one.
    The universe timer is opt-in and nothing in the repo installs it, so a caller
    that has not checked whether it is enabled must not get the graded tier by
    accident. `main` passes members only when `universe_timer_enabled` says so.

    An empty watchlist (unreadable or absent) likewise puts its series in the
    unscheduled tier. Both defaults degrade toward "nothing is graded", which
    reports an absence, rather than toward "everything is graded against a
    cadence it does not have", which would report ~1,100 phantom faults.
    """
    stale: list[Graded] = []
    scheduled_total = 0
    unscheduled: list[date] = []

    for series in rows:
        cadence = resolve_cadence(
            series.symbol,
            series.timeframe,
            watchlist=scheduled_symbols,
            universe=universe_symbols,
        )
        graded = grade(series, now=now, sessions_fn=sessions_fn)
        if cadence is None:
            unscheduled.append(graded.newest_session)
            continue
        scheduled_total += 1
        graded = replace(graded, cadence=cadence.name)
        limit = tolerance_sessions_for(series.timeframe, cadence)
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
        universe_scheduled=bool(universe_symbols),
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
        raw = json.loads(path.read_text(encoding="utf-8"))
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
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if isinstance(raw, list):
        return frozenset(str(s) for s in raw)
    if isinstance(raw, dict):
        # `universe_policy` is a config block, not a symbol.
        return frozenset(str(k) for k in raw if k != "universe_policy")
    return frozenset()


def read_universe_symbols(path: Path = DEFAULT_UNIVERSE) -> frozenset[str]:
    """The research breadth universe's ACTIVE members; empty when unreadable.

    Reads the file directly rather than through `load_research_universe`, for
    `read_scheduled_symbols`' reason: this is a probe, and it must report an
    absent universe rather than crash on one.

    Delisted members are excluded, and that is a cadence claim rather than a
    tidying one. `analytics_runner` resolves `--universe` through
    `active_symbols()`, so the weekly sync refreshes the active set and nothing
    refreshes a delisted one — by design, since its tape has stopped. Grading it
    anyway would report a permanent STALE for a decision that was made on
    purpose, and a leg that can never be green stops being read.

    A member whose value is not a dict is KEPT, not dropped: an unreadable
    member degrades to being graded, never to being silently excused, which is
    the same direction every other unreadable state takes here.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(raw, dict):
        return frozenset()
    members = raw.get("members")
    if not isinstance(members, dict):
        return frozenset()
    return frozenset(
        str(k)
        for k, v in members.items()
        if not (isinstance(v, dict) and v.get("delisted"))
    )


def universe_timer_enabled(timer: str = UNIVERSE_TIMER) -> bool:
    """Whether the weekly universe timer is enabled; see `timer_enabled`."""
    return timer_enabled(timer)


def timer_enabled(timer: str) -> bool:
    """Whether ``timer`` is enabled in this user's systemd, or as a Windows task.

    Generic over the unit so `tools/session_digest.py` can ask the same question of
    `wifey-signal-watch.timer` — a box that never registered it reads False here.

    This is the probe's one piece of non-DB observed state, and it is what
    keeps the universe tier honest. The units are opt-in and nothing in the repo
    installs them, so "the universe has a cadence" is true on one box and false
    on the next. Deriving it here means the tool reports an ABSENCE where the
    timer is not installed and grades where it is, instead of hardcoding either
    answer (asserting "nothing refreshes the 505-member research universe" as a
    constant would couple the file to one box).

    Every failure degrades to False — no systemd, no `systemctl`, a timeout, a
    permission error. False means "report the absence", which is
    the direction that cannot invent faults; True on a box with no timer would
    grade ~1,100 series against a schedule that never runs.

    **A non-Linux box is not one of those failures.** `systemctl` is absent on
    Windows, so an `OSError` branch returning False would report the absence even
    when the job is registered with Task Scheduler instead; the same False would
    then mean "no cadence" about a job running every Saturday, and the leg would
    stay silent forever on the one host it was wrong about. Degrading to the safe
    answer is only safe while the safe answer is also the true one.
    """
    if host_platform.is_windows():
        return _scheduled_task_enabled(task_name_for_unit(timer))
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-enabled", timer],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # `is-enabled` prints the state and uses the exit code for it; "enabled" and
    # "enabled-runtime" both mean the timer will fire. "static", "disabled",
    # "masked" and "not-found" do not.
    return (proc.stdout or "").strip() in {"enabled", "enabled-runtime"}


def task_name_for_unit(unit: str) -> str:
    """`wifey-universe-sync.timer` -> `wifey-universe-sync`.

    A Task Scheduler name carries no type suffix, and `.timer` in one would read as a
    file extension. It is a function rather than a convention so
    `deploy/windows/install-tasks.ps1` and this probe cannot drift — the installer
    registers under exactly this transform and a test pins that both ends agree.
    """
    return unit.removesuffix(".service").removesuffix(".timer")


def _scheduled_task_enabled(name: str, task_path: str = WINDOWS_TASK_PATH) -> bool:
    """Whether a Windows scheduled task exists and is enabled.

    **`State` is the field, not existence.** `install-tasks.ps1` registers
    `wifey-backup-offsite` and then disables it on purpose, so "the task is there" and
    "the task will fire" are genuinely different answers here, and only the second one
    licenses grading a cadence.

    Degrades to False exactly as the systemd branch does: no PowerShell, a timeout, a
    task that is not registered. `-ErrorAction SilentlyContinue` keeps an absent task
    from being an error rather than an answer.
    """
    script = (
        f"$t = Get-ScheduledTask -TaskPath '{task_path}' -TaskName '{name}' "
        "-ErrorAction SilentlyContinue; if ($t) { $t.State }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # `Ready` = registered and will fire on its trigger. `Running` = firing right now.
    # `Disabled` must read False — that is the offsite job's deliberate state.
    return (proc.stdout or "").strip() in {"Ready", "Running"}


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


#: Every consecutive bar pair whose spacing exceeds the smallest gap any timeframe
#: treats as a resumption; `gapped_breaks` applies the per-timeframe limit.
LEVEL_BREAK_SQL = """
WITH x AS (
    SELECT symbol, timeframe, open_time, close,
           lag(open_time) OVER w AS prev_open_time,
           lag(close) OVER w AS prev_close
    FROM ohlcv
    WINDOW w AS (PARTITION BY symbol, timeframe ORDER BY open_time)
)
SELECT symbol, timeframe, prev_open_time, prev_close, open_time, close
FROM x
WHERE prev_open_time IS NOT NULL AND open_time - prev_open_time > ?
ORDER BY symbol, timeframe, open_time
"""

_DAY_MS = 86_400_000


def gapped_breaks(
    rows: Iterable[tuple[str, str, int, float, int, float]],
) -> list[LevelBreakRow]:
    """Keep the pairs `classify_level_break` calls gapped. Pure."""
    out: list[LevelBreakRow] = []
    for symbol, timeframe, prev_t, prev_close, t, close in rows:
        brk = classify_level_break(
            int(prev_t), float(prev_close), int(t), float(close), str(timeframe)
        )
        if brk is not None and brk.kind == "gapped":
            out.append(
                LevelBreakRow(
                    str(symbol),
                    str(timeframe),
                    session_date(int(t)),
                    brk.prev_close,
                    brk.close,
                    brk.gap_days,
                )
            )
    return out


def read_level_breaks(db_path: Path) -> list[LevelBreakRow] | None:
    """Gapped level breaks across every stored series; None when unreadable."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover - duckdb is a runtime dep
        return None
    if not db_path.exists():
        return None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return None
    try:
        min_gap_ms = int(min(LEVEL_BREAK_GAP_DAYS.values()) * _DAY_MS)
        rows = conn.execute(LEVEL_BREAK_SQL, [min_gap_ms]).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    return gapped_breaks(rows)


#: Every stored `4h` bar, grouped for a per-series slot scan.
SLOT_GAP_SQL = """
SELECT symbol, open_time FROM ohlcv WHERE timeframe = '4h'
ORDER BY symbol, open_time
"""

BoundsFn = Callable[[date, date], list[tuple[date, int, int]]]


def slot_gaps(
    rows: Iterable[tuple[str, int]], bounds_fn: BoundsFn
) -> list[tuple[str, int]]:
    """Missing `4h` slots per symbol, from ``(symbol, open_time)`` rows. Pure."""
    by_symbol: dict[str, list[int]] = {}
    for symbol, t in rows:
        by_symbol.setdefault(str(symbol), []).append(int(t))
    if not by_symbol:
        return []
    every = [t for ts in by_symbol.values() for t in ts]
    bounds = bounds_fn(_et_date(min(every)), _et_date(max(every)))
    return [
        (symbol, slot)
        for symbol, ts in sorted(by_symbol.items())
        for slot in detect_slot_gaps(ts, "4h", bounds).missing
    ]


def read_slot_gaps(db_path: Path) -> list[tuple[str, int]] | None:
    """Missing `4h` slots across every stored series; None when unreadable."""
    try:
        import duckdb
    except ImportError:  # pragma: no cover - duckdb is a runtime dep
        return None
    if not db_path.exists():
        return None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return None
    try:
        rows = conn.execute(SLOT_GAP_SQL).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    from analytics.trading_calendar import nyse_session_bounds

    return slot_gaps(rows, nyse_session_bounds)


def unrecorded(script_ids: Sequence[str], recorded: frozenset[str]) -> tuple[str, ...]:
    """Scripts with no applied record, in script order. Pure."""
    return tuple(i for i in script_ids if i not in recorded)


def read_unrecorded_migrations(
    db_path: Path, migrations_dir: Path = DEFAULT_MIGRATIONS
) -> tuple[str, ...] | None:
    """Migration scripts this DB has no record of; None when the DB is unreadable.

    Reads the record, never the scripts' predicates: a predicate re-run flags
    config drift (002) and by-design residuals (008) as pending (#467).
    """
    try:
        import duckdb
    except ImportError:  # pragma: no cover - duckdb is a runtime dep
        return None
    if not db_path.exists():
        return None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return None
    try:
        recorded = recorded_ids(conn)
    except Exception:
        return None
    finally:
        conn.close()
    return unrecorded(script_ids(migrations_dir), recorded)


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
        # There is no single tolerance to quote: the daily and weekly cadences
        # carry different ones, and `1wk` adds its own bar-span term on top. A
        # figure here would be right for one tier and wrong for the other, which
        # is worse than naming neither.
        lines.append(
            f"      + FRESH   all {report.scheduled_total} scheduled series within"
            " their declared cadence"
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
            "        Not a fault: no timer on this box covers them. ⚠ It IS a"
            " hazard for a pooled"
        )
        lines.append(
            "        cross-section, which would mix these with the fresh"
            " watchlist inside one query."
        )
        lines.append(
            "        Refresh with `make wifey-universe-sync` /"
            " `make wifey-pundit-sync` before any"
        )
        lines.append("        breadth study.")
        if not report.universe_scheduled:
            lines.append(
                "        · The 505-member universe has a WEEKLY TIMER available"
                " and NOT ENABLED here"
            )
            lines.append(
                "          (`wifey-universe-sync.timer`). Until it is, this tier"
                " is a hand-run"
            )
            lines.append(
                "          habit — see deploy/README.md. The pundit ledger has"
                " no timer at all."
            )

    if report.level_breaks:
        lines.append(
            f"      ! LEVEL BREAK  {len(report.level_breaks)} gapped resumption(s)"
            " at a different level — a probable wrong instrument (#469)"
        )
        for brk in report.level_breaks[:10]:
            lines.append(
                f"          {brk.symbol:<10} {brk.timeframe:<4} {brk.session}"
                f"  {brk.prev_close:.2f} -> {brk.close:.2f} (x{brk.ratio:.3f})"
                f" after {brk.gap_days:.1f} days"
            )
        if len(report.level_breaks) > 10:
            lines.append(f"          … and {len(report.level_breaks) - 10} more")
        lines.append(
            "        Advisory: nothing was dropped. Compare the series with the"
            " provider's quote"
        )
        lines.append("        before purging; migration 007 (AVB) is the repair shape.")
    if report.slot_gaps:
        by_slot: dict[int, list[str]] = {}
        for symbol, slot in report.slot_gaps:
            by_slot.setdefault(slot, []).append(symbol)
        lines.append(
            f"      ! SLOT GAP  {len(report.slot_gaps)} missing 4h slot(s) in"
            f" {len(by_slot)} session slot(s) otherwise present (#327)"
        )
        for slot, symbols in sorted(by_slot.items())[:10]:
            label = datetime.fromtimestamp(slot / 1000, tz=UTC).strftime(
                "%Y-%m-%d %H:%MZ"
            )
            shown = ", ".join(symbols[:4]) + ("…" if len(symbols) > 4 else "")
            lines.append(f"          {label}  {len(symbols):>3} symbol(s): {shown}")
        if len(by_slot) > 10:
            lines.append(f"          … and {len(by_slot) - 10} more slots")
        lines.append(
            "        Advisory: nothing was dropped. Re-fetch with"
            " `make wifey-analytics-backfill"
        )
        lines.append(
            "        TIMEFRAMES=4h SINCE=<date> SYMBOLS=…`; a slot the provider"
            " no longer serves stays missing."
        )
    if report.unrecorded_migrations:
        lines.append(
            f"      ! UNRECORDED MIGRATION  {len(report.unrecorded_migrations)}"
            " script(s) in migrations/ with no schema_migrations row (#467)"
        )
        for mid in report.unrecorded_migrations[:10]:
            lines.append(f"          {mid}")
        if len(report.unrecorded_migrations) > 10:
            lines.append(
                f"          … and {len(report.unrecorded_migrations) - 10} more"
            )
        lines.append(
            "        Dry-run each to see whether it is pending here, then --apply"
            " (backup + fresh .bak)."
        )
        lines.append(
            "        A DB that predates the record table: run"
            " migrations/seed_applied_2026_10_09.py."
        )
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


def collect(
    *,
    leg: str = "both",
    db: Path = DEFAULT_DB,
    state: Path = DEFAULT_STATE,
    stocks: Path = DEFAULT_STOCKS,
    universe: Path = DEFAULT_UNIVERSE,
    migrations: Path = DEFAULT_MIGRATIONS,
) -> tuple[SignalReport | None, OhlcvReport | None]:
    """Read observed state and grade it; the I/O half `main` and the digest share."""
    from analytics.trading_calendar import nyse_sessions

    today = latest_session(datetime.now(UTC), nyse_sessions)

    signal = None
    if leg in ("signal", "both"):
        signal = evaluate_signal(
            read_watermarks(state),
            now=today,
            sessions_fn=nyse_sessions,
            source=state,
        )

    ohlcv = None
    if leg in ("ohlcv", "both"):
        series = read_series(db)
        # Members are passed only when the timer is enabled. Handing them over
        # unconditionally would grade the universe against a schedule that may
        # not exist on this box — see `universe_timer_enabled`.
        members = (
            read_universe_symbols(universe) if universe_timer_enabled() else frozenset()
        )
        ohlcv = evaluate_ohlcv(
            series or [],
            now=today,
            scheduled_symbols=read_scheduled_symbols(stocks),
            sessions_fn=nyse_sessions,
            universe_symbols=members,
        )
        ohlcv = replace(
            ohlcv,
            level_breaks=tuple(read_level_breaks(db) or ()),
            unrecorded_migrations=read_unrecorded_migrations(db, migrations),
            slot_gaps=tuple(read_slot_gaps(db) or ()),
        )
    return signal, ohlcv


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
        "--universe",
        type=Path,
        default=DEFAULT_UNIVERSE,
        help="Path to the research breadth universe.",
    )
    ap.add_argument(
        "--exit-nonzero",
        action="store_true",
        help="Exit 1 when any leg reports STALE, for use as a shell condition.",
    )
    args = ap.parse_args()

    signal, ohlcv = collect(
        leg=args.leg,
        db=args.db,
        state=args.state,
        stocks=args.stocks,
        universe=args.universe,
    )
    print(render(signal, ohlcv))

    if args.exit_nonzero:
        bad = (signal is not None and not signal.ok) or (
            ohlcv is not None and not ohlcv.ok
        )
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
