# Config Reference

Detailed reference for the files under `config/`. Load this when adding a symbol, changing the
research universe, or editing a strategy-params TOML.

Two universes exist and they are **not** the same thing:

| File | Tracked? | Purpose |
| --- | --- | --- |
| `config/stocks.json` | gitignored | the 13-symbol **live-alert watchlist** the daemon scans |
| `config/universe.json` | committed | the 505-member **research breadth universe** the sleeves study |

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

- **505 members** = 501 stocks (tracking the **current S&P 500** — expanded in experiment #1,
  PR #98, 2026-06-21, from the prior 101-name S&P-100/OEX set via
  `tools/expand_universe_sp500.py`) + 4 index ETFs.
- Each member is `{sector, kind: "stock"|"etf", delisted: bool}`, plus an optional `listed`
  first-trading date (`YYYY-MM-DD`) on post-backfill listings. The file also carries a reused
  `universe_policy` block and a `membership_as_of` snapshot date.
- The pre-expansion 101 S&P-100 stocks are snapshotted to `config/universe_sp100_snapshot.json`
  (the stable "mega" arm of experiment #1's 2×2).
- **ONE ISSUER PER MEMBER — a deliberate deviation from the S&P 500 list.** Where a company
  lists two share classes the redundant one is dropped and the Class A / more liquid ticker
  kept: `GOOGL` over `GOOG`, `FOXA` over `FOX`, `NWSA` over `NWS`. This is a cross-sectional
  research universe, not an index tracker — two near-identical series for one company take two
  slots in any top-N ranking and inject near-collinearity into `xsmom/residual.py`'s regression
  and into beta estimation. The S&P-100 selection made this call for `GOOG` (the 101-name
  snapshot correctly excludes it); the 2026-06-21 S&P 500 merge silently re-added it **and**
  `FOX`/`NWS`, because the expander merges constituents verbatim and nothing de-duplicates
  issuers on load. All three removed 2026-08-06 (508 → 505). `MSTR` is separately kept as a
  watchlist-carryover survivor. Enforced by
  `tests/test_config_validation.py::TestShippedUniverseFile` — the pair list is checked as
  data, so a future expansion re-introducing any of them fails and names the pair.
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
`signal_watch_weekdays.toml`. Contains `[bias]`, `[backtest]` defaults (including
`[backtest.cost_model]` and `[backtest.live_parity]`) and per-strategy `volume_suppress` /
`volume_spike_boost` flags.

**`[backtest.live_parity]` — five gates on, `conflict_resolver` OFF and that is load-bearing**
(2026-08-07). Until this block existed the ratings sweep ran every gate off, so `confidence_ratings`
measured the raw population while the daemon gated dispatch with all six. `regime`,
`direction_filter`, `f8_htf_ema`, `adr_bias` and `cooldown` are pure functions of price + config, so
they are safe. `conflict_resolver` **reads `confidence_ratings`**, so enabling it inside the sweep
that produces them makes `make db-update` a fixed-point iteration rather than a computation:
measured over three full passes, 108 → 66 rows differing, damping but not converged, with cells
still oscillating at iteration 3. With five gates two consecutive passes differ on **0 of 160**
rows. **It lives in this shared base and not behind a Makefile flag on purpose** — a flag is exactly
how ad-hoc `--live-parity` research runs and the routine sweep diverged unnoticed for ~2.5 months.
Full write-up: `docs/audits/2026-08-07-live-parity-ratings-sweep.md`.

**GATE CONJUNCTION — `volume_suppress*` requires `adr_exempt = true`.** The `[bias]` ADR gate keeps
only bars that have consumed little of their typical daily range; `volume_suppress` keeps only bars
with ≥1.5× mean volume. Range and volume correlate at **+0.61 (1d) / +0.67 (4h)**, so the two select
for opposite bars and their conjunction is nearly empty: measured `P(pass both) = 0.0046` against
`0.036` under independence, an **8× shortfall**. `load_signal_config` now raises on the pairing
(`voided_volume_gates`, the sibling of #139's `dead_timeframes`); `load_backtest_config` inherits it.
Before the 2026-08-06 fix this left `doji × 1d` at **0** signals against 1,247 raw detector fires,
`orb × 4h` at n=1, and **inverted the measured sign** on `engulfing × 1d` and `bos × 1d`.
Full write-up: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

- `adr_exempt` belongs in **this shared base**, never in one day-filter config. `bos` carried it in
  `signal_watch.toml` only, so `signal_watch_weekdays.toml` never inherited it and ran `bos` voided.
  A flag whose correctness depends on a second flag must live beside it.
- Every `tp_r` / `atr_sl_multiplier` for `doji`, `orb`, `engulfing`, `bos` was calibrated **under**
  the conjunction, on the surviving 2–8% subsample — flagged as debt in both configs and
  deliberately **not** re-derived (frozen TA-sweep work).

**CRYPTO-ERA PROVENANCE — sweep this file by DATE, not by flag name.** Everything here was
inherited from the crypto parent at the 2026-05-14 fork and is Binance-calibrated until re-derived.
PR #141 removed four `volume_suppress*` flags of that vintage; #143 then found `bos.suppress_long`
— same origin, same week, **one grep away** — untouched, because it greps as a *direction* flag
rather than a *volume* flag. The durable query is `git log -S "<flag> = true" -- config/`: a parent
PR number (#300–#500+, vs wifey's #22 upward) dated on or before 2026-05-14 means crypto-era.

- The cheapest tell is the cited `n`. `bos.suppress_long` claimed *"avg_r=−0.268R on n=34,767"* out
  of n=72,643; this repo's entire `backtest_trades` table holds **24,196 rows**. A sample larger
  than the whole database is not equity data.
- **Re-derivation can INVERT, not merely weaken** — on equities `bos`'s direction claim flips sign
  on both timeframes. The correct prior for an inherited flag is *unknown*, never *attenuated*.
- **Soft mode hides this class rather than defusing it.** `[bias.direction_filter]` ships
  `mode = "soft"` (logs, keeps), so a wrong flag costs nothing — while that block instructs a future
  session to flip it to hard after "≥2 weeks". Doing so would have suppressed `bos`'s better leg.
- Guarded by `tests/test_signal_config.py::test_no_shipped_strategy_sets_direction_suppress`,
  the sibling of #141's volume guard. Still un-swept: `[bias.regime.per_strategy]`
  (`bos = ["high_vol", "range"]` is from the *same* 2026-05-13 audit), `atr_sl_multiplier_*`,
  and the `tp_r` book. Audit: `docs/audits/2026-08-06-bos-timeframe-and-crypto-era-direction-flag.md`.

**`[strategy_timeframes]` is per-config, and the two configs are two populations.** `bos` is
restricted to `["1d"]` in `signal_watch.toml` but deliberately **unrestricted** in
`signal_watch_weekdays.toml`. That asymmetry is the measurement, not an oversight: across the five
`bos` cells, Benjamini–Hochberg at FDR 0.05 condemns `signal_watch 4h` (−0.282, p=0.0012) but
**not** `weekdays 4h` (−0.143, p=0.054, n=495 vs 316). A finding on one config is not a finding on
the other — never mirror a config edit because the configs look alike.

- As of 2026-05-19 the per-strategy `tp_r_long` / `tp_r_short` directional overrides have been
  **retired** (the pin_bar audit closed the last one); directional overrides now live in the
  per-day-filter configs as `tp_r_long_<tf>` / `tp_r_short_<tf>` (the per-TF directional
  architecture from Task A).
- T13 (2026-05-17) removed the parent crypto era's `signal_watch_all.toml`, `conservative.toml`,
  `scalping.toml`, `swing.toml`, and `backtest_sample.toml`; the two surviving signal_watch TOMLs
  cover the equity surface.
