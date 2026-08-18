"""Tests for the CI waiter — the first this tool has had.

Each leg ships a **positive control**, and three of them exist because the guard
they cover was absent or wrong in a shipped version:

* `is_settled` is checked against the *vacuous* input (nothing found), because
  "no check is unresolved" is trivially true of an empty rollup and that is what
  made a naive loop exit instantly and read as all-green.
* `gh` is asserted to **raise**. The previous implementation returned `""` on a
  non-zero exit, which is how a hand-rolled waiter reported `jobs=0` against a
  live `total_count=2`.
* `verdict` is checked on unreadable step counts, because the old code printed
  "all green, all executed real steps" in exactly that case — asserting the one
  thing it had failed to observe.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from tools.wait_ci import (
    EXIT_BILLING,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_UNOBSERVED,
    GhError,
    JobRow,
    gh,
    gh_json,
    is_settled,
    jobs_for_sha,
    main,
    pending,
    poll,
    verdict,
    wait_branch,
)


class TestIsSettled:
    def test_empty_result_is_not_settled(self) -> None:
        """The vacuity trap: nothing found must never read as "all done"."""
        assert not is_settled(total=0, completed=0, floor=5)

    def test_below_the_floor_is_not_settled(self) -> None:
        """The real 2026-08-18 state: 4 jobs exist, 3 done, the 5th uncreated."""
        assert not is_settled(total=4, completed=3, floor=5)

    def test_floor_reached_but_one_still_running(self) -> None:
        assert not is_settled(total=6, completed=5, floor=5)

    def test_floor_reached_and_all_complete(self) -> None:
        assert is_settled(total=5, completed=5, floor=5)


class TestPending:
    def test_empty_string_conclusion_is_pending(self) -> None:
        """A queued check returns `""`, not `null`."""
        assert pending([{"name": "a", "conclusion": ""}]) != []

    def test_missing_conclusion_is_pending(self) -> None:
        assert pending([{"name": "a"}]) != []

    def test_concluded_check_is_not_pending(self) -> None:
        assert pending([{"name": "a", "conclusion": "SUCCESS"}]) == []


class TestVerdict:
    def test_all_green_with_real_steps(self) -> None:
        rows = [JobRow("lint", "SUCCESS", 16), JobRow("md", "SUCCESS", 8)]
        code, lines = verdict(rows)
        assert code == EXIT_OK
        assert "all executed real steps" in "\n".join(lines)

    def test_zero_steps_is_billing_not_failure(self) -> None:
        rows = [JobRow("lint", "FAILURE", 0)]
        code, lines = verdict(rows)
        assert code == EXIT_BILLING
        assert "BILLING" in "\n".join(lines)

    def test_billing_outranks_a_genuine_failure(self) -> None:
        """A billing-dead matrix must not be reported as a code failure."""
        rows = [JobRow("a", "FAILURE", 0), JobRow("b", "FAILURE", 12)]
        assert verdict(rows)[0] == EXIT_BILLING

    def test_genuine_failure(self) -> None:
        rows = [JobRow("a", "SUCCESS", 9), JobRow("b", "FAILURE", 12)]
        assert verdict(rows)[0] == EXIT_FAILED

    def test_unreadable_steps_is_not_green(self) -> None:
        """The false green this rewrite removes."""
        rows = [JobRow("a", "SUCCESS", None)]
        code, lines = verdict(rows)
        assert code == EXIT_UNOBSERVED
        assert "NOT confirmed" in "\n".join(lines)

    def test_one_unreadable_among_green_still_flags(self) -> None:
        rows = [JobRow("a", "SUCCESS", 9), JobRow("b", "SUCCESS", None)]
        assert verdict(rows)[0] == EXIT_UNOBSERVED


class TestGhRaises:
    def test_non_zero_exit_raises_rather_than_returning_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        def fake_run(argv: Any, **kw: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:  # the token fetch
                return subprocess.CompletedProcess(argv, 0, stdout="tok\n", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GhError, match="boom"):
            gh("api", "whatever")

    def test_empty_output_is_an_error_not_an_empty_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tools.wait_ci.gh", lambda *a: "   ")
        with pytest.raises(GhError):
            gh_json("api", "whatever")


class TestPoll:
    def test_retries_a_transient_failure_then_succeeds(self) -> None:
        seq: list[GhError | bool] = [GhError("blip"), True]

        def probe() -> bool:
            item = seq.pop(0)
            if isinstance(item, GhError):
                raise item
            return item

        assert poll(probe, deadline=_soon(), poll_sec=0, label="t")

    def test_returns_false_when_the_deadline_passes(self) -> None:
        assert not poll(lambda: False, deadline=0.0, poll_sec=0, label="t")

    def test_a_persistent_failure_does_not_read_as_settled(self) -> None:
        """A blip must be retried; it must never resolve as success."""

        def probe() -> bool:
            raise GhError("down")

        assert not poll(probe, deadline=0.0, poll_sec=0, label="t")


def _soon() -> float:
    import time

    return time.time() + 60


class TestJobsForSha:
    """The event filter, found by running the tool rather than by reading it."""

    RUNS = {
        "workflow_runs": [
            {"id": 1, "event": "push", "name": "CI"},
            {"id": 2, "event": "push", "name": "security-scan"},
            {"id": 3, "event": "dynamic", "name": "Configured Graph Update: pip in /."},
        ]
    }
    JOBS = {
        1: {"jobs": [{"name": "lint-typecheck-test"}, {"name": "Regression tests"}]},
        2: {"jobs": [{"name": "Trivy filesystem scan"}]},
        3: {"jobs": [{"name": "update-pip-graph"}]},
    }

    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(*args: str) -> Any:
            if "actions/runs?head_sha=" in args[-1]:
                return self.RUNS
            run_id = int(args[-1].split("/runs/")[1].split("/jobs")[0])
            return self.JOBS[run_id]

        monkeypatch.setattr("tools.wait_ci.gh_json", fake)

    def test_unfiltered_includes_the_dependency_graph_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Characterizes why the filter is needed: 6 where every doc says 5."""
        self._patch(monkeypatch)
        names = [j["name"] for j in jobs_for_sha("sha")]
        assert "update-pip-graph" in names

    def test_push_filter_excludes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch)
        names = [j["name"] for j in jobs_for_sha("sha", events=("push",))]
        assert "update-pip-graph" not in names
        assert len(names) == 3


