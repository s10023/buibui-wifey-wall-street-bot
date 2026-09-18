r"""The Windows scheduling layer: `load-env.sh`, `job.sh` and `install-tasks.ps1`.

These cover code that only ever EXECUTES on Windows, which is exactly why they are
worth having: the Linux host will never exercise them, so nothing else can tell you the
wrapper stopped preserving an exit code or the installer drifted from the unit files.

⚠ **The shell tests invoke `bash <script>`, never the script directly.** Windows refuses
a shebang'd `.sh` with `OSError: [WinError 193] %1 is not a valid Win32 application`, and
~29 of the parent's 60 Windows test failures are that one mistake. `tests/
test_backup_local_coverage.py` already uses the `bash` form for the same reason.

⚠ **The PowerShell test does not RUN the installer.** Registering a task needs an
elevated shell and would put real jobs on the operator's machine as a side effect of a
test run. It parses the `$Jobs` table instead and diffs it against the systemd units,
which is the claim that actually rots — the installer's own comment says the two "can be
diffed by eye", and this is that diff, mechanised.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools import freshness_check, host_platform
from tools.freshness_check import (
    UNIVERSE_TIMER,
    WINDOWS_TASK_PATH,
    task_name_for_unit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
JOB = REPO_ROOT / "deploy/windows/job.sh"
LOAD_ENV = REPO_ROOT / "deploy/windows/load-env.sh"
INSTALLER = REPO_ROOT / "deploy/windows/install-tasks.ps1"
UNIT_DIR = REPO_ROOT / "deploy/systemd/user"


def _bash(
    script: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a bash snippet from the repo root, with UTF-8 decoding made explicit.

    ⚠ `encoding="utf-8"` is not decoration. Without it `text=True` decodes via the ANSI
    codepage on Windows, the decode raises inside the reader THREAD, and `stdout` comes
    back `None` — surfacing far away as `'NoneType' object has no attribute ...`.
    """
    return subprocess.run(  # noqa: S602 - fixed argv, test-local script text
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env={**os.environ, **(env or {})},
    )


class TestLoadEnv:
    """The `EnvironmentFile=` stand-in. Every case here is silent when it goes wrong."""

    def _load(self, tmp_path: Path, body: bytes, key: str) -> str:
        env_file = tmp_path / "env"
        env_file.write_bytes(body)
        posix = env_file.as_posix()
        res = _bash(
            f'. "{LOAD_ENV.as_posix()}"; load_env "{posix}"; printf "%s" "${key}"'
        )
        assert res.returncode == 0, res.stderr
        return res.stdout

    def test_a_plain_assignment_loads(self, tmp_path: Path) -> None:
        assert self._load(tmp_path, b"FOO=bar\n", "FOO") == "bar"

    def test_CRLF_does_not_leak_a_carriage_return(self, tmp_path: Path) -> None:
        """The measured one, and the reason this file is testable at all.

        A CRLF `.env` is what any Windows editor writes by default. Without the strip,
        `TELEGRAM_BOT_TOKEN=123:abc` loads as EIGHT characters — the token prints
        correctly, Telegram rejects it, and the symptom is "the bot stopped alerting"
        over a config that looks perfect.
        """
        got = self._load(
            tmp_path, b"TELEGRAM_BOT_TOKEN=123:abc\r\n", "TELEGRAM_BOT_TOKEN"
        )
        assert got == "123:abc"
        assert len(got) == 7
        assert "\r" not in got

    def test_a_value_containing_a_space_survives_whole(self, tmp_path: Path) -> None:
        """Positive control for parsing rather than sourcing.

        `. ./.env` would assign only `--transfers` and then try to RUN `4`.
        """
        assert (
            self._load(
                tmp_path, b"WIFEY_RCLONE_FLAGS=--transfers 4\n", "WIFEY_RCLONE_FLAGS"
            )
            == "--transfers 4"
        )

    def test_one_layer_of_quotes_is_stripped(self, tmp_path: Path) -> None:
        assert self._load(tmp_path, b'FOO="bar baz"\n', "FOO") == "bar baz"

    def test_comments_and_blanks_are_skipped(self, tmp_path: Path) -> None:
        assert self._load(tmp_path, b"# note\n\nFOO=bar\n", "FOO") == "bar"

    def test_a_malformed_key_is_not_exported(self, tmp_path: Path) -> None:
        """A key with shell-unsafe characters is a malformed line, not a variable."""
        assert self._load(tmp_path, b"not a key=value\nFOO=bar\n", "FOO") == "bar"

    def test_a_missing_file_is_not_an_error(self) -> None:
        """`EnvironmentFile=-` means exactly this, and every unit uses the `-` form."""
        res = _bash(f'. "{LOAD_ENV.as_posix()}"; load_env /nonexistent/env; echo ok')
        assert res.returncode == 0 and "ok" in res.stdout


