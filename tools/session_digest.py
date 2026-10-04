"""Session digest: what is broken, what is overdue, what is open — in one screen.

Replaces asking "what's next" at the start of every session. Two consumers:

* **`SessionStart` hook** (`.claude/settings.json`). Every session in this repo opens
  with the digest already in context. The hook's stdout reaches the model, not the
  operator's screen, so the banner tells the model to lead its first reply with any
  RED line.
* **`wifey-daily-check` scheduled job** (`make session-digest TELEGRAM=1`). A hook
  fires only when a session opens; this push covers the days none does. It sends
  EVERY day, green included, so a missing message is itself the signal that the
  scheduler on this box has stopped.

It composes the existing probes rather than re-implementing them —
`freshness_check`, `backup_check`, `cadence_check` — and adds the one question none
of them asks directly: is `wifey-signal-watch` scheduled on this box at all? The
2026-09-18 outage was exactly that: the laptop move left no `\\wifey\\` tasks, the
watchlist froze, and `freshness-check` said so only to whoever ran it.

Two properties are load-bearing (ported from fund-management's `open_issues.py`):

1. It ALWAYS exits 0 unless ``--exit-nonzero`` is passed. A digest must never block
   a session, so a probe that raises becomes a BROKE line, never a traceback.
2. A failure prints LOUDLY. "Could not fetch Issues" and "no open Issues" must not
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_SLUG = "s10023/buibui-wifey-wall-street-bot"
HANDOFF = Path("docs/plans/next-conversation-prompt.md")
SIGNAL_TIMER = "wifey-signal-watch.timer"
GH_TIMEOUT_S = 20

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
    elif ohlcv.stale:
        oldest = min(g.newest_session for g in ohlcv.stale)
        out.append(
            Finding(
                "RED",
                "watchlist OHLCV stale",
                f"{len(ohlcv.stale)} of {ohlcv.scheduled_total} scheduled series behind;"
                f" oldest last session {oldest}",
                "CATCH_UP=1 make go-live, then check why the scheduled run did not",
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
        lines.append("OK    scheduler, watchlist OHLCV, backup, cadence")

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


def fetch_issue_items(state: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Issues in ``state`` (``open``, ``closed`` or ``all``) via `gh api`.

    The issues endpoint also returns pull requests, and they are dropped here, so
    every consumer sees Issues only. Returns ``(None, reason)`` on any failure.
    """
    env = owner_env()
    items: list[dict[str, Any]] = []
    for page in range(1, MAX_ISSUE_PAGES + 1):
        try:
            proc = subprocess.run(
                ["gh", "api", ISSUES_PATH.format(state=state, page=page)],
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
        items.extend(it for it in batch if "pull_request" not in it)
        if len(batch) < 100:
            return items, ""
    return None, f"more than {MAX_ISSUE_PAGES} pages of Issues; refusing a partial read"


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


def collect_findings() -> list[Finding]:
    findings: list[Finding] = []
    findings += _guarded("scheduler probe", _scheduler)
    findings += _guarded("freshness probe", _freshness)
    findings += _guarded("backup probe", _backup)
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
    issues, issues_error = fetch_issues()
    handoff = (
        handoff_first_moves(HANDOFF.read_text(encoding="utf-8"))
        if HANDOFF.exists()
        else None
    )

    print(render(findings, issues, issues_error, handoff, for_model=not args.telegram))

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
        )
        send_telegram_message(text[:TELEGRAM_MAX_CHARS])

    if args.exit_nonzero and any(f.level in ("RED", "BROKE") for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
