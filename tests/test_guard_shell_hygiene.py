"""Guards for `.claude/hooks/guard-shell-hygiene.py` (ported, parent #743/#744/#753).

⚠ **These live in `tests/`, NOT beside the hook, and that is deliberate.**
`pyproject.toml` sets `testpaths = ["tests"]`, so a test file under `.claude/hooks/`
-- where the parent keeps its copies -- would be collected by NOTHING here. It would
pass review, run never, and read exactly like coverage. That is the same dead-check
class as `missed_ports.py`'s hardcoded paths (#301) and `orphan_test_audit.py`'s
allowlist (#302), both found the same week.

It also diverges from `advise-foreground-run.py`'s `--selftest` convention on
purpose: a self-test only runs when a session remembers to run it, and CLAUDE.md
already names "a self-check outside CI is not a check". Here the suite is the gate.

The hook's filename is hyphenated, so it is not importable; it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "guard-shell-hygiene.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("guard_shell_hygiene", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(command: str, tool_name: str = "Bash", hook: Path = HOOK) -> str:
    """Drive the hook as the harness does: JSON on stdin, JSON or nothing out."""
    payload = {
        "tool_name": tool_name,
        "tool_input": {"command": command},
        # A fresh session id per call, so the once-per-session dedup marker never
        # makes one test's result depend on another's having run first.
        "session_id": f"test-{uuid.uuid4()}",
    }
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert proc.returncode == 0, f"hook must always exit 0, got {proc.returncode}"
    return proc.stdout


class TestEachRuleFires:
    def test_hand_rolled_waiter(self) -> None:
        out = _run("until ! pgrep -f 'pytest tests/'; do sleep 5; done")
        assert "hand-rolled waiter" in out

    def test_gate_piped_into_tail(self) -> None:
        out = _run("make preflight | tail -8")
        assert "exit status is SWALLOWED" in out

    def test_trailing_echo_swallows_the_status(self) -> None:
        out = _run('make preflight > f 2>&1; echo "EXIT=$?"')
        assert "exit status is SWALLOWED" in out

    def test_trailing_tail_swallows_the_status(self) -> None:
        out = _run("make preflight > f 2>&1; rc=$?; tail -20 f")
        assert "exit status is SWALLOWED" in out

    def test_any_always_ok_trailer_counts(self) -> None:
        out = _run("poetry run pytest tests/ -q > f; cat f")
        assert "exit status is SWALLOWED" in out

    def test_the_old_recommended_form_fires(self) -> None:
        """The rule's own advice until parent #779 ended in `tail`, so it exited 0.

        A test here pinned that string as silent -- the defect asserted as correct.
        """
        out = _run(
            'make preflight > /tmp/p.log 2>&1; echo "exit=$?"; tail -8 /tmp/p.log'
        )
        assert "exit status is SWALLOWED" in out

    def test_gh_auth_switch(self) -> None:
        out = _run("gh auth switch --user someone")
        assert "gh auth switch" in out


class TestTheThingsItMustNotSayAnythingAbout:
    """False positives are the whole cost of an advisory hook."""

    def test_gh_auth_token_is_the_sanctioned_reader(self) -> None:
        """The scoped form the rule RECOMMENDS must not trip the rule."""
        out = _run("GH_TOKEN=$(gh auth token --user s10023) gh pr view 1")
        assert out.strip() == "", f"sanctioned gh form tripped the hook: {out}"

    def test_the_corrected_recommended_form_is_silent(self) -> None:
        out = _run(
            "make preflight > /tmp/p.log 2>&1; rc=$?; tail -8 /tmp/p.log; exit $rc"
        )
        assert out.strip() == "", f"the recommended form tripped: {out}"

    def test_an_always_ok_command_mid_chain_is_silent(self) -> None:
        """The end-of-string anchor: only the LAST segment decides the status."""
        out = _run('make test > f 2>&1; rc=$?; echo "rc=$rc" >> f; exit $rc')
        assert out.strip() == "", f"mid-chain echo tripped: {out}"

    def test_a_non_gate_with_a_trailing_echo_is_silent(self) -> None:
        out = _run("git status --short; echo done")
        assert out.strip() == ""

    def test_a_heredoc_body_is_data_not_a_command(self) -> None:
        """A commit message DOCUMENTING these rules must not trip them.

        This is the failure the parent hit the moment its hook shipped.
        """
        out = _run(
            "git commit -F - <<'MSG'\n"
            "docs: explain why until ... pgrep waiters are banned\n"
            "and why gh auth switch is banned too\n"
            "MSG"
        )
        assert out.strip() == "", f"heredoc body tripped the hook: {out}"

    def test_a_non_bash_tool_is_ignored(self) -> None:
        out = _run("until ! pgrep -f x; do :; done", tool_name="Edit")
        assert out.strip() == ""


class TestDedupIsPerRulePerSession:
    def test_second_occurrence_of_the_same_rule_is_silent(self) -> None:
        mod = _load()
        session = f"test-{uuid.uuid4()}"
        assert mod._already_spoken(session, "waiter") is False
        assert mod._already_spoken(session, "waiter") is True

    def test_a_different_rule_still_speaks(self) -> None:
        mod = _load()
        session = f"test-{uuid.uuid4()}"
        assert mod._already_spoken(session, "waiter") is False
        assert mod._already_spoken(session, "gh-auth-switch") is False


class TestTheGateListIsThisReposOwn:
    """⚠ The port's one hard divergence: the gate list was RE-DERIVED, not copied.

    Upstream's list names `daily_check.py` and omits half of wifey's gates. A
    copied list would match nothing on the commands a session here actually
    writes -- and matching nothing reads exactly like no problems found.
    """

    def test_every_named_make_gate_exists_in_this_makefile(self) -> None:
        mod = _load()
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        import re

        named = set(re.findall(r"[a-z-]+(?=\||\)|$)", mod._GATE.split("|python")[0]))
        targets = set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.M))
        # Only assert about tokens that look like Make targets we listed.
        claimed = {t for t in named if t in targets or f"{t}:" in makefile}
        assert claimed, "the gate regex named no recognisable Make target"
        missing = sorted(t for t in claimed if t not in targets)
        assert missing == [], f"gate regex names targets this Makefile lacks: {missing}"

    def test_it_does_not_carry_the_parents_daily_check(self) -> None:
        mod = _load()
        assert "daily_check" not in mod._GATE, (
            "daily_check.py is the PARENT's script and does not exist here; "
            "carrying it means the list was copied rather than re-derived"
        )

    def test_a_real_wifey_only_gate_is_matched(self) -> None:
        """Positive control on the re-derivation, not just on its absence."""
        out = _run("make cadence-check | head -5")
        assert "exit status is SWALLOWED" in out


class TestNotPortedRulesAreAbsentOnPurpose:
    """⚠ Three upstream rules are deliberately absent -- pin that, so a later
    'completeness' pass cannot quietly restore a rule that can never fire here.
    """

    def test_no_card_rule(self) -> None:
        mod = _load()
        ids = {rule_id for rule_id, _, _ in mod.RULES}
        assert "card-chain" not in ids, (
            "wifey has no /card skill; that rule could never fire"
        )

    def test_no_rule_shells_out_to_pgrep(self) -> None:
        """`pgrep` does not exist on this host, so a rule depending on it is dead.

        The literal string appears in rule 1's PATTERN (it matches a command a
        session might write); what must not appear is the hook itself CALLING it.
        """
        source = HOOK.read_text(encoding="utf-8")
        assert "subprocess" not in source, (
            "this hook must not spawn a process probe: pgrep is absent on Windows "
            "and a silently-dead rule is the class #301/#302 existed to fix"
        )


class TestTheSemicolonHalfIsScoped:
    """Mutation: without the always-ok set the `;` half must go silent while the
    pipe half still fires -- otherwise a dead rule would pass the positives above.
    """

    def test_dropping_the_always_ok_set_silences_only_the_semicolon_half(
        self, tmp_path: Path
    ) -> None:
        source = HOOK.read_text(encoding="utf-8")
        target = r'_ALWAYS_OK = r"(?::|true|echo|printf|tail|head|cat|ls|wc)\b"'
        assert target in source, "mutation anchor moved; update this test"
        mutant = tmp_path / "no-always-ok.py"
        mutant.write_text(
            source.replace(target, r'_ALWAYS_OK = r"(?:__never__)\b"'),
            encoding="utf-8",
        )
        assert (
            _run('make preflight > f 2>&1; echo "EXIT=$?"', hook=mutant).strip() == ""
        )
        assert "exit status is SWALLOWED" in _run(
            "make preflight | tail -1", hook=mutant
        )