class TestJobWrapper:
    """The unit file's imperative half."""

    def _run(
        self, tmp_path: Path, command: str, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """⚠ TWO independent brakes on the notifier, deliberately.

        The first version relied on `WIFEY_NOTIFY=""` alone. `job.sh` read it with
        `${VAR:-default}`, which substitutes on EMPTY as well as unset, so every failing
        test resolved to the real notifier and sent live Telegram messages to the
        operator. One mechanism was one typo away from doing that again, so the value is
        now also a path that does not exist — either brake alone stops it.
        """
        env = {
            "WIFEY_LOG_DIR": tmp_path.as_posix(),
            "WIFEY_NOTIFY": (tmp_path / "no-such-notifier").as_posix(),
        }
        env.update(extra or {})
        return _bash(f'bash "{JOB.as_posix()}" testjob -- {command}', env=env)

    def test_a_successful_command_exits_zero(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, "true").returncode == 0

    def test_the_wrapped_commands_exit_code_is_PRESERVED(self, tmp_path: Path) -> None:
        """Load-bearing: Task Scheduler stores this, and it is the only verdict the
        scheduler keeps. Collapsing it to 0/1 would make every failure look alike."""
        assert self._run(tmp_path, "sh -c 'exit 7'").returncode == 7

    def test_output_reaches_the_logfile(self, tmp_path: Path) -> None:
        """Windows has no journal and a task's stdout goes NOWHERE, so this file is the
        only record that a run happened at all."""
        self._run(tmp_path, "sh -c 'echo marker-xyz'")
        assert "marker-xyz" in (tmp_path / "testjob.log").read_text(encoding="utf-8")

    def test_the_log_is_CAPPED(self, tmp_path: Path) -> None:
        """The README promises nothing grows unmanaged on the laptop; on this host the
        scheduler will not keep that promise for us."""
        self._run(tmp_path, "sh -c 'seq 1 500'", extra={"WIFEY_LOG_MAX_LINES": "40"})
        assert (
            len((tmp_path / "testjob.log").read_text(encoding="utf-8").splitlines())
            <= 40
        )

    def test_PYTHONUTF8_is_exported_to_the_child(self, tmp_path: Path) -> None:
        """Measured 0-vs-41 suite failures on this host; a job that hits the codepage
        without it dies outright, and no job here pings a healthcheck."""
        self._run(tmp_path, "sh -c 'echo utf8=$PYTHONUTF8'")
        assert "utf8=1" in (tmp_path / "testjob.log").read_text(encoding="utf-8")

    def test_it_runs_from_the_REPO_ROOT_whatever_the_cwd(self, tmp_path: Path) -> None:
        """An off-by-one in the `cd` runs the whole job somewhere else and still reports
        success, because every command it wraps is happy to find nothing to do."""
        self._run(tmp_path, "sh -c 'ls pyproject.toml'")
        assert "pyproject.toml" in (tmp_path / "testjob.log").read_text(
            encoding="utf-8"
        )

    def test_the_notifier_fires_ON_FAILURE(self, tmp_path: Path) -> None:
        stub = tmp_path / "notify.sh"
        stub.write_text('#!/usr/bin/env bash\necho "NOTIFIED $1"\n', encoding="utf-8")
        stub.chmod(0o755)
        self._run(tmp_path, "sh -c 'exit 3'", extra={"WIFEY_NOTIFY": stub.as_posix()})
        assert "NOTIFIED testjob" in (tmp_path / "testjob.log").read_text(
            encoding="utf-8"
        )

    def test_the_notifier_does_NOT_fire_on_success(self, tmp_path: Path) -> None:
        """The control that makes the test above mean something: without it, a notifier
        that fired unconditionally would pass the failure case too."""
        stub = tmp_path / "notify.sh"
        stub.write_text('#!/usr/bin/env bash\necho "NOTIFIED $1"\n', encoding="utf-8")
        stub.chmod(0o755)
        self._run(tmp_path, "true", extra={"WIFEY_NOTIFY": stub.as_posix()})
        assert "NOTIFIED" not in (tmp_path / "testjob.log").read_text(encoding="utf-8")

    def test_an_EMPTY_notifier_disables_it_rather_than_defaulting(
        self, tmp_path: Path
    ) -> None:
        """Regression control for a live incident.

        `job.sh` used `${WIFEY_NOTIFY:-deploy/notify-failure.sh}`. The `:` form
        substitutes on EMPTY as well as unset, so `WIFEY_NOTIFY=""` — the obvious way to
        say "do not notify" — resolved to the REAL notifier and sent Telegram messages
        to the operator's channel during this file's own test run.

        Asserted by side effect rather than by reading the source: the stub writes a
        marker, and an empty value must leave no marker behind.
        """
        marker = tmp_path / "fired"
        stub = tmp_path / "notify.sh"
        stub.write_text(
            f'#!/usr/bin/env bash\ntouch "{marker.as_posix()}"\n', encoding="utf-8"
        )
        stub.chmod(0o755)

        # Control first: the stub DOES fire when named, so the assertion below is not
        # vacuous.
        self._run(tmp_path, "sh -c 'exit 3'", extra={"WIFEY_NOTIFY": stub.as_posix()})
        assert marker.exists(), "control failed: the stub never fired"
        marker.unlink()

        self._run(tmp_path, "sh -c 'exit 3'", extra={"WIFEY_NOTIFY": ""})
        assert not marker.exists(), "an EMPTY WIFEY_NOTIFY still invoked a notifier"

    def test_a_failing_notifier_does_not_change_the_verdict(
        self, tmp_path: Path
    ) -> None:
        """A Telegram outage must not turn a failed backup into a differently-failed
        task, nor a successful one into a failure."""
        stub = tmp_path / "notify.sh"
        stub.write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
        stub.chmod(0o755)
        res = self._run(
            tmp_path, "sh -c 'exit 3'", extra={"WIFEY_NOTIFY": stub.as_posix()}
        )
        assert res.returncode == 3


@dataclass(frozen=True)
class _Job:
    """One `$Jobs` row, typed so an assertion cannot silently compare `object`s."""

    utc: list[str]
    days: list[str]
    body: str


def _installer_jobs() -> dict[str, _Job]:
    """Parse the `$Jobs` table out of the installer.

    Text-parsed rather than executed: running it needs an elevated shell and would
    register real tasks on the operator's machine as a side effect of `make test`.
    """
    src = INSTALLER.read_text(encoding="utf-8")
    block = re.search(r"\$Jobs = @\((.*?)\n\)\n", src, re.DOTALL)
    assert block, "could not locate the $Jobs table"

    jobs: dict[str, _Job] = {}
    for chunk in re.split(r"@\{", block.group(1))[1:]:
        name = re.search(r"Name\s*=\s*'([^']+)'", chunk)
        assert name, chunk[:120]
        times = re.findall(r"'(\d{2}:\d{2})'", chunk)
        days = re.findall(
            r"'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)'", chunk
        )
        jobs[name.group(1)] = _Job(utc=times, days=days, body=chunk)
    return jobs


def _installer_code() -> str:
    """The installer with comments removed, so a rule can be asserted against what the
    script DOES rather than against what it explains."""
    src = re.sub(r"<#.*?#>", "", INSTALLER.read_text(encoding="utf-8"), flags=re.DOTALL)
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )


