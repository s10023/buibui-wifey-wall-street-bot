"""Author identity for the pundit ledger.

The bug these lock is silent by construction: nothing crashes when one person
is written two ways, the scorer just reports two shorter track records and
both may fall under a min_n marker that either would have cleared.

No key in this fork's ledger collides today — see the module docstring for the
measured state and the named latent trigger.
"""

from __future__ import annotations

from analytics.pundit_authors import normalize_author


def test_leading_at_is_not_identity() -> None:
    """The named latent split in this ledger: 'SailorManCrypto' is stored bare
    here while the same person is '@SailorManCrypto' upstream."""
    assert normalize_author("@SailorManCrypto") == normalize_author("SailorManCrypto")
    assert normalize_author("@fenggemeigu") == normalize_author("fenggemeigu")


def test_whitespace_is_not_identity() -> None:
    assert normalize_author("  @luckychartape ") == normalize_author("luckychartape")


def test_is_idempotent() -> None:
    """Applied at read time on both sides of the ledger/priors join, so it must
    survive being applied twice."""
    for raw in ("@benjaminjcowen", "benjaminjcowen", "  @@weird  ", "buibui_card"):
        once = normalize_author(raw)
        assert normalize_author(once) == once


def test_does_not_invent_a_key_for_a_degenerate_author() -> None:
    """'@' alone must not normalise to '' and silently become a shared key for
    every malformed row — callers treat a falsy author as skipped."""
    assert normalize_author("@") == "@"
    assert normalize_author("   ") == ""


def test_distinct_authors_stay_distinct() -> None:
    """The fix must not over-merge: these are different people."""
    assert normalize_author("@fenggemeigu") != normalize_author("@benjaminjcowen")
    assert normalize_author("SailorManCrypto") != normalize_author("SailorManCrypt")


def test_case_variants_are_documented_as_NOT_handled() -> None:
    """Deliberate scope boundary, pinned so a future reader sees it was a
    decision and not an oversight: X handles are case-insensitive, so these ARE
    one account, but folding case here would lowercase every display name. A
    curated roster is the agreed home for alias mapping and subsumes this.
    """
    assert normalize_author("@fenggemeigu") != normalize_author("@Fenggemeigu")
