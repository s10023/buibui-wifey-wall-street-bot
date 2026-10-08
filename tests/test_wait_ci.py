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
import time
from typing import Any

import pytest

from tools.session_digest import owner_env
from tools.wait_ci import (
    EXIT_BILLING,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_UNOBSERVED,
    GhError,
    JobRow,
    check_runs,
    fmt_steps,
    gh,
    gh_json,
    is_permanent_failure,
    is_settled,
    jobs_for_sha,
    main,
    pending,
    poll,
    step_counts,
    verdict,
    wait_branch,
    wait_pr,
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


class TestSkippedIsNotBilling:
    """`steps=0` is billing only when the job also failed (parent #755).

    A job GitHub never created settles SKIPPED declaring nothing. This repo wires
    that shape directly: `.github/workflows/lint.yaml` gives `Regression tests`
    `needs: lint-typecheck-test`, so one timed-out test leaves it SKIPPED at zero
    steps. Reading that as billing would tell the reader to flip a private repo public
    in order to debug a test failure.

    CLAUDE.md names the discriminator ("a path-filtered skip reports
    SKIPPED, an exhausted allowance reports FAILURE, both at steps=0"), so the
    code must look at the conclusion before branching on steps.
    """

    def test_a_dependency_skip_is_not_billing(self) -> None:
        """The wired shape: one real failure, one job skipped behind it."""
        rows = [
            JobRow("lint-typecheck-test", "FAILURE", 14, 12),
            JobRow("Regression tests", "SKIPPED", 0, 0),
        ]
        code, lines = verdict(rows)
        text = "\n".join(lines)
        assert code == EXIT_FAILED
        assert "BILLING" not in text
        assert "SKIPPED, which is NOT billing" in text
        assert "fix the failure, not the skip" in text

    def test_a_skipped_job_among_green_is_green(self) -> None:
        """A job-level `if:` filter skips the whole job; that is not a failure."""
        rows = [
            JobRow("lint-typecheck-test", "SUCCESS", 14, 5),
            JobRow("Regression tests", "SKIPPED", 0, 0),
        ]
        assert verdict(rows)[0] == EXIT_OK

    def test_a_real_billing_matrix_is_still_caught(self) -> None:
        """The MUTATION case — the one this fix could plausibly have blinded.

        An exhausted allowance ALSO leaves chained jobs SKIPPED, so the fix must
        not read the whole matrix through its skips: the FAILURE row still
        declares zero steps, and that is what settles it.
        """
        rows = [
            JobRow("lint-typecheck-test", "FAILURE", 0, 0),
            JobRow("Regression tests", "SKIPPED", 0, 0),
        ]
        code, lines = verdict(rows)
        assert code == EXIT_BILLING
        assert "BILLING" in "\n".join(lines)

    def test_a_lowercase_conclusion_is_still_a_skip(self) -> None:
        """`gh` returns lowercase, the REST API uppercase — both are one skip."""
        rows = [JobRow("a", "SUCCESS", 9), JobRow("b", "skipped", 0, 0)]
        assert verdict(rows)[0] == EXIT_OK

    def test_a_skip_never_invents_a_failure(self) -> None:
        """A skip alone settles nothing — it is neither billing nor a failure."""
        assert verdict([JobRow("Regression tests", "SKIPPED", 0, 0)])[0] == EXIT_OK


class TestExecutedVersusDeclaredSteps:
    """ST50(f): a paths-filtered job DECLARES every step and skips the body.

    Reporting the declared count alone reads as "the heavy leg ran on a docs
    diff" — the exact opposite of what happened. Measured on #670, where
    `lint-typecheck-test` declared 14 and executed 5.
    """

    def test_skipped_steps_are_declared_but_not_executed(self) -> None:
        job = {
            "steps": [
                {"conclusion": "success"},
                {"conclusion": "success"},
                {"conclusion": "skipped"},
                {"conclusion": "skipped"},
            ]
        }
        assert step_counts(job) == (4, 2)

    def test_a_job_with_no_steps_is_zero_zero(self) -> None:
        """The billing shape: an exhausted allowance declares nothing."""
        assert step_counts({"steps": []}) == (0, 0)
        assert step_counts({}) == (0, 0)

    def test_a_step_missing_its_conclusion_counts_as_executed(self) -> None:
        """Only an explicit `skipped` is a non-execution; absence is not."""
        assert step_counts({"steps": [{}, {"conclusion": None}]}) == (2, 2)

    def test_the_670_shape_does_not_render_as_the_declared_count(self) -> None:
        """The MUTATION case — printing `steps=14` here is the whole defect."""
        row = JobRow("lint-typecheck-test", "SUCCESS", 14, 5)
        assert fmt_steps(row) == "5/14"
        assert "steps=14 " not in f"steps={fmt_steps(row)} "

    def test_a_filtered_job_is_still_green(self) -> None:
        """Skipping a body on an unrelated diff is correct, not a failure."""
        rows = [JobRow("lint-typecheck-test", "SUCCESS", 14, 5)]
        code, lines = verdict(rows)
        assert code == EXIT_OK
        assert "5/14" in "\n".join(lines)

    def test_billing_discriminator_is_untouched(self) -> None:
        """Declared 0 is still BILLING — the fix must not move that test."""
        assert verdict([JobRow("lint", "FAILURE", 0, 0)])[0] == EXIT_BILLING

    def test_unreadable_executed_count_never_prints_a_bare_declared(self) -> None:
        assert fmt_steps(JobRow("a", "SUCCESS", 12, None)) == "?/12"

    def test_unreadable_declared_count_is_a_bare_question_mark(self) -> None:
        assert fmt_steps(JobRow("a", "SUCCESS", None, None)) == "?"

    def test_the_legend_names_the_order(self) -> None:
        """`5/14` is ambiguous without it, and the wrong reading is the defect."""
        _, lines = verdict([JobRow("a", "SUCCESS", 14, 5)])
        assert "EXECUTED/DECLARED" in "\n".join(lines)


class TestGhRaises:
    def test_non_zero_exit_raises_rather_than_returning_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
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

    def test_bare_invocation_works(self) -> None:
        """The documented direct call, run with **no** `PYTHONPATH` (#425).

        Without the `sys.path` bootstrap this died at import with exit 1, the same
        code as a genuine CI failure, so a session read an import error as red CI.
        """
        import sys as _sys
        from pathlib import Path

        from tools.child_env import python_child_env

        repo = Path(__file__).resolve().parent.parent
        proc = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            [_sys.executable, str(repo / "tools" / "wait_ci.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(repo),
            env=python_child_env(drop=("PYTHONPATH",)),
            check=False,
            encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert "--pr" in proc.stdout


class TestGhAuthFallback:
    """Parent #887: a failed token lookup falls back to ambient auth, never raises.

    ``gh`` takes its environment from ``session_digest.owner_env``, the one auth
    helper the digest and ``cadence_check`` already share.
    """

    def test_failed_token_lookup_runs_with_ambient_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        envs: list[Any] = []

        def fake_run(argv: Any, **kw: Any) -> Any:
            if argv[:3] == ["gh", "auth", "token"]:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no")
            envs.append(kw.get("env"))
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gh("api", "whatever") == "ok"
        assert len(envs) == 1 and "GH_TOKEN" not in envs[0]

    def test_missing_gh_binary_on_lookup_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)

        def fake_run(argv: Any, **kw: Any) -> Any:
            raise FileNotFoundError("gh")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert "GH_TOKEN" not in owner_env()

    def test_explicit_gh_token_wins_and_skips_the_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "mine")
        argvs: list[Any] = []

        def fake_run(argv: Any, **kw: Any) -> Any:
            argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        gh("api", "x")
        assert argvs == [["gh", "api", "x"]]

    def test_successful_lookup_sets_the_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "tok\n", ""),
        )
        assert owner_env()["GH_TOKEN"] == "tok"