def _unit_calendars(unit: str) -> list[str]:
    timer = (UNIT_DIR / f"{unit}.timer").read_text(encoding="utf-8")
    return re.findall(r"^OnCalendar=.*?(\d{2}:\d{2}):\d{2}", timer, re.MULTILINE)


class TestInstallerMirrorsTheUnits:
    """The installer's own comment says the two can be diffed by eye. This is that diff.

    A schedule is the kind of value that is right when written and wrong after one edit,
    and the two halves live in different languages in different directories — so nothing
    but a test connects them.
    """

    UNITS = (
        "wifey-signal-watch",
        "wifey-backup",
        "wifey-backup-offsite",
        "wifey-universe-sync",
    )

    def test_every_unit_has_a_job_row(self) -> None:
        assert set(_installer_jobs()) == set(self.UNITS)

    @pytest.mark.parametrize("unit", UNITS)
    def test_the_UTC_times_match_the_timer(self, unit: str) -> None:
        assert sorted(_installer_jobs()[unit].utc) == sorted(_unit_calendars(unit))

    def test_signal_watch_is_weekdays_only(self) -> None:
        """`day_filter` aside, a weekend run has no session to scan."""
        assert _installer_jobs()["wifey-signal-watch"].days == [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
        ]

    def test_universe_sync_is_saturday_only(self) -> None:
        assert _installer_jobs()["wifey-universe-sync"].days == ["Saturday"]

    def test_NO_job_repeats_sub_daily(self) -> None:
        """⚠ The parent's `buibui-signal-watch.timer` fires every 15 minutes against a
        24h crypto tape. wifey has ONE RTH session and fires ONCE a day, and its own
        timer file carries the comment "Do NOT port the parent's OnCalendar=*:01/15".

        Pinned here because the divergence is invisible at the call site: both files
        look like a perfectly ordinary trigger list.

        ⚠ Asserted against CODE, with comments stripped. The first version of this test
        searched the raw file and failed on the installer's own comment explaining the
        parent's `PT0S` bug — a check that cannot tell a declaration from a warning
        about that declaration would force the warning to be deleted to go green, which
        is the opposite of what it is for.
        """
        assert _installer_code().count("Repetition") == 0

    def test_offsite_is_registered_DISABLED(self) -> None:
        """`backup-offsite.sh` runs `rclone sync`, which MIRRORS DELETIONS. Until this
        host has its own remote pinned to its own root_folder_id, a scheduled run could
        mirror an empty local tree over the snapshots it exists to protect."""
        assert "Disabled = $true" in _installer_jobs()["wifey-backup-offsite"].body

    def test_the_probe_and_the_installer_agree_on_the_task_PATH(self) -> None:
        """⚠ A probe looking in the wrong folder returns "not enabled" rather than an
        error, so it is indistinguishable from a box that installed nothing — the
        failure reports the absence it exists to detect. Only a test connects the two,
        because they live in different languages in different directories.
        """
        declared = re.search(r"\$TaskPath\s*=\s*'([^']+)'", _installer_code())
        assert declared, "installer has no -TaskPath default"
        assert declared.group(1) == WINDOWS_TASK_PATH

    def test_the_probe_and_the_installer_agree_on_the_task_NAME(self) -> None:
        """`task_name_for_unit` is the transform; the installer registers under the
        bare name. A suffix mismatch is the same silent "not enabled"."""
        assert task_name_for_unit(UNIVERSE_TIMER) in _installer_jobs()

    @pytest.mark.parametrize(
        "setting",
        [
            "AllowStartIfOnBatteries",
            "DontStopIfGoingOnBatteries",
            "DontStopOnIdleEnd",
            "StartWhenAvailable",
            "ExecutionTimeLimit",
        ],
    )
    def test_the_laptop_killing_defaults_are_set_explicitly(self, setting: str) -> None:
        """Each default is wrong here and none announces itself — the task simply does
        not run, or stops mid-flight, and the stop is recorded as the job's own verdict."""
        assert setting in INSTALLER.read_text(encoding="utf-8")


