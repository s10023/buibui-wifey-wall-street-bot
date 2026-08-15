"""Structural checks on `deploy/systemd/user/*.{service,timer}`.

`wifey-backup.service` shipped with `OnFailure=` in `[Service]` from its
introduction until 2026-08-15. systemd parses that as an unknown key, logs
"ignoring" at load, and starts the unit anyway — so the failure alert was never
armed, which is the exact thing it exists to prevent. `systemctl start` cannot
catch it, because the unit genuinely works.

`deploy/README.md` had said "verify a unit parses before trusting it" the whole
time. Prose did not enforce it, so this does. It deliberately re-implements the
section rules rather than shelling out to `systemd-analyze`: that binary is not
guaranteed in CI, and a test that skips when its tool is missing is green
without ever having run — the failure mode this repo already calls the
silent-surface class.
"""

from __future__ import annotations

from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "user"
REPO_ROOT = Path(__file__).resolve().parents[1]

# Directives systemd only accepts in [Unit]. Anywhere else they are silently
# ignored. Not exhaustive — these are the ones a unit here plausibly uses.
UNIT_ONLY = {
    "Description",
    "Documentation",
    "Requires",
    "Requisite",
    "Wants",
    "BindsTo",
    "PartOf",
    "Conflicts",
    "Before",
    "After",
    "OnFailure",
    "OnSuccess",
    "OnFailureJobMode",
    "StopWhenUnneeded",
    "RefuseManualStart",
    "RefuseManualStop",
    "AllowIsolate",
    "DefaultDependencies",
}

# Directives systemd only accepts in [Install].
INSTALL_ONLY = {"Alias", "WantedBy", "RequiredBy", "Also", "DefaultInstance"}


def unit_files() -> list[Path]:
    return sorted(p for p in UNIT_DIR.iterdir() if p.suffix in {".service", ".timer"})


def parse(path: Path) -> list[tuple[str, str, str]]:
    """Return (section, key, value) for every directive, comments stripped."""
    out: list[tuple[str, str, str]] = []
    section = ""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            out.append((section, key.strip(), value.strip()))
    return out


def test_unit_dir_is_not_empty() -> None:
    """Positive control: every check below is vacuous over an empty directory."""
    units = unit_files()
    assert len(units) >= 5, f"expected the 5 known units, found {len(units)}"


@pytest.mark.parametrize("path", unit_files(), ids=lambda p: p.name)
def test_unit_only_directives_are_in_the_unit_section(path: Path) -> None:
    """The defect this file exists for: a [Unit] directive in [Service]."""
    misplaced = [
        (sec, key) for sec, key, _ in parse(path) if key in UNIT_ONLY and sec != "Unit"
    ]
    assert not misplaced, (
        f"{path.name}: {misplaced} — systemd IGNORES these outside [Unit] and "
        f"starts the unit anyway, so the directive is silently inert."
    )


@pytest.mark.parametrize("path", unit_files(), ids=lambda p: p.name)
def test_install_only_directives_are_in_the_install_section(path: Path) -> None:
    misplaced = [
        (sec, key)
        for sec, key, _ in parse(path)
        if key in INSTALL_ONLY and sec != "Install"
    ]
    assert not misplaced, f"{path.name}: {misplaced} — ignored outside [Install]"


def template_of(unit: str) -> str:
    """Resolve `foo@%N.service` to the template file `foo@.service`.

    systemd instance names are expanded at load time, so the file on disk is
    always the bare `name@.suffix`. Checking the literal string would report a
    dangling reference for every correctly-written template.
    """
    if "@" not in unit:
        return unit
    head, _, tail = unit.partition("@")
    suffix = tail[tail.rindex(".") :] if "." in tail else ""
    return f"{head}@{suffix}"


@pytest.mark.parametrize("path", unit_files(), ids=lambda p: p.name)
def test_onfailure_targets_a_unit_that_exists(path: Path) -> None:
    """A dangling alert reference fails exactly when the alert is needed."""
    for _section, key, value in parse(path):
        if key != "OnFailure":
            continue
        for target in value.split():
            resolved = template_of(target)
            assert (UNIT_DIR / resolved).exists(), (
                f"{path.name}: OnFailure={target} resolves to {resolved}, "
                f"which is not in {UNIT_DIR}"
            )


def test_template_of_resolves_instances_and_leaves_plain_names() -> None:
    """Control for the helper above — without it the check could pass vacuously.

    A `template_of` that returned its input unchanged would make every OnFailure
    assertion above trivially true for plain names and trivially false for
    templates, so pin both directions.
    """
    assert template_of("wifey-alert@%N.service") == "wifey-alert@.service"
    assert template_of("wifey-alert@wifey-backup.service") == "wifey-alert@.service"
    assert template_of("plain.service") == "plain.service"


@pytest.mark.parametrize("path", unit_files(), ids=lambda p: p.name)
def test_referenced_repo_paths_exist(path: Path) -> None:
    """ExecStart / EnvironmentFile pointing at a moved script fails at fire time.

    Only in-repo absolute paths are checked. A leading `-` on EnvironmentFile
    means "tolerate absence" and is skipped, which is why `.env` — gitignored
    and absent on a fresh clone — does not fail this.
    """
    for _section, key, value in parse(path):
        if key not in {"ExecStart", "EnvironmentFile"}:
            continue
        token = value.split()[0] if value else ""
        if key == "EnvironmentFile" and token.startswith("-"):
            continue
        if not token.startswith(str(REPO_ROOT)):
            continue
        assert Path(token).exists(), f"{path.name}: {key} points at missing {token}"
