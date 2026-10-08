"""Edge-hunt #4 (PEAD-lite): EDGAR client parser tests (fixture-only, no network)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from utils import edgar_client
from utils.edgar_client import (
    _is_transient,
    parse_announce_dates,
    parse_eps_facts,
    ticker_to_cik,
)


def _req() -> urllib.request.Request:
    return urllib.request.Request("https://www.sec.gov/x", headers={"User-Agent": "t"})


class _FakeResponse:
    """Minimal context-manager stand-in for an ``http.client.HTTPResponse``."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


_FIX = Path(__file__).parent / "fixtures" / "edgar"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return data


def test_ticker_to_cik_zero_pads_to_10() -> None:
    tickers = _load("company_tickers_trimmed.json")
    assert ticker_to_cik(tickers, "AAPL") == "0000320193"
    assert ticker_to_cik(tickers, "aapl") == "0000320193"  # case-insensitive
    assert ticker_to_cik(tickers, "NOPE") is None


def test_parse_eps_facts_returns_quarterly_only() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    # every returned fact is ~one fiscal quarter (Q4 is derived, no native span)
    for f in facts:
        span = (f.period_end - f.period_start).days
        assert 80 <= span <= 100 or f.fp == "Q4"
    # fiscal labels present and de-duplicated by (fy, fp) — YTD rows excluded
    keys = [(f.fy, f.fp) for f in facts]
    assert keys == [(2023, "Q1"), (2023, "Q2"), (2023, "Q3"), (2023, "Q4")]


def test_parse_eps_facts_keeps_earliest_filed_on_restatement() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    target = next(f for f in facts if (f.fy, f.fp) == (2023, "Q1"))
    assert target.filed == "2023-02-03"  # the original, not the 2023-05-05 amendment
    assert target.eps_diluted == 1.88


def test_parse_eps_facts_derives_q4_from_fy_minus_interims() -> None:
    facts = parse_eps_facts(_load("aapl_companyfacts_trimmed.json"))
    q4 = next(f for f in facts if (f.fy, f.fp) == (2023, "Q4"))
    # FY 6.13 − (1.88 + 1.52 + 1.26) = 1.47
    assert abs(q4.eps_diluted - 1.47) < 1e-9


def test_parse_announce_dates_prefers_8k_item_202() -> None:
    subs = _load("aapl_submissions_trimmed.json")
    dates = parse_announce_dates(subs)
    assert dates == ["2023-02-02", "2023-05-04"]  # 8-K item-2.02 only, sorted
    assert "2023-01-15" not in dates  # the 5.02 8-K is excluded


class TestTransientClassification:
    """Which failures ``_open_with_retry`` will retry, and which it must not."""

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_server_side_codes_are_transient(self, code: int) -> None:
        exc = urllib.error.HTTPError("u", code, "msg", {}, None)  # type: ignore[arg-type]
        assert _is_transient(exc)

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_client_side_codes_are_NOT_transient(self, code: int) -> None:
        """403 is the User-Agent contract and 404 is a document that does not
        exist. Retrying either burns the budget three times over and buries a
        configuration error under what looks like flakiness."""
        exc = urllib.error.HTTPError("u", code, "msg", {}, None)  # type: ignore[arg-type]
        assert not _is_transient(exc)

    def test_network_and_timeout_failures_are_transient(self) -> None:
        assert _is_transient(urllib.error.URLError("dns"))
        assert _is_transient(TimeoutError("read timed out"))

    def test_an_unrelated_error_is_not_transient(self) -> None:
        assert not _is_transient(ValueError("parse"))


class TestOpenWithRetry:
    """The retry loop itself, with sleeps stubbed out."""

    @staticmethod
    def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edgar_client.time, "sleep", lambda _s: None)

    def test_a_transient_failure_is_retried_and_can_succeed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._no_sleep(monkeypatch)
        calls = {"n": 0}

        def fake_urlopen(_req: Any, timeout: int = 30) -> Any:
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("boom")
            return _FakeResponse(b"payload")

        monkeypatch.setattr(edgar_client.urllib.request, "urlopen", fake_urlopen)
        assert edgar_client._open_with_retry(_req()) == b"payload"
        assert calls["n"] == 3

    def test_it_gives_up_after_max_attempts_and_RAISES(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run must learn a fetch failed. Returning empty bytes here would
        hand the caller a filing with no transactions, indistinguishable from a
        filing that genuinely reported none."""
        self._no_sleep(monkeypatch)
        calls = {"n": 0}

        def always_fail(_req: Any, timeout: int = 30) -> Any:
            calls["n"] += 1
            raise urllib.error.URLError("boom")

        monkeypatch.setattr(edgar_client.urllib.request, "urlopen", always_fail)
        with pytest.raises(urllib.error.URLError):
            edgar_client._open_with_retry(_req())
        assert calls["n"] == edgar_client._MAX_ATTEMPTS

    def test_a_non_transient_failure_is_raised_on_the_FIRST_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control against the test above: the loop is not simply
        retrying everything to the cap."""
        self._no_sleep(monkeypatch)
        calls = {"n": 0}

        def forbidden(_req: Any, timeout: int = 30) -> Any:
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(edgar_client.urllib.request, "urlopen", forbidden)
        with pytest.raises(urllib.error.HTTPError):
            edgar_client._open_with_retry(_req())
        assert calls["n"] == 1
