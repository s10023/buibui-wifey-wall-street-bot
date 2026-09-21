"""Guards for ``tools/orphan_test_audit.py``'s exemption allowlist.

The module shipped with no tests, and that is how its exemptions came to be a
silent no-op on Windows: ``EXEMPT_CLASSES`` is keyed with forward slashes, while
``str(path.relative_to(REPO_ROOT))`` renders backslashes there. Every lookup
missed, so all four exempt classes reported as findings forever.

⚠ **The bug is PLATFORM-DEPENDENT, which is why a green suite hid it.** On Linux
``str()`` and ``as_posix()`` agree, so the defect could only ever appear on the
host that had no test running against it. ``test_exemption_keys_are_posix``
therefore pins the *convention* rather than the symptom: it fails on either OS if
someone writes a key with a backslash, which is the direction the fix can regress.
"""

from __future__ import annotations

from pathlib import Path

from tools.orphan_test_audit import EXEMPT_CLASSES, REPO_ROOT, audit


class TestExemptionsAreHonoured:
    """The decisive control: an exempt class must not surface as a finding."""

    def test_every_exempt_class_still_exists_in_the_tree(self) -> None:
        """The perturbation must ARRIVE, or the assertion below is vacuous.

        ⚠ Measured: with the separator bug reintroduced, a bare
        ``reported & set(EXEMPT_CLASSES)`` assertion still PASSES — the reported
        keys carry backslashes, so the intersection is empty whether the lookup
        works or misses entirely. An exemption naming a class that no longer
        exists would pass it the same way.
        """
        for key in EXEMPT_CLASSES:
            rel, cls = key.split("::")
            body = (REPO_ROOT / Path(rel)).read_text(encoding="utf-8")
            assert f"class {cls}" in body, (
                f"{key} exempts a class that is no longer in {rel} — the "
                "exemption is now a mute, not a narrowing"
            )

    def test_no_exempt_class_is_reported(self) -> None:
        """Pre-fix on Windows this returned all four exempt classes."""
        reported = {f"{f.file}::{f.cls}" for f in audit()}
        leaked = sorted(reported & set(EXEMPT_CLASSES))
        assert leaked == [], (
            f"exempt classes reported as findings: {leaked} — the allowlist "
            "lookup is not matching, most likely a path-separator mismatch"
        )

    def test_the_audit_builds_posix_keys(self) -> None:
        """Positive control: a finding's key must be comparable to the allowlist.

        Asserting only the absence above is satisfied by two worlds — the lookup
        working, and the audit reporting nothing at all. This observes the channel.
        """
        keys = [f"{f.file}::{f.cls}" for f in audit()]
        assert all("\\" not in k for k in keys), (
            f"audit emitted a backslash key: {[k for k in keys if chr(92) in k]}"
        )


class TestExemptionAllowlistIsLive:
    """An allowlist needs a liveness test, not a review.

    Without one an exemption outlives the finding it narrowed and silently
    becomes a mute — the same rule the ``negative-claims`` allowlist follows.
    """

    def test_keys_are_posix(self) -> None:
        for key in EXEMPT_CLASSES:
            assert "\\" not in key, f"{key!r} must use forward slashes"
            assert "::" in key, f"{key!r} must be '<path>::<ClassName>'"

    def test_every_exempt_file_still_exists(self) -> None:
        missing = [
            key
            for key in EXEMPT_CLASSES
            if not (REPO_ROOT / Path(key.split("::")[0])).is_file()
        ]
        assert missing == [], f"exemption names a file that is gone: {missing}"

    def test_every_exemption_carries_a_reason(self) -> None:
        blank = [k for k, reason in EXEMPT_CLASSES.items() if not reason.strip()]
        assert blank == [], f"exemption without a reason: {blank}"
