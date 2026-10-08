"""Wait for CI to settle, then report whether it actually RAN.

Two gates, one tool:

* ``--pr N`` — a PR's own checks, before merge.
* ``--branch main --min-jobs 5`` — **the flip-back gate**: main's push run after
  a merge, which must reach a job-count FLOOR before the repo goes private again.
  Flipping early kills whatever is created after the flip, and `Regression tests`
  ``needs:`` lint-typecheck-test so it is not created until ~4 minutes in — main
  then looks red for billing reasons rather than code ones.

The branch mode existed only as hand-rolled shell at exactly the step with a
documented trap, and CLAUDE.md's rule is that *a hand walk is not the walk*. One
hand-rolled version reported ``jobs=0`` against a live ``total_count=2`` because
it swallowed a `gh` failure into "empty means zero".

Every guard here is a recorded scar, so none of them is decoration:

* **A job/check-count FLOOR, not "nothing pending".** An empty rollup satisfies
  "no unresolved checks", and the rollup is empty for the first ~30-60s after
  `gh pr create` — so the naive loop exits instantly and renders identically to
  "all green" (wifey #194). The same vacuity bites the branch gate while a
  chained job does not yet exist: the count arrives as 3, then 5.
* **An empty-string conclusion is pending.** A queued check returns `""`, not
  `null`, so a guard written against `null` never fires (#195).
* **`steps == 0` is a billing failure, never a code one.** When the Actions
  allowance is exhausted every job fails in 2-4s having executed nothing, which
  renders exactly like a real test failure. Flip the repo public; never debug it.
* **But `steps == 0` alone does not settle it — the conclusion does.** A job
  GitHub never created settles ``SKIPPED`` with no steps, and that happens two
  ways that are not billing: a job-level ``if:`` filter, and a ``needs:``
  dependency that failed. This repo has the second shape wired —
  ``Regression tests`` declares ``needs: lint-typecheck-test`` — so a single
  timed-out test leaves it ``SKIPPED`` at ``steps=0``. Reading that as billing
  tells the reader to flip a private repo public in order to debug a test
  failure, which is the most expensive possible wrong action. **Billing requires
  a `FAILED` conclusion at zero declared steps**; an exhausted allowance also
  leaves chained jobs skipped, so the discriminator is the failing row, never the
  matrix read through its skips.
* **A pass in seconds needs the same scrutiny as a fail in seconds.** Some checks
  legitimately finish in 7s (`markdownlint`, `frontend-check`) because they sit
  behind a `dorny/paths-filter`. Duration narrows suspicion; only `steps` settles it.
* **`steps` is reported executed/declared, because declared alone reads
  backwards.** A job behind a `dorny/paths-filter` still *declares* every step it
  might run and then skips most of them: measured upstream on the parent's #670, a
  docs-only PR declared 14 steps in `lint-typecheck-test` and executed 5, and a
  bare ``steps=14`` reads as "the heavy leg ran on a docs diff" — the exact
  opposite of what happened, and it contradicts the paths-filter claim in
  `CLAUDE.md`, which is correct. The billing discriminator is unchanged: an
  exhausted allowance declares nothing, so ``steps=0/0`` still settles it, and
  populated-vs-empty remains the test.
* **A `gh` failure raises; it is never turned into data.** Returning `""` on a
  non-zero exit would leave every step count at `None` for an unreadable
  `actions/runs` response, and the tool would print **"all green, all executed
  real steps"** — a false green asserting the one thing it had just failed to
  observe. Transient failures are retried
  inside the poll loop; an unrecoverable one propagates.

Exit codes: ``0`` green and observed · ``1`` genuine failure · ``2`` timeout ·
``3`` billing (FAILED at ``steps=0``) · ``4`` settled green but step counts
unreadable.

Usage:  make wait-ci PR=200  ·  make wait-ci-main
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: S404 - gh plumbing, fixed argv, no shell
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tools.session_digest import REPO_SLUG, owner_env

#: One home for the slug and for the token-or-ambient auth lookup:
#: ``session_digest`` and ``cadence_check`` already share them.
REPO = REPO_SLUG

#: Five checks: lint-typecheck-test, Regression tests, Trivy, markdownlint,
#: frontend-check. `Regression tests` `needs:` lint-typecheck-test and is not
#: CREATED until that finishes, so a floor of 5 is correct but only reachable
#: late — expect 4 first, on both the PR and the main push run. The main push
#: run is exactly these 5; a 6th job seen on a `main` SHA belongs to GitHub's
#: `dynamic` dependency-graph run, which `jobs_for_sha` filters out.
EXPECTED_CHECKS = 5

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TIMEOUT = 2
EXIT_BILLING = 3
EXIT_UNOBSERVED = 4


class GhError(RuntimeError):
    """A `gh` invocation failed. Never convert this into an empty result.

    ``permanent`` marks a 4xx that no retry will fix (403, 404, 401, 422 ...).
    The poll loop re-raises those instead of retrying: the cloud proxy answers
    every GraphQL call with a 403, and a loop that retries it prints "transient"
    dozens of times until killed, never reaching its banner or exit code (parent #893).
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


#: 4xx statuses that ARE worth retrying: request timeout and rate limiting.
_RETRYABLE_4XX = frozenset({408, 429})


def is_permanent_failure(stderr: str) -> bool:
    """Whether `gh`'s stderr names a 4xx that a retry cannot fix.

    `gh` reports API errors as ``HTTP 403: <message>``. A 5xx, a network error
    or anything unparseable stays transient — the conservative default is to
    keep waiting, as before, and only a status that names itself stops the run.
    """
    match = re.search(r"\bHTTP (4\d\d)\b", stderr)
    return match is not None and int(match.group(1)) not in _RETRYABLE_4XX


@dataclass(frozen=True)
class JobRow:
    """One settled check: what it concluded, and whether it executed anything.

    ``steps`` is the DECLARED count and ``executed`` the subset that actually
    ran. Billing gates on ``steps`` (an exhausted allowance declares nothing);
    everything a human reads gates on ``executed``.
    """

    name: str
    conclusion: str
    steps: int | None
    executed: int | None = None


def gh(*args: str) -> str:
    """Run `gh` with the s10023 token. Raises :class:`GhError` on failure.

    ``owner_env`` inherits the environment (a bare env= drops HOME, and `gh` then
    litters `.local/state/gh/` into the CWD), keeps an explicit ``GH_TOKEN``, and
    falls back to ambient auth when the token lookup fails. The lookup does not use
    ``check=True``: in a cloud container s10023 is not in `gh`'s keyring, so
    that would raise a raw ``CalledProcessError`` past the banner and the exit-code
    contract (parent #887).
    """
    out = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=owner_env(),
    )
    if out.returncode != 0:
        raise GhError(
            f"gh {' '.join(args)} failed: {out.stderr.strip()[:200]}",
            permanent=is_permanent_failure(out.stderr),
        )
    return out.stdout


