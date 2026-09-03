import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
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


_DEFAULT_UNIVERSE_PATH = Path("config/universe.json")
_VALID_MEMBER_KIND = frozenset({"stock", "etf"})
_MEMBERSHIP_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNIVERSE_MEMBER_FIELDS = frozenset({"sector", "kind", "delisted"})
_UNIVERSE_MEMBER_OPTIONAL_FIELDS = frozenset({"listed"})


@dataclass(frozen=True)
class UniverseMember:
    """One member of the research breadth universe (N3).

    delisted is a lifecycle seam, and it is now USED rather than merely present:
    3 of 505 members are flagged (EA, EQR, SATS, 2026-09-02). A flagged member is
    RETAINED, never deleted -- removing it is the survivorship edit this seam
    exists to expose -- so ``symbols()`` stays 505 while ``active_symbols()`` /
    ``stocks()`` / ``n_active`` return 502. PIT membership is still deliberately
    not scraped, so selection bias is bounded rather than eliminated.

    listed is the member's first available 1d bar date (``YYYY-MM-DD``, ≈ the
    first-trading / when-issued date), or ``None`` for a full-history survivor
    that lists on or before the universe backfill start. It is the history seam
    consumed by ``ResearchUniverse.with_min_history`` to exclude short-history
    names from pooled cross-sectional studies.
    """

    symbol: str
    sector: str
    kind: str
    delisted: bool
    listed: str | None = None


