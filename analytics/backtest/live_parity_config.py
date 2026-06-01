"""T6 live-parity gate toggles for run_backtest().

Each flag defaults to False so passing a default-constructed `LiveParityConfig()`
(or `None`) keeps the engine's current behaviour. Set `enabled=True` to flip
every individual flag on at once; per-gate flags remain effective on top of the
master switch so callers can compose `--live-parity --without-cooldown`.

PR-1 lands the dataclass + plumbing only. Per-gate logic ports ship in PRs 2-5.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveParityConfig:
    """Toggle live-only gates inside run_backtest().

    `enabled` is the master switch. `is_on(gate)` returns True when the master
    switch OR the specific gate field is set. Cooldown bars per timeframe are
    optional and fall back to the engine's baked-in defaults when None.
    """

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