def gh_json(*args: str) -> Any:
    raw = gh(*args)
    if not raw.strip():
        raise GhError(f"gh {' '.join(args)} returned no output")
    return json.loads(raw)


# --------------------------------------------------------------------------
# Pure predicates — the part worth testing without a network.
# --------------------------------------------------------------------------


def pending(checks: Sequence[dict]) -> list[dict]:
    """Checks with no conclusion yet. `""` is pending; only `null` would not be."""
    return [c for c in checks if not (c.get("conclusion") or "")]


def is_settled(total: int, completed: int, floor: int) -> bool:
    """Settled means the FLOOR is reached AND nothing is still running.

    Both halves are load-bearing. Dropping the floor makes an empty result
    vacuously settled; dropping the equality lets a run settle while a job it
    already created is mid-flight.
    """
    return completed >= floor and completed == total


def fmt_steps(row: JobRow) -> str:
    """Render a row's step count as ``executed/declared``.

    Declared alone reads backwards on a paths-filtered job, so it is never
    printed on its own; ``?`` means the count could not be observed at all.
    """
    if row.steps is None:
        return "?"
    if row.executed is None:
        return f"?/{row.steps}"
    return f"{row.executed}/{row.steps}"


def verdict(rows: Sequence[JobRow]) -> tuple[int, list[str]]:
    """Exit code plus printable lines for a settled set of checks."""
    out: list[str] = ["  (steps are EXECUTED/DECLARED; a filtered job skips its body)"]
    billing = failed = unknown = skipped = cancelled = 0
    for row in sorted(rows, key=lambda r: r.name):
        flag = ""
        conclusion = row.conclusion.upper()
        # A SKIPPED job was never created, so it declares nothing. That is NOT
        # billing: a `needs:` dependency failing produces exactly this row, and
        # an exhausted allowance ALSO leaves chained jobs skipped -- so the skip
        # never settles the matrix in either direction. The FAILED row does.
        if conclusion == "SKIPPED":
            skipped += 1
            flag = "  <-- SKIPPED, which is NOT billing (the job was never created)"
        # A cancelled job can execute zero steps exactly as a skip can, and reading it
        # as billing would tell the reader to flip the repo public (parent #880).
        elif conclusion == "CANCELLED":
            cancelled += 1
            flag = "  <-- CANCELLED, which is NOT billing"
        elif row.steps == 0:
            billing += 1
            flag = "  <-- steps=0: BILLING, flip the repo public; do NOT debug"
        elif conclusion != "SUCCESS":
            failed += 1
            flag = "  <-- real failure"
        elif row.steps is None:
            unknown += 1
            flag = "  <-- step count unreadable; execution NOT confirmed"
        out.append(f"  {row.name:<26} {row.conclusion:<8} steps={fmt_steps(row)}{flag}")

    if billing:
        out.append(
            f"\n{billing} check(s) never ran (steps=0). "
            "This is the Actions allowance, not your code."
        )
        return EXIT_BILLING, out
    if failed:
        out.append(f"\n{failed} check(s) genuinely failed.")
        if skipped:
            # Deliberately does not assert WHICH cause. A failed `needs:` and a
            # job-level `if:` filter produce an identical row, and this banner
            # cannot tell them apart -- naming one would be a guess the reader
            # would then carry. The advice is the same either way.
            out.append(
                f"{skipped} further check(s) read SKIPPED and declared nothing "
                "— fix the failure, not the skip."
            )
        return EXIT_FAILED, out
    if cancelled:
        out.append(
            f"\n{cancelled} check(s) were CANCELLED, which is NOT billing: a newer "
            "push in the same concurrency group, or a manual cancel. Re-run the "
            "workflow, or gate on the newer commit."
        )
        return EXIT_FAILED, out
    if unknown:
        out.append(
            f"\nsettled green, but {unknown} step count(s) were unreadable — "
            "execution is NOT confirmed. Re-run before trusting this."
        )
        return EXIT_UNOBSERVED, out
    declared = sum(r.steps or 0 for r in rows)
    executed = sum(r.executed or 0 for r in rows)
    out.append(
        f"\nall green, all executed real steps — {executed}/{declared} declared "
        "steps ran (the rest were skipped by a paths filter)."
    )
    return EXIT_OK, out