class TestWindowsTimerProbe:
    """`universe_timer_enabled` on a Windows host.

    ⚠ This leg licenses GRADING ~1,100 series, so a wrong True invents faults and a
    wrong False silences a real cadence. Before this branch the Windows answer was
    always False — correct while the job could not be scheduled here, and wrong the
    moment it could.
    """

    def _probe(
        self, monkeypatch: pytest.MonkeyPatch, state: str, *, rc: int = 0
    ) -> bool:
        monkeypatch.setattr(host_platform, "is_windows", lambda: True)

        def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], rc, state, "")

        monkeypatch.setattr(freshness_check.subprocess, "run", fake_run)
        return freshness_check.universe_timer_enabled()

    @pytest.mark.parametrize("state", ["Ready", "Running"])
    def test_a_live_task_reads_enabled(
        self, state: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._probe(monkeypatch, f"{state}\n") is True

    def test_a_DISABLED_task_reads_not_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existence is not the question. `install-tasks.ps1` registers
        `wifey-backup-offsite` and then disables it deliberately, so a probe keying on
        "the task is there" would grade against a job that never fires."""
        assert self._probe(monkeypatch, "Disabled\n") is False

    def test_an_absent_task_reads_not_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`-ErrorAction SilentlyContinue` makes this empty output, not an error."""
        assert self._probe(monkeypatch, "") is False

    def test_no_powershell_degrades_to_not_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same direction as the systemd branch: report the absence, never invent a
        cadence."""
        monkeypatch.setattr(host_platform, "is_windows", lambda: True)

        def boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
            raise OSError("no powershell")

        monkeypatch.setattr(freshness_check.subprocess, "run", boom)
        assert freshness_check.universe_timer_enabled() is False

    def test_the_POSIX_branch_is_still_taken_off_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: the dispatch must actually branch. Without this, a probe hardcoded
        to the Windows path would pass every test above and break the Linux host."""
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "enabled\n", "")

        monkeypatch.setattr(host_platform, "is_windows", lambda: False)
        monkeypatch.setattr(freshness_check.subprocess, "run", fake_run)
        assert freshness_check.universe_timer_enabled() is True
        assert seen and seen[0][0] == "systemctl"
