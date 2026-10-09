"""Session digest: what is broken, what is overdue, what is open — in one screen.

Replaces asking "what's next" at the start of every session. Two consumers:

* **`SessionStart` hook** (`.claude/settings.json`). Every session in this repo opens
  with the digest already in context. The hook's stdout reaches the model, not the
  operator's screen, so the banner tells the model to lead its first reply with any
  RED line.
* **`wifey-daily-check` scheduled job** (`make session-digest TELEGRAM=1`). A hook
  fires only when a session opens; this push covers the days none does. It sends
  every day, green included, so a missing message is itself the signal that the
  scheduler on this box has stopped.

It composes the existing probes rather than re-implementing them —
`freshness_check`, `backup_check`, `cadence_check` — and adds the one question none
of them asks directly: is `wifey-signal-watch` scheduled on this box at all? The
2026-09-18 outage was exactly that: the laptop move left no `\\wifey\\` tasks, the
watchlist froze, and `freshness-check` said so only to whoever ran it.

Two properties are load-bearing (ported from fund-management's `open_issues.py`):

1. It always exits 0 unless ``--exit-nonzero`` is passed. A digest must never block
   a session, so a probe that raises becomes a BROKE line, never a traceback.
2. A failure prints loudly. "Could not fetch Issues" and "no open Issues" must not
   look alike; a silent degrade teaches its reader to trust an empty list.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# A bare `python tools/session_digest.py` puts tools/ on sys.path rather than the repo
# root. Every repo import here is function-level, so `--help` survived while each probe
# read BROKE with ModuleNotFoundError (#436). The guarantee is
# `tests/test_tools_bare_invocation.py`, never this line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SLUG = "s10023/buibui-wifey-wall-street-bot"
HANDOFF = Path("docs/plans/next-conversation-prompt.md")
SIGNAL_TIMER = "wifey-signal-watch.timer"
GH_TIMEOUT_S = 20

#: The off-site leg's own result, which `backup_check` cannot see: it grades the
#: local tree, so the daily digest stayed green for two days while every off-site
#: run refused (#443). `deploy/windows/job.sh` heads each run with a `=== <ts> |`
#: line, and `deploy/backup-offsite.sh` ends a good one with `OFFSITE_OK`.
OFFSITE_TIMER = "wifey-backup-offsite.timer"
OFFSITE_OK = "off-site backup OK"
#: Daily job: one missed run of slack before a green last run stops counting.
OFFSITE_MAX_AGE_S = 2 * 86400
#: A run with no result line younger than this is still uploading.
OFFSITE_RUNNING_GRACE_S = 2 * 3600

#: Ordered best-first. An Issue carrying none is untriaged and sorts last, visibly.
PRIORITIES = ("p1", "p2", "p3")

#: Effort labels name the /effort level an Issue wants. Unset prints ``effort:?``
#: so an unlabelled row is visible rather than silently defaulted.
EFFORT_PREFIX = "effort:"

#: Telegram's hard cap is 4096 characters; leave room for the header.
TELEGRAM_MAX_CHARS = 3800
TELEGRAM_MAX_ISSUES = 10


@dataclass(frozen=True)
class Finding:
    """One line of the digest. ``level`` is RED (act now), AMBER, or BROKE."""

    level: str
    label: str
    detail: str
    action: str = ""


# --- Pure core --------------------------------------------------------------


def scheduler_findings(signal_scheduled: bool) -> list[Finding]:
    if signal_scheduled:
        return []
    return [
        Finding(
            "RED",
            "signal-watch not scheduled",
            f"{SIGNAL_TIMER} is not enabled on this box, so go-live runs only by hand"
            " and every skipped weekday destroys that day's alerts",
            "Windows: run deploy/windows/install-tasks.ps1 from an ELEVATED shell;"
            " Linux: systemctl --user enable --now wifey-signal-watch.timer",
        )
    ]


def freshness_findings(signal: Any, ohlcv: Any) -> list[Finding]:
    """Map `freshness_check` reports onto digest lines. Takes the report objects
    duck-typed so the pure half stays importable without the analytics stack."""
    out: list[Finding] = []
    if signal is not None and signal.newest_session is None:
        out.append(
            Finding(
                "RED",
                "no dispatch watermarks",
                f"nothing readable in {signal.source}",
                "restore signal_state.json from the newest backup snapshot",
            )
        )
    if ohlcv is None:
        return out
    if ohlcv.scheduled_total == 0 and ohlcv.unscheduled_total == 0:
        out.append(
            Finding(
                "AMBER",
                "ohlcv unreadable",
                "analytics.db is absent, locked (a go-live may be running) or empty",
                "re-run `make freshness-check` once the writer exits",
            )
        )
    elif ohlcv.scheduled_total == 0:
        out.append(
            Finding(
                "AMBER",
                "watchlist unreadable",
                "config/stocks.json is absent, so no scheduled series was graded",
            )
        )
    else:
        # Split by the cadence that graded each series: a universe straggler is a
        # research-data gap, not a live-path fault, and must not read as one (#389).
        # An untagged series counts as watchlist, the strict tier.
        universe = [g for g in ohlcv.stale if g.cadence == "universe"]
        watchlist = [g for g in ohlcv.stale if g.cadence != "universe"]
        if watchlist:
            out.append(
                Finding(
                    "RED",
                    "watchlist OHLCV stale",
                    f"{len(watchlist)} of {ohlcv.scheduled_total} scheduled series behind;"
                    f" oldest last session {min(g.newest_session for g in watchlist)}",
                    "CATCH_UP=1 make go-live, then check why the scheduled run did not",
                )
            )
        if universe:
            out.append(
                Finding(
                    "AMBER",
                    "universe OHLCV stale",
                    f"{len(universe)} of {ohlcv.scheduled_total} scheduled series behind;"
                    f" oldest last session {min(g.newest_session for g in universe)}",
                    "check the wifey-universe-sync task; `make freshness-check` lists them",
                )
            )
    if ohlcv.level_breaks:
        shown = "; ".join(
            f"{b.symbol} {b.timeframe} {b.session} x{b.ratio:.3f} after {b.gap_days:.0f}d"
            for b in ohlcv.level_breaks[:3]
        )
        out.append(
            Finding(
                "AMBER",
                "probable wrong-instrument series",
                f"{len(ohlcv.level_breaks)} gapped level break(s): {shown}",
                "`make freshness-check` lists them; confirm against the provider's"
                " quote, then purge in migration 007's shape (#469)",
            )
        )
    return out


def backup_findings(report: Any) -> list[Finding]:
    if report.ok:
        return []
    return [
        Finding(
            "RED",
            f"backup {report.verdict}",
            report.detail,
            "make backup, then check the wifey-backup task/timer",
        )
    ]


def offsite_findings(log_text: str | None, now: datetime) -> list[Finding]:
    """Grade the last run in the off-site job's log; ``None`` is an absent log."""
    runs: list[tuple[datetime, list[str]]] = []
    for line in (log_text or "").splitlines():
        if line.startswith("=== "):
            stamp = line[4:].split(" | ", 1)[0].strip()
            try:
                started = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                continue
            runs.append((started.replace(tzinfo=UTC), []))
        elif runs:
            runs[-1][1].append(line)
    action = (
        "check `rclone listremotes` and logs/wifey-backup-offsite.log"
        " (never paste rclone config output)"
    )
    if not runs:
        return [
            Finding(
                "AMBER",
                "off-site backup never logged",
                "the task is enabled but logs/wifey-backup-offsite.log holds no run",
                action,
            )
        ]
    started, body = runs[-1]
    stamp = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    age_s = (now - started).total_seconds()
    if any(line.strip() == OFFSITE_OK for line in body):
        if age_s <= OFFSITE_MAX_AGE_S:
            return []
        return [
            Finding(
                "RED",
                "off-site backup stale",
                f"last run {stamp}, {age_s / 86400:.1f}d ago: the task stopped firing",
                action,
            )
        ]
    if age_s < OFFSITE_RUNNING_GRACE_S:
        return []
    error = next((ln.strip() for ln in body if "ERROR" in ln or "CRITICAL" in ln), "")
    detail = error[:160] if error else f"did not print '{OFFSITE_OK}'"
    return [Finding("RED", "off-site backup failed", f"{stamp}: {detail}", action)]