# --------------------------------------------------------------------------
# Network reads
# --------------------------------------------------------------------------


def pr_head_sha(pr: str) -> str:
    """A PR's head SHA over REST.

    **Never `gh pr view --json`** — that is GraphQL, and the cloud-session
    proxy refuses GraphQL outright with a 403 (parent #893). REST works on every
    host.
    """
    return str(gh_json("api", f"repos/{REPO}/pulls/{pr}")["head"]["sha"])


def check_runs(sha: str) -> list[dict]:
    """Check runs on one SHA over REST, normalised to ``{name, conclusion}``.

    REST reports a pending run's conclusion as ``null`` and keeps ``status``
    separately; a run not yet ``completed`` is mapped to ``""`` so
    :func:`pending` reads it exactly as it read the GraphQL rollup.
    """
    data = gh_json("api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    return [
        {
            "name": c.get("name", "?"),
            "conclusion": (c.get("conclusion") or "")
            if c.get("status") == "completed"
            else "",
        }
        for c in data.get("check_runs") or []
    ]


def branch_head_sha(branch: str) -> str:
    """Resolve a branch to its head SHA **on the remote**.

    Asked of GitHub rather than of `git rev-parse`, because this gate exists to
    describe what the remote is running; a stale local ref would gate the wrong
    commit. Removing the caller's obligation to pass a SHA is the one ergonomic
    gap between this and the scratch script it replaces, which took `sys.argv[1]`
    and died with a raw `IndexError` when invoked bare.
    """
    return str(gh_json("api", f"repos/{REPO}/commits/{branch}")["sha"])


