import json
from pathlib import Path
from typing import Any

import pytest

from utils.config_validation import (
    DEFAULT_UNIVERSE_POLICY,
    UNIVERSE_POLICY_KEY,
    ResearchUniverse,
    UniverseMember,
    UniversePolicy,
    load_research_universe,
    load_stocks_config,
    load_universe_policy,
    validate_coins_config,
    validate_research_universe,
    validate_stocks_config,
    validate_universe_policy,
)


class TestValidateCoinsConfig:
    """Tests for validate_coins_config()."""

    def test_valid_config(self, sample_coins_config: dict[str, Any]) -> None:
        """Valid config returns True."""
        assert validate_coins_config(sample_coins_config) is True

    def test_empty_config(self) -> None:
        """Empty dict is valid (no symbols configured)."""
        assert validate_coins_config({}) is True

    def test_single_symbol(self) -> None:
        """Single valid symbol."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_not_a_dict(self) -> None:
        """Non-dict input raises ValueError."""
        with pytest.raises(ValueError, match="Config must be a dict"):
            validate_coins_config("not a dict")  # type: ignore[arg-type]

    def test_list_input(self) -> None:
        """List input raises ValueError."""
        with pytest.raises(ValueError, match="Config must be a dict"):
            validate_coins_config([{"leverage": 25}])  # type: ignore[arg-type]

    def test_missing_leverage(self) -> None:
        """Missing leverage key raises ValueError."""
        config = {"BTCUSDT": {"sl_percent": 2.0}}
        with pytest.raises(ValueError, match="missing 'leverage'"):
            validate_coins_config(config)

    def test_missing_sl_percent(self) -> None:
        """Missing sl_percent key raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25}}
        with pytest.raises(ValueError, match="missing 'sl_percent'"):
            validate_coins_config(config)

    def test_params_not_dict(self) -> None:
        """Non-dict params raises ValueError."""
        config = {"BTCUSDT": "invalid"}
        with pytest.raises(ValueError, match="must be a dict"):
            validate_coins_config(config)

    def test_leverage_not_numeric(self) -> None:
        """Non-numeric leverage raises ValueError."""
        config = {"BTCUSDT": {"leverage": "high", "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="leverage must be a number"):
            validate_coins_config(config)

    def test_leverage_too_low(self) -> None:
        """Leverage below 1 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 0, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_leverage_too_high(self) -> None:
        """Leverage above 150 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 200, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_leverage_boundary_low(self) -> None:
        """Leverage at minimum boundary (1) is valid."""
        config = {"BTCUSDT": {"leverage": 1, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_leverage_boundary_high(self) -> None:
        """Leverage at maximum boundary (150) is valid."""
        config = {"BTCUSDT": {"leverage": 150, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_sl_percent_not_numeric(self) -> None:
        """Non-numeric sl_percent raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": "tight"}}
        with pytest.raises(ValueError, match="sl_percent must be a number"):
            validate_coins_config(config)

    def test_sl_percent_too_low(self) -> None:
        """sl_percent below 0.1 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 0.05}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_sl_percent_too_high(self) -> None:
        """sl_percent above 100 raises ValueError."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 150}}
        with pytest.raises(ValueError, match="out of range"):
            validate_coins_config(config)

    def test_sl_percent_boundary_low(self) -> None:
        """sl_percent at minimum boundary (0.1) is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 0.1}}
        assert validate_coins_config(config) is True

    def test_sl_percent_boundary_high(self) -> None:
        """sl_percent at maximum boundary (100) is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 100}}
        assert validate_coins_config(config) is True

    def test_leverage_as_float(self) -> None:
        """Float leverage is valid."""
        config = {"BTCUSDT": {"leverage": 25.0, "sl_percent": 2.0}}
        assert validate_coins_config(config) is True

    def test_sl_percent_as_int(self) -> None:
        """Integer sl_percent is valid."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": 3}}
        assert validate_coins_config(config) is True

    def test_multiple_symbols_one_invalid(self) -> None:
        """If any symbol is invalid, raises ValueError."""
        config = {
            "BTCUSDT": {"leverage": 25, "sl_percent": 2.0},
            "ETHUSDT": {"leverage": 0, "sl_percent": 2.5},
        }
        with pytest.raises(ValueError, match="ETHUSDT.*out of range"):
            validate_coins_config(config)

    def test_leverage_bool_true_rejected(self) -> None:
        """True (bool) must not pass as valid leverage."""
        config = {"BTCUSDT": {"leverage": True, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="leverage must be a number"):
            validate_coins_config(config)

    def test_leverage_bool_false_rejected(self) -> None:
        """False (bool) must not pass as valid leverage."""
        config = {"BTCUSDT": {"leverage": False, "sl_percent": 2.0}}
        with pytest.raises(ValueError, match="leverage must be a number"):
            validate_coins_config(config)

    def test_sl_percent_bool_rejected(self) -> None:
        """True (bool) must not pass as valid sl_percent."""
        config = {"BTCUSDT": {"leverage": 25, "sl_percent": True}}
        with pytest.raises(ValueError, match="sl_percent must be a number"):
            validate_coins_config(config)


class TestValidateStocksConfig:
    """Tests for validate_stocks_config()."""

    def test_valid_config(self) -> None:
        config = {
            "AAPL": {"sl_pct": 0.05},
            "MSTR": {"sl_pct": 0.08},
        }
        assert validate_stocks_config(config) is True

    def test_empty_config(self) -> None:
        assert validate_stocks_config({}) is True

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="Config must be a dict"):
            validate_stocks_config("not a dict")  # type: ignore[arg-type]

    def test_params_not_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            validate_stocks_config({"AAPL": "invalid"})

    def test_missing_sl_pct(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {}})

    def test_sl_pct_negative(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {"sl_pct": -0.1}})

    def test_sl_pct_zero(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {"sl_pct": 0}})

    def test_sl_pct_not_numeric(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {"sl_pct": "tight"}})

    def test_sl_pct_bool_rejected(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {"sl_pct": True}})

    def test_sl_pct_too_high(self) -> None:
        with pytest.raises(ValueError, match="sl_pct"):
            validate_stocks_config({"AAPL": {"sl_pct": 1.5}})

    def test_multiple_symbols_one_invalid(self) -> None:
        config = {
            "AAPL": {"sl_pct": 0.05},
            "MSTR": {"sl_pct": -0.08},
        }
        with pytest.raises(ValueError, match="MSTR.*sl_pct"):
            validate_stocks_config(config)


