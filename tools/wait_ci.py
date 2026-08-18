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
  "no unresolved checks", and the rollup IS empty for the first ~30-60s after
  `gh pr create` — so the naive loop exits instantly and renders identically to
  "all green" (wifey #194). The same vacuity bites the branch gate while a
  chained job does not yet exist: the count arrives as 3, then 5.
* **An empty-string conclusion is PENDING.** A queued check returns `""`, not
  `null`, so a guard written against `null` never fires (#195).
* **`steps == 0` is a BILLING failure, never a code one.** When the Actions
  allowance is exhausted every job fails in 2-4s having executed nothing, which
  renders exactly like a real test failure. Flip the repo public; never debug it.
* **A PASS in seconds needs the same scrutiny as a FAIL in seconds.** Some checks
  legitimately finish in 7s (`markdownlint`, `frontend-check`) because they sit
  behind a `dorny/paths-filter`. Duration narrows suspicion; only `steps` settles it.
* ⚠ **A `gh` failure RAISES; it is never turned into data.** This is the fix that
  motivated the rewrite. The previous `gh()` returned `""` on a non-zero exit, so
  an unreadable `actions/runs` response left every step count at `None` and the
  tool printed **"all green, all executed real steps"** — a false green asserting
  the one thing it had just failed to observe. Transient failures are retried
  inside the poll loop; an unrecoverable one propagates.

Exit codes: ``0`` green and observed · ``1`` genuine failure · ``2`` timeout ·
``3`` billing (``steps=0``) · ``4`` settled green but step counts unreadable.

Usage:  make wait-ci PR=200  ·  make wait-ci-main
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404 - gh plumbing, fixed argv, no shell
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

REPO = "s10023/buibui-wifey-wall-street-bot"

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
    """A `gh` invocation failed. NEVER convert this into an empty result."""


@dataclass(frozen=True)
class JobRow:
    """One settled check: what it concluded, and whether it executed anything."""

    name: str
    conclusion: str
    steps: int | None


def gh(*args: str) -> str:
    """Run `gh` with the s10023 token. Raises :class:`GhError` on failure."""
    token = subprocess.run(
        ["gh", "auth", "token", "--user", "s10023"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # INHERIT the environment and override only GH_TOKEN. A bare env= drops HOME,
    # and `gh` then falls back to writing its state relative to the CWD — which
    # littered an untracked `.local/state/gh/` into the repo root on first run.
    out = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    if out.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed: {out.stderr.strip()[:200]}")
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


def verdict(rows: Sequence[JobRow]) -> tuple[int, list[str]]:
    """Exit code plus printable lines for a settled set of checks."""
    out: list[str] = []
    billing = failed = unknown = 0
    for row in sorted(rows, key=lambda r: r.name):
        flag = ""
        if row.steps == 0:
            billing += 1
            flag = "  <-- steps=0: BILLING, flip the repo public; do NOT debug"
        elif row.conclusion.upper() != "SUCCESS":
            failed += 1
            flag = "  <-- real failure"
        elif row.steps is None:
            unknown += 1
            flag = "  <-- step count unreadable; execution NOT confirmed"
        shown = "?" if row.steps is None else row.steps
        out.append(f"  {row.name:<26} {row.conclusion:<8} steps={shown}{flag}")

    if billing:
        out.append(
            f"\n{billing} check(s) never ran (steps=0). "
            "This is the Actions allowance, not your code."
        )
        return EXIT_BILLING, out
    if failed:
        out.append(f"\n{failed} check(s) genuinely failed.")
        return EXIT_FAILED, out
    if unknown:
        out.append(
            f"\nsettled green, but {unknown} step count(s) were unreadable — "
            "execution is NOT confirmed. Re-run before trusting this."
        )
        return EXIT_UNOBSERVED, out
    out.append("\nall green, all executed real steps.")
    return EXIT_OK, out


# --------------------------------------------------------------------------
# Network reads
# --------------------------------------------------------------------------


def rollup(pr: str) -> list[dict]:
    data = gh_json("pr", "view", pr, "--repo", REPO, "--json", "statusCheckRollup")
    return data.get("statusCheckRollup") or []


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

    ⚠ **The branch gate MUST filter to `push`.** A SHA on `main` also carries a
    GitHub-managed `dynamic` run — "Configured Graph Update: pip in /." — created
    several minutes AFTER the push runs finish. Counting its `update-pip-graph`
    job makes the flip-back gate wait on dependency-graph submission, which it has
    no interest in, and can push it past the timeout. Found by running the new
    mode against a real merged SHA, which reported `jobs=6` where every document
    here says 5; reading the code would not have shown it.
    """
    runs = gh_json("api", f"repos/{REPO}/actions/runs?head_sha={sha}")["workflow_runs"]
    if events is not None:
        runs = [r for r in runs if r.get("event") in events]
    jobs: list[dict] = []
    for run in runs:
        jobs += gh_json("api", f"repos/{REPO}/actions/runs/{run['id']}/jobs")["jobs"]
    return jobs


def job_steps(sha: str) -> dict[str, int]:
    """Map job name -> step count for every workflow run on this SHA."""
    return {j["name"]: len(j.get("steps") or []) for j in jobs_for_sha(sha)}


def poll(probe: Callable[[], bool], deadline: float, poll_sec: int, label: str) -> bool:
    """Call `probe` until it returns True or the deadline passes.

    A transient `GhError` is reported and retried — a network blip must not kill
    a twenty-minute wait — but it is never silently treated as "nothing found",
    which is the failure this tool exists to make impossible.
    """
    while time.time() < deadline:
        try:
            if probe():
                return True
        except GhError as exc:
            print(f"  {label}: transient gh failure, retrying — {exc}", flush=True)
        time.sleep(poll_sec)
    return False


def wait_pr(pr: str, floor: int, deadline: float, poll_sec: int) -> int:
    checks: list[dict] = []

    def probe() -> bool:
        nonlocal checks
        checks = rollup(pr)
        waiting = pending(checks)
        print(f"  checks={len(checks)} pending={len(waiting)}", flush=True)
        return is_settled(len(checks), len(checks) - len(waiting), floor)

    if not poll(probe, deadline, poll_sec, "pr"):
        print("TIMEOUT waiting for PR checks", file=sys.stderr)
        return EXIT_TIMEOUT

    sha = str(
        gh_json("pr", "view", pr, "--repo", REPO, "--json", "headRefOid")["headRefOid"]
    )
    steps = job_steps(sha)
    rows = [
        JobRow(
            c.get("name", "?"), c.get("conclusion", "?"), steps.get(c.get("name", "?"))
        )
        for c in checks
    ]
    print(f"\nsettled — {len(rows)} checks on {sha[:8]}")
    code, lines = verdict(rows)
    print("\n".join(lines))
    return code


def wait_branch(branch: str, floor: int, deadline: float, poll_sec: int) -> int:
    sha = branch_head_sha(branch)
    print(f"gating {branch} @ {sha[:8]} on >={floor} completed jobs", flush=True)
    jobs: list[dict] = []

    def probe() -> bool:
        nonlocal jobs
        jobs = jobs_for_sha(sha, events=("push",))
        done = [j for j in jobs if j.get("status") == "completed"]
        print(f"  jobs={len(jobs)} completed={len(done)}", flush=True)
        return is_settled(len(jobs), len(done), floor)

    if not poll(probe, deadline, poll_sec, branch):
        print(f"TIMEOUT waiting for {branch}'s push run", file=sys.stderr)
        return EXIT_TIMEOUT

    rows = [
        JobRow(j["name"], j.get("conclusion") or "?", len(j.get("steps") or []))
        for j in jobs
    ]
    print(f"\nsettled — {len(rows)} jobs on {sha[:8]}")
    code, lines = verdict(rows)
    print("\n".join(lines))
    if code == EXIT_OK:
        print("safe to flip the repo back to private.")
    return code


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
