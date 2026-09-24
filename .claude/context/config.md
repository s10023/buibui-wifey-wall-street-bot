# Config Reference

Detailed reference for the files under `config/`. Load this when adding a symbol, changing the
research universe, or editing a strategy-params TOML.

Two universes exist and they are not the same thing:

| File | Tracked? | Purpose |
| --- | --- | --- |
| `config/stocks.json` | gitignored | the 13-symbol live-alert watchlist the daemon scans |
| `config/universe.json` | committed | the 505-member research breadth universe the sleeves study |

## config/stocks.json

Phase A US-equities watchlist (gitignored; see `stocks.json.example`). 13 symbols:
AAPL / MSFT / GOOGL / AMZN / META / ORCL / ADBE / NVDA / AMD / TSLA / MSTR / SPY / QQQ.

Schema is `{ticker: {sl_pct: float in (0, 1.0)}}` validated by
`utils.config_validation.validate_stocks_config`, plus an optional reserved top-level
`universe_policy` block (`{scope, as_of: "fixed"|"today", survivorship_note}`, Phase 0.1) declaring
how the watchlist was selected — absent means `DEFAULT_UNIVERSE_POLICY` applies.

Keep the live `stocks.json` in sync with the committed `stocks.json.example`, which declares this
block, by hand: nothing enforces the two matching. A live file missing the block silently falls
back to `DEFAULT_UNIVERSE_POLICY`, so `backtest_runs.universe_policy` records an undeclared
default rather than an error — measured on 2026-08-14, when the example carried the block and the
live file did not. The default's text happened to describe this same 13-name watchlist, so the
drift produced no wrong values, only an undocumented one, which is why it stood unnoticed. The
live file is gitignored, so CI cannot assert on it directly; `TestShippedStocksExample` guards the
example instead, since that is what a fresh clone copies. Ask what an absent value means before
trusting a loader that has a default.

## config/universe.json

Phase A research breadth universe (N3, committed/tracked — a reproducible research artifact,
intentionally distinct from the gitignored `stocks.json` live-alert watchlist).

- 505 members = 501 stocks (tracking the current S&P 500 — expanded in experiment #1, 2026-06-21,
  from the prior 101-name S&P-100/OEX set via `tools/expand_universe_sp500.py`) + 4 index ETFs.
- Each member is `{sector, kind: "stock"|"etf", delisted: bool}`, plus an optional `listed` first-
  trading date (`YYYY-MM-DD`) on post-backfill listings. The file also carries a reused
  `universe_policy` block and a `membership_as_of` snapshot date.