_POLICY_BLOCK = {
    "scope": "liquid_large_cap",
    "as_of": "today",
    "survivorship_note": "Hand-picked mega-caps; survivorship bias unbounded.",
}


class TestValidateUniversePolicy:
    """Tests for validate_universe_policy()."""

    def test_valid_block(self) -> None:
        assert validate_universe_policy(_POLICY_BLOCK) is True

    def test_fixed_as_of(self) -> None:
        assert validate_universe_policy({**_POLICY_BLOCK, "as_of": "fixed"}) is True

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            validate_universe_policy("nope")

    def test_missing_field(self) -> None:
        block = {k: v for k, v in _POLICY_BLOCK.items() if k != "survivorship_note"}
        with pytest.raises(ValueError, match="missing required"):
            validate_universe_policy(block)

    def test_unknown_field(self) -> None:
        with pytest.raises(ValueError, match="unknown field"):
            validate_universe_policy({**_POLICY_BLOCK, "selected_at": "2026-05-14"})

    def test_bad_as_of(self) -> None:
        with pytest.raises(ValueError, match="as_of"):
            validate_universe_policy({**_POLICY_BLOCK, "as_of": "yesterday"})

    def test_empty_scope(self) -> None:
        with pytest.raises(ValueError, match="scope"):
            validate_universe_policy({**_POLICY_BLOCK, "scope": "  "})