def cadence_findings(statuses: list[Any]) -> list[Finding]:
    return [
        Finding(
            "AMBER",
            f"{st.task.label} overdue",
            st.detail,
            f"run it, then: make cadence-stamp TASK={st.task.slug}",
        )
        for st in statuses
        if st.overdue
    ]


def core_findings(
    as_of: date, now: datetime, sessions_fn: Callable[[date, date], list[date]]
) -> list[Finding]:
    """AMBER when ``^GSPC`` misses a session that has closed by ``now`` (UTC).

    The rule is :func:`analytics.overlay.live.missing_sessions`, shared with the
    web UI's core card.
    """
    from analytics.overlay.live import missing_sessions

    closed = missing_sessions(as_of, now, sessions_fn)
    if not closed:
        return []
    return [
        Finding(
            "AMBER",
            "core stale",
            f"^GSPC last close {as_of}, {len(closed)} closed session(s) missing,"
            " so the core line describes an old position",
            "make core-sync",
        )
    ]


def sort_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Priority first, then number. Untriaged sorts after every priority."""

    def key(issue: dict[str, Any]) -> tuple[int, int]:
        names = {lb["name"] for lb in issue.get("labels", [])}
        rank = next(
            (i for i, p in enumerate(PRIORITIES) if p in names), len(PRIORITIES)
        )
        return rank, int(issue["number"])

    return sorted(issues, key=key)


def format_issue(issue: dict[str, Any]) -> str:
    names = [lb["name"] for lb in issue.get("labels", [])]
    prio = next((p for p in PRIORITIES if p in names), "untriaged")
    effort = next((n for n in names if n.startswith(EFFORT_PREFIX)), "effort:?")
    rest = [n for n in names if n not in PRIORITIES and not n.startswith(EFFORT_PREFIX)]
    tags = " ".join([prio, effort, *rest])
    return f"#{issue['number']} [{tags}] {issue['title']}"


def handoff_first_moves(text: str) -> list[str]:
    """The handoff's `## ▶` headings: the moves the last session queued first."""
    return [
        line.lstrip("#").strip().lstrip("▶").strip()
        for line in text.splitlines()
        if line.startswith("## ▶")
    ]