class TestSupersededRun:
    """Parent #880: a run cancelled by a newer push on the gated branch is not a failure.

    The parent's observed shape (2026-10-01): the gate pinned one SHA, two merges moved main,
    GitHub cancelled the pinned run while it was still PENDING (so it created no
    jobs at all), and the run on the new head was green. This repo's workflows
    share the same per-ref concurrency groups, so the shape applies here.
    """

    OLD, NEW = "6a55fcf" + "0" * 33, "afba967" + "0" * 33

    @staticmethod
    def _cancelled(n: int, *, steps: int) -> list[dict]:
        return [
            {
                "name": f"job{i}",
                "status": "completed",
                "conclusion": "cancelled",
                "steps": [{}] * steps,
            }
            for i in range(n)
        ]

    @staticmethod
    def _green(n: int) -> list[dict]:
        return [
            {
                "name": f"job{i}",
                "status": "completed",
                "conclusion": "success",
                "steps": [{}] * 9,
            }
            for i in range(n)
        ]

    def test_follows_the_new_head_and_reports_it_green(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        heads = [self.OLD, self.NEW, self.NEW]
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: heads.pop(0))
        by_sha = {self.OLD: self._cancelled(3, steps=0), self.NEW: self._green(5)}
        seen: list[str] = []

        def jobs(sha: str, **_: Any) -> list[dict]:
            seen.append(sha)
            return by_sha[sha]

        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", jobs)

        assert wait_branch("main", 5, _soon(), 0) == EXIT_OK
        out = capsys.readouterr().out
        assert "SUPERSEDED" in out
        assert "afba967" in out
        assert "safe to flip the repo back to private." in out
        assert seen == [self.OLD, self.NEW]

    def test_a_superseded_zero_step_run_is_never_called_billing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A cancelled job can execute nothing; read as billing it says go public."""
        heads = [self.OLD, self.NEW, self.NEW]
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: heads.pop(0))
        by_sha = {self.OLD: self._cancelled(5, steps=0), self.NEW: self._green(5)}
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: by_sha[s])

        assert wait_branch("main", 5, _soon(), 0) == EXIT_OK
        assert "BILLING" not in capsys.readouterr().out

    def test_a_cancel_with_the_head_unmoved_is_cancelled_not_billing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: self.OLD)
        monkeypatch.setattr(
            "tools.wait_ci.jobs_for_sha", lambda s, **k: self._cancelled(5, steps=0)
        )

        assert wait_branch("main", 5, _soon(), 0) == EXIT_FAILED
        out = capsys.readouterr().out
        assert "CANCELLED" in out
        assert "SUPERSEDED" not in out
        assert "BILLING" not in out

    def test_a_run_cancelled_before_creating_jobs_is_followed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CI `cancelled` with ZERO jobs, beside one green Trivy job. No job carries
        the cancel, so only the RUN shows it; a job-level check sat under the floor
        until the timeout."""
        heads = [self.OLD, self.NEW]
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: heads.pop(0))
        by_sha = {self.OLD: self._green(1), self.NEW: self._green(5)}
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: by_sha[s])
        runs = {
            self.OLD: [{"conclusion": "success"}, {"conclusion": "cancelled"}],
            self.NEW: [{"conclusion": None}],
        }
        monkeypatch.setattr("tools.wait_ci.runs_for_sha", lambda s, **k: runs[s])

        assert wait_branch("main", 5, time.time() + 3, 0) == EXIT_OK
        out = capsys.readouterr().out
        assert "SUPERSEDED" in out
        assert "safe to flip the repo back to private." in out

    def test_a_pending_run_cancelled_by_hand_ends_at_once(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Head unmoved, no jobs coming: report CANCELLED, never wait for a timeout."""
        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: self.OLD)
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: self._green(1))
        monkeypatch.setattr(
            "tools.wait_ci.runs_for_sha", lambda s, **k: [{"conclusion": "cancelled"}]
        )

        assert wait_branch("main", 5, time.time() + 3, 0) == EXIT_FAILED
        out = capsys.readouterr().out
        assert "CANCELLED" in out
        assert "SUPERSEDED" not in out
        assert "BILLING" not in out

    def test_a_settled_gate_never_lists_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(s: str, **k: Any) -> list[dict]:
            raise AssertionError("listed runs for a gate whose jobs had settled")

        monkeypatch.setattr("tools.wait_ci.branch_head_sha", lambda b: self.OLD)
        monkeypatch.setattr("tools.wait_ci.jobs_for_sha", lambda s, **k: self._green(5))
        monkeypatch.setattr("tools.wait_ci.runs_for_sha", boom)

        assert wait_branch("main", 5, _soon(), 0) == EXIT_OK

    def test_verdict_does_not_read_a_cancelled_row_as_billing(self) -> None:
        code, lines = verdict([JobRow("lint", "CANCELLED", 0, 0)])
        assert code == EXIT_FAILED
        assert "BILLING" not in "\n".join(lines)


class TestPermanentFailure:
    """Parent #893: a 403 from the cloud proxy was retried as transient forever."""

    GRAPHQL_403 = (
        "HTTP 403: GitHub GraphQL is not available from Claude Code sessions; "
        "use the REST API"
    )

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_a_named_4xx_is_permanent(self, code: int) -> None:
        assert is_permanent_failure(f"HTTP {code}: nope")

    @pytest.mark.parametrize(
        "stderr", ["HTTP 429: slow down", "HTTP 408: timeout", "HTTP 502: bad", "eof"]
    )
    def test_rate_limit_server_and_network_errors_stay_transient(
        self, stderr: str
    ) -> None:
        assert not is_permanent_failure(stderr)

    def test_gh_marks_the_graphql_403_permanent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "x")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 1, "", self.GRAPHQL_403
            ),
        )
        with pytest.raises(GhError) as info:
            gh("pr", "view", "1")
        assert info.value.permanent

    def test_poll_propagates_a_permanent_failure_at_once(self) -> None:
        calls = {"n": 0}

        def probe() -> bool:
            calls["n"] += 1
            raise GhError("HTTP 403", permanent=True)

        with pytest.raises(GhError):
            poll(probe, deadline=_soon(), poll_sec=0, label="t")
        assert calls["n"] == 1

    def test_main_reaches_its_failure_banner(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def denied(pr: str) -> str:
            raise GhError(self.GRAPHQL_403, permanent=True)

        monkeypatch.setattr("tools.wait_ci.pr_head_sha", denied)
        assert main(["--pr", "887", "--poll-sec", "0"]) == EXIT_FAILED
        assert "gh failed unrecoverably" in capsys.readouterr().err


class TestPrPathIsRest:
    """Parent #893: the PR path must never shell out to GraphQL (`gh pr view --json`)."""

    SHA = "d" * 40

    @staticmethod
    def _runs(*rows: tuple[str, str, str | None]) -> dict:
        return {
            "check_runs": [
                {"name": n, "status": st, "conclusion": c} for n, st, c in rows
            ]
        }

    def test_check_runs_maps_unfinished_to_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = self._runs(
            ("a", "completed", "success"),
            ("b", "in_progress", None),
            ("c", "queued", None),
        )
        monkeypatch.setattr("tools.wait_ci.gh_json", lambda *a: payload)
        checks = check_runs(self.SHA)
        assert [c["conclusion"] for c in checks] == ["success", "", ""]
        assert len(pending(checks)) == 2

    def test_wait_pr_settles_over_rest_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        names = ["j1", "j2", "j3", "j4", "j5"]
        states = [
            self._runs(*[(n, "in_progress", None) for n in names[:4]]),
            self._runs(*[(n, "completed", "success") for n in names]),
        ]
        argvs: list[tuple[str, ...]] = []

        def fake(*args: str) -> Any:
            argvs.append(args)
            path = args[1]
            if path.endswith("/pulls/887"):
                return {"head": {"sha": self.SHA}}
            if "/check-runs" in path:
                return states.pop(0) if len(states) > 1 else states[0]
            if "actions/runs?" in path:
                return {"workflow_runs": [{"id": 1, "event": "pull_request"}]}
            if path.endswith("/runs/1/jobs"):
                return {
                    "jobs": [
                        {"name": n, "steps": [{"conclusion": "success"}]} for n in names
                    ]
                }
            raise AssertionError(f"unexpected gh call {args}")

        monkeypatch.setattr("tools.wait_ci.gh_json", fake)
        assert wait_pr("887", 5, _soon(), 0) == EXIT_OK
        assert all(a[0] == "api" for a in argvs), argvs
        assert f"checks on {self.SHA[:8]}" in capsys.readouterr().out