class TestStocksConfigWithUniversePolicy:
    """The reserved universe_policy key inside stocks.json."""

    def test_policy_block_accepted(self) -> None:
        config: dict[str, Any] = {
            UNIVERSE_POLICY_KEY: dict(_POLICY_BLOCK),
            "AAPL": {"sl_pct": 0.05},
        }
        assert validate_stocks_config(config) is True

    def test_invalid_policy_block_rejected(self) -> None:
        config: dict[str, Any] = {
            UNIVERSE_POLICY_KEY: {"scope": "x"},
            "AAPL": {"sl_pct": 0.05},
        }
        with pytest.raises(ValueError, match="universe_policy"):
            validate_stocks_config(config)

    def test_load_strips_policy_key(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}})
        )
        loaded = load_stocks_config(path)
        assert list(loaded.keys()) == ["AAPL"]


class TestLoadUniversePolicy:
    """Tests for load_universe_policy()."""

    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert load_universe_policy(tmp_path / "absent.json") == DEFAULT_UNIVERSE_POLICY

    def test_absent_block_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(json.dumps({"AAPL": {"sl_pct": 0.05}}))
        assert load_universe_policy(path) == DEFAULT_UNIVERSE_POLICY

    def test_block_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}})
        )
        policy = load_universe_policy(path)
        assert policy.scope == "liquid_large_cap"
        assert policy.as_of == "today"

    def test_invalid_block_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(json.dumps({UNIVERSE_POLICY_KEY: {"scope": "x"}}))
        with pytest.raises(ValueError):
            load_universe_policy(path)