def jobs_for_sha(sha: str, events: tuple[str, ...] | None = None) -> list[dict]:
    """Actions jobs for one SHA, optionally restricted to given trigger events.

    **The branch gate must filter to `push`.** A SHA on `main` also carries a
    GitHub-managed `dynamic` run — "Configured Graph Update: pip in /." — created
    several minutes after the push runs finish. Counting its `update-pip-graph`
    job makes the flip-back gate wait on dependency-graph submission, which it has
    no interest in, and can push it past the timeout. Found by running the new
    mode against a real merged SHA, which reported `jobs=6` where every document
    here says 5; reading the code would not have shown it.
    """
    jobs: list[dict] = []
    for run in runs_for_sha(sha, events):
        jobs += gh_json("api", f"repos/{REPO}/actions/runs/{run['id']}/jobs")["jobs"]
    return jobs


def runs_for_sha(sha: str, events: tuple[str, ...] | None = None) -> list[dict]:
    """Workflow runs for one SHA, optionally restricted to given trigger events."""
    runs = gh_json("api", f"repos/{REPO}/actions/runs?head_sha={sha}")["workflow_runs"]
    if events is not None:
        runs = [r for r in runs if r.get("event") in events]
    return list(runs)


def was_cancelled(jobs: Sequence[dict], runs: Callable[[], list[dict]]) -> bool:
    """Whether the gated SHA's push run was cancelled, at job or run level.

    **The job-level test alone misses the common case.** A concurrency group
    holds one running and one pending run; a newer push cancels the pending one
    before it creates any jobs, so the cancel shows only on the run (measured
    upstream, parent #880: CI run `cancelled` with zero jobs, beside a green Trivy
    job). With no cancelled job to see, the gate sat under its floor until the
    timeout. ``runs`` is called only when no job shows the cancel.
    """
    done = [j for j in jobs if j.get("status") == "completed"]
    if any((j.get("conclusion") or "").lower() == "cancelled" for j in done):
        return True
    return any((r.get("conclusion") or "").lower() == "cancelled" for r in runs())


def step_counts(job: dict) -> tuple[int, int]:
    """Return ``(declared, executed)`` step counts for one job payload.

    A step GitHub reports as ``skipped`` was declared but never ran, which is
    the whole distinction: a paths-filtered job declares its full step list on
    every diff and skips the body on most of them.
    """
    steps = job.get("steps") or []
    executed = sum(1 for s in steps if (s.get("conclusion") or "") != "skipped")
    return len(steps), executed


def job_step_counts(sha: str) -> dict[str, tuple[int, int]]:
    """Map job name -> ``(declared, executed)`` for every run on this SHA."""
    return {j["name"]: step_counts(j) for j in jobs_for_sha(sha)}


def poll(probe: Callable[[], bool], deadline: float, poll_sec: int, label: str) -> bool:
    """Call `probe` until it returns True or the deadline passes.

    A transient `GhError` is reported and retried — a network blip must not kill
    a twenty-minute wait — but it is never silently treated as "nothing found",
    which is the failure this tool exists to make impossible. A PERMANENT one
    (a non-retryable 4xx) propagates at once to `main`'s failure banner.
    """
    while time.time() < deadline:
        try:
            if probe():
                return True
        except GhError as exc:
            if exc.permanent:
                raise
            print(f"  {label}: transient gh failure, retrying — {exc}", flush=True)
        time.sleep(poll_sec)
    return False


def wait_pr(pr: str, floor: int, deadline: float, poll_sec: int) -> int:
    checks: list[dict] = []
    sha = ""

    def probe() -> bool:
        nonlocal checks, sha
        # Re-read the head every poll, so a push mid-wait gates the new commit.
        sha = pr_head_sha(pr)
        checks = check_runs(sha)
        waiting = pending(checks)
        print(f"  checks={len(checks)} pending={len(waiting)}", flush=True)
        return is_settled(len(checks), len(checks) - len(waiting), floor)

    if not poll(probe, deadline, poll_sec, "pr"):
        print("TIMEOUT waiting for PR checks", file=sys.stderr)
        return EXIT_TIMEOUT

    counts = job_step_counts(sha)
    rows = []
    for c in checks:
        name = c.get("name", "?")
        declared, executed = counts.get(name, (None, None))
        rows.append(JobRow(name, c.get("conclusion", "?"), declared, executed))
    print(f"\nsettled — {len(rows)} checks on {sha[:8]}")
    code, lines = verdict(rows)
    print("\n".join(lines))
    return code


