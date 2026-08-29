"""H-024 phase 1: Form 4 parser tests (fixture-only, no network).

The XML shape these fixtures reconstruct was validated against a real filing
(Apple Inc., accession 0001140361-26-034741) on 2026-08-29. The fixtures are
synthetic because a real Form 4 names a private individual.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analytics.insider.form4 import (
    iter_form4_filings,
    parse_form4,
    raw_document_name,
)

_FIX = Path(__file__).parent / "fixtures" / "edgar"


def _xml(name: str) -> bytes:
    return (_FIX / name).read_bytes()


def test_raw_document_name_strips_the_xsl_renderer_prefix() -> None:
    # EDGAR's primaryDocument for a Form 4 points at the rendered HTML; the raw
    # ownership XML is the bare filename under the same accession.
    assert raw_document_name("xslF345X06/form4.xml") == "form4.xml"
    assert raw_document_name("form4.xml") == "form4.xml"


def test_parse_single_sale_reads_every_field() -> None:
    outcome = parse_form4(_xml("form4_single_sale.xml"))
    assert outcome.ok
    assert len(outcome.transactions) == 1
    t = outcome.transactions[0]
    assert t.owner_cik == "0000000901"
    assert t.owner_name == "Example Insider A"
    assert t.is_officer is True
    assert t.is_director is False
    assert t.transaction_date == "2026-08-25"
    assert t.transaction_code == "S"
    assert t.shares == 1500.0
    assert t.price_per_share == 232.45
    assert t.acquired_disposed == "D"


def test_joint_filing_emits_one_row_per_owner_and_transaction() -> None:
    # A joint Form 4 reports ONE set of transactions for several insiders. The
    # study's unit is the insider, so both owners must inherit both trades —
    # attributing to the first owner alone would delete an insider's history,
    # which is exactly the input the routine/opportunistic classifier keys on.
    outcome = parse_form4(_xml("form4_joint_owners.xml"))
    assert outcome.ok
    assert len(outcome.transactions) == 4  # 2 owners x 2 transactions
    owners = {t.owner_cik for t in outcome.transactions}
    assert owners == {"0000000901", "0000000902"}
    for cik in owners:
        codes = sorted(
            t.transaction_code for t in outcome.transactions if t.owner_cik == cik
        )
        assert codes == ["P", "S"]
    # seq distinguishes the two transactions within the filing, so the primary
    # key (accession, owner_cik, seq) does not collapse them.
    seqs = sorted({(t.owner_cik, t.seq) for t in outcome.transactions})
    assert len(seqs) == 4


def test_boolean_flags_accept_both_filer_agent_spellings() -> None:
    # form4_single_sale.xml writes isOfficer as "true"; form4_joint_owners.xml
    # writes it as "1". Both must read as True, or a whole filer agent's output
    # silently loses its officer/director labels.
    assert parse_form4(_xml("form4_single_sale.xml")).transactions[0].is_officer is True
    joint = parse_form4(_xml("form4_joint_owners.xml")).transactions
    assert any(t.is_officer for t in joint)
    assert any(t.is_director for t in joint)


def test_footnote_only_price_is_reported_as_a_failure_not_dropped() -> None:
    # The acceptance observable is a parse RATE, so a transaction the parser
    # cannot read must survive as a failure. Dropping it would report 100%
    # coverage by construction.
    outcome = parse_form4(_xml("form4_footnote_price.xml"))
    assert outcome.transactions == []
    assert len(outcome.failures) == 1
    assert "price" in outcome.failures[0]
    assert not outcome.ok


def test_derivative_only_filing_is_empty_but_not_a_failure() -> None:
    # A Form 4 reporting only option activity is in-scope-empty, not broken;
    # counting it as a parse failure would depress the coverage metric with
    # filings that are working exactly as intended.
    outcome = parse_form4(_xml("form4_derivative_only.xml"))
    assert outcome.transactions == []
    assert outcome.failures == []


def test_malformed_xml_is_a_failure_not_an_exception() -> None:
    outcome = parse_form4(b"<ownershipDocument><unclosed>")
    assert outcome.transactions == []
    assert outcome.failures and outcome.failures[0].startswith("xml:")


def test_filing_with_no_owner_cik_fails_closed() -> None:
    outcome = parse_form4(
        b"<ownershipDocument><reportingOwner><reportingOwnerId>"
        b"<rptOwnerName>No CIK</rptOwnerName></reportingOwnerId>"
        b"</reportingOwner></ownershipDocument>"
    )
    assert outcome.transactions == []
    assert outcome.failures == ["no reporting owner with a CIK"]


def _submissions(forms: list[str], dates: list[str]) -> dict[str, Any]:
    n = len(forms)
    return {
        "filings": {
            "recent": {
                "form": forms,
                "accessionNumber": [f"0000000000-26-{i:06d}" for i in range(n)],
                "primaryDocument": ["xslF345X06/form4.xml"] * n,
                "filingDate": dates,
                "acceptanceDateTime": [f"{d}T22:30:30.000Z" for d in dates],
            }
        }
    }


def test_iter_form4_filings_selects_form_4_only() -> None:
    subs = _submissions(["4", "8-K", "4", "10-Q"], ["2026-01-02"] * 4)
    filings = iter_form4_filings(subs)
    assert len(filings) == 2
    assert all(f.primary_document == "xslF345X06/form4.xml" for f in filings)
    assert filings[0].acceptance == "2026-01-02T22:30:30.000Z"


def test_iter_form4_filings_since_filter_excludes_earlier_filings() -> None:
    subs = _submissions(["4", "4", "4"], ["2014-01-01", "2015-06-10", "2020-03-04"])
    assert len(iter_form4_filings(subs)) == 3
    kept = iter_form4_filings(subs, since="2015-01-01")
    assert [f.filing_date for f in kept] == ["2015-06-10", "2020-03-04"]


def test_iter_form4_filings_reads_a_bare_shard_payload() -> None:
    # Older-filing shards carry the column arrays at top level, with no
    # {"filings": {"recent": ...}} wrapper. Both shapes must work, or the shard
    # walk silently yields nothing and a long history reads as a quiet insider.
    shard = _submissions(["4", "4"], ["2016-01-01", "2017-01-01"])["filings"]["recent"]
    assert len(iter_form4_filings(shard)) == 2


def test_json_fixture_dir_is_reachable() -> None:
    # Guards the fixture path itself: an empty read here would make every
    # fixture-driven assertion above vacuous.
    assert _FIX.is_dir()
    assert json.loads('{"ok": true}')["ok"] is True