class TestWaitBranch:
    def _jobs(self, n: int, completed: int) -> list[dict]:
        return [
            {
                "name": f"job{i}",
                "status": "completed" if i < completed else "in_progress",
                "conclusion": "success" if i < completed else None,
                "steps": [{}] * 9,
            }
            for i in range(n)
        ]

    def test_refuses_to_exit_below_the_floor_then_settles(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The documented 4-then-5 arrival, which is the whole point of the floor."""
        states = [self._jobs(4, 3), self._jobs(5, 5)]
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: "a" * 40)
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: states.pop(0))

        assert wait_branch("main", 5, _soon(), 0) == EXIT_OK
        out = capsys.readouterr().out
        assert "jobs=4 completed=3" in out
        assert "safe to flip the repo back to private." in out

    def test_billing_run_is_reported_as_billing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        jobs = [
            {
                "name": f"j{i}",
                "status": "completed",
                "conclusion": "failure",
                "steps": [],
            }
            for i in range(5)
        ]
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: "b" * 40)
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: jobs)
        assert wait_branch("main", 5, _soon(), 0) == EXIT_BILLING

    def test_timeout_when_the_floor_is_never_reached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: "c" * 40)
        monkeypatch.setattr(
            "tools.wait_ci.jobs_for_sha", lambda s, **k: self._jobs(4, 4)
        )
        assert wait_branch("main", 5, 0.0, 0) == EXIT_TIMEOUT


class TestCli:
    def test_pr_and_branch_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            main(["--pr", "1", "--branch", "main"])

    def test_a_target_is_required(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_branch_mode_defaults_to_the_five_job_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, int] = {}

        def fake(branch: str, floor: int, deadline: float, poll_sec: int) -> int:
            seen["floor"] = floor
            return EXIT_OK

        monkeypatch.setattr("tools.wait_ci.wait_branch", fake)
        assert main(["--branch", "main"]) == EXIT_OK
        assert seen["floor"] == 5

    def test_unrecoverable_gh_failure_does_not_report_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: Any, **k: Any) -> int:
            raise GhError("token expired")

        monkeypatch.setattr("tools.wait_ci.wait_branch", boom)
        assert main(["--branch", "main"]) == EXIT_FAILED
