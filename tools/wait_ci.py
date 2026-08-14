"""Wait for a PR's checks to settle, then report whether they actually RAN.

Replaces ~25 lines of polling prose in the session handoff. Every guard here is a
recorded scar, so none of them is decoration:

* **A check-count FLOOR, not just "nothing pending".** An empty rollup satisfies
  "no unresolved checks", and the rollup IS empty for the first ~30-60s after
  `gh pr create` -- so the naive loop exits instantly and renders identically to
  "all green" (hit on wifey #194).
* **An empty-string conclusion is PENDING.** A queued check returns `""`, not
  `null`, so a guard written against `null` never fires (#195).
* **`steps == 0` is a BILLING failure, never a code one.** When the Actions
  allowance is exhausted every job fails in 2-4s having executed nothing, which
  renders exactly like a real test failure. The fix is to flip the repo public,
  never to debug it.
* **A PASS in seconds needs the same scrutiny as a FAIL in seconds.** Some checks
  legitimately finish in 7s (`markdownlint`, `frontend-check`) because they sit
  behind a `dorny/paths-filter`. Duration narrows suspicion; only `steps` settles it.

Usage:  make wait-ci PR=200
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

REPO = "s10023/buibui-wifey-wall-street-bot"
#: Five checks: lint-typecheck-test, Regression tests, Trivy, markdownlint, frontend-check.
#: `Regression tests` `needs:` lint-typecheck-test and is not CREATED until that
#: finishes, so a floor of 5 is correct but only reachable late -- expect 4 first.
EXPECTED_CHECKS = 5


def gh(*args: str) -> str:
    token = subprocess.run(
        ["gh", "auth", "token", "--user", "s10023"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    out = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        env={"GH_TOKEN": token, "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    if out.returncode != 0:
        return ""
    return out.stdout


def rollup(pr: str) -> list[dict]:
    raw = gh("pr", "view", pr, "--repo", REPO, "--json", "statusCheckRollup")
    if not raw.strip():
        return []
    return json.loads(raw).get("statusCheckRollup") or []


def job_steps(sha: str) -> dict[str, int]:
    """Map job name -> step count for every workflow run on this SHA."""
    raw = gh(
        "api",
        f"repos/{REPO}/actions/runs?head_sha={sha}",
        "--jq",
        ".workflow_runs[].id",
    )
    steps: dict[str, int] = {}
    for run_id in raw.split():
        jr = gh(
            "api",
            f"repos/{REPO}/actions/runs/{run_id}/jobs",
            "--jq",
            '.jobs[] | "\\(.name)\\t\\(.steps|length)"',
        )
        for line in jr.strip().splitlines():
            if "\t" in line:
                name, n = line.rsplit("\t", 1)
                steps[name] = int(n)
    return steps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True)
    ap.add_argument("--timeout-min", type=int, default=20)
    args = ap.parse_args()

    deadline = time.time() + args.timeout_min * 60
    checks: list[dict] = []
    while time.time() < deadline:
        checks = rollup(args.pr)
        pending = [c for c in checks if not (c.get("conclusion") or "")]
        print(f"  checks={len(checks)} pending={len(pending)}", flush=True)
        if len(checks) >= EXPECTED_CHECKS and not pending:
            break
        time.sleep(30)
    else:
        print(f"TIMEOUT after {args.timeout_min}m", file=sys.stderr)
        return 2

    sha = json.loads(gh("pr", "view", args.pr, "--repo", REPO, "--json", "headRefOid"))[
        "headRefOid"
    ]
    steps = job_steps(sha)

    print(f"\nsettled — {len(checks)} checks on {sha[:8]}")
    billing = failed = 0
    for c in sorted(checks, key=lambda c: c.get("name", "")):
        name, concl = c.get("name", "?"), c.get("conclusion", "?")
        n = steps.get(name)
        flag = ""
        if n == 0:
            flag, billing = (
                "  <-- steps=0: BILLING, flip the repo public; do NOT debug",
                billing + 1,
            )
        elif concl != "SUCCESS":
            flag, failed = "  <-- real failure", failed + 1
        print(f"  {name:<26} {concl:<8} steps={n if n is not None else '?'}{flag}")

    if billing:
        print(
            f"\n{billing} check(s) never ran (steps=0). This is the Actions allowance, not your code."
        )
        return 3
    if failed:
        print(f"\n{failed} check(s) genuinely failed.")
        return 1
    print("\nall green, all executed real steps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