@dataclass(frozen=True)
class ResearchUniverse:
    """The research breadth universe — distinct from the live-alert watchlist.

    Carries the Phase 0.1 UniversePolicy verbatim plus a membership snapshot date
    and the per-symbol member list.
    """

    policy: UniversePolicy
    membership_as_of: str
    members: tuple[UniverseMember, ...]

    def symbols(self) -> list[str]:
        """Every member symbol, in declaration order."""
        return [m.symbol for m in self.members]

    def active_symbols(self) -> list[str]:
        """Member symbols that are not flagged delisted (still tradeable)."""
        return [m.symbol for m in self.members if not m.delisted]

    def stocks(self) -> list[str]:
        """Active single-name stocks only (excludes ETFs) — the XS-momentum set."""
        return [m.symbol for m in self.members if not m.delisted and m.kind == "stock"]

    @property
    def n_active(self) -> int:
        """Count of non-delisted members."""
        return len(self.active_symbols())

    def with_min_history(self, min_history_days: int) -> "ResearchUniverse":
        """Return a copy keeping only members with enough history.

        A member qualifies when it has at least ``min_history_days`` of history
        as of ``membership_as_of`` — i.e. its ``listed`` date is on or before
        ``membership_as_of - min_history_days``. Members with no ``listed`` date
        are assumed full-history survivors and are always kept. A cap of ``0``
        (or less) is the identity (every member kept).

        Anchored to the fixed ``membership_as_of`` snapshot (not today's date)
        so the filtered set is reproducible regardless of run time. Pure — no DB
        access; the original is untouched (frozen dataclass).
        """
        if min_history_days <= 0:
            return self
        cutoff = date.fromisoformat(self.membership_as_of) - timedelta(
            days=min_history_days
        )
        kept = tuple(
            m
            for m in self.members
            if m.listed is None or date.fromisoformat(m.listed) <= cutoff
        )
        return ResearchUniverse(
            policy=self.policy,
            membership_as_of=self.membership_as_of,
            members=kept,
        )

    def describe(self) -> str:
        """Multi-line honesty paragraph for coverage-report / CLI output."""
        return (
            f"Research universe: {self.policy.scope} "
            f"(as_of={self.policy.as_of}, membership_as_of={self.membership_as_of}, "
            f"{self.n_active} active members)\n"
            f"  ⚠ {self.policy.survivorship_note}"
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


def validate_research_universe(config_dict: Any) -> bool:
    """Validate a research-universe config dict. Raises ValueError if invalid.

    Shape::

        {
          "universe_policy": {scope, as_of, survivorship_note},
          "membership_as_of": "YYYY-MM-DD",
          "members": {SYMBOL: {sector, kind, delisted}, ...}
        }
    """
    if not isinstance(config_dict, dict):
        raise ValueError("research universe config must be a dict")
    if UNIVERSE_POLICY_KEY not in config_dict:
        raise ValueError("research universe: missing 'universe_policy'")
    validate_universe_policy(config_dict[UNIVERSE_POLICY_KEY])

    as_of = config_dict.get("membership_as_of")
    if not isinstance(as_of, str) or not _MEMBERSHIP_DATE_RE.match(as_of):
        raise ValueError(
            "research universe: membership_as_of must be a 'YYYY-MM-DD' string, "
            f"got {as_of!r}"
        )

    members = config_dict.get("members")
    if members is None:
        raise ValueError("research universe: missing 'members'")
    if not isinstance(members, dict):
        raise ValueError("research universe: 'members' must be a dict")
    if not members:
        raise ValueError("research universe: 'members' must have at least one member")

    for symbol, params in members.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(
                f"research universe: member key {symbol!r} must be a string"
            )
        if not isinstance(params, dict):
            raise ValueError(f"research universe: member '{symbol}' must be a dict")
        missing = _UNIVERSE_MEMBER_FIELDS - set(params)
        if missing:
            raise ValueError(
                f"research universe: member '{symbol}' missing required field "
                f"'{sorted(missing)[0]}'"
            )
        allowed = _UNIVERSE_MEMBER_FIELDS | _UNIVERSE_MEMBER_OPTIONAL_FIELDS
        unknown = set(params) - allowed
        if unknown:
            raise ValueError(
                f"research universe: member '{symbol}' unknown field(s) "
                f"{sorted(unknown)}"
            )
        for field_name in ("sector", "kind"):
            value = params[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"research universe: member '{symbol}' {field_name} must be "
                    "a non-empty string"
                )
        if params["kind"] not in _VALID_MEMBER_KIND:
            raise ValueError(
                f"research universe: member '{symbol}' kind must be one of "
                f"{sorted(_VALID_MEMBER_KIND)}, got {params['kind']!r}"
            )
        if not isinstance(params["delisted"], bool):
            raise ValueError(
                f"research universe: member '{symbol}' delisted must be a bool"
            )
        listed = params.get("listed")
        if listed is not None and (
            not isinstance(listed, str) or not _MEMBERSHIP_DATE_RE.match(listed)
        ):
            raise ValueError(
                f"research universe: member '{symbol}' listed must be a "
                f"'YYYY-MM-DD' string or absent, got {listed!r}"
            )
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_coins_config(config_dict: dict[str, Any]) -> bool:
    """Validate the stocks.json config dict. Raises ValueError if invalid."""
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


def load_research_universe(
    path: Path = _DEFAULT_UNIVERSE_PATH,
    *,
    min_history_days: int | None = None,
) -> ResearchUniverse:
    """Load and validate ``config/universe.json``. Raises if missing or invalid.

    When ``min_history_days`` is given, the loaded universe is passed through
    ``ResearchUniverse.with_min_history`` so short-history names (post-backfill
    listings carrying a ``listed`` date) are excluded — useful for pooled
    cross-sectional studies that need a uniform lookback. ``None`` (the default)
    keeps every member.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — the research breadth universe (N3) lives here."
        )
    with path.open() as f:
        config: dict[str, Any] = json.load(f)
    validate_research_universe(config)
    block = config[UNIVERSE_POLICY_KEY]
    policy = UniversePolicy(
        scope=block["scope"],
        as_of=block["as_of"],
        survivorship_note=block["survivorship_note"],
    )
    members = tuple(
        UniverseMember(
            symbol=symbol,
            sector=params["sector"],
            kind=params["kind"],
            delisted=params["delisted"],
            listed=params.get("listed"),
        )
        for symbol, params in config["members"].items()
    )
    universe = ResearchUniverse(
        policy=policy,
        membership_as_of=config["membership_as_of"],
        members=members,
    )
    if min_history_days is not None:
        universe = universe.with_min_history(min_history_days)
    return universe


#: A Stream-C row with no resolved ticker is unscoreable. Without this guard a JSON
#: ``null`` stringifies to ``"None"`` and becomes a symbol that cannot exist — a
#: yfinance fetch for it fails, and `pundit_score` reports UNRESOLVABLE for the wrong
#: reason. Single definition on purpose: `tools/pundit_score.py` imports this rather
#: than carrying its own copy, because two answers to "what is not a symbol" is how a
#: guard silently stops covering one of its callers.
INVALID_LEDGER_SYMBOLS = frozenset({"", "none", "null", "n/a", "unspecified", "tbd"})

_DEFAULT_PUNDIT_LEDGER_PATH = Path("docs/plans/pundit-calls.jsonl")


def load_pundit_ledger_symbols(
    path: Path = _DEFAULT_PUNDIT_LEDGER_PATH,
) -> list[str]:
    """Return the distinct symbols named by the pundit ledger, sorted.

    This is a *third* universe, and it does not overlap the other two by
    construction: `stocks.json` and `universe.json` hold tradeable equity and ETF
    tickers, while the ledger records whatever underlying a pundit actually quoted —
    index and futures symbols like ``^GSPC`` or ``GC=F`` that no watchlist carries.
    Syncing one set has never refreshed the other, which is why a ledger row can sit
    permanently unresolved while `make go-live` reports success.

    Malformed lines are skipped rather than raised on, matching
    ``pundit_score.load_ledger``: the ledger is append-only research capture, so one
    bad row must not block a refresh of the other fifty.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — the pundit call ledger (Stream C) lives here."
        )
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        symbol = str(obj.get("symbol") or "").strip()
        if symbol.lower() in INVALID_LEDGER_SYMBOLS:
            continue
        seen.add(symbol)
    return sorted(seen)
