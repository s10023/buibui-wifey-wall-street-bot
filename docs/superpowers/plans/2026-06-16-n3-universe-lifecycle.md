# N3 — Breadth Universe + Lifecycle Data (PR 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a committed, versioned *research breadth universe* (~50 liquid US large-caps,
distinct from the 13-symbol live-alert watchlist) with explicit lifecycle metadata and a
selection-bias bound, plus a read-only coverage report and a universe-aware backfill path.

**Architecture:** A new `config/universe.json` (tracked, not gitignored — it is a reproducible
research artifact, not personal prefs) declares the breadth universe: a reused 3-field
`universe_policy` block (Phase 0.1), a `membership_as_of` snapshot date, and a `members` map
carrying per-symbol `sector`, `kind` (`stock`/`etf`) and `delisted` flags. `utils/config_validation.py`
gains `UniverseMember` + `ResearchUniverse` dataclasses, a `validate_research_universe` validator and
a `load_research_universe` loader — mirroring the existing `validate_stocks_config` /
`load_stocks_config` pattern. The analytics backfill path gains an opt-in `--universe` flag so the
breadth set can be ingested without touching the default live-watchlist behaviour. A new
`tools/universe_coverage.py` reads the DuckDB `ohlcv` table read-only and prints a deterministic
coverage table (bars present, date range, missing symbols) headed by the universe policy's
lifecycle-bias caveat — satisfying the N3 acceptance criteria ("coverage report; every XS surface
carries a lifecycle-bias flag; survivorship claim explicitly bounded").

**Tech Stack:** Python 3.11+, DuckDB (analytics DB), pandas, argparse, pytest + unittest.mock,
Poetry, ruff (format/lint), mypy (strict). No new runtime dependency in PR 1.

---

## Scope & decisions (read before starting)

This plan covers **PR 1 of the N3 track only** — the universe data model + honesty tooling. It is
independently shippable and testable. Subsequent N3 PRs are sketched in the roadmap at the end.

Resolved scope (2026-06-16, with the user):

- **PIT depth = bound-only.** No scraping of historical S&P constituent changes. For ~S&P 100
  mega-caps in-sample delisting ≈ 0, so the value is marginal and delisted *price* series stay
  paywalled regardless. Match gap-map decision #6: bound the claim explicitly, carry a
  lifecycle-bias flag, revisit only if broad-universe claims become necessary (post-G2 XS work).
  The `delisted` field is therefore a **schema seam** (all current members are survivors,
  `delisted: false`) — it exists so the coverage report can flag lifecycle bias honestly and so a
  future PIT ingest has a place to write.
- **Universe size = curated ~50 liquid large-caps** (a sector-diversified superset of the 13
  watchlist names), as a **separate research universe**. The live signal scanner keeps resolving
  symbols from `stocks.json` (13 names) so Telegram is not flooded; the breadth set is for
  backtests / coverage / future cross-sectional momentum. Trivially expandable to ~S&P 100 later by
  adding rows + re-running the universe backfill.
- **Earnings dates = deferred to post-G2** per the data-cost policy. Not in N3 PR 1. The
  `kind`/`sector` fields and a future event-calendar table are the seam; no earnings ingest here.
- **`config/universe.json` is committed (tracked).** This diverges from the `stocks.json`
  gitignore-and-ship-`.example` pattern *on purpose*: the research universe is not personal/secret —
  every backtest and coverage result depends on it, so it must be versioned and reproducible. The
  existing `.gitignore` rule `!config/` already keeps new files in `config/` tracked; no `.gitignore`
  edit is needed and no `.example` is shipped.

Key facts confirmed against the codebase:

- `utils/config_validation.py` already defines `UniversePolicy` (frozen, 3 fields:
  `scope`/`as_of`/`survivorship_note`), `validate_universe_policy`, `DEFAULT_UNIVERSE_POLICY`,
  `UNIVERSE_POLICY_KEY = "universe_policy"`, `_VALID_AS_OF = {"fixed","today"}`. **Reuse these
  unchanged** — do not extend `UniversePolicy`.
- The OHLCV table is named `ohlcv` (not `market_data`); columns include `symbol`, `timeframe`,
  `open_time` (BIGINT, epoch ms). `DEFAULT_DB_PATH` is importable from `analytics.store`.
- Tools pattern (`tools/strategy_edge_audit.py`, `tools/live_outcomes_report.py`): `argparse`,
  `duckdb.connect(str(args.db), read_only=True)`, deterministic ASCII tables, `from analytics.store
  import DEFAULT_DB_PATH`.
- `analytics/analytics_runner.py` has `_resolve_symbols(symbols)` → falls back to
  `load_stocks_config().keys()`; `run_backfill(symbols, timeframes, since_ms, db_path)` and
  `run_sync(...)`. The CLI (`cli/analytics.py`) exposes `--symbols`, `--timeframes`, `--since`.
- Equity timeframes are `4h`, `1d`, `1wk` (the CLI defaults `1h 4h` are crypto-era leftovers —
  always pass equity TFs explicitly).

## File structure

- **Modify** `utils/config_validation.py` — add `UniverseMember`, `ResearchUniverse`,
  `validate_research_universe`, `load_research_universe`, `_DEFAULT_UNIVERSE_PATH`,
  `_VALID_MEMBER_KIND`.
- **Create** `config/universe.json` — committed breadth universe (~50 members) + policy +
  `membership_as_of`.
- **Modify** `analytics/analytics_runner.py` — thread a `use_universe: bool` flag through
  `_resolve_symbols` / `run_backfill` / `run_sync`.
- **Modify** `cli/analytics.py` — add a `--universe` flag to the `backfill` and `sync` subparsers.
- **Create** `tools/universe_coverage.py` — read-only coverage report over `ohlcv`.
- **Modify** `Makefile` — add `wifey-universe-backfill` + `universe-coverage` targets (+ `.PHONY`).
- **Modify** `tests/test_config_validation.py` — validator + loader + shipped-file tests.
- **Create** `tests/test_universe_coverage.py` — coverage-report tests over in-memory DuckDB.
- **Modify** `CLAUDE.md` / `README.md` — document the new config, tools, and CLI flag (handled by
  `/post-branch`, but Task 8 lists the exact edits).

---

## Task 1: `UniverseMember` + `ResearchUniverse` dataclasses

**Files:**

- Modify: `utils/config_validation.py`
- Test: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_validation.py` (import the new names at the top of the file alongside the
existing `utils.config_validation` imports):

```python
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
                UniverseMember(symbol="AAPL", sector="Information Technology",
                               kind="stock", delisted=False),
                UniverseMember(symbol="SPY", sector="ETF", kind="etf", delisted=False),
                UniverseMember(symbol="OLD", sector="Energy", kind="stock", delisted=True),
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_validation.py::TestResearchUniverseDataclasses -v`
Expected: FAIL with `ImportError`/`NameError` (`UniverseMember` / `ResearchUniverse` not defined).

- [ ] **Step 3: Write minimal implementation**

In `utils/config_validation.py`, add after the `DEFAULT_UNIVERSE_POLICY` block (and add
`from collections.abc import Sequence` to the imports if not present — otherwise use `tuple`):

```python
_DEFAULT_UNIVERSE_PATH = Path("config/universe.json")
_VALID_MEMBER_KIND = frozenset({"stock", "etf"})


@dataclass(frozen=True)
class UniverseMember:
    """One member of the research breadth universe (N3).

    delisted is a lifecycle seam: all current members are survivors (False); the
    field exists so the coverage report can flag lifecycle bias and a future PIT
    ingest has somewhere to write.
    """

    symbol: str
    sector: str
    kind: str
    delisted: bool


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
        return [
            m.symbol for m in self.members if not m.delisted and m.kind == "stock"
        ]

    @property
    def n_active(self) -> int:
        """Count of non-delisted members."""
        return len(self.active_symbols())

    def describe(self) -> str:
        """Multi-line honesty paragraph for coverage-report / CLI output."""
        return (
            f"Research universe: {self.policy.scope} "
            f"(as_of={self.policy.as_of}, membership_as_of={self.membership_as_of}, "
            f"{self.n_active} active members)\n"
            f"  ⚠ {self.policy.survivorship_note}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_validation.py::TestResearchUniverseDataclasses -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/config_validation.py tests/test_config_validation.py
git commit -m "feat(universe): UniverseMember + ResearchUniverse carriers (N3 PR1)"
```

---

## Task 2: `validate_research_universe`

**Files:**

- Modify: `utils/config_validation.py`
- Test: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_validation.py`:

```python
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
                "AAPL": {"sector": "Information Technology", "kind": "stock",
                         "delisted": False},
                "SPY": {"sector": "ETF", "kind": "etf", "delisted": False},
            },
        }

    def test_valid_config(self) -> None:
        assert validate_research_universe(self._valid()) is True

    def test_not_a_dict(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            validate_research_universe([])  # type: ignore[arg-type]

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
        cfg["members"]["AAPL"] = {"sector": "Information Technology", "kind": "stock"}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_validation.py::TestValidateResearchUniverse -v`
Expected: FAIL (`validate_research_universe` not defined).

- [ ] **Step 3: Write minimal implementation**

In `utils/config_validation.py`, add (after `validate_universe_policy`). Add `import re` to the top
imports if not present:

```python
_MEMBERSHIP_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNIVERSE_MEMBER_FIELDS = frozenset({"sector", "kind", "delisted"})


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
            raise ValueError(f"research universe: member key {symbol!r} must be a string")
        if not isinstance(params, dict):
            raise ValueError(f"research universe: member '{symbol}' must be a dict")
        missing = _UNIVERSE_MEMBER_FIELDS - set(params)
        if missing:
            raise ValueError(
                f"research universe: member '{symbol}' missing required field "
                f"'{sorted(missing)[0]}'"
            )
        unknown = set(params) - _UNIVERSE_MEMBER_FIELDS
        if unknown:
            raise ValueError(
                f"research universe: member '{symbol}' unknown field(s) {sorted(unknown)}"
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
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_validation.py::TestValidateResearchUniverse -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/config_validation.py tests/test_config_validation.py
git commit -m "feat(universe): validate_research_universe schema validator (N3 PR1)"
```

---

## Task 3: `load_research_universe`

**Files:**

- Modify: `utils/config_validation.py`
- Test: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_validation.py`:

```python
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
                "AAPL": {"sector": "Information Technology", "kind": "stock",
                         "delisted": False},
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
        with pytest.raises(FileNotFoundError, match="universe.json"):
            load_research_universe(tmp_path / "nope.json")

    def test_invalid_file_raises(self, tmp_path: Path) -> None:
        payload = self._payload()
        payload["members"]["AAPL"]["kind"] = "future"
        path = self._write(tmp_path, payload)
        with pytest.raises(ValueError, match="kind must be one of"):
            load_research_universe(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_validation.py::TestLoadResearchUniverse -v`
Expected: FAIL (`load_research_universe` not defined).

- [ ] **Step 3: Write minimal implementation**

In `utils/config_validation.py`, add after `load_universe_policy`:

```python
def load_research_universe(path: Path = _DEFAULT_UNIVERSE_PATH) -> ResearchUniverse:
    """Load and validate ``config/universe.json``. Raises if missing or invalid."""
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
        )
        for symbol, params in config["members"].items()
    )
    return ResearchUniverse(
        policy=policy,
        membership_as_of=config["membership_as_of"],
        members=members,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_validation.py::TestLoadResearchUniverse -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add utils/config_validation.py tests/test_config_validation.py
git commit -m "feat(universe): load_research_universe loader (N3 PR1)"
```

---

## Task 4: Ship `config/universe.json` (committed breadth universe)

**Files:**

- Create: `config/universe.json`
- Test: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config_validation.py` (this guards the *real* shipped file):

```python
class TestShippedUniverseFile:
    """The committed config/universe.json must load, validate, and be sane."""

    def test_shipped_universe_loads(self) -> None:
        uni = load_research_universe(Path("config/universe.json"))
        assert uni.n_active >= 50
        # the 13 live-watchlist single names are all present in the breadth set
        for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AMD", "TSLA"):
            assert sym in uni.symbols()
        # ETFs are tagged kind="etf" and excluded from the stocks() cross-section
        assert "SPY" in uni.symbols()
        assert "SPY" not in uni.stocks()
        # bounded claim is declared
        assert uni.policy.as_of == "today"
        assert "survivorship" in uni.policy.survivorship_note.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_validation.py::TestShippedUniverseFile -v`
Expected: FAIL with `FileNotFoundError` (file not created yet).

- [ ] **Step 3: Create the file**

Create `config/universe.json` with this exact content (50 stocks + 4 ETFs = 54 members; a
sector-diversified superset of the 13 live-watchlist names). `delisted` is `false` for every member
(all current survivors — the field is the lifecycle seam, see Task 1):

```json
{
  "universe_policy": {
    "scope": "liquid_large_cap_breadth",
    "as_of": "today",
    "survivorship_note": "Research breadth universe (~50 liquid US large-caps + 4 index/sector ETFs) selected 2026-06-16 from today's survivors. This is a forward-looking selection over the backtested history: it carries survivorship/selection bias because names that crashed, shrank, were acquired or delisted never enter the sample. PIT constituent membership was deliberately NOT scraped (gap-map decision #6: bound the claim, do not chase paywalled delisted-price history). Bound every cross-sectional result to this fixed membership; do not extrapolate beyond it."
  },
  "membership_as_of": "2026-06-16",
  "members": {
    "AAPL": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "MSFT": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "NVDA": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "AVGO": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "ORCL": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "ADBE": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "CRM": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "AMD": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "CSCO": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "ACN": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "TXN": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "QCOM": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "INTC": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "IBM": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "MSTR": { "sector": "Information Technology", "kind": "stock", "delisted": false },
    "GOOGL": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "META": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "NFLX": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "DIS": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "CMCSA": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "VZ": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "T": { "sector": "Communication Services", "kind": "stock", "delisted": false },
    "AMZN": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "TSLA": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "HD": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "MCD": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "NKE": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "LOW": { "sector": "Consumer Discretionary", "kind": "stock", "delisted": false },
    "WMT": { "sector": "Consumer Staples", "kind": "stock", "delisted": false },
    "PG": { "sector": "Consumer Staples", "kind": "stock", "delisted": false },
    "KO": { "sector": "Consumer Staples", "kind": "stock", "delisted": false },
    "PEP": { "sector": "Consumer Staples", "kind": "stock", "delisted": false },
    "COST": { "sector": "Consumer Staples", "kind": "stock", "delisted": false },
    "BRK-B": { "sector": "Financials", "kind": "stock", "delisted": false },
    "JPM": { "sector": "Financials", "kind": "stock", "delisted": false },
    "V": { "sector": "Financials", "kind": "stock", "delisted": false },
    "MA": { "sector": "Financials", "kind": "stock", "delisted": false },
    "BAC": { "sector": "Financials", "kind": "stock", "delisted": false },
    "WFC": { "sector": "Financials", "kind": "stock", "delisted": false },
    "GS": { "sector": "Financials", "kind": "stock", "delisted": false },
    "AXP": { "sector": "Financials", "kind": "stock", "delisted": false },
    "UNH": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "JNJ": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "LLY": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "ABBV": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "MRK": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "PFE": { "sector": "Health Care", "kind": "stock", "delisted": false },
    "XOM": { "sector": "Energy", "kind": "stock", "delisted": false },
    "CVX": { "sector": "Energy", "kind": "stock", "delisted": false },
    "CAT": { "sector": "Industrials", "kind": "stock", "delisted": false },
    "HON": { "sector": "Industrials", "kind": "stock", "delisted": false },
    "SPY": { "sector": "ETF", "kind": "etf", "delisted": false },
    "QQQ": { "sector": "ETF", "kind": "etf", "delisted": false },
    "IWM": { "sector": "ETF", "kind": "etf", "delisted": false },
    "DIA": { "sector": "ETF", "kind": "etf", "delisted": false }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_validation.py::TestShippedUniverseFile -v`
Expected: PASS.

- [ ] **Step 5: Confirm the file is tracked (not gitignored) and commit**

```bash
git check-ignore config/universe.json; echo "exit=$?"   # expect: exit=1 (NOT ignored)
git add config/universe.json tests/test_config_validation.py
git commit -m "feat(universe): ship config/universe.json breadth universe (N3 PR1)"
```

If `git check-ignore` prints the path (exit=0), STOP — a `.gitignore` rule is catching it; do not
force-add. Investigate the rule first.

---

## Task 5: `--universe` opt-in for backfill / sync

**Files:**

- Modify: `analytics/analytics_runner.py`
- Modify: `cli/analytics.py`
- Test: `tests/test_analytics_runner.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create/extend `tests/test_analytics_runner.py`:

```python
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from analytics import analytics_runner


class TestResolveSymbols:
    def test_explicit_symbols_win(self) -> None:
        assert analytics_runner._resolve_symbols(["AAPL"], use_universe=True) == ["AAPL"]

    @patch("analytics.analytics_runner.load_stocks_config")
    def test_default_uses_stocks_watchlist(self, mock_stocks: MagicMock) -> None:
        mock_stocks.return_value = {"AAPL": {}, "MSFT": {}}
        with patch("analytics.analytics_runner.load_universe_policy"):
            assert analytics_runner._resolve_symbols(None, use_universe=False) == [
                "AAPL",
                "MSFT",
            ]

    @patch("analytics.analytics_runner.load_research_universe")
    def test_universe_flag_uses_research_universe(self, mock_uni: MagicMock) -> None:
        uni = MagicMock()
        uni.active_symbols.return_value = ["AAPL", "SPY", "JPM"]
        uni.describe.return_value = "Research universe: ..."
        mock_uni.return_value = uni
        assert analytics_runner._resolve_symbols(None, use_universe=True) == [
            "AAPL",
            "SPY",
            "JPM",
        ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_analytics_runner.py -v`
Expected: FAIL (`_resolve_symbols` takes 1 positional arg, no `use_universe`).

- [ ] **Step 3: Write minimal implementation**

In `analytics/analytics_runner.py`, update the import line and the three functions:

```python
from utils.config_validation import (
    load_research_universe,
    load_stocks_config,
    load_universe_policy,
)
```

```python
def _resolve_symbols(symbols: list[str] | None, *, use_universe: bool = False) -> list[str]:
    if symbols:
        return symbols
    if use_universe:
        try:
            universe = load_research_universe()
        except Exception as e:
            logging.error("Failed to load research universe: %s", e)
            sys.exit(1)
        resolved = universe.active_symbols()
        logging.info("%s", universe.describe())
        return resolved
    try:
        resolved = list(load_stocks_config().keys())
    except Exception as e:
        logging.error("Failed to load stocks config: %s", e)
        sys.exit(1)
    policy = load_universe_policy()
    logging.info("Universe: %s (%d symbols)", policy.summary(), len(resolved))
    return resolved
```

Update `run_backfill` and `run_sync` to accept and forward the flag:

```python
def run_backfill(
    symbols: list[str] | None,
    timeframes: list[str],
    since_ms: int,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    use_universe: bool = False,
) -> None:
    resolved = _resolve_symbols(symbols, use_universe=use_universe)
    with _open_session(db_path) as conn:
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Backfilling %s %s ...", symbol, timeframe)
                total = backfill(conn, symbol, timeframe, since_ms)
                logging.info(
                    "Backfill complete: %s %s — %d rows", symbol, timeframe, total
                )


def run_sync(
    symbols: list[str] | None,
    timeframes: list[str],
    db_path: Path = DEFAULT_DB_PATH,
    *,
    use_universe: bool = False,
) -> None:
    resolved = _resolve_symbols(symbols, use_universe=use_universe)
    with _open_session(db_path) as conn:
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Syncing %s %s ...", symbol, timeframe)
                try:
                    total = sync(conn, symbol, timeframe)
                    logging.info(
                        "Sync complete: %s %s — %d new rows", symbol, timeframe, total
                    )
                except ValueError as e:
                    logging.warning("%s — skipping (run backfill first)", e)
```

In `cli/analytics.py`, thread the flag through both runners and add the argparse option to both
subparsers:

```python
def run_analytics_backfill(args: argparse.Namespace) -> None:
    analytics_runner.run_backfill(
        symbols=args.symbols,
        timeframes=args.timeframes,
        since_ms=parse_since_to_ms(args.since),
        use_universe=args.universe,
    )


def run_analytics_sync(args: argparse.Namespace) -> None:
    analytics_runner.run_sync(
        symbols=args.symbols,
        timeframes=args.timeframes,
        use_universe=args.universe,
    )
```

Add to each subparser (after its `--timeframes` argument), backfill and sync alike:

```python
    backfill_parser.add_argument(
        "--universe",
        action="store_true",
        help="Resolve symbols from config/universe.json (research breadth universe) "
        "instead of the stocks.json live watchlist",
    )
```

```python
    sync_parser.add_argument(
        "--universe",
        action="store_true",
        help="Resolve symbols from config/universe.json (research breadth universe) "
        "instead of the stocks.json live watchlist",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_analytics_runner.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add analytics/analytics_runner.py cli/analytics.py tests/test_analytics_runner.py
git commit -m "feat(universe): --universe opt-in for analytics backfill/sync (N3 PR1)"
```

---

## Task 6: `tools/universe_coverage.py` coverage report

**Files:**

- Create: `tools/universe_coverage.py`
- Test: `tests/test_universe_coverage.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_universe_coverage.py`:

```python
from typing import Any

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools import universe_coverage
from utils.config_validation import (
    ResearchUniverse,
    UniverseMember,
    UniversePolicy,
)

_TF_MS = 4 * 60 * 60 * 1000  # 4h in ms


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, n: int) -> None:
    rows: list[dict[str, Any]] = []
    for i in range(n):
        rows.append(
            {
                "symbol": symbol,
                "timeframe": tf,
                "open_time": i * _TF_MS,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _universe() -> ResearchUniverse:
    return ResearchUniverse(
        policy=UniversePolicy(
            scope="liquid_large_cap_breadth",
            as_of="today",
            survivorship_note="bounded to survivors",
        ),
        membership_as_of="2026-06-16",
        members=(
            UniverseMember(symbol="AAPL", sector="Information Technology",
                           kind="stock", delisted=False),
            UniverseMember(symbol="MSFT", sector="Information Technology",
                           kind="stock", delisted=False),
        ),
    )


def test_build_coverage_rows_counts_bars_and_flags_missing() -> None:
    conn = _conn()
    _seed(conn, "AAPL", "4h", 10)
    # MSFT has no 4h bars -> should show 0 / missing
    rows = universe_coverage.build_coverage_rows(conn, _universe(), ["4h"])
    by_sym = {r.symbol: r for r in rows}
    assert by_sym["AAPL"].bars == 10
    assert by_sym["AAPL"].present is True
    assert by_sym["MSFT"].bars == 0
    assert by_sym["MSFT"].present is False


def test_summarize_reports_missing_symbols() -> None:
    conn = _conn()
    _seed(conn, "AAPL", "4h", 5)
    rows = universe_coverage.build_coverage_rows(conn, _universe(), ["4h"])
    summary = universe_coverage.summarize(rows, ["4h"])
    assert summary.n_symbols == 2
    assert "MSFT" in summary.missing_symbols
    assert "AAPL" not in summary.missing_symbols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_universe_coverage.py -v`
Expected: FAIL (`tools.universe_coverage` has no `build_coverage_rows` / `summarize`).

- [ ] **Step 3: Write minimal implementation**

Create `tools/universe_coverage.py`:

```python
"""Universe coverage report (N3) — read-only OHLCV coverage over the research
breadth universe.

For every (member symbol × timeframe) in config/universe.json, reports bars
present in the analytics DB, the date range, and which members are missing. The
report is headed by the universe policy's lifecycle-bias caveat so every reader
sees the bounded-claim flag. Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/universe_coverage.py
    PYTHONPATH=. poetry run python tools/universe_coverage.py --timeframes 4h 1d 1wk
    PYTHONPATH=. poetry run python tools/universe_coverage.py --db analytics.db
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import ResearchUniverse, load_research_universe

_DEFAULT_TFS = ["4h", "1d", "1wk"]


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    timeframe: str
    kind: str
    sector: str
    bars: int
    first_ms: int | None
    last_ms: int | None

    @property
    def present(self) -> bool:
        return self.bars > 0


@dataclass(frozen=True)
class CoverageSummary:
    n_symbols: int
    n_timeframes: int
    missing_symbols: list[str]


def _fmt_date(ms: int | None) -> str:
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def build_coverage_rows(
    conn: duckdb.DuckDBPyConnection,
    universe: ResearchUniverse,
    timeframes: list[str],
) -> list[CoverageRow]:
    """One CoverageRow per (member, timeframe) — bars present + date range."""
    rows: list[CoverageRow] = []
    for member in universe.members:
        for tf in timeframes:
            res = conn.execute(
                """
                SELECT COUNT(*) AS n, MIN(open_time) AS first_ms, MAX(open_time) AS last_ms
                FROM ohlcv
                WHERE symbol = ? AND timeframe = ?
                """,
                [member.symbol, tf],
            ).fetchone()
            n = int(res[0]) if res is not None else 0
            first_ms = int(res[1]) if res is not None and res[1] is not None else None
            last_ms = int(res[2]) if res is not None and res[2] is not None else None
            rows.append(
                CoverageRow(
                    symbol=member.symbol,
                    timeframe=tf,
                    kind=member.kind,
                    sector=member.sector,
                    bars=n,
                    first_ms=first_ms,
                    last_ms=last_ms,
                )
            )
    return rows


def summarize(rows: list[CoverageRow], timeframes: list[str]) -> CoverageSummary:
    """Roll up coverage rows: symbol count + symbols missing ALL timeframes."""
    symbols = sorted({r.symbol for r in rows})
    missing = sorted(
        sym
        for sym in symbols
        if all(not r.present for r in rows if r.symbol == sym)
    )
    return CoverageSummary(
        n_symbols=len(symbols),
        n_timeframes=len(timeframes),
        missing_symbols=missing,
    )


def _print_report(
    universe: ResearchUniverse,
    rows: list[CoverageRow],
    summary: CoverageSummary,
) -> None:
    print(universe.describe())
    print()
    cols = ["symbol", "kind", "tf", "bars", "first", "last"]
    table = [
        (r.symbol, r.kind, r.timeframe, str(r.bars), _fmt_date(r.first_ms),
         _fmt_date(r.last_ms))
        for r in rows
    ]
    widths = [
        max(len(c), max((len(row[i]) for row in table), default=0))
        for i, c in enumerate(cols)
    ]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for row in table:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))
    print()
    covered = summary.n_symbols - len(summary.missing_symbols)
    print(
        f"Coverage: {covered}/{summary.n_symbols} members have data in "
        f"≥1 of {summary.n_timeframes} timeframe(s)."
    )
    if summary.missing_symbols:
        print(f"  Missing (no data any TF): {', '.join(summary.missing_symbols)}")
        print(
            "  → backfill with: poetry run python wifey.py analytics backfill "
            "--universe --timeframes 4h 1d 1wk --since 2018-01-01"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--universe-path",
        type=Path,
        default=Path("config/universe.json"),
        help="Path to the research universe config",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=_DEFAULT_TFS,
        help="Timeframes to report (default: 4h 1d 1wk)",
    )
    args = parser.parse_args()

    universe = load_research_universe(args.universe_path)
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        rows = build_coverage_rows(conn, universe, args.timeframes)
    finally:
        conn.close()
    summary = summarize(rows, args.timeframes)
    _print_report(universe, rows, summary)


if __name__ == "__main__":
    main()
```

Add an empty `tools/__init__.py` only if `from tools import universe_coverage` fails to import in
the test (check first: `ls tools/__init__.py`). If the existing tools are imported as scripts (not a
package) and the test import fails, prefer `import importlib.util` loading in the test instead of
adding a package marker — match whatever the repo already does for tool imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_universe_coverage.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/universe_coverage.py tests/test_universe_coverage.py
git commit -m "feat(universe): universe coverage report tool (N3 PR1)"
```

---

## Task 7: Makefile convenience targets

**Files:**

- Modify: `Makefile`

- [ ] **Step 1: Add the targets**

Add to the `.PHONY` line: `wifey-universe-backfill universe-coverage`. Then add the targets near the
other `wifey-*` analytics targets (recipe lines below are shown space-indented to
keep markdownlint happy — in the real Makefile they MUST be TAB-indented):

```makefile
wifey-universe-backfill:
    @echo "📥 Backfilling the research breadth universe (config/universe.json)..."
    @poetry run python wifey.py analytics backfill --universe \
        --timeframes 4h 1d 1wk --since $(or $(SINCE),2018-01-01)

universe-coverage:
    @PYTHONPATH=. poetry run python tools/universe_coverage.py \
        $(if $(DB),--db $(DB),)
```

- [ ] **Step 2: Verify the targets parse**

Run: `make -n wifey-universe-backfill universe-coverage`
Expected: prints the underlying commands without error.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build(universe): make targets for universe backfill + coverage (N3 PR1)"
```

---

## Task 8: Full gate + docs + operational backfill

**Files:**

- Modify: `CLAUDE.md`, `README.md`
- Operational: backfill the universe (not committed — `analytics.db` is gitignored)

- [ ] **Step 1: Run the full quality gate**

Run, in order:

```bash
make lint-py
make typecheck
make test
make lint-md
```

Expected: all green. The suite count should rise (~+19 tests from Tasks 1–6). Goldens are NOT
touched (no detector / backtest / cost change), so `make test-regression` stays green untouched.

- [ ] **Step 2: Operational backfill + eyeball the coverage report**

This populates the new ~41 breadth symbols (the 13 watchlist names may already be present). It is an
operational step — the resulting `analytics.db` is gitignored and not part of the PR:

```bash
poetry run python wifey.py analytics backfill --universe --timeframes 4h 1d 1wk --since 2018-01-01
PYTHONPATH=. poetry run python tools/universe_coverage.py
```

Expected: the coverage report prints the lifecycle-bias caveat header, a per-(symbol, tf) table, and
`Coverage: N/54 members have data ...`. Note any symbols yfinance cannot serve (e.g. ticker-format
issues) in the PR description — do NOT silently drop them from `universe.json`.

- [ ] **Step 3: Update docs (CLAUDE.md)**

In `CLAUDE.md`, under the config files list (near the `config/stocks.json` bullet), add:

```markdown
- `config/universe.json` — Phase A research **breadth universe** (N3, committed/tracked — a
  reproducible research artifact, distinct from the gitignored `stocks.json` live-alert watchlist).
  ~50 liquid large-caps + 4 ETFs with per-symbol `sector` / `kind` (`stock`|`etf`) / `delisted`
  lifecycle flags, a reused `universe_policy` block, and a `membership_as_of` snapshot date. Loaded
  via `utils.config_validation.load_research_universe()` (`ResearchUniverse` / `UniverseMember`
  carriers; `validate_research_universe`). `delisted` is a lifecycle seam — all current members are
  survivors (PIT membership deliberately not scraped; selection bias bounded, not eliminated).
```

Under `tools/`, add:

```markdown
  - `universe_coverage.py` — read-only OHLCV coverage report over the research breadth universe;
    bars present + date range per (member × timeframe), missing-symbol roll-up, headed by the
    universe lifecycle-bias caveat. Run via `make universe-coverage` or
    `PYTHONPATH=. poetry run python tools/universe_coverage.py`.
```

Under the `analytics` CLI / `data_sync` notes, mention the new flag:

```markdown
  - `wifey analytics backfill --universe` / `sync --universe` resolves symbols from
    `config/universe.json` (research breadth universe) instead of the `stocks.json` live watchlist;
    default (no flag) is unchanged.
```

- [ ] **Step 4: Update docs (README.md)**

In `README.md`, wherever the watchlist / `stocks.json` and the OHLCV backfill commands are
described, add a short note that the **research breadth universe** lives in `config/universe.json`
and is backfilled with `make wifey-universe-backfill` (or `wifey analytics backfill --universe`),
separate from the live-alert watchlist. Add `make universe-coverage` to any "useful commands" list.

- [ ] **Step 5: Final gate + commit**

```bash
make lint-md
git add CLAUDE.md README.md
git commit -m "docs(universe): document config/universe.json + coverage tool (N3 PR1)"
```

---

## Self-review checklist (run before opening the PR)

- [ ] **Spec coverage** — N3 acceptance criteria: breadth universe (Task 4 ✓), PIT membership
  policy (bound-only, declared in policy + `membership_as_of` ✓), delisting flags at ingest
  (`delisted` seam ✓), coverage report (Task 6 ✓), every XS surface carries a lifecycle-bias flag
  (coverage header + policy note ✓), survivorship claim explicitly bounded (`survivorship_note` ✓).
  Calendar-aware sessions + earnings = subsequent N3 PRs (roadmap below), explicitly out of PR 1.
- [ ] **No placeholders** — every code/JSON block above is complete and runnable.
- [ ] **Type consistency** — `ResearchUniverse.stocks()` / `.active_symbols()` / `.symbols()` /
  `.n_active` / `.describe()`; `UniverseMember(symbol, sector, kind, delisted)`;
  `build_coverage_rows(conn, universe, timeframes)` / `summarize(rows, timeframes)` /
  `CoverageRow.present`; `_resolve_symbols(symbols, *, use_universe=False)`. Names match across
  tasks.
- [ ] Default behaviour unchanged — `_resolve_symbols(None)` still reads `stocks.json`; the live
  scanner and `make wifey-analytics-backfill` are untouched. Goldens unmoved.

---

## Roadmap — subsequent N3 PRs (NOT in this plan)

- **N3 PR 2 — Calendar-aware sessions / gap detection.** Audit inherited 24/7 assumptions from the
  crypto parent; add a US-equity trading calendar (NYSE holidays + RTH) so `analytics/data_quality.py`
  can do calendar-aware gap detection (currently "intentionally out of scope (no trading-calendar
  dependency)"). New runtime dep (`exchange_calendars` or `pandas_market_calendars`, via
  `poetry add`) — its own PR because it adds a dependency and touches the data-quality monitor.
- **N3 PR 3 — Universe expansion to ~S&P 100.** Once the PR-1 plumbing is proven, add the remaining
  rows to `config/universe.json` + re-run `make wifey-universe-backfill`. Pure config + data; trivial.
- **Deferred (post-G2) — Earnings dates as a first-class event calendar.** Per the data-cost policy,
  ingest earnings dates (yfinance/EDGAR) only when PEAD research needs them. The `members` schema +
  a future `events` table are the seam.
