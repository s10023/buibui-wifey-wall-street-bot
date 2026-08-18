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


# Characterization test, not a protection. It pins a known hole rather than
# asserting a guard we have not built — see the note in the docstring below.
_TP_R_DECLARED_BUT_IGNORED = {
    "doji",
    "engulfing",
    "hammer_hanging_man",
    "inside_bar",
    "morning_evening_star",
    "pin_bar",
}
_TP_R_USED_BUT_UNDECLARED = {"ema", "ote_entry"}


def test_tp_r_sweep_surface_is_inverted() -> None:
    """Pin the inversion between the declared `tp_r` knob and the effective one.

    Every strategy declaring a sweepable `tp_r` ParamSpec ignores it: detectors
    are always called as `plugin["detector"](df)` with no params, and these six
    set no `tp_price`, so the alert and the backtest engine each derive the
    target from the config-resolved `tp_r` instead. A sweep over the declared
    knob therefore returns a flat surface, which is indistinguishable from a
    knob already at its optimum.

    The two detectors whose `tp_r` does reach a target — via a structural
    `tp_price` the engine honours — declare nothing, so they cannot be swept.
    The intersection of the two sets is empty.

    Not fixed here, because both repairs are decisions rather than cleanups:
    dropping the declarations would turn a silent flat surface into a KeyError
    at `tools/multi_symbol_wfo.py`'s unguarded `row.params["tp_r"]`, and moving
    them to the two strategies that work changes the sweep surface while the TA
    book is frozen. This test fails the moment either set moves, which is the
    point — read this note before interpreting a `tp_r` sweep on these cells.
    """
    declared = {
        name
        for name, spec in STRATEGY_REGISTRY.items()
        if any(p.name == "tp_r" for p in spec.params)
    }
    assert declared == _TP_R_DECLARED_BUT_IGNORED
    assert declared.isdisjoint(_TP_R_USED_BUT_UNDECLARED)
