"""Tests for GET /api/universe-policy (Phase 0.1 honesty surface)."""

import pytest
from fastapi.testclient import TestClient

from utils.config_validation import UniversePolicy

_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note="Test caveat: survivors only.",
)


def test_universe_policy_returns_policy_with_symbol_count(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoint returns the active policy plus the watchlist size."""
    monkeypatch.setattr("web.api.routers.config.load_universe_policy", lambda: _POLICY)
    monkeypatch.setattr(
        "web.api.routers.config.load_stocks_config",
        lambda: {"AAPL": {"sl_pct": 0.05}, "MSFT": {"sl_pct": 0.05}},
    )
    resp = web_client.get("/api/universe-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "liquid_large_cap"
    assert data["as_of"] == "today"
    assert data["survivorship_note"] == "Test caveat: survivors only."
    assert data["n_symbols"] == 2


def test_universe_policy_symbol_count_none_when_config_unreadable(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing/broken stocks config degrades to n_symbols=None, not an error."""
    monkeypatch.setattr("web.api.routers.config.load_universe_policy", lambda: _POLICY)

    def _boom() -> dict[str, dict[str, float]]:
        raise FileNotFoundError("no stocks.json")

    monkeypatch.setattr("web.api.routers.config.load_stocks_config", _boom)
    resp = web_client.get("/api/universe-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "liquid_large_cap"
    assert data["n_symbols"] is None
