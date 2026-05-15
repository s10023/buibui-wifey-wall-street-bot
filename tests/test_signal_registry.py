"""Tests for signal registry completeness and correctness."""

from analytics.strategies import (
    KNOWN_STRATEGIES,
    KNOWN_STRATEGY_TYPES,
    STRATEGY_REGISTRY,
    STRATEGY_TYPE_GROUPS,
)
from signals.registry import SIGNAL_REGISTRY

_REGISTRY_EXCLUDED = {"seasonality"}


def test_registry_excludes_inactive_strategies() -> None:
    for name in _REGISTRY_EXCLUDED:
        assert name not in SIGNAL_REGISTRY, (
            f"{name} should be excluded from SIGNAL_REGISTRY"
        )


def test_registry_covers_all_active_strategies() -> None:
    expected = {s for s in KNOWN_STRATEGIES if s not in _REGISTRY_EXCLUDED}
    assert set(SIGNAL_REGISTRY.keys()) == expected


def test_all_plugins_have_callable_detector() -> None:
    for name, plugin in SIGNAL_REGISTRY.items():
        assert callable(plugin["detector"]), f"{name} missing callable detector"


def test_all_strategies_have_boolean_flags_in_strategy_registry() -> None:
    for name in SIGNAL_REGISTRY:
        spec = STRATEGY_REGISTRY[name]
        assert isinstance(spec.requires_funding, bool), (
            f"{name}: requires_funding not bool"
        )


def test_all_strategies_have_strategy_type() -> None:
    """Every strategy in the registry must have a non-empty, known strategy_type."""
    for name, spec in STRATEGY_REGISTRY.items():
        assert spec.strategy_type, f"{name}: strategy_type is empty"
        assert spec.strategy_type in KNOWN_STRATEGY_TYPES, (
            f"{name}: unknown strategy_type {spec.strategy_type!r}"
        )


def test_strategy_type_groups_cover_all_strategies() -> None:
    """STRATEGY_TYPE_GROUPS must account for every strategy in the registry."""
    grouped = {s for strategies in STRATEGY_TYPE_GROUPS.values() for s in strategies}
    assert grouped == set(KNOWN_STRATEGIES)


def test_all_strategies_have_valid_confidence() -> None:
    # Confidence is now per-TF dict or plain int. Validate all resolved values are 1–5.
    sample_tfs = ["15m", "1h", "4h", "1d"]
    for name, spec in STRATEGY_REGISTRY.items():
        for tf in sample_tfs:
            c = spec.get_confidence(tf)
            assert isinstance(c, int) and 1 <= c <= 5, (
                f"{name}/{tf}: confidence must be 1–5, got {c}"
            )
