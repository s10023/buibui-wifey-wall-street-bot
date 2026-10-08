import json
import re
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
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}}),
            encoding="utf-8",
        )
        loaded = load_stocks_config(path)
        assert list(loaded.keys()) == ["AAPL"]


class TestLoadUniversePolicy:
    """Tests for load_universe_policy()."""

    def test_missing_file_returns_default(self, tmp_path: Path) -> None:
        assert load_universe_policy(tmp_path / "absent.json") == DEFAULT_UNIVERSE_POLICY

    def test_absent_block_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(json.dumps({"AAPL": {"sl_pct": 0.05}}), encoding="utf-8")
        assert load_universe_policy(path) == DEFAULT_UNIVERSE_POLICY

    def test_block_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: _POLICY_BLOCK, "AAPL": {"sl_pct": 0.05}}),
            encoding="utf-8",
        )
        policy = load_universe_policy(path)
        assert policy.scope == "liquid_large_cap"
        assert policy.as_of == "today"

    def test_invalid_block_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "stocks.json"
        path.write_text(
            json.dumps({UNIVERSE_POLICY_KEY: {"scope": "x"}}), encoding="utf-8"
        )
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

    def _staggered(self) -> ResearchUniverse:
        # membership_as_of 2026-06-16; OLD has full history (listed=None),
        # MID listed ~6yr ago, NEW listed ~2yr ago.
        return ResearchUniverse(
            policy=UniversePolicy(
                scope="liquid_large_cap_breadth",
                as_of="today",
                survivorship_note="bounded to today's survivors",
            ),
            membership_as_of="2026-06-16",
            members=(
                UniverseMember("OLD", "Energy", "stock", False),
                UniverseMember(
                    "MID", "Industrials", "stock", False, listed="2020-09-30"
                ),
                UniverseMember(
                    "NEW", "Information Technology", "stock", False, listed="2024-03-27"
                ),
            ),
        )

    def test_with_min_history_drops_short_history_names(self) -> None:
        # require ~3yr of history: NEW (≈2.2yr) drops, MID (≈5.7yr) and OLD stay
        filtered = self._staggered().with_min_history(min_history_days=365 * 3)
        assert filtered.symbols() == ["OLD", "MID"]

    def test_with_min_history_keeps_untagged_full_history(self) -> None:
        # even a huge cap keeps listed=None members (assumed full history)
        filtered = self._staggered().with_min_history(min_history_days=365 * 100)
        assert filtered.symbols() == ["OLD"]

    def test_with_min_history_boundary_inclusive(self) -> None:
        # MID listed exactly cap days before membership_as_of stays (>= is kept)
        uni = self._staggered()
        # 2026-06-16 minus 2020-09-30 = 2085 days
        filtered = uni.with_min_history(min_history_days=2085)
        assert "MID" in filtered.symbols()

    def test_with_min_history_zero_is_identity(self) -> None:
        assert self._staggered().with_min_history(min_history_days=0).symbols() == [
            "OLD",
            "MID",
            "NEW",
        ]

    def test_with_min_history_returns_new_universe(self) -> None:
        uni = self._staggered()
        filtered = uni.with_min_history(min_history_days=365 * 100)
        # original is untouched (frozen dataclass, pure filter)
        assert uni.symbols() == ["OLD", "MID", "NEW"]
        assert filtered is not uni


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

    def test_optional_listed_accepted(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"]["listed"] = "2020-09-30"
        assert validate_research_universe(cfg) is True

    def test_bad_listed_format_rejected(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"]["listed"] = "09/30/2020"
        with pytest.raises(ValueError, match="listed must be a 'YYYY-MM-DD'"):
            validate_research_universe(cfg)

    def test_unknown_field_still_rejected(self) -> None:
        cfg = self._valid()
        cfg["members"]["AAPL"]["ipo"] = "2020-09-30"
        with pytest.raises(ValueError, match="unknown field"):
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
        p.write_text(json.dumps(payload), encoding="utf-8")
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

    def test_parses_listed_field(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["members"]["AAPL"]["listed"] = "2020-09-30"
        path = self._write(tmp_path, payload)
        uni = load_research_universe(path)
        members = {m.symbol: m for m in uni.members}
        assert members["AAPL"].listed == "2020-09-30"
        assert members["SPY"].listed is None

    def test_min_history_days_filters_at_load(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["members"]["NEW"] = {
            "sector": "Information Technology",
            "kind": "stock",
            "delisted": False,
            "listed": "2024-03-27",
        }
        path = self._write(tmp_path, payload)
        # default keeps everything
        assert load_research_universe(path).symbols() == ["AAPL", "SPY", "NEW"]
        # a 3yr floor drops the 2024 listing only
        filtered = load_research_universe(path, min_history_days=365 * 3)
        assert filtered.symbols() == ["AAPL", "SPY"]


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

    def test_shipped_short_history_names_are_tagged(self) -> None:
        """Every post-floor listing carries its first-1d-bar date.

        Stamped from DB ground truth by ``tools/stamp_universe_listed.py``. Until
        2026-08-13 only **3 of 505** members were tagged (GEV/PLTR/UBER, by hand),
        so ``min_history_days`` filtered almost nothing — FDXF cleared an 8-year
        floor on 17 bars, because an absent ``listed`` means "full-history
        survivor". The tool reproduced all three hand-stamped dates exactly and
        added the other 23.
        """
        uni = load_research_universe(Path("config/universe.json"))
        listed = {m.symbol: m.listed for m in uni.members if m.listed is not None}
        assert listed == {
            "VRT": "2018-08-02",
            "MRNA": "2018-12-07",
            "FOXA": "2019-03-12",
            "DOW": "2019-03-20",
            "UBER": "2019-05-10",
            "CTVA": "2019-05-24",
            "CRWD": "2019-06-12",
            "DDOG": "2019-09-19",
            "CARR": "2020-03-19",
            "OTIS": "2020-03-19",
            "PLTR": "2020-09-30",
            "DASH": "2020-12-09",
            "ABNB": "2020-12-10",
            "EXE": "2021-02-10",
            "COIN": "2021-04-14",
            "APP": "2021-04-15",
            "HOOD": "2021-07-29",
            "CEG": "2022-01-19",
            "GEHC": "2022-12-15",
            "KVUE": "2023-05-04",
            "VLTO": "2023-10-04",
            "SOLV": "2024-03-26",
            "GEV": "2024-03-27",
            "SNDK": "2025-02-13",
            "Q": "2025-10-27",
            "FDXF": "2026-05-27",
        }

    def test_no_shipped_listed_date_sits_at_the_truncation_floor(self) -> None:
        """A ``listed`` date at/below the backfill floor is a truncation artifact.

        Our 1d history starts at the universe backfill's ``--since`` (2018-01-01 →
        first NYSE session 2018-01-02), so 477 survivors share that first bar and
        it carries no listing information. Stamping it would assert a listing date
        the sample cannot evidence and would make ``with_min_history`` drop
        full-history names. No DB is reachable from the suite, so this is the
        invariant that can be checked here — it fails if the stamper's floor rule
        is ever loosened.
        """
        uni = load_research_universe(Path("config/universe.json"))
        floor = "2018-01-02"
        at_or_below = sorted(
            m.symbol for m in uni.members if m.listed is not None and m.listed <= floor
        )
        assert not at_or_below, (
            f"listed <= backfill floor {floor} for {at_or_below} — a truncated "
            "survivor stamped as a listing"
        )

    def test_shipped_min_history_filter_drops_recent_listings(self) -> None:
        """The filter must actually bite — the point of the N3 residual fix.

        Anchored to ``membership_as_of`` (2026-06-16), not today. Before the
        2026-08-13 restamp a 1-year floor dropped **nothing** at all; the counts
        below are the whole reason the seam exists.
        """
        path = Path("config/universe.json")
        full = load_research_universe(path)
        dropped = {
            days: set(full.symbols())
            - set(load_research_universe(path, min_history_days=days).symbols())
            for days in (365, 365 * 3, 365 * 6, 365 * 8)
        }
        # a 1yr floor drops the two shortest histories (FDXF has 17 bars, Q 162)
        assert dropped[365] == {"FDXF", "Q"}
        # each wider floor is a superset of the narrower one, and all 26 tagged
        # names fall out by ~8yr (only the 2018-01-02 floor cohort + the two
        # deeper-history ETFs survive)
        assert dropped[365] < dropped[365 * 3] < dropped[365 * 6] < dropped[365 * 8]
        assert len(dropped[365 * 3]) == 6
        assert len(dropped[365 * 6]) == 16
        assert dropped[365 * 8] == {
            m.symbol for m in full.members if m.listed is not None
        }
        assert len(dropped[365 * 8]) == 26

    def test_note_states_the_true_member_counts(self) -> None:
        """The self-description must match the file, or it is provenance fiction.

        This binding is the durable half of the 2026-08-06 fix. The note claimed
        "~100 liquid US large-caps ... 105 members" for a **508**-member file from
        2026-06-21 (the S&P 500 expansion, PR #98) until 2026-08-06, because
        ``tools/expand_universe_sp500.py`` rewrites ``members`` and never touches
        ``universe_policy``. Nothing failed, and nothing could:
        ``test_shipped_universe_loads`` asserts only ``n_active >= 50``, which waves
        through any size at all. ``describe()`` even printed the true count on one
        line and the false one on the next.
        """
        uni = load_research_universe(Path("config/universe.json"))
        note = uni.policy.survivorship_note

        counts = re.search(r"(\d+) members = (\d+) stocks", note)
        assert counts, f"note must state 'N members = M stocks'; got: {note[:120]}"
        stated_total, stated_stocks = int(counts.group(1)), int(counts.group(2))

        etfs = re.search(r"\+ (\d+) index ETFs", note)
        assert etfs, f"note must state the index-ETF count; got: {note[:120]}"
        stated_etfs = int(etfs.group(1))

        # MEMBERSHIP counts, so they are taken over every member by kind rather
        # than through stocks(), which returns ACTIVE stocks. Conflating the two
        # would make the note go stale the moment any member is flagged delisted
        # — i.e. exactly when the lifecycle seam is doing its job.
        actual_total = len(uni.symbols())
        actual_stocks = sum(1 for m in uni.members if m.kind == "stock")
        assert stated_total == actual_total, (
            f"note says {stated_total} members, file has {actual_total}"
        )
        assert stated_stocks == actual_stocks, (
            f"note says {stated_stocks} stocks, file has {actual_stocks}"
        )
        assert stated_etfs == actual_total - actual_stocks, (
            f"note says {stated_etfs} ETFs, file has {actual_total - actual_stocks}"
        )
        assert stated_stocks + stated_etfs == stated_total, "note is self-inconsistent"

        # The lifecycle split needs its own external referent. Dropping the old
        # "stated_total == n_active" line and replacing it with nothing would
        # leave delisted membership undeclared and unchecked — the same
        # provenance fiction this test exists to prevent, one field over.
        lifecycle = re.search(r"(\d+) members? flagged delisted", note)
        assert lifecycle, f"note must state the delisted count; got: {note[:120]}"
        stated_delisted = int(lifecycle.group(1))
        active = re.search(r"leaving (\d+) active", note)
        assert active, f"note must state the active count; got: {note[:120]}"
        stated_active = int(active.group(1))

        actual_delisted = sum(1 for m in uni.members if m.delisted)
        assert stated_delisted == actual_delisted, (
            f"note says {stated_delisted} delisted, file has {actual_delisted}"
        )
        assert stated_active == uni.n_active, (
            f"note says {stated_active} active, file has {uni.n_active}"
        )
        assert stated_delisted + stated_active == stated_total, (
            "note is self-inconsistent: delisted + active must equal members"
        )

    def test_one_issuer_per_member_no_dual_share_classes(self) -> None:
        """No company may occupy two slots via a second share class.

        This is a cross-sectional research universe, not an index tracker, so it
        deviates from the S&P 500 list on purpose: two near-identical series for one
        issuer would take two slots in any top-N ranking and inject near-collinearity
        into residualisation and beta estimation.

        The S&P 100 selection made this call for GOOG. The 2026-06-21 S&P 500 merge
        silently re-added it — and FOX and NWS with it — because the expander merges
        constituents verbatim and nothing de-duplicates issuers on load. All three
        were removed 2026-08-06. The pair list is checked as data rather than as one
        hard-coded assertion so that a future expansion re-introducing *any* of them
        fails here, naming the pair.
        """
        uni = load_research_universe(Path("config/universe.json"))
        stocks = set(uni.stocks())
        # (redundant class that must stay OUT, sibling that must stay IN)
        dual_class = [("GOOG", "GOOGL"), ("FOX", "FOXA"), ("NWS", "NWSA")]
        both = [f"{a}+{b}" for a, b in dual_class if {a, b} <= stocks]
        assert not both, (
            f"dual share classes of one issuer both present: {both}. "
            "Drop the redundant class (keep the Class A / more liquid ticker) and "
            "update universe_policy.survivorship_note's counts in the same commit."
        )
        # the retained sibling must actually be there — dropping both would be a
        # different bug that the count test alone would not catch
        for _, keep in dual_class:
            assert keep in stocks, f"{keep} should be retained as the surviving class"

    def test_note_declares_the_one_issuer_per_member_rule(self) -> None:
        """The deviation from the S&P 500 list must be stated, not silent.

        A universe that quietly differs from the index it claims to track is the
        same provenance defect as a wrong count — a reader reconciling 501 against
        S&P 500 membership needs the reason in the file, not in a commit message.
        """
        note = load_research_universe(Path("config/universe.json")).policy
        assert "ONE ISSUER PER MEMBER" in note.survivorship_note
        for sym in ("GOOGL", "FOXA", "NWSA"):
            assert sym in note.survivorship_note, (
                f"note must name {sym} as the retained class"
            )


class TestShippedStocksExample:
    """``config/stocks.json.example`` must DECLARE a universe policy.

    The live ``config/stocks.json`` is gitignored, so CI never sees it and no test
    can assert on it — the example is the only committed surface, and it is what a
    fresh clone copies. That asymmetry is exactly how the drift happened: the
    example carried a valid ``universe_policy`` block while the live file (created
    before the block existed) did not, so ``load_universe_policy`` silently
    returned ``DEFAULT_UNIVERSE_POLICY`` and ``backtest_runs.universe_policy``
    recorded an **undeclared default** on every run.

    Nothing recorded was WRONG — the default's text describes this same watchlist —
    which is why it stayed invisible for months: a permissive fallback that happens
    to be accurate reads exactly like a declaration. Guard the example so the
    copy-the-example path yields a declared policy, and keep the live file in sync
    by hand.
    """

    _EXAMPLE = Path("config/stocks.json.example")

    def test_example_is_valid_stocks_config(self) -> None:
        config = json.loads(self._EXAMPLE.read_text(encoding="utf-8"))
        assert validate_stocks_config(config)
        symbols = [k for k in config if k != UNIVERSE_POLICY_KEY]
        assert len(symbols) >= 10, "example should ship a usable watchlist"

    def test_example_declares_a_valid_universe_policy(self) -> None:
        config = json.loads(self._EXAMPLE.read_text(encoding="utf-8"))
        assert UNIVERSE_POLICY_KEY in config, (
            "the example must DECLARE the policy — an absent block falls back to "
            "DEFAULT_UNIVERSE_POLICY, which is a silent default, not a declaration"
        )
        assert validate_universe_policy(config[UNIVERSE_POLICY_KEY])

    def test_example_policy_is_declared_not_defaulted(self, tmp_path: Path) -> None:
        """The load path must return the example's own block, not the fallback.

        This is the assertion with teeth: the two previous tests still pass if the
        loader ignores the block entirely.

        ⚠ IT PERTURBS THE BLOCK RATHER THAN COMPARING TO THE DEFAULT, and that is
        a correctness fix, not a style one. The original asserted
        `policy != DEFAULT_UNIVERSE_POLICY`, which reads as "the loader returned
        the declaration" only while the shipped note happens to differ from the
        default's text. It is a proxy, and the sentence above is the claim: an
        input satisfying the claim but failing the guard is a block that IS
        declared and IS returned but whose value coincides with the default —
        legitimate, and exactly what happened on 2026-08-26 when the declared note
        was restored to the default's fuller bounding wording. The guard was
        stricter than its own sentence, so it forbade a legal state and would have
        forced the config to stay divergent just to keep a test green.

        Perturbing observes the channel directly: write a copy whose note nothing
        else could produce, and require the loader to hand it back. That stays red
        if the loader ignores the block, and stays green however the shipped text
        happens to compare to the default.
        """
        marker = "SENTINEL-declared-block-was-read"
        config = json.loads(self._EXAMPLE.read_text(encoding="utf-8"))
        config[UNIVERSE_POLICY_KEY]["survivorship_note"] = marker
        perturbed = tmp_path / "stocks.json"
        perturbed.write_text(json.dumps(config), encoding="utf-8")

        assert load_universe_policy(perturbed).survivorship_note == marker, (
            "the loader returned the fallback for a file that declares a block"
        )
        # The fallback is still reachable, so the control is not vacuous: an
        # undeclared file must yield exactly DEFAULT_UNIVERSE_POLICY.
        del config[UNIVERSE_POLICY_KEY]
        undeclared = tmp_path / "undeclared.json"
        undeclared.write_text(json.dumps(config), encoding="utf-8")
        assert load_universe_policy(undeclared) == DEFAULT_UNIVERSE_POLICY

        policy = load_universe_policy(self._EXAMPLE)
        assert policy.as_of in ("today", "fixed")
        assert "survivorship" in policy.survivorship_note.lower(), (
            "the bounded claim must survive in the declared note, not only in the "
            "default it replaces"
        )
