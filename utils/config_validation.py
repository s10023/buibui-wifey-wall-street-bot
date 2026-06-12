import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_STOCKS_PATH = Path("config/stocks.json")

_MAX_LEVERAGE = 150
_MIN_LEVERAGE = 1
_MIN_SL_PCT = 0.1
_MAX_SL_PCT = 100

_MAX_STOCK_SL_FRAC = 1.0

UNIVERSE_POLICY_KEY = "universe_policy"
_VALID_AS_OF = frozenset({"fixed", "today"})
_UNIVERSE_POLICY_FIELDS = frozenset({"scope", "as_of", "survivorship_note"})


@dataclass(frozen=True)
class UniversePolicy:
    """Explicit universe metadata — bounds selection/survivorship bias (Phase 0.1).

    as_of semantics: "today" = symbols picked with current knowledge (forward-
    looking over backtested history); "fixed" = list frozen at a date that
    predates the sample (point-in-time-ish).
    """

    scope: str
    as_of: str
    survivorship_note: str

    def summary(self) -> str:
        """One-line stamp, e.g. 'liquid_large_cap (as_of=today)'."""
        return f"{self.scope} (as_of={self.as_of})"

    def to_json(self) -> str:
        """Compact JSON for the backtest_runs.universe_policy column."""
        return json.dumps(
            {
                "scope": self.scope,
                "as_of": self.as_of,
                "survivorship_note": self.survivorship_note,
            },
            sort_keys=True,
        )

    def describe(self, n_symbols: int | None = None) -> str:
        """Multi-line honesty paragraph for CLI backtest output."""
        count = f", {n_symbols} symbols" if n_symbols is not None else ""
        return (
            f"Universe: {self.scope} (as_of={self.as_of}{count})\n"
            f"  ⚠ {self.survivorship_note}"
        )


DEFAULT_UNIVERSE_POLICY = UniversePolicy(
    scope="liquid_large_cap",
    as_of="today",
    survivorship_note=(
        "Watchlist hand-picked at fork time (2026-05-14) from symbols that are "
        "liquid US mega-caps today — i.e. survivors. Backtests over earlier "
        "history carry forward-looking selection (survivorship) bias: names "
        "that crashed, shrank or delisted never enter the sample. The delisting "
        "bias is small for mega-caps (rare mid-sample delistings) but the "
        "selection bias is unbounded for broader universes — do not extrapolate "
        "these results beyond this watchlist."
    ),
)


def validate_universe_policy(block: Any) -> bool:
    """Validate a universe_policy block. Raises ValueError if invalid."""
    if not isinstance(block, dict):
        raise ValueError("universe_policy must be a dict")
    unknown = set(block) - _UNIVERSE_POLICY_FIELDS
    if unknown:
        raise ValueError(f"universe_policy: unknown field(s) {sorted(unknown)}")
    missing = _UNIVERSE_POLICY_FIELDS - set(block)
    if missing:
        raise ValueError(
            f"universe_policy: missing required field(s) {sorted(missing)}"
        )
    for field_name in ("scope", "survivorship_note"):
        value = block[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"universe_policy: {field_name} must be a non-empty string"
            )
    if block["as_of"] not in _VALID_AS_OF:
        raise ValueError(
            f"universe_policy: as_of must be one of {sorted(_VALID_AS_OF)}, "
            f"got {block['as_of']!r}"
        )
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_coins_config(config_dict: dict[str, Any]) -> bool:
    """Validate the coins.json config dict. Raises ValueError if invalid."""
    if not isinstance(config_dict, dict):
        raise ValueError("Config must be a dict of symbol: {leverage, sl_percent}")

    for symbol, params in config_dict.items():
        if not isinstance(symbol, str):
            raise ValueError(f"Symbol key '{symbol}' is not a string.")
        if not isinstance(params, dict):
            raise ValueError(f"Value for symbol '{symbol}' must be a dict.")
        if "leverage" not in params:
            raise ValueError(f"Symbol '{symbol}' missing 'leverage'.")
        if "sl_percent" not in params:
            raise ValueError(f"Symbol '{symbol}' missing 'sl_percent'.")

        lev = params["leverage"]
        if not _is_number(lev):
            raise ValueError(f"Symbol '{symbol}' leverage must be a number.")
        if not (_MIN_LEVERAGE <= lev <= _MAX_LEVERAGE):
            raise ValueError(
                f"Symbol '{symbol}' leverage {lev} out of range "
                f"({_MIN_LEVERAGE}-{_MAX_LEVERAGE})."
            )

        sl = params["sl_percent"]
        if not _is_number(sl):
            raise ValueError(f"Symbol '{symbol}' sl_percent must be a number.")
        if not (_MIN_SL_PCT <= sl <= _MAX_SL_PCT):
            raise ValueError(
                f"Symbol '{symbol}' sl_percent {sl} out of range "
                f"({_MIN_SL_PCT}-{_MAX_SL_PCT})."
            )
    return True