def wait_branch(branch: str, floor: int, deadline: float, poll_sec: int) -> int:
    sha = branch_head_sha(branch)
    while True:
        code, newer = _gate_sha(branch, sha, floor, deadline, poll_sec)
        if newer is None:
            return code
        print(
            f"\nSUPERSEDED — the run on {sha[:8]} was cancelled because {branch} "
            f"moved to {newer[:8]}. That is not a failure; gating on {newer[:8]} "
            "instead.",
            flush=True,
        )
        sha = newer


def _gate_sha(
    branch: str, sha: str, floor: int, deadline: float, poll_sec: int
) -> tuple[int, str | None]:
    """Gate one SHA's push run. Returns ``(code, None)``, or ``(_, newer_sha)``.

    The second form means a cancel was seen (job or run level, `was_cancelled`)
    AND the branch head has moved, so the caller should gate on the newer SHA.
    The head is re-read only once a cancel appears.

    A cancel with the head UNMOVED that left the run short of the floor (a
    pending run cancelled by hand never creates its jobs) ends the gate as
    CANCELLED at once rather than waiting out the deadline for jobs that will
    never exist.
    """
    print(f"gating {branch} @ {sha[:8]} on >={floor} completed jobs", flush=True)
    jobs: list[dict] = []
    superseded_by: str | None = None
    cancelled_short = False

    def probe() -> bool:
        nonlocal jobs, superseded_by, cancelled_short
        jobs = jobs_for_sha(sha, events=("push",))
        done = [j for j in jobs if j.get("status") == "completed"]
        print(f"  jobs={len(jobs)} completed={len(done)}", flush=True)
        settled = is_settled(len(jobs), len(done), floor)

        def fetch_runs() -> list[dict]:
            # A settled gate needs no run listing: its jobs already say it all.
            return [] if settled else runs_for_sha(sha, events=("push",))

        if was_cancelled(jobs, fetch_runs):
            head = branch_head_sha(branch)
            if head != sha:
                superseded_by = head
                return True
            if not settled and len(done) == len(jobs):
                cancelled_short = True
                return True
        return settled

    if not poll(probe, deadline, poll_sec, branch):
        print(f"TIMEOUT waiting for {branch}'s push run", file=sys.stderr)
        return EXIT_TIMEOUT, None
    if superseded_by is not None:
        return EXIT_FAILED, superseded_by
    if cancelled_short:
        print(
            f"\nCANCELLED — a push run on {sha[:8]} was cancelled with {branch} "
            f"still at {sha[:8]}, before reaching {floor} jobs. NOT billing and "
            "not superseded: a manual cancel. Re-run the workflow.",
        )
        return EXIT_FAILED, None

    rows = []
    for j in jobs:
        declared, executed = step_counts(j)
        rows.append(JobRow(j["name"], j.get("conclusion") or "?", declared, executed))
    print(f"\nsettled — {len(rows)} jobs on {sha[:8]}")
    code, lines = verdict(rows)
    print("\n".join(lines))
    if code == EXIT_OK:
        print("safe to flip the repo back to private.")
    return code, None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="wait_ci",
        description="Wait for CI to settle and report whether it actually ran.",
    )
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", help="wait for this PR's checks")
    target.add_argument(
        "--branch", help="wait for this branch's push run (flip-back gate)"
    )
    ap.add_argument(
        "--min-jobs",
        type=int,
        default=EXPECTED_CHECKS,
        help=f"job/check-count floor (default {EXPECTED_CHECKS})",
    )
    ap.add_argument("--timeout-min", type=int, default=20)
    ap.add_argument("--poll-sec", type=int, default=20)
    args = ap.parse_args(argv)

    deadline = time.time() + args.timeout_min * 60
    try:
        if args.pr:
            return wait_pr(args.pr, args.min_jobs, deadline, args.poll_sec)
        return wait_branch(args.branch, args.min_jobs, deadline, args.poll_sec)
    except GhError as exc:
        print(f"gh failed unrecoverably: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
