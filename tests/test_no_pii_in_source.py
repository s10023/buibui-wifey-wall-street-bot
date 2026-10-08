"""Guard: no contact address or credential literal in tracked source.

This repo is public (flipped 2026-08-06). Everything `git ls-files` returns is
world-readable, and git history cannot be un-published — so the only durable
control is keeping such values out of a commit in the first place.

The concrete case this encodes: `utils/edgar_client.py` carried a work email as
a module-level `_UA` literal from PR #104. That is correct-by-context while the
repo is private and a PII disclosure once it is public, with nothing in CI to
notice. SEC fair-access genuinely requires a contact in the User-Agent, so the
fix is not "remove the contact" but "read it from the environment"
(`EDGAR_CONTACT_EMAIL`, `.env` is gitignored).

Scope is deliberately tracked `.py` only: config/docs carry example addresses on
purpose, and widening this to prose would make it a nuisance rather than a gate.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Split so this file's own source cannot match the pattern it defines.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

CREDENTIAL_RES = {
    "telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "groq key": re.compile(r"\bgsk_[A-Za-z0-9]{40,}"),
    "openai key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

# `git@github.com`-style host specs are not contact addresses.
ALLOWED_EMAIL_SUBSTRINGS = (
    "git@github.com",
    "@example.com",
    "@users.noreply.github.com",
)

SELF = Path(__file__).name


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    ).stdout
    return [REPO_ROOT / p for p in out.split("\0") if p and Path(p).name != SELF]


def test_no_email_address_in_tracked_python_source() -> None:
    """A reachable contact belongs in the environment, never in a committed file."""
    offenders: list[str] = []
    for path in _tracked_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in EMAIL_RE.finditer(text):
            hit = match.group(0)
            if any(ok in hit for ok in ALLOWED_EMAIL_SUBSTRINGS):
                continue
            line = text.count("\n", 0, match.start()) + 1
            local, _, domain = hit.partition("@")
            redacted = f"{local[:2]}...@{domain}"
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} -> {redacted}")

    assert not offenders, (
        "Contact address(es) found in tracked source — this repo is PUBLIC.\n"
        "Read the value from the environment instead (see utils/edgar_client.py::"
        "_user_agent and EDGAR_CONTACT_EMAIL in .env.example).\n  "
        + "\n  ".join(offenders)
    )


def test_no_credential_literal_in_tracked_python_source() -> None:
    offenders: list[str] = []
    for path in _tracked_python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in CREDENTIAL_RES.items():
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} -> {label}")

    assert not offenders, (
        "Credential-shaped literal(s) in tracked source — this repo is PUBLIC.\n  "
        + "\n  ".join(offenders)
    )