def validate_stocks_config(config_dict: dict[str, Any]) -> bool:
    """Validate the stocks.json watchlist config. Raises ValueError if invalid.

    Each key is a ticker symbol (e.g. "AAPL", "MSTR"). Each value must have:
      sl_pct: number, 0 < sl_pct < 1.0 — stop-loss as a fraction of price
              (e.g. 0.05 = 5%). Capped strictly below 1.0 to guard against
              the common 5-vs-0.05 typo.
    """
    if not isinstance(config_dict, dict):
        raise ValueError("Config must be a dict of symbol: {sl_pct}")

    for symbol, params in config_dict.items():
        if symbol == UNIVERSE_POLICY_KEY:
            validate_universe_policy(params)
            continue
        if not isinstance(symbol, str):
            raise ValueError(f"Symbol key '{symbol}' is not a string.")
        if not isinstance(params, dict):
            raise ValueError(f"Value for symbol '{symbol}' must be a dict.")
        if "sl_pct" not in params:
            raise ValueError(
                f"stocks.json: symbol '{symbol}' missing required field 'sl_pct'"
            )
        sl = params["sl_pct"]
        if not _is_number(sl):
            raise ValueError(
                f"stocks.json: symbol '{symbol}' sl_pct must be a number, got {sl!r}"
            )
        if not (0 < sl < _MAX_STOCK_SL_FRAC):
            raise ValueError(
                f"stocks.json: symbol '{symbol}' sl_pct {sl} out of range "
                f"(0, {_MAX_STOCK_SL_FRAC})"
            )
    return True


def load_stocks_config(path: Path = _DEFAULT_STOCKS_PATH) -> dict[str, Any]:
    """Load and validate ``config/stocks.json``. Raises if missing or invalid."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — copy stocks.json.example to {path} and edit."
        )
    with path.open() as f:
        config: dict[str, Any] = json.load(f)
    validate_stocks_config(config)
    config.pop(UNIVERSE_POLICY_KEY, None)
    return config


def load_universe_policy(path: Path = _DEFAULT_STOCKS_PATH) -> UniversePolicy:
    """Return the universe_policy from stocks.json, or the honest default.

    Missing file or absent block → DEFAULT_UNIVERSE_POLICY (describes current
    behaviour). A present-but-invalid block raises ValueError, matching
    load_stocks_config's fail-loud validation.
    """
    if not path.exists():
        return DEFAULT_UNIVERSE_POLICY
    with path.open() as f:
        config: dict[str, Any] = json.load(f)
    block = config.get(UNIVERSE_POLICY_KEY)
    if block is None:
        return DEFAULT_UNIVERSE_POLICY
    validate_universe_policy(block)
    return UniversePolicy(
        scope=block["scope"],
        as_of=block["as_of"],
        survivorship_note=block["survivorship_note"],
    )