class TestUniversePolicyType:
    """Tests for the UniversePolicy dataclass helpers."""

    def test_summary(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert policy.summary() == "liquid_large_cap (as_of=today)"

    def test_to_json_round_trip(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert json.loads(policy.to_json()) == _POLICY_BLOCK

    def test_describe_contains_note_and_count(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        text = policy.describe(13)
        assert "Universe: liquid_large_cap (as_of=today, 13 symbols)" in text
        assert _POLICY_BLOCK["survivorship_note"] in text

    def test_describe_without_count(self) -> None:
        policy = UniversePolicy(**_POLICY_BLOCK)
        assert "Universe: liquid_large_cap (as_of=today)" in policy.describe()

    def test_default_policy_describes_current_behaviour(self) -> None:
        assert DEFAULT_UNIVERSE_POLICY.scope == "liquid_large_cap"
        assert DEFAULT_UNIVERSE_POLICY.as_of == "today"
        assert "survivorship" in DEFAULT_UNIVERSE_POLICY.survivorship_note


class TestResearchUniverseDataclasses:
    """Tests for UniverseMember + ResearchUniverse carriers."""

    def _universe(self) -> ResearchUniverse:
        return ResearchUniverse(
            policy=UniversePolicy(
                scope="liquid_large_cap_breadth",
                as_of="today",
                survivorship_note="bounded to today's survivors",
            ),
            membership_as_of="2026-06-16",
            members=(
                UniverseMember(
                    symbol="AAPL",
                    sector="Information Technology",
                    kind="stock",
                    delisted=False,
                ),
                UniverseMember(symbol="SPY", sector="ETF", kind="etf", delisted=False),
                UniverseMember(
                    symbol="OLD", sector="Energy", kind="stock", delisted=True
                ),
            ),
        )

    def test_symbols_preserves_order(self) -> None:
        assert self._universe().symbols() == ["AAPL", "SPY", "OLD"]

    def test_active_symbols_drops_delisted(self) -> None:
        assert self._universe().active_symbols() == ["AAPL", "SPY"]

    def test_stocks_filters_to_kind_stock(self) -> None:
        # active stocks only — excludes the ETF and the delisted name
        assert self._universe().stocks() == ["AAPL"]

    def test_n_active_counts_non_delisted(self) -> None:
        assert self._universe().n_active == 2

    def test_describe_mentions_scope_and_membership_date(self) -> None:
        text = self._universe().describe()
        assert "liquid_large_cap_breadth" in text
        assert "2026-06-16" in text
        assert "bounded to today's survivors" in text


class TestValidateResearchUniverse:
    """Tests for validate_research_universe()."""

    def _valid(self) -> dict[str, Any]:
        return {
            "universe_policy": {
                "scope": "liquid_large_cap_breadth",
                "as_of": "today",
                "survivorship_note": "bounded to today's survivors",
            },
            "membership_as_of": "2026-06-16",
            "members": {
                "AAPL": {
                    "sector": "Information Technology",
                    "kind": "stock",
                    "delisted": False,
                },
                "SPY": {"sector": "ETF", "kind": "etf", "delisted": False},
            },
        }

    def test_valid_config(self) -> None:
        assert validate_research_universe(self._valid()) is True

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            validate_research_universe([])

    def test_missing_members(self) -> None:
        cfg = self._valid()
        del cfg["members"]
        with pytest.raises(ValueError, match="missing 'members'"):
            validate_research_universe(cfg)

    def test_empty_members(self) -> None:
        cfg = self._valid()
        cfg["members"] = {}
        with pytest.raises(ValueError, match="at least one member"):
            validate_research_universe(cfg)

    def test_bad_membership_as_of(self) -> None:
        cfg = self._valid()
        cfg["membership_as_of"] = "06/16/2026"
        with pytest.raises(ValueError, match="membership_as_of"):
            validate_research_universe(cfg)

    def test_member_missing_field(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"] = {
            "sector": "Information Technology",
            "kind": "stock",
        }
        with pytest.raises(ValueError, match="missing required field 'delisted'"):
            validate_research_universe(cfg)

    def test_bad_kind(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"]["kind"] = "future"
        with pytest.raises(ValueError, match="kind must be one of"):
            validate_research_universe(cfg)

    def test_delisted_not_bool(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"]["delisted"] = "no"
        with pytest.raises(ValueError, match="delisted must be a bool"):
            validate_research_universe(cfg)

    def test_invalid_policy_propagates(self) -> None:
        cfg = self._valid()
        cfg["universe_policy"]["as_of"] = "yesterday"
        with pytest.raises(ValueError, match="as_of must be one of"):
            validate_research_universe(cfg)


class TestLoadResearchUniverse:
    """Tests for load_research_universe()."""

    def _write(self, tmp_path: Path, payload: dict[str, Any]) -> Path:
        p = tmp_path / "universe.json"
        p.write_text(json.dumps(payload))
        return p

    def _payload(self) -> dict[str, Any]:
        return {
            "universe_policy": {
                "scope": "liquid_large_cap_breadth",
                "as_of": "today",
                "survivorship_note": "bounded to today's survivors",
            },
            "membership_as_of": "2026-06-16",
            "members": {
                "AAPL": {
                    "sector": "Information Technology",
                    "kind": "stock",
                    "delisted": False,
                },
                "SPY": {"sector": "ETF", "kind": "etf", "delisted": False},
            },
        }

    def test_loads_and_parses(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, self._payload())
        uni = load_research_universe(path)
        assert isinstance(uni, ResearchUniverse)
        assert uni.policy.scope == "liquid_large_cap_breadth"
        assert uni.membership_as_of == "2026-06-16"
        assert uni.symbols() == ["AAPL", "SPY"]
        assert uni.stocks() == ["AAPL"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            load_research_universe(tmp_path / "nope.json")

    def test_invalid_file_raises(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["members"]["AAPL"]["kind"] = "future"
        path = self._write(tmp_path, payload)
        with pytest.raises(ValueError, match="kind must be one of"):
            load_research_universe(path)


class TestShippedUniverseFile:
    """The committed config/universe.json must load, validate, and be sane."""

    def test_shipped_universe_loads(self) -> None:
        uni = load_research_universe(Path("config/universe.json"))
        assert uni.n_active >= 50
        # the live-watchlist single names are all present in the breadth set
        for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AMD", "TSLA"):
            assert sym in uni.symbols()
        # ETFs are tagged kind="etf" and excluded from the stocks() cross-section
        assert "SPY" in uni.symbols()
        assert "SPY" not in uni.stocks()
        # bounded claim is declared
        assert uni.policy.as_of == "today"
        assert "survivorship" in uni.policy.survivorship_note.lower()