def render(
    findings: list[Finding],
    issues: list[dict[str, Any]] | None,
    issues_error: str,
    handoff: list[str] | None,
    *,
    for_model: bool,
    max_issues: int | None = None,
    core: str = "",
) -> str:
    reds = [f for f in findings if f.level in ("RED", "BROKE")]
    lines: list[str] = []
    if for_model:
        lines.append(
            "SESSION DIGEST (tools/session_digest.py, SessionStart hook). Lead your"
            " first reply with every RED/BROKE line below, verbatim, before anything"
            " else. Open Issues are the planning queue."
        )
    head = f"{len(reds)} RED" if reds else "all green"
    lines.append(f"wifey daily check — {head}")
    for f in findings:
        lines.append(f"{f.level:<5} {f.label}: {f.detail}")
        if f.action:
            lines.append(f"      → {f.action}")
    if not findings:
        lines.append("OK    scheduler, watchlist OHLCV, backup, off-site, cadence")
    if core:
        lines.append(core)

    if issues is None:
        lines.append(f"BROKE could not fetch open Issues: {issues_error}")
    else:
        shown = sort_issues(issues)
        if max_issues is not None:
            shown = shown[:max_issues]
        lines.append(f"Open Issues ({len(issues)}):")
        lines.extend(f"  {format_issue(i)}" for i in shown)
        if len(shown) < len(issues):
            lines.append(f"  … and {len(issues) - len(shown)} more")

    if handoff is None:
        lines.append(f"Handoff: {HANDOFF} absent on this box")
    elif handoff:
        lines.append("Handoff first move: " + " | ".join(handoff))
    return "\n".join(lines)


# --- I/O --------------------------------------------------------------------


