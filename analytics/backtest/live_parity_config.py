"""T6 live-parity gate toggles for run_backtest().

Each flag defaults to False so passing a default-constructed `LiveParityConfig()`
(or `None`) keeps the engine's current behaviour. Set `enabled=True` to flip
every individual flag on at once; per-gate flags remain effective on top of the
master switch so callers can compose `--live-parity --without-cooldown`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class LiveParityConfig:
    """Toggle live-only gates inside run_backtest().

    `enabled` is the master switch. `is_on(gate)` returns True when the master
    switch OR the specific gate field is set. Cooldown bars per timeframe are
    optional and fall back to the engine's baked-in defaults when None.
    """

    #: Every gate, in the order the engine applies them. Canonical — the CLI
    #: builds its `--with-/--without-` flags from this, so a new gate cannot be
    #: added to one surface and forgotten on the other.
    GATES: ClassVar[tuple[str, ...]] = (
        "regime",
        "direction_filter",
        "f8_htf_ema",
        "adr_bias",
        "conflict_resolver",
        "cooldown",
    )

    enabled: bool = False
    regime: bool = False
    direction_filter: bool = False
    f8_htf_ema: bool = False
    adr_bias: bool = False
    conflict_resolver: bool = False
    cooldown: bool = False
    cooldown_bars_per_tf: dict[str, int] | None = None

    def is_on(self, gate: str) -> bool:
        """Return True iff the named gate field is set.

        Note: `enabled` is a *resolver-time* convenience — the CLI/TOML resolver
        expands it into per-gate True values *before* the engine sees the
        config, so an explicit `--without-<gate>` can still cleanly disable
        one gate while the master switch stays on (the acceptance contract).
        """
        return bool(getattr(self, gate))

    def describe(self) -> str:
        """One-line resolved gate state for the run banner.

        Every rating in `confidence_ratings` is conditional on this line: a
        recorded parameter that is never echoed cannot be checked against what
        actually executed, so print the resolved state on every run rather
        than trusting the declared config.
        """
        return " ".join(f"{g}={'on' if self.is_on(g) else 'off'}" for g in self.GATES)

    def identity(self) -> str | None:
        """Canonical token for the gate set that executed, for the run_id hash.

        Returns None when no gate is on, so a default-constructed config appends
        no suffix and every run_id written before this axis existed is unchanged
        — the same contract the optional flags above it keep.

        Built from `is_on`, not from the fields directly, because `is_on` is what
        the engine consults: `enabled` is a resolver-time convenience that never
        reaches `run_backtest`, so folding it in here would invent a distinction
        the engine does not make. Same rule as `effective_adr_threshold` — credit
        the gate that ran, never the one that was declared.

        `cooldown_bars_per_tf` joins the token only while `cooldown` is on, since
        the map is inert otherwise and an inert value must not split one cell into
        two identities.
        """
        on = [g for g in self.GATES if self.is_on(g)]
        if not on:
            return None
        token = "+".join(on)
        if self.is_on("cooldown") and self.cooldown_bars_per_tf is not None:
            bars = ",".join(
                f"{tf}:{self.cooldown_bars_per_tf[tf]}"
                for tf in sorted(self.cooldown_bars_per_tf)
            )
            token += f"/bars({bars})"
        return token
