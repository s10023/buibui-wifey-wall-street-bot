"""EDGAR User-Agent contract — the shape SEC actually accepts.

Measured 2026-08-29 against both SEC hosts (data.sec.gov / www.sec.gov):

    name + email          200 / 200
    name only             200 / 403
    URL, with parens      403 / 403
    URL, without parens   403 / 403

The module previously fell back to the repo URL when ``EDGAR_CONTACT_EMAIL`` was
unset, documented as "SEC may throttle an address-less UA harder". That was the
wrong shape entirely: it 403s everywhere, so ``make wifey-pead-backfill`` could
not run on an unconfigured box and failed as if SEC were down. These tests pin
the loud failure that replaced it.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from utils.edgar_client import EdgarContactMissing, _user_agent


def test_user_agent_carries_the_configured_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGAR_CONTACT_EMAIL", "someone@example.com")
    ua = _user_agent()
    assert "someone@example.com" in ua
    # No URL anywhere in the header: a URL is the shape SEC refuses.
    assert "http" not in ua


def test_unset_contact_raises_instead_of_buying_a_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGAR_CONTACT_EMAIL", raising=False)
    with pytest.raises(EdgarContactMissing, match="EDGAR_CONTACT_EMAIL"):
        _user_agent()


def test_blank_contact_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGAR_CONTACT_EMAIL", "   ")
    with pytest.raises(EdgarContactMissing):
        _user_agent()


def test_non_email_contact_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exact regression: a URL used to be the *default* value here.
    monkeypatch.setenv(
        "EDGAR_CONTACT_EMAIL", "https://github.com/s10023/buibui-wifey-wall-street-bot"
    )
    with pytest.raises(EdgarContactMissing):
        _user_agent()


@pytest.mark.parametrize("module", ["tools.insider_backfill", "tools.pead_backfill"])
def test_backfill_entry_point_reads_dotenv_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    """Both EDGAR backfills must load ``.env`` as their first act.

    Neither did until 2026-09-01, so a box with ``EDGAR_CONTACT_EMAIL`` set in
    ``.env`` — the only place the error message and ``.env.example`` tell you to
    put it — still died on ``EdgarContactMissing``. The advice named a file the
    entry point never read, which reads as a config mistake rather than a defect.

    Raising from the patched loader pins the ORDERING as well as the call: if
    ``load_dotenv()`` were to move below the argparse or fetch lines, one of
    those would raise first and the sentinel would never escape.
    """
    mod = importlib.import_module(module)

    class _Sentinel(Exception):
        pass

    called: list[bool] = []

    def _fake_load_dotenv(*args: Any, **kwargs: Any) -> None:
        called.append(True)
        raise _Sentinel

    monkeypatch.setattr(mod, "load_dotenv", _fake_load_dotenv)
    with pytest.raises(_Sentinel):
        mod.main([])
    assert called == [True]
