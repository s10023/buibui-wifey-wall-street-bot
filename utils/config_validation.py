import json
from pathlib import Path
from typing import Any

_DEFAULT_STOCKS_PATH = Path("config/stocks.json")

_MAX_LEVERAGE = 150
_MIN_LEVERAGE = 1
_MIN_SL_PCT = 0.1
_MAX_SL_PCT = 100

_MAX_STOCK_SL_FRAC = 1.0


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

        if "smt_secondary" in params:
            sec = params["smt_secondary"]
            if not isinstance(sec, str) or not sec:
                raise ValueError(
                    f"Symbol '{symbol}' smt_secondary must be a non-empty string."
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
    return config