- The pre-expansion 101 S&P-100 stocks are snapshotted to `config/universe_sp100_snapshot.json`
  (the stable "mega" arm of experiment #1's 2×2).
- Keep one issuer per member: where a company lists two share classes, drop the redundant one and
  keep the Class A / more liquid ticker (`GOOGL` over `GOOG`, `FOXA` over `FOX`, `NWSA` over
  `NWS`). This is a cross-sectional research universe, not an index tracker — two near-identical
  series for one company take two slots in any top-N ranking and inject near-collinearity into
  `xsmom/residual.py`'s regression and into beta estimation. The S&P-100 selection had made this
  call for `GOOG` (the 101-name snapshot correctly excludes it); the 2026-06-21 S&P 500 merge
  silently re-added it and `FOX`/`NWS`, because the expander merges constituents verbatim and
  nothing de-duplicates issuers on load. All three were removed 2026-08-06 (508 → 505). `MSTR` is
  kept separately as a watchlist-carryover survivor. Enforced by
  `tests/test_config_validation.py::TestShippedUniverseFile` — the pair list is checked as data,
  so a future expansion re-introducing any of them fails and names the pair.
- Loaded via `utils.config_validation.load_research_universe()` (`ResearchUniverse` /
  `UniverseMember`; `validate_research_universe` — `sector` is a free string, `kind` is one of
  `stock` or `etf`).

**Lifecycle seam.** `delisted` exists so survivorship bias is visible, and it is used: 3 of 505
members are flagged (`EA`, `EQR`, `SATS`, 2026-09-02). A flagged member is retained, never
deleted — removing it would be the survivorship edit the seam exists to expose — so `symbols()`
stays 505 while `active_symbols()` / `stocks()` / `n_active` return 502. Point-in-time membership
is still deliberately not scraped, so selection bias is bounded, not eliminated.

**History seam.** All 26 post-floor listings carry an optional `listed` first-trading date sourced
from DB first-1d-bar ground truth, so `load_research_universe(min_history_days=…)` /
`ResearchUniverse.with_min_history(days)` can exclude short-history names from pooled
cross-sectional studies. Stamped by `tools/stamp_universe_listed.py`
(`make universe-stamp-listed`, report-only unless `WRITE=1`); pinned by `TestShippedUniverseFile`.

An absent `listed` is the permissive value, which makes this seam fail silently for any member
never stamped: it was hand-stamped on only 3 of 505 members until 2026-08-13, so
`min_history_days` filtered almost nothing over that period — `FDXF` cleared an 8-year floor on 17
bars, because no date reads as "full-history survivor". A 1-year floor dropped 0 members before
the restamp and drops `FDXF` + `Q` after it. A new constituent from `expand_universe_sp500.py`
arrives unstamped, i.e. permissive: re-run the stamper after any membership or backfill change.

The floor itself is not observable, which bounds what `listed` can mean. 1d history starts at the
backfill's `--since` (2018-01-01 → first NYSE session 2018-01-02), and 477 of 505 members share
that first bar, so a bar on the floor cannot distinguish a truncated survivor from a genuine
listing that day. Only a first bar strictly after the floor is stamped; the 2 members reaching
2007 (deeper `wifey-xasset-backfill` history) stay `None` too.
`test_no_shipped_listed_date_sits_at_the_truncation_floor` is the invariant, since the suite
cannot reach the DB.

**Run:** backfill with `wifey analytics backfill --universe` (or `make wifey-universe-backfill`,
which defaults `SINCE=2018-01-01`); audit coverage with `make universe-coverage`; reconcile the
history seam with `make universe-stamp-listed` (`WRITE=1` to apply). Re-expand or refresh
membership via `tools/expand_universe_sp500.py` (default scrapes Wikipedia's S&P 500 list;
`--from-csv` for a deterministic/offline run), then re-run the backfill and the stamper.

## config/strategy_params.toml

Shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml` and
`signal_watch_weekdays.toml`. Contains `[bias]`, `[backtest]` defaults (including
`[backtest.cost_model]` and `[backtest.live_parity]`) and per-strategy `volume_suppress` /
`volume_spike_boost` flags.

**`[backtest.live_parity]` ships five gates on and `conflict_resolver` off, and that split is
load-bearing.** `regime`, `direction_filter`, `f8_htf_ema`, `adr_bias` and `cooldown` are pure
functions of price plus config, so they are safe to run inside the ratings sweep.
`conflict_resolver` reads `confidence_ratings`, so enabling it inside the sweep that produces them
would make `make db-update` a fixed-point iteration rather than a computation: measured over three
full passes, 108 → 66 rows differing, damping but not converging, with cells still oscillating at
iteration 3. With the five safe gates on, two consecutive passes differ on 0 of 160 rows. This
lives in the shared base rather than behind a Makefile flag on purpose, because a flag is exactly
how an ad-hoc `--live-parity` research run and the routine sweep can diverge unnoticed — as they
did for about 2.5 months before this block existed, when the sweep ran every gate off and measured
the raw population while the daemon gated dispatch with all six. `TestSharedBaseGateState` enforces
the five-on state. Full write-up: `docs/audits/2026-08-07-live-parity-ratings-sweep.md`.

**`volume_suppress*` requires `adr_exempt = true`.** The `[bias]` ADR gate keeps only bars that
have consumed little of their typical daily range; `volume_suppress` keeps only bars with ≥1.5×
mean volume. Range and volume correlate at +0.61 (1d) / +0.67 (4h), so the two select for opposite
bars and their conjunction is nearly empty: measured `P(pass both) = 0.0046` against `0.036` under
independence, an 8× shortfall. `load_signal_config` raises on the pairing (`voided_volume_gates`,
the sibling of #139's `dead_timeframes`); `load_backtest_config` inherits it. Before the 2026-08-06
fix this left `doji × 1d` at 0 signals against 1,247 raw detector fires, `orb × 4h` at n=1, and
inverted the measured sign on `engulfing × 1d` and `bos × 1d`. Full write-up:
`docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

- `adr_exempt` belongs in this shared base, never in one day-filter config — a flag whose
  correctness depends on a second flag must live beside it. `bos` once carried it in
  `signal_watch.toml` only, so `signal_watch_weekdays.toml` never inherited it and ran `bos`
  voided; `eqh_eql` had the same shape and was moved to the shared base on 2026-08-14. Its
  residue was narrower and had no loud failure mode, because `eqh_eql` declares no
  `volume_suppress` in either config, so the voided conjunction could not fire and
  `voided_volume_gates` would have refused the pairing anyway. What was live there was pure
  cross-config incomparability: `signal_watch`'s `eqh_eql × 4h` ran exempt while
  `signal_watch_weekdays`'s took the ADR haircut, so the two configs measured different
  populations for one cell — scoped to `4h` only, since `adr_gate_applies` is intraday-only. A
  fix that removes a defect's loud symptom does not remove the defect from its siblings: the
  `bos` conjunction was what made the missing `adr_exempt` visible in the first place, and the
  same inheritance gap survived in `eqh_eql` for eight more days because of it. When a rule is
  written because one instance broke, scan for every other instance of the rule, not of the
  symptom that surfaced it.
- Every `tp_r` / `atr_sl_multiplier` for `doji`, `orb`, `engulfing`, `bos` was calibrated under the
  conjunction, on the surviving 2–8% subsample — flagged as debt in both configs and deliberately
  not re-derived (frozen TA-sweep work).

**Sweep this file by date, not by flag name: everything not yet re-derived is crypto-era.**
Everything here was inherited from the crypto parent at the 2026-05-14 fork and is
Binance-calibrated until re-derived. One sweep (PR #141) removed four `volume_suppress*` flags of
that vintage; a second (PR #143) then found `bos.suppress_long` from the same origin and the same
week, one grep away, still untouched — because it greps as a direction flag rather than a volume
flag. The durable query is `git log -S "<flag> = true" -- config/`: a parent commit dated on or
before 2026-05-14 means crypto-era. The parent's PR numbering runs from #300 up, against wifey's
own numbering from #22 up, which narrows the search, but the date is the actual test.

- The cheapest tell is the cited `n`. `bos.suppress_long` claimed avg_r=−0.268R on n=34,767 out of
  n=72,643; this repo's entire `backtest_trades` table holds 24,196 rows. A sample larger than the
  whole database is not equity data.
- Re-derivation can invert a flag's direction, not merely weaken it: on equities `bos`'s direction
  claim flips sign on both timeframes. Treat an inherited flag's correct prior as unknown, never as
  attenuated.
- Soft mode hides this class of error rather than defusing it. `[bias.direction_filter]` ships
  `mode = "soft"` (logs, keeps), so a wrong flag costs nothing today — while that block instructs a
  future session to flip it to hard after "≥2 weeks". Doing so before re-deriving `bos` would have
  suppressed its better leg.
- Guarded by `tests/test_signal_config.py::test_no_shipped_strategy_sets_direction_suppress`, the
  sibling of #141's volume guard. Still unswept: `[bias.regime.per_strategy]` (`bos =
  ["high_vol", "range"]` is from the same 2026-05-13 audit), `atr_sl_multiplier_*`, and the `tp_r`
  book. Audit: `docs/audits/2026-08-06-bos-timeframe-and-crypto-era-direction-flag.md`.

**`[strategy_timeframes]` is per-config, and the two configs are two populations.** `bos` is
restricted to `["1d"]` in `signal_watch.toml` but deliberately unrestricted in
`signal_watch_weekdays.toml`. That asymmetry is the measurement, not an oversight: across the five
`bos` cells, Benjamini–Hochberg at FDR 0.05 condemns `signal_watch 4h` (−0.282, p=0.0012) but not
`weekdays 4h` (−0.143, p=0.054, n=495 vs 316). A finding on one config is not a finding on the
other — never mirror a config edit because the configs look alike.

- Per-strategy `tp_r_long` / `tp_r_short` directional overrides do not exist; directional overrides
  live in the per-day-filter configs instead, as `tp_r_long_<tf>` / `tp_r_short_<tf>` (the per-TF
  directional architecture from Task A). The pin_bar audit (2026-05-19) closed the last
  strategy-level override.
- T13 (2026-05-17) removed the parent crypto era's `signal_watch_all.toml`, `conservative.toml`,
  `scalping.toml`, `swing.toml`, and `backtest_sample.toml`; the two surviving signal_watch TOMLs
  cover the equity surface.
