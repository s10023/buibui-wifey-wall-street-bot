# Config Reference

Detailed reference for the files under `config/`. Load this when adding a symbol, changing the
research universe, or editing a strategy-params TOML.

Two universes exist and they are **not** the same thing:

| File | Tracked? | Purpose |
| --- | --- | --- |
| `config/stocks.json` | gitignored | the 13-symbol **live-alert watchlist** the daemon scans |
| `config/universe.json` | committed | the 508-member **research breadth universe** the sleeves study |

## config/stocks.json

Phase A US-equities watchlist (gitignored; see `stocks.json.example`). 13 symbols:
AAPL / MSFT / GOOGL / AMZN / META / ORCL / ADBE / NVDA / AMD / TSLA / MSTR / SPY / QQQ.

Schema is `{ticker: {sl_pct: float in (0, 1.0)}}` validated by
`utils.config_validation.validate_stocks_config`, plus an optional reserved top-level
`universe_policy` block (`{scope, as_of: "fixed"|"today", survivorship_note}`, Phase 0.1) declaring
how the watchlist was selected — absent → `DEFAULT_UNIVERSE_POLICY` applies.

## config/universe.json

Phase A research **breadth universe** (N3, **committed/tracked** — a reproducible research
artifact, intentionally distinct from the gitignored `stocks.json` live-alert watchlist).

- **508 members** = 504 stocks (tracking the **current S&P 500** — expanded in experiment #1,
  PR #98, 2026-06-21, from the prior 101-name S&P-100/OEX set via
  `tools/expand_universe_sp500.py`) + 4 index ETFs.
- Each member is `{sector, kind: "stock"|"etf", delisted: bool}`, plus an optional `listed`
  first-trading date (`YYYY-MM-DD`) on post-backfill listings. The file also carries a reused
  `universe_policy` block and a `membership_as_of` snapshot date.
- The pre-expansion 101 S&P-100 stocks are snapshotted to `config/universe_sp100_snapshot.json`
  (the stable "mega" arm of experiment #1's 2×2).
- Two deliberate adjustments were made to the *S&P-100* selection, and **only one survived the
  expansion**: `MSTR` is still kept as a watchlist-carryover survivor, but the `GOOG` drop did
  **not** survive. The 101-name snapshot correctly excludes `GOOG` (keeping `GOOGL` alone, no
  issuer double-count); the S&P 500 merge re-added it, and nothing de-duplicates issuers on load —
  so **`GOOG` and `GOOGL` are both live in `stocks()` today** and Alphabet is counted twice in
  every cross-sectional sleeve. Recorded as a KNOWN DEFECT in the file's own `survivorship_note`
  and bound both ways by `tests/test_config_validation.py::TestShippedUniverseFile`. Fixing it is
  a *research* decision (it changes membership, hence any future sleeve run), not a docs one.
- Loaded via `utils.config_validation.load_research_universe()`
  (`ResearchUniverse` / `UniverseMember`; `validate_research_universe` — `sector` is a free string,
  `kind` ∈ `{stock, etf}`).

**Lifecycle seam.** `delisted` exists so survivorship bias is *visible*: all current members are
survivors, and PIT membership is deliberately not scraped — selection bias is bounded, not
eliminated.

**History seam.** The three post-2018 listings — GEV / PLTR / UBER — carry an optional `listed`
first-trading date sourced from actual DB first-1d-bar ground truth (GEV `2024-03-27`,
PLTR `2020-09-30`, UBER `2019-05-10`), so `load_research_universe(min_history_days=…)` /
`ResearchUniverse.with_min_history(days)` can exclude short-history names from pooled
cross-sectional studies.

**Run:** backfill with `wifey analytics backfill --universe` (or `make wifey-universe-backfill`,
which defaults `SINCE=2018-01-01`); audit coverage with `make universe-coverage`. Re-expand or
refresh membership via `tools/expand_universe_sp500.py` (default scrapes Wikipedia's S&P 500 list;
`--from-csv` for a deterministic/offline run), then re-run the backfill.

## config/strategy_params.toml

Shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml` and
`signal_watch_weekdays.toml`. Contains `[bias]`, `[backtest]` defaults and per-strategy
`volume_suppress` / `volume_spike_boost` flags.

- As of 2026-05-19 the per-strategy `tp_r_long` / `tp_r_short` directional overrides have been
  **retired** (the pin_bar audit closed the last one); directional overrides now live in the
  per-day-filter configs as `tp_r_long_<tf>` / `tp_r_short_<tf>` (the per-TF directional
  architecture from Task A).
- T13 (2026-05-17) removed the parent crypto era's `signal_watch_all.toml`, `conservative.toml`,
  `scalping.toml`, `swing.toml`, and `backtest_sample.toml`; the two surviving signal_watch TOMLs
  cover the equity surface.