def owner_env() -> dict[str, str]:
    """The environment with ``GH_TOKEN`` set to the repo owner's token.

    The owner's token is fetched explicitly because the gh default account on this
    box may be a different one (memory `feedback_gh_account_stay_s10023`). A gh that
    cannot answer leaves the environment as it was; the caller's own gh call then
    fails loudly, so nothing is swallowed here. `cadence_check` shares this.
    """
    env = dict(os.environ)
    if env.get("GH_TOKEN"):
        return env
    owner = REPO_SLUG.split("/", 1)[0]
    try:
        tok = subprocess.run(
            ["gh", "auth", "token", "--user", owner],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GH_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return env
    if tok.returncode == 0 and tok.stdout.strip():
        env["GH_TOKEN"] = tok.stdout.strip()
    return env


#: REST, not ``gh issue list``: that command goes through GraphQL, which cloud
#: sessions are refused, while ``gh api`` on a ``repos/{owner}/{repo}`` path works
#: on every host. Pages are walked here rather than with ``--paginate``, which
#: follows GitHub's ``repositories/{id}`` Link URLs, and the cloud proxy refuses
#: those too.
ISSUES_PATH = f"repos/{REPO_SLUG}/issues?state={{state}}&per_page=100&page={{page}}"
#: A runaway guard, not a limit anyone should reach: 50 pages is 5,000 items.
MAX_ISSUE_PAGES = 50


#: Every Issue and PR conversation comment in the repo, one listing (#373).
ISSUE_COMMENTS_PATH = f"repos/{REPO_SLUG}/issues/comments?per_page=100&page={{page}}"
#: Every PR review (diff-line) comment, which the listing above does not carry.
PR_REVIEW_COMMENTS_PATH = f"repos/{REPO_SLUG}/pulls/comments?per_page=100&page={{page}}"
#: Every Issue and PR timeline event; its ``renamed`` events keep each title a
#: rename replaced (#448).
ISSUE_EVENTS_PATH = f"repos/{REPO_SLUG}/issues/events?per_page=100&page={{page}}"
#: The events listing's own runaway guard. Measured 2026-10-09: 20 pages (1,990
#: events, 25 renames) growing ~23 events a day, so ``MAX_ISSUE_PAGES`` would
#: turn the read UNREADABLE within about 130 days.
MAX_EVENT_PAGES = 200


def fetch_gh_pages(
    path: str, *, max_pages: int = MAX_ISSUE_PAGES
) -> tuple[list[dict[str, Any]] | None, str]:
    """Every item of a paged REST listing; ``path`` carries a ``{page}`` slot.

    Returns ``(None, reason)`` on any failure, including a listing longer than
    ``max_pages``: a partial read must never pass for a complete one.
    """
    env = owner_env()
    items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        try:
            proc = subprocess.run(
                ["gh", "api", path.format(page=page)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GH_TIMEOUT_S,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"gh unavailable ({exc})"
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()[:200]
            return None, err or f"gh exit {proc.returncode}"
        try:
            batch = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            return None, f"unparseable gh output ({exc})"
        if not isinstance(batch, list):
            return None, f"unexpected gh output on page {page}"
        items.extend(batch)
        if len(batch) < 100:
            return items, ""
    return None, f"more than {max_pages} pages; refusing a partial read"


def fetch_issue_items(
    state: str, *, include_prs: bool = False
) -> tuple[list[dict[str, Any]] | None, str]:
    """Issues in ``state`` (``open``, ``closed`` or ``all``) via `gh api`.

    The issues endpoint also returns pull requests. They are dropped unless
    ``include_prs``, so every planning consumer sees Issues only; the
    `sensitive-terms` leg keeps them, because a PR publishes on the same flip.
    Returns ``(None, reason)`` on any failure.
    """
    items, err = fetch_gh_pages(ISSUES_PATH.replace("{state}", state))
    if items is None or include_prs:
        return items, err
    return [it for it in items if "pull_request" not in it], err


def fetch_issues() -> tuple[list[dict[str, Any]] | None, str]:
    """Open Issues, as the repo owner's account."""
    return fetch_issue_items("open")


def _guarded(label: str, probe: Callable[[], list[Finding]]) -> list[Finding]:
    """Run one probe; a raise becomes a BROKE line rather than a traceback."""
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - the digest must never crash a session
        return [Finding("BROKE", label, f"{type(exc).__name__}: {exc}")]


def _freshness() -> list[Finding]:
    from tools import freshness_check

    signal, ohlcv = freshness_check.collect()
    return freshness_findings(signal, ohlcv)


def _backup() -> list[Finding]:
    from tools import backup_check

    root = Path(
        os.environ.get("WIFEY_BACKUP_ROOT") or str(backup_check.DEFAULT_ROOT)
    ).expanduser()
    report = backup_check.evaluate(
        root, datetime.now(UTC), backup_check.DEFAULT_MAX_AGE_DAYS
    )
    return backup_findings(report)


def _offsite() -> list[Finding]:
    """Only `job.sh`, the Windows wrapper, writes `logs/`; a systemd unit logs to the
    journal, so a Linux box reads silent here rather than AMBER forever."""
    from tools import freshness_check, host_platform

    if not host_platform.is_windows() or not freshness_check.timer_enabled(
        OFFSITE_TIMER
    ):
        return []
    log = Path(os.environ.get("WIFEY_LOG_DIR") or "logs") / "wifey-backup-offsite.log"
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else None
    return offsite_findings(text, datetime.now(UTC))


def _cadence() -> list[Finding]:
    from tools import cadence_check

    now = datetime.now(UTC)
    return cadence_findings(
        [
            cadence_check.evaluate(t, cadence_check.MARKS, now)
            for t in cadence_check.TASKS
        ]
    )


def _scheduler() -> list[Finding]:
    from tools import freshness_check

    return scheduler_findings(freshness_check.timer_enabled(SIGNAL_TIMER))


def collect_core() -> tuple[str, list[Finding]]:
    """The core line and its staleness finding, read-only from ``analytics.db``.

    An unreadable DB (a go-live holding the lock, or no file) is AMBER, as in
    the freshness probe; any other raise becomes BROKE through ``_guarded``.
    """
    line = ""

    def probe() -> list[Finding]:
        nonlocal line
        import duckdb

        from analytics.overlay.frame import load_gspc_close
        from analytics.overlay.live import completed_closes, core_state, format_core
        from analytics.store import DEFAULT_DB_PATH
        from analytics.trading_calendar import nyse_sessions

        unreadable = Finding(
            "AMBER",
            "core unreadable",
            "analytics.db is absent or locked (a go-live may be running)",
            "re-run `make session-digest` once the writer exits",
        )
        if not Path(DEFAULT_DB_PATH).exists():
            return [unreadable]
        try:
            conn = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True)
        except duckdb.Error:
            return [unreadable]
        try:
            close = load_gspc_close(conn)
        finally:
            conn.close()
        now = datetime.now(UTC)
        state = core_state(completed_closes(close, now))
        line = format_core(state)
        return core_findings(state.as_of, now, nyse_sessions)

    findings = _guarded("core probe", probe)
    return line, findings


def collect_findings() -> list[Finding]:
    findings: list[Finding] = []
    findings += _guarded("scheduler probe", _scheduler)
    findings += _guarded("freshness probe", _freshness)
    findings += _guarded("backup probe", _backup)
    findings += _guarded("off-site probe", _offsite)
    findings += _guarded("cadence probe", _cadence)
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--telegram",
        action="store_true",
        help="send the digest to the personal Telegram channel (never the wife channel)",
    )
    ap.add_argument(
        "--exit-nonzero",
        action="store_true",
        help="exit 1 when any line is RED or BROKE (opt-in; the hook never passes it)",
    )
    args = ap.parse_args()
    # Every probe reads repo-relative defaults; a hook may start anywhere.
    os.chdir(REPO_ROOT)
    # Issue titles are arbitrary Unicode and a Windows console is not UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    findings = collect_findings()
    core, core_extra = collect_core()
    findings += core_extra
    issues, issues_error = fetch_issues()
    handoff = (
        handoff_first_moves(HANDOFF.read_text(encoding="utf-8"))
        if HANDOFF.exists()
        else None
    )

    print(
        render(
            findings,
            issues,
            issues_error,
            handoff,
            for_model=not args.telegram,
            core=core,
        )
    )

    if args.telegram:
        from dotenv import load_dotenv

        from utils.telegram import send_telegram_message

        # python-dotenv strips the CR a Windows editor leaves on TELEGRAM_BOT_TOKEN.
        load_dotenv()

        text = render(
            findings,
            issues,
            issues_error,
            handoff,
            for_model=False,
            max_issues=TELEGRAM_MAX_ISSUES,
            core=core,
        )
        send_telegram_message(text[:TELEGRAM_MAX_CHARS])

    if args.exit_nonzero and any(f.level in ("RED", "BROKE") for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
