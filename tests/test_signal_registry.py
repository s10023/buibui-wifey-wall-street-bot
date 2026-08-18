"""Tests for signal registry completeness and correctness."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from analytics.strategies import (
    DETECTOR_REGISTRY,
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


# ---------------------------------------------------------------------------
# DETECTOR_REGISTRY wiring
#
# `signals/registry.py` builds `_DETECTORS` from direct `from analytics.strategies
# import detect_*` statements; it never reads `DETECTOR_REGISTRY`. The two dicts are
# hand-maintained duplicates in different files, so nothing above this point can see
# them diverge: `test_registry_covers_all_active_strategies` ties SIGNAL_REGISTRY to
# STRATEGY_REGISTRY and stops there. A strategy present in both of those but absent
# from DETECTOR_REGISTRY fires live alerts while being invisible to backtest dispatch,
# the regression golden and `test_lookahead.py`'s causality property test — a silent
# hole in exactly the direction /new-strategy warns about.
#
# `_REGISTRY_EXCLUDED` is the deliberate opt-out for both registries, not just for
# SIGNAL_REGISTRY. A detector taking a second positional arg (funding rates, a
# secondary OHLCV frame) cannot fit DETECTOR_REGISTRY's
# `Callable[[pd.DataFrame], pd.DataFrame]` and is wired through explicit branches
# in `backtest_runner.detect_signals_for_strategy` instead -- `seasonality` is the
# one live instance. So a failure here has two legitimate resolutions, and the
# messages below are worded to leave both open: wire the missing entry, or add the
# name to `_REGISTRY_EXCLUDED` because it is one of those. See
# `/new-strategy` section "Strategies needing extra data".
# ---------------------------------------------------------------------------


def _wiring_errors(
    strategy: Mapping[str, object],
    detector: Mapping[str, object],
    signal: Mapping[str, Mapping[str, object]],
    excluded: AbstractSet[str],
) -> list[str]:
    """Report every way the three registries can disagree, in both directions.

    Pure over its arguments so the live check and its positive controls run the
    same comparison — a guard tested only against the real registries proves
    nothing, because those are currently consistent.
    """
    errors: list[str] = []
    dispatchable = set(strategy) - set(excluded)
    for name in sorted(dispatchable - set(detector)):
        errors.append(f"{name}: in STRATEGY_REGISTRY, missing from DETECTOR_REGISTRY")
    for name in sorted(set(detector) - dispatchable):
        errors.append(f"{name}: in DETECTOR_REGISTRY, not an active strategy")
    for name in sorted(set(signal) - set(detector)):
        errors.append(f"{name}: in SIGNAL_REGISTRY, missing from DETECTOR_REGISTRY")
    for name in sorted(set(detector) - set(signal)):
        errors.append(f"{name}: in DETECTOR_REGISTRY, missing from SIGNAL_REGISTRY")
    for name in sorted(set(signal) & set(detector)):
        if signal[name]["detector"] is not detector[name]:
            errors.append(
                f"{name}: SIGNAL_REGISTRY and DETECTOR_REGISTRY bind different functions"
            )
    return errors


def test_detector_registry_is_wired_to_the_other_two() -> None:
    """The live gate: all three registries agree on names and on bindings."""
    assert (
        _wiring_errors(
            STRATEGY_REGISTRY, DETECTOR_REGISTRY, SIGNAL_REGISTRY, _REGISTRY_EXCLUDED
        )
        == []
    )


def test_wiring_check_catches_a_strategy_missing_its_detector() -> None:
    """Positive control: the /new-strategy failure mode this gate exists for."""
    detector = {k: v for k, v in DETECTOR_REGISTRY.items() if k != "doji"}
    errors = _wiring_errors(
        STRATEGY_REGISTRY, detector, SIGNAL_REGISTRY, _REGISTRY_EXCLUDED
    )
    assert "doji: in STRATEGY_REGISTRY, missing from DETECTOR_REGISTRY" in errors
    assert "doji: in SIGNAL_REGISTRY, missing from DETECTOR_REGISTRY" in errors


def test_wiring_check_catches_a_detector_outliving_its_strategy() -> None:
    """Positive control: the other direction — a stale detector entry."""
    detector = {**DETECTOR_REGISTRY, "retired_pattern": DETECTOR_REGISTRY["doji"]}
    errors = _wiring_errors(
        STRATEGY_REGISTRY, detector, SIGNAL_REGISTRY, _REGISTRY_EXCLUDED
    )
    assert "retired_pattern: in DETECTOR_REGISTRY, not an active strategy" in errors
    assert (
        "retired_pattern: in DETECTOR_REGISTRY, missing from SIGNAL_REGISTRY" in errors
    )


def test_wiring_check_catches_a_divergent_binding() -> None:
    """Positive control: same name on both sides, different function.

    Key-set equality cannot see this, and it is reachable: the two dicts import
    their detectors independently, so live alerts and backtests would silently
    disagree about what the strategy is.
    """
    detector = {**DETECTOR_REGISTRY, "doji": DETECTOR_REGISTRY["pin_bar"]}
    errors = _wiring_errors(
        STRATEGY_REGISTRY, detector, SIGNAL_REGISTRY, _REGISTRY_EXCLUDED
    )
    assert errors == [
        "doji: SIGNAL_REGISTRY and DETECTOR_REGISTRY bind different functions"
    ]
