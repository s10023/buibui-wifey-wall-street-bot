# Buibui Wifey Wall Street Bot

A yfinance-backed US-equities **signal bot** (Phase A: signals only). Multi-strategy detection on 4h / 1d / 1wk bars, dual-channel Telegram alerts with statistical context (primary trader-facing + condensed BUY/WAIT-relabelled wife channel), and a FastAPI + Svelte web UI for charts, backtests, signal history, and stats. Phase B (order layer + equities broker) is deferred.

Forked from the parent `buibui-moon-trader-bot` (crypto / Binance Futures); the analytics + signals engine carries over, the data source is yfinance, and the live order layer has been removed.

---

## Features

### Core Tools

- **24/7 Signal Detection Daemon**
  Polls closed candles, runs the 16 actionable equity strategies in `SIGNAL_REGISTRY` (`bos`, `doji`, `ema`, `engulfing`, `eqh_eql`, `fvg`, `hammer_hanging_man`, `inside_bar`, `marubozu`, `morning_evening_star`, `orb`, `order_block`, `ote_entry`, `pin_bar`, `trend_day`, `wick_fill` — `seasonality` is detectable but not alertable, so `STRATEGY_REGISTRY` holds 17), and sends Telegram alerts with computed SL/TP levels. Two-layer dedup prevents spam.
  Alerts include a session-tagged header (Pre-Market / RTH / Power Hour / After Hours), `$CASHTAG` symbol, and a 2-line statistical context: direction-aware P1/P2 day bias, ADR consumed %, per-DOW empirical peak hour, and weekly P2 timing probability.

- **Statistical Context Engine**
  Probability dashboard computed from historical OHLCV. Per-symbol stats:
  P1/P2 daily (was low made before high? by day-of-week), hourly extreme distribution (empirical kill zones),
  average daily range + today's consumed %, day-of-week patterns, US equity session breakdown
  (Pre-Market / RTH / Power Hour / After Hours, America/New_York wall-clock), and
  weekly P1/P2, avg return by day-of-week, and weekly P2 timing with P1 flip risk — plus M5
  daily + weekly **path-distribution cones** (percentile bands of ADR/AWR-normalized intraday
  and intraweek paths, with a live today/this-week overlay) and the **Live Alert Outcomes**
  card (real fired-alert results with symbol chips, sortable tables, and a marked open-alert
  panel). Cached in DB, served via `GET /api/stats/{symbol}`, shown on the Stats web page.

- **Backtest Engine**
  Sweep, combo, and cross-TF backtest modes against the same detectors that drive the live scanner. Walk-forward optimisation (`wifey param-sweep`) for per-strategy `tp_r` tuning, with each sweep reporting a Deflated Sharpe Ratio + Probability of Backtest Overfitting (Bailey & López de Prado) so the chosen `tp_r` is haircut for the number of grid trials. The sweep footer also prints a commit-gate verdict (`COMMIT` / `DO-NOT-COMMIT` / `INSUFFICIENT`; requires DSR ≥ 0.95, PBO ≤ 0.5, and enough observations) so a `tp_r` that fails the multiple-testing gate is flagged before it is committed to TOML. Sweeps can optionally run purged + embargoed K-fold CV (`--cv-mode purged`, López de Prado AFML ch. 7) instead of the single contiguous split, censoring trades that straddle fold boundaries and embargoing the bars after each test fold.

- **Equity cost model (Phase 0.4, ON for live configs since step 2)**
  The `[backtest.cost_model]` TOML block replaces the flat `fee_pct` with half-spread by liquidity bucket + square-root market impact + short borrow + commission, charged per-trade in R. Enabled in `config/strategy_params.toml` (inherited by both `signal_watch` configs) since Phase 0.4 step 2 (#80) — the flip moved the regression goldens by design. The code default is still off (`cost_model=None` → byte-identical flat-fee path), so the single-combo backtest CLI and web `POST /api/backtest` stay flat-fee.

- `/ingest-x` *(Claude Code skill)* — turn one or more pasted X/Twitter post URLs into
  routed research items. Fetches each post's text + chart images with no login/scraping
  via the public syndication endpoint (`tools/x_fetch.py`, randomized cooldown + per-id
  dedup cache), vision-reads each chart in a sonnet subagent, classifies via a
  content-type gate + 4-bucket verdict taxonomy (`tools/x_route.py`), then — after one
  consolidated review digest and one approval — routes into three gitignored research
  streams: hypotheses (`docs/plans/thesis-inbox.md`), mechanics
  (`docs/plans/mechanics-backlog.md`), and pundit setups
  (`docs/plans/pundit-calls.jsonl`). Ported from parent #466/#467. A post inside a
  thread is recovered upward to its root (`--thread`): the syndication endpoint exposes
  the reply-to chain but has no replies/children field, so **bookmark the LAST post of a
  thread, never the parent**. Posts carrying video are handed to `/ingest-video` rather
  than skipped (parent #591). Every setup row is
  sign-checked before it is written (`make wifey-check-levels`, or
  `tools/x_route.py --check-levels`): a long must satisfy `stop < entry < target` and a
  short `target < entry < stop`. The check warns and never drops — it exists because an
  invalidation level ("**unless** it reclaims X") mis-filed as a `target` puts the row
  instantly in profit and scores a phantom win, silently.

- **Routing dedup** (`tools/route_dedup.py`, ported from parent #518/#521) — the fetch
  caches dedup *fetches*; nothing dedupped *routing*, so a re-ingested post could append
  a second copy of a row that was already in a sink. Two layers, deliberately different:
  an **identity ledger** (`docs/plans/routed-ledger.json`, keyed on source id + item
  offset + sink) blocks an exact re-route, and a **semantic pass** scores a pending claim
  against the sink's existing entries on shared price levels and term overlap. Only the
  identity layer blocks; the semantic one surfaces candidates in the review digest and
  never drops a row, because a false positive costs a glance and a false negative costs a
  corrupted sink. Both ingest skills call it — `check` before the digest, `mark` strictly
  *after* a successful append. Seed it from the existing ledger with
  `make wifey-route-dedup-seed` (read-only; `APPLY=1` to write).

- `/ingest-video` *(Claude Code skill)* — turn a pasted YouTube or X video URL, including
  Chinese-language video, into routed research items. Fetches metadata + transcript
  (`tools/video_fetch.py`: yt-dlp captions, Groq `whisper-large-v3` fallback for
  caption-less video, per-video dedup cache), then two sonnet subagent passes — text-only
  segmenting/ranking, then vision over a small set of transcript-selected frames
  (`tools/video_marks.py`; frames follow deictic phrases and spoken price levels, never
  scene-change, so a 38-minute video costs 8–15 images instead of ~100). The in-video call
  time is resolved deterministically in code (`tools/video_calltime.py`, never by model
  date arithmetic) — a stated time is preferred but bounded below the video's publish
  timestamp, so a backlog video can't be scored against price action the speaker had
  already seen. One consolidated review digest, one approval, then routes into the same
  three research streams as `/ingest-x` plus a durable per-video note under
  `docs/plans/video-notes/`. Ported from parent #513. Items are routed across the two
  repos by **subject, never by repo priority** — equities/macro/gold/oil/DXY/bonds stay
  here, crypto goes to the crypto parent (which has perp data and the pundit scorer), and
  a video covering both legitimately yields rows in both. Routed setups are scored by
  `tools/pundit_score.py` (`make wifey-pundit-score`) — an equity-native port that counts
  horizons in NYSE sessions and resolves overnight gaps, not a copy of the parent's
  24/7-tape scorer.

- `/ingest-feed` *(Claude Code skill)* — poll a personal YouTube follow list for new
  uploads and hand the picked videos to `/ingest-video`, so discovery stops being manual
  URL-pasting. `tools/yt_feed.py` reads each channel's uploads playlist through the
  YouTube Data API v3 (`YOUTUBE_API_KEY` in `.env`; ~2–3 quota units per channel per day,
  and it never calls the expensive `search.list`), plus `backfill` for a channel's deep
  back-catalogue, `resolve` to turn an `@handle` into a config block, and `hint` to feed
  `/ingest-video` a channel's per-video knobs. The follow list is
  `config/youtube_channels.toml` (gitignored — a follow list is personal; see the
  committed `.example`), and state lives in `docs/plans/yt-feed-state.json`.
  **`poll` and `backfill` write nothing**: consumption is stamped only by `mark`, and only
  after the review gate has routed the batch — an aborted run can therefore never
  permanently eat a video, which is the same watermark-on-send defect this repo fixed in
  #68. Ported from parent #515 at parent HEAD, so its six follow-up fixes land with it.

---

## Risk Rules

Per-symbol `sl_pct` defined in `config/stocks.json` (see `stocks.json.example`).
Live SL flows from the active signal-watch TOML; per-symbol overrides take precedence.

### Universe policy (Phase 0.1)

`config/stocks.json` carries a reserved top-level `universe_policy` block
(`{scope, as_of: "fixed"|"today", survivorship_note}`) declaring how the
watchlist was selected. The watchlist was hand-picked at fork time from
today's mega-caps — a forward-looking selection over the backtested history —
so every backtest surface states the bias instead of hiding it: the CLI
runners print the policy + survivorship caveat, every saved `backtest_runs`
row is stamped with the active policy JSON (`universe_policy` column), and
the Backtest web page shows the caveat via `GET /api/universe-policy`. When
the block is absent the documented default applies (`liquid_large_cap`,
`as_of="today"`). Free point-in-time constituent data is out of scope —
the policy bounds the bias, it does not remove it.

### Research breadth universe (N3)

`config/stocks.json` is the **live-alert watchlist** (13 symbols the daemon
scans). For backtest / cross-sectional research there is a separate, larger
**research breadth universe** in `config/universe.json` (committed/tracked, not
gitignored — it is a reproducible research artifact): **505 members** (501 liquid
US large-caps plus 4 index/sector ETFs), seeded from the S&P 100 (OEX) and
widened to the S&P 500 since — minus one ticker per dual-class issuer (`GOOGL` over
`GOOG`, `FOXA` over `FOX`, `NWSA` over `NWS`), a deliberate deviation from the index
list so no company takes two slots in a cross-section. Each is tagged with
`sector`, `kind` (`stock`|`etf`), a `delisted` lifecycle flag
and an optional `listed` first-trading date (on names that list after the
backfill start), under its own `universe_policy` + a `membership_as_of` snapshot
date. Membership is **point-in-time-bounded, not scraped**: in-sample delisting
was assumed ≈ 0 for mega-caps, and 3 of 501 (`EA`, `EQR`, `SATS`) were flagged
within the universe's first year — so the universe *declares* the selection bias
rather than eliminating it, and it is bounded rather than absent. A flagged
member is retained, never deleted, so the history stays addressable while the
active accessors drop it. For pooled cross-sectional studies
that need a uniform lookback, `load_research_universe(min_history_days=…)`
excludes short-history names via that `listed` seam — carried by all **26**
members that list after the backfill floor (first NYSE session at/after
`SINCE`, i.e. 2018-01-02), stamped from DB first-1d-bar ground truth by
`make universe-stamp-listed`. A first bar *on* the floor is shared by 477
truncated survivors and so is deliberately left unstamped. Because an absent
`listed` reads as "full-history", **re-run the stamper after any membership or
backfill change** — a new constituent arrives unstamped.

```bash
make wifey-universe-backfill      # ingest the breadth universe OHLCV (4h/1d/1wk)
make universe-coverage            # read-only coverage report (bars + date range,
                                  # missing-symbol roll-up, lifecycle-bias header)
make universe-stamp-listed        # reconcile the `listed` history seam against the
                                  # DB (report-only; WRITE=1 rewrites the file)
# or directly:
poetry run python wifey.py analytics backfill --universe --timeframes 4h 1d 1wk
```

The live signal scanner is unaffected — it still resolves symbols from
`stocks.json`, so the breadth universe never floods the alert channel.

---

## Directory Structure

Package-level map. **Module-level detail lives in `.claude/context/*.md`** — that is the
single reference. This table is orientation only; it deliberately does not restate what
each module does, because a second copy of the module map is what rotted the first one.

| Path | What it is | Deep reference |
| --- | --- | --- |
| `wifey.py` · `cli/` | Thin CLI entry shim + argparse subcommand package (`signal` / `analytics` / `backtest` / `digest` / `param` / `recalibrate` / `web`) | — |
| `analytics/` | DuckDB analytics layer: `store/`, `strategies/` (18 detector modules, 17 in `STRATEGY_REGISTRY`), `backtest/`, `signal/`, `stats/`, `research_guards/`, plus data ingest / quality / NYSE-calendar | `.claude/context/analytics.md` |
| `signals/` · `utils/` | Alerting + two-layer dedup daemon (detection itself lives in `analytics/`); shared Telegram / yfinance / EDGAR clients and the config-universe loaders | `.claude/context/signals.md` |
| `web/` | FastAPI backend (`web/api/`) + Svelte 5 / Vite UI (`web/ui/`) | `.claude/context/web.md` |
| `tools/` | One-shot analysis, audit, and research-ingest scripts; not part of the daemon or CLI surface | `.claude/context/tools.md` |
| `config/` | `stocks.json` (gitignored 13-symbol live watchlist), `universe.json` (committed 505-member research breadth universe), `strategy_params.toml`, `signal_watch*.toml` | `.claude/context/config.md` |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `migrations/` | One-shot DB migration scripts, run by hand — routine schema changes go through `analytics/store/schema.py` instead | — |
| `trade/` | Empty placeholder package marking the Phase B seam (both files are 0 bytes) — the fork's Binance order opener was stripped. Phase B fills it with an equities broker adapter | — |
| `deploy/` | Verified local backup of `analytics.db`, the gitignored `docs/plans/` research tree and the memory tree (which lives outside the repo), plus **opt-in** `wifey-*` systemd user units (nothing installs them) | `deploy/README.md` |

Repo-root files: `Makefile` (dev & run commands), `Dockerfile` / `docker-compose.yml`,
`pyproject.toml` (Poetry), `.env.example`, and `.github/workflows/` (`lint.yaml` CI,
`docker-build.yaml`, `security-scan.yaml`).

---

## Stats Dashboard

The Stats page (`#/stats`) shows BrighterData-style probability tables computed from historical 1h OHLCV data. Each card has a **?** button that explains what it shows and how to use it in trading decisions.

| Card | What it answers | Interactions |
| ---- | --------------- | ------------ |
| **P1/P2 Daily** | Was the daily low or high made first? Per-day-of-week breakdown. Also shows "P1 strong %" — fraction of P1 candles where the P1-direction wick was < 20% of range (closed near the extreme). | Toggle **Low First / High First** for bullish/bearish context. Today's DOW highlighted. |
| **Average Daily Range (ADR)** | ADR(14) = 2-week average (short-term vol). ADR(30) = monthly baseline. Today's range consumed as a progress bar; turns red + warning if ≥80%. | — |
| **Hourly Extreme Distribution** | Which MYT hour (0–23) most often produces the daily high (green) vs low (red). Empirically-derived kill zones. | Current MYT hour highlighted with accent border. |
| **Day-of-Week Patterns** | **Avg Range** (relative bar) and **Med Range** beside it, bull/bear split bar + %, **Avg Return** and **Med Return**, and **Str H / Str L** columns — fraction of days each day-of-week formed a strong high (upper wick < 20% of range) or strong low (lower wick < 20% of range). | Today's DOW row highlighted. Read Med Range against Avg Range: a mean well above the median means one violent day is carrying the average. **Both return columns dim inside 2.576 × SE** (Bonferroni over 5 weekdays) — dimmed means "no direction", not "small direction". Med Range is never dimmed, because range is unsigned. |
| **Session Breakdown** | Which US equity session (Pre-Market 04:00–09:30 / RTH 09:30–15:00 / Power Hour 15:00–16:00 / After Hours 16:00–20:00, America/New_York) most often makes the daily high vs low. Overnight 20:00–04:00 ET is excluded. | Active session shown with a pulsing ● indicator. |
| **Weekly P1/P2** | Which day of the week most commonly forms the weekly high vs low, shown as a per-DOW bar chart. | Toggle **Bear** (when does weekly HIGH form?) or **Bull** (when does weekly LOW form?). Defaults to Bear. Today's DOW highlighted. |
| **Avg Return by Day** | Average `(close−open)/open` per weekday — shows which days are historically bullish or bearish. Bars grow from bottom; green = positive, red = negative. | Today's DOW highlighted. |
| **Weekly P2 Timing** | 5-column per-DOW table: how often the weekly low/high is still ahead after each DOW (still-ahead %) and how often the running P1 gets undercut later in the week (flip risk %). Conditioned view shows P(P2 still ahead \| P1 direction, DOW). | Today's DOW highlighted; flip risk ≥ 30% shown in amber. Toggle **All / Bullish P1 / Bearish P1** to condition on which extreme was set first. |
| **Daily Path Cone** (hero) | Historical intraday paths — hourly closes as × ADR14 from the session open — pooled into p10–p90 percentile bands over the 7-bar RTH session, with low/high timing ("by now" %), excursion stats, price-mapped pivots, and a dotted live today-overlay. | Direction chips (All/Bull/Bear) × weekday chips (Mon–Fri); n-badge with thin-sample ⚠ below n=30; hover for per-bar percentiles. |
| **Weekly Path Cone** (hero) | The same chart one horizon up: × AWR14-normalized paths over the 35-bar Mon–Fri trading week. Gray band = unconditional reference (all weeks); colored cone = weeks that closed bull/bear (conditional on outcome, not a forecast). Dotted amber line = this week so far. | Bull/Bear chips; hover for per-bar percentiles. |
| **P1 Wick Rank** | Current week's P1 wick (normalised by open × ADR14) ranked against all historical P1 wicks. Shows exceedance %, direction (Bullish/Bearish P1), and a rank bar. "P1 not yet set" shown if both weekly extremes haven't formed yet. Live — recomputed on every page load. | — |
| **Live Alert Outcomes** | REAL outcomes of every fired Telegram alert from the `signal_alert_outcomes` ledger: roll-up (fired/resolved/open/no-TP integrity), per-strategy and per-(strategy, tf, direction) win-rate/avg-R tables with expired counts. | Period + min-n chips; symbol chips (global, all-time); sortable columns; expandable open panel marks unresolved alerts to the newest stored close (gross uR, →SL/→TP distances, 30s refresh). |

A 2-line summary of the most actionable stats is injected into every Telegram signal alert:

```text
📐 Mon closes bullish 67% · Daily low set first 69% of Mondays · ADR 4.3% (82% used)
⏰ Daily high typically peaks ~23:00 MYT on Mondays · Weekly low: 78% of weeks still ahead
```

---

## Setup

### 1. Clone this repo

```bash
git clone git@github.com-personal:s10023/buibui-wifey-wall-street-bot.git
cd buibui-wifey-wall-street-bot
```

### 2. Install dependencies

Requires **Python >= 3.11** and [Poetry](https://python-poetry.org/).

```bash
poetry install --no-root
```

To update later:

```bash
poetry update
```

### 3. Add your API keys

Create a `.env` file (see `.env.example` for the full template). Phase A needs **no
market-data key** — yfinance is unauthenticated.

```bash
# Protects all /api/* endpoints
# generate: python3 -c "import secrets; print(secrets.token_hex(32))"
API_TOKEN=your_web_api_token_here

# Primary Telegram channel — full trader-facing alert
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Wife Telegram channel — condensed BUY/WAIT alert
TELEGRAM_BOT_TOKEN_2=your_wife_telegram_bot_token_here
TELEGRAM_CHAT_ID_2=your_wife_telegram_chat_id_here
TELEGRAM_WIFE_DRY_RUN=1   # 1 = log instead of send (rollout safety)

# Optional — only for /ingest-video on caption-less video
GROQ_API_KEY=

# REQUIRED for the PEAD and insider sleeves — SEC EDGAR contact for the
# User-Agent (fair-access policy asks automated clients to identify themselves
# with a reachable address). Keep it here, never in source.
# Unset is a hard failure rather than a slowdown: a User-Agent carrying a URL, or
# no contact at all, is 403'd by BOTH SEC hosts, so the fetchers raise
# EdgarContactMissing instead of degrading.
EDGAR_CONTACT_EMAIL=
```

### 4. Configure your watchlist

Copy `config/stocks.json.example` to `config/stocks.json` and edit it to define the
symbols the daemon scans and each symbol's stop-loss percent. `config/stocks.json` is
gitignored (personal watchlist); only the `.example` ships.

```sh
cp config/stocks.json.example config/stocks.json
```

```json
{
  "universe_policy": {
    "scope": "liquid_large_cap",
    "as_of": "today",
    "survivorship_note": "Watchlist hand-picked at fork time from today's mega-caps — a forward-looking selection over the backtested history."
  },
  "AAPL": { "sl_pct": 0.05 },
  "MSFT": { "sl_pct": 0.05 },
  "NVDA": { "sl_pct": 0.05 }
}
```

The reserved `universe_policy` block is not a symbol — it declares how the watchlist was
selected and is stamped onto every saved `backtest_runs` row (see
[Universe policy](#universe-policy-phase-01) above). Live SL flows from the active
signal-watch TOML; a per-symbol `sl_pct` here takes precedence.

---

## Usage

### Analytics — Backfill Historical Data

The analytics module stores OHLCV candles in a local DuckDB database for offline
analysis and strategy backtesting.

**First run — backfill historical data:**

```bash
poetry run python wifey.py analytics backfill --since 2023-01-01
```

Options:

- `--since YYYY-MM-DD` — start date for backfill (default: `2023-01-01`). Deep starts are
  served in full: yfinance returns at most 5,000 bars per call, so `backfill` pages until a
  short page arrives (`^GSPC 1d --since 1927-12-01` stores all 24,774 bars). `1h`/`4h` remain
  capped at yfinance's own 730-day window regardless of `--since`
- `--symbols AAPL MSFT` — symbols to fetch (default: all symbols in `config/stocks.json`)
- `--timeframes 4h 1d 1wk` — timeframes to fetch (default: `1h 4h`). Supported: `1h`, `4h`, `1d`, `1wk`
- `--universe` — resolve symbols from `config/universe.json` (505-member research breadth universe) instead of the watchlist
- `--pundit` — resolve symbols from the pundit call ledger (`docs/plans/pundit-calls.jsonl`) instead of the watchlist. Mutually exclusive with `--universe`

**Incremental sync — fetch new candles since last stored:**

```bash
poetry run python wifey.py analytics sync
```

Options:

- `--symbols` / `--timeframes` / `--universe` / `--pundit` — same as backfill
- Requires backfill to have been run first for each symbol/timeframe

**Three universes, and syncing one never refreshes another.** `config/stocks.json` is the
13-symbol live watchlist, `config/universe.json` the 505-member research breadth universe,
and the pundit ledger a third set that overlaps neither by construction: it records the
index and futures **underlyings** a pundit actually quoted (`^GSPC`, `GC=F`) rather than the
tradeable ETF proxies a watchlist carries. `make go-live` syncs only the first, so the
ledger's symbols need their own refresh:

```bash
make wifey-pundit-sync        # 1d only — these are horizon-scored calls, not intraday signals
make wifey-pundit-backfill    # first-time history for a newly-quoted underlying
```

Skipping this does not fail loudly: `pundit_score` degrades a symbol with stale bars to
`STALE` rather than erroring, so a permanently-unresolving ledger reads as "nothing has
triggered yet".

Data is stored in `analytics.db` (auto-created in CWD).

### Backtest Trading Strategies

Backtest runs in two modes: **single-combo** (one symbol + strategy) or **sweep** (all combinations ranked by avg R).

**Single-combo mode:**

```bash
poetry run python wifey.py backtest --symbol AAPL --strategy fvg --interval 4h --days 90
```

**Sweep mode — TOML config:**

```bash
poetry run python wifey.py backtest --config config/signal_watch.toml
```

**Sweep mode — CLI flags:**

```bash
poetry run python wifey.py backtest --symbols AAPL MSFT --timeframes 4h 1d --strategies fvg bos --days 90
```

**Available strategies:**

The 17 keys in `STRATEGY_REGISTRY`. All but `seasonality` are alertable (`SIGNAL_REGISTRY`
= 16). **Star ratings are deliberately not listed here** — they are per-strategy × per-TF ×
per-config, live in the `confidence_ratings` DB table, and are rewritten by
`make wifey-recalibrate`. Read them from the Backtest web page or `wifey digest`.

| Strategy | Description |
| --- | --- |
| `bos` | Break of Structure / Change of Character (BOS/CHoCH); signal stamped at the confirmation bar |
| `doji` | Doji (open ≈ close) followed by a strongly directional confirmation candle |
| `ema` | EMA pullback continuation (Variant A): trend (slow EMA + slope) + regime gate, pullback wick into fast EMA, body-fraction trigger |
| `engulfing` | Bullish/Bearish Engulfing: current candle body fully engulfs the prior candle body |
| `eqh_eql` | Equal Highs/Lows: sweep of a double-top or double-bottom; both pivots must be intact (price must not have breached the level between their formations) |
| `fvg` | Fair Value Gap — 3-candle imbalance zone fill with EMA-50 trend filter |
| `hammer_hanging_man` | Hammer (bullish reversal) / Hanging Man (bearish): pin-bar shape with trend context |
| `inside_bar` | Inside Bar breakout: body contained within prior candle, signal on breakout close |
| `marubozu` | Retest of a wickless candle's open price (order block) |
| `morning_evening_star` | Morning Star (3-candle bullish reversal) / Evening Star (3-candle bearish reversal) |
| `orb` | Opening Range Breakout — first N candles of US RTH session (13:30 UTC anchor) form the range; breakout enters |
| `order_block` | ICT Order Block — last up/down candle before displacement; entry on retest |
| `ote_entry` | Optimal Trade Entry (0.618–0.786) after confirmed BOS — deeper, more selective retracement |
| `pin_bar` | Pin Bar: small body with a long rejection wick (≥2× body) |
| `seasonality` | Average return by day-of-week, hour, and week-of-month. **Detect-only — not dispatched to alerts** |
| `trend_day` | Trend Day: candle opens near one extreme, closes near the other (large body, tiny leading wick) — **4h/1d only** |
| `wick_fill` | Price revisits a significant wick zone |

Two modules under `analytics/strategies/` are present but unregistered and therefore never
run: `fibonacci_retracement.py` and `funding_extreme.py` (the latter is crypto-only).

**Single-combo options:**

- `--symbol AAPL` — symbol to backtest
- `--strategy fvg` — strategy name from table above
- `--interval 4h` — candle timeframe (default: `4h`); one of `1h`, `4h`, `1d`, `1wk`

**Sweep options (TOML or CLI):**

- `--config FILE` — TOML preset file (see `config/signal_watch.toml`)
- `--symbols AAPL MSFT` — symbols to sweep
- `--strategies fvg bos` — strategies to sweep
- `--timeframes 1h 4h` — timeframes to sweep
- `--min-trades 20` — hide combos below this trade count (default: `20`)

**Shared options:**

- `--days 90` — lookback period in days (default: `90`)
- `--since YYYY-MM-DD` — anchor start date for stable, comparable runs (e.g. `--since 2025-09-12`). Overrides `--days` when set — use this for saved runs so results don't drift day-to-day.
- `--sl-pct 0.02` — stop loss as decimal fraction (default: `0.02` = 2%)
- `--tp-r 2.0` — take profit in R multiples (default: `2.0`)
- `--fee-pct 0.0005` — flat fee per leg (default: `0.0`). Live configs use the richer `[backtest.cost_model]` block instead — see **Equity cost model** above
- `--day-filter` — suppress Monday and Friday signals before backtesting (ICT weekly cycle)
- `--save` — persist results to `backtest_runs` and `backtest_trades` tables in `analytics.db`
- `--combo` — run co-firing confluence backtests across all strategy pairs; detects pairs within `--window` candles
- `--window N` — co-firing window: ±N candles for strategy pair detection (default: `5`)
- `--cross-tf` — run cross-TF co-firing backtests (HTF sets context, LTF is entry); sweeps all symbol × HTF/LTF-pair × strategy pairs
- `--htf-ltf 1d:4h 4h:1h` — HTF:LTF pairs to sweep (default: the 5 canonical pairs `4h:1h`, `1d:4h`, `1d:1h`, `1wk:1d`, `1wk:4h`)
- `--window-hours N` — cross-TF lookback in hours: HTF signal must have fired within N hours of the LTF signal (default: `4.0`)
- `--workers N` — parallel workers for combo backtest, one per symbol×TF chunk (default: `min(4, cpu_count-1)`); pass `1` for serial mode

**Live-parity options (T6, PR-1 plumbing + PR-2 regime + PR-3 direction_filter + F8 HTF-EMA + PR-4 ADR bias + PR-4b conflict resolver + PR-5 cooldown — series complete):**

- `--live-parity` — master switch; expands to enabling every per-gate flag below
- `--with-regime` / `--without-regime` — **wired (PR-2)**. Runs live's `_apply_regime_gate` against backtest signals via per-signal HTF regime lookup: each historical signal is evaluated against the regime active at its own `open_time` (mirrors live's `iloc[-2]` semantics — the last fully-closed HTF candle before the signal). Reads `[bias.regime]` from the same TOML the live daemon consumes (`enabled` / `mode=soft|hard` / `htf_tf` / `enabled_regimes` / `per_strategy`). Gate is a no-op unless **all of** `--with-regime`, `bias.regime_enabled=true`, and an HTF series is loadable for the symbol — otherwise falls open (matches live cache-miss).
- `--with-direction-filter` / `--without-direction-filter` — **wired (PR-3)**. Pure per-event flag check — reads `[strategy_params.<name>].suppress_long` / `.suppress_short` and drops events whose direction is suppressed when `[bias.direction_filter].mode = "hard"` (soft mode logs only). No HTF state, no time-series — cheapest gate in the chain. Gate is a no-op unless `--with-direction-filter`, `bias.direction_filter_enabled=true`, and `strategy_params` are all supplied.
- `--with-f8-htf-ema` / `--without-f8-htf-ema` — **wired (PR-3)**. Per-signal HTF EMA-slope lookup: pre-computes `(ema - ema.shift(slb)) / ema.shift(slb)` per `(symbol × anchor)` once per sweep, then resolves each signal's slope at its own `open_time` (same `iloc[-2]` semantics as regime). Drops longs opposing a negative slope / shorts opposing a positive slope when `[bias.htf_ema].mode = "hard"` (soft mode logs only); `|slope| < deadband_pct` lets both directions through. Reads `[bias.htf_ema]` and `[bias.htf_ema.per_strategy]` from the same TOML the live daemon consumes. Gate is a no-op unless `--with-f8-htf-ema`, `bias.htf_ema_enabled=true`, and the slope series is supplied for the symbol (otherwise falls open — matches live cache-miss).
- `--with-adr-bias` / `--without-adr-bias` — **wired (PR-4)**. Reuses live's `_filter_signals_by_adr` via an engine adapter that splits signals by per-direction `_is_adr_exempt(strategy, direction)`, filters the non-exempt slice only, then concats back. Honours `[strategy_params.<name>].adr_exempt = true` (wifey has only the strategy-wide flag — parent's per-direction `adr_exempt_long`/`adr_exempt_short` overrides from PR #380 were not ported, so both directions inherit the strategy-wide value). When the gate is on, the runner skips its legacy strategy-wide ADR pre-filter so the engine path takes over without double-filtering. Gate is a no-op unless `--with-adr-bias` and `bias.adr_suppress_threshold` is set — **and unless the timeframe is intraday** (`adr_gate_applies()`; `1d` / `1wk` no-op since 2026-08-06).
- `--with-conflict-resolver` / `--without-conflict-resolver` — **wired (PR-4b)**. Pools backtest signals across strategies per `(symbol, tf)` and applies the lifted live conflict resolver (`_apply_conflict_resolver`, shared with `scanner.run_scan_cycle`) per `open_time` moment. The resolver's continuous tiebreaker reads `confidence_ratings.avg_r` keyed on `(strategy, tf, direction)` for the TOML stem (`cfg.config_name` → `signal_watch`, `signal_watch_weekdays`), preferring directional → falling back to `'combined'` → 0.0 for unrated. Implemented at the runner level (`_collect_sweep_results` is a three-phase pipeline: detect → resolve → backtest+save); the engine path is unchanged. Default-off is byte-identical (phase 2 short-circuits when the gate is off). Run `make db-update` to keep `confidence_ratings` fresh — the gate's effect depends on the avg_r values, but the default-off path is unchanged.
- `--with-cooldown` / `--without-cooldown` — **wired (PR-5)**. Engine-side N-bar cooldown keyed by `(symbol, timeframe, strategy, direction)` via a per-call `_CooldownState` ledger — replays live's `cooldown_store` candle-watermark / per-strategy suppression against historical signals. Walks signals in `open_time` order and drops a row when `open_time < last_fire + cooldown_bars × tf_ms`; opposing-direction signals are not suppressed (separate key). Equity baked-in defaults: 4h=2, 1d=1, 1wk=1 bars (**not** parent's intraday `15m=4 / 1h=3 / 4h=2 / 1d=1`); unknown TF → 1; override via `[backtest.live_parity.cooldown_bars]` TOML sub-table. State is instantiated fresh inside `run_backtest()` per call, so two identical back-to-back calls are byte-equal.
- TOML equivalent: `[backtest.live_parity]` block with `enabled` / `regime` / `direction_filter` / `f8_htf_ema` / `adr_bias` / `conflict_resolver` / `cooldown` keys + optional `[backtest.live_parity.cooldown_bars]` per-tf sub-table. CLI `--without-<gate>` wins over TOML; `--live-parity --without-cooldown` cleanly disables a single gate. **Defaults (all `False`) are a no-op for every gate, so existing callers see no behavioural change — proven by the regression-golden contract (`make test-regression` byte-identical).** As of 2026-08-07 the shipped configs no longer take those defaults: `config/strategy_params.toml` declares `[backtest.live_parity]` with **five** gates on (`regime`, `direction_filter`, `f8_htf_ema`, `adr_bias`, `cooldown`), inherited by both live configs, so `make db-update-backtest` measures the population the daemon actually dispatches. **`conflict_resolver` stays off deliberately** — it is the only gate that reads `confidence_ratings`, so running it inside the sweep that produces them makes the ratings a non-convergent fixed-point iteration rather than a computation (measured: 108 → 66 rating rows differing across three passes, still oscillating at iteration 3). See `docs/audits/2026-08-07-live-parity-ratings-sweep.md`. As of 2026-08-26 the resolved gate set is part of a saved run's identity (`|lp:<gates>` in the `run_id` hash, plus a `live_parity` column), so an ad-hoc `--without-<gate>` run with `--save` accumulates beside the routine sweep's row instead of replacing it — see `docs/audits/2026-08-26-run-id-live-parity-axis.md`.

**Single-combo example output:**

```text
Backtest: AAPL 4h — fvg
────────────────────────────────────────────────────
Signals:     42 total, 39 closed
Win rate:    61.5%  (24W / 15L)
Avg R:       +0.61R
Total R:     +23.92R
Max DD:      -4.00R
```

**Sweep example output:**

```text
Backtest Sweep — 3 symbol(s) × 2 timeframe(s) × 4 strategy/ies (90d)
══════════════════════════════════════════════════════════════════
Symbol          TF    Strategy            Win%  Trades   Avg R
──────────────────────────────────────────────────────────────────
AAPL          4h    fvg                  62.5%      48  +1.84R
MSFT          1d    order_block          58.3%      24  +1.61R
NVDA          1wk   bos                  54.1%      85  +1.42R
──────────────────────────────────────────────────────────────────
  Hidden: 3 combo(s) with < 20 trades
```

> **Note:** Requires backfill to be run first for each symbol/timeframe.

### WFO Parameter Tools — `param-sweep` and `param-audit`

Two subcommands, and the difference is which direction they run:

```bash
wifey param-sweep --config config/signal_watch.toml    # search: grid over tp_r, pick a winner
wifey param-audit --config config/signal_watch.toml    # verify: score the values already in TOML
```

`param-sweep` walks a `tp_r` grid per strategy × timeframe out-of-sample and reports DSR, PBO
and the commit-gate verdict for each cell. `param-audit` takes no grid — it scores the values
the config already carries, so it answers "is what we shipped still the right choice" rather
than "what should we ship". Both are wrapped by `/wfo-sweep`, which is the trusted production
path for `tp_r`; run them directly only outside that chain.

⚠ Both are dormant while the TA book is frozen (see CLAUDE.md).

### Recalibrate — Update Confidence Star Ratings

Reads `backtest_runs` from `analytics.db` and maps real avg R per strategy to 1–5 star
confidence ratings. Avg R is **pooled over trades** across symbols
(`sum(avg_r × closed_trades) / sum(closed_trades)`), matching the win rate beside it — a
symbol with 1 trade cannot move a star as far as one with 50. Each signal-watch TOML config gets its own set of ratings stored in the
`confidence_ratings` DB table — stars are no longer shared globals baked into source code.

```bash
# Per-config workflow (preferred — no source patching)
poetry run python wifey.py recalibrate --config config/signal_watch.toml            # dry-run
poetry run python wifey.py recalibrate --config config/signal_watch.toml --apply    # write to DB
poetry run python wifey.py recalibrate --config config/signal_watch_weekdays.toml --apply

# Legacy: write global ratings directly to analytics/strategies/_registry.py (still works, no --config needed)
poetry run python wifey.py recalibrate --apply
poetry run python wifey.py recalibrate --min-trades 20 --apply
```

`--config` derives `day_filter`, `config_name`, `adr_suppress_threshold` and the declared
cell set from the TOML file, then filters `backtest_runs` before computing stars.
`--apply` with `--config` writes to the `confidence_ratings` table keyed by config name —
signal watch loads these at startup so each TOML config uses its own calibrated stars.

Two of those filters exist because their absence failed silently until 2026-08-06:

- **Only cells the config declares** are rated, honouring `strategy_timeframes`; rows for
  any other cell are **pruned** on `--apply`, since the upsert cannot delete. Without this
  a cell keeps its stars indefinitely after leaving the config — `fib_golden_zone × 4h`
  displayed 3★ +0.4688, the second-highest-rated cell in the `signal_watch` table, 2.5
  months after removal.
- **Only sweep runs** (`sweep_id IS NOT NULL`). The live EV gate writes `backtest_runs`
  rows too — one per direction-leg, single strategy, no live-parity params — and since
  rows are deduplicated by recency those newer rows used to supersede the competed sweep
  rows for that symbol. As of 2026-08-07 the two writers are also keyed apart: their
  `run_id` was identical, so the live gate's `INSERT OR REPLACE` **overwrote** the sweep
  row rather than competing with it, and this filter then dropped the cell's symbols
  instead of recovering them (`signal_watch` was rated on 263 of 312 rows). Every writer
  now stamps an `origin`, so the rows accumulate side by side.

`make check-dead-surfaces` fails on any orphaned rating that survives, and gates
`make db-update`'s completion banner.

**Star rating thresholds (avg R):**

| avg R | Stars |
| --- | --- |
| < 0 | ★☆☆☆☆ |
| 0 – 0.2 | ★★☆☆☆ |
| 0.2 – 0.5 | ★★★☆☆ |
| 0.5 – 0.9 | ★★★★☆ |
| ≥ 0.9 | ★★★★★ |

Strategies with fewer than `--min-trades` (default: 10) closed trades are excluded and shown as `(no data)`.

**Full workflow:**

```bash
# After any backtest sweep with SAVE=1 — recalibrate each config independently
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1
make wifey-recalibrate CONFIG=config/signal_watch.toml             # preview
make wifey-recalibrate CONFIG=config/signal_watch.toml APPLY=1    # write to DB
make wifey-signal-watch CONFIG=config/signal_watch.toml            # restart; loads DB stars
```

### Signal Watch — 24/7 Strategy Alerts

Runs a polling daemon that scans closed candles every N seconds and sends Telegram alerts
when a strategy fires. Requires `analytics backfill` to have been run first.

```bash
poetry run python wifey.py signal watch
```

**Options:**

- `--config config/signal_watch.toml` — load all options from a TOML file; CLI flags override file values
- `--symbols AAPL MSFT` — symbols to scan (default: all from `config/stocks.json`)
- `--timeframes 4h 1d 1wk` — candle timeframes (default: `4h`)
- `--strategies fvg bos` — strategies to run (default: all 16 actionable from `SIGNAL_REGISTRY`)
- `--tp-r 2.0` — R multiplier for TP level in alert messages (default: `2.0`)
- `--telegram` — send alerts via Telegram
- `--once` — run a single scan cycle and exit (for cron / once-a-day use) instead of looping as a daemon
- `--catch-up` — replay every un-alerted closed candle since the last run, not just the latest, so ledger rows from skipped run-days are recovered instead of lost (off by default). Backfilled candles are **recorded but never sent to Telegram** — only candles inside `max_alert_age_hours` can alert (shared base ships 24.0; `0.0` = newest closed candle only), so replay never floods the chat with stale, already-played-out setups. The first run for a fresh `signal_state.json` only seeds the latest candle. Depth is bounded by the 200-candle scan window (4h ~33 days, 1d ~200 days, 1wk ~4 years); gating context (regime/HTF-EMA/ADR/bias) is evaluated as-of-now, so a deep backfill is not clean out-of-sample evidence. Pairs naturally with `--once` for a once-a-day cron.
- `--state-file signal_state.json` — path to cooldown/watermark state file
- `--min-sl-pct 0.003` — minimum SL distance as a fraction of price (e.g. `0.003` = 0.3%); overrides structural SL if too tight (default: disabled)

**`day_filter`** suppresses signals on Monday and Friday (ICT weekly cycle — manipulation/distribution days). Off by default; enable in TOML:

```toml
day_filter = true
```

> The fork inherited this section's measured win-rate / avg-R tables from the crypto
> parent (BTC/ETH/SOL). They were **never re-measured on equities** and have been removed
> rather than left to read as this repo's findings. Current equity numbers come from
> `wifey digest`, the Backtest web page, or a fresh `make db-update`.

**`trend_day`** detects candles where price opens near one extreme and closes near the
other — a large body (≥65% of range) with a tiny leading wick (≤15%). Configurable via
`body_pct_min` and `wick_max` params in the Backtest UI.

The `[backtest]` table in `config/signal_watch.toml` controls a per-alert expected-value filter:

```toml
[backtest]
mode = "hard"           # "soft": append win rate | "hard": suppress low performers | "off"
days = 365              # lookback window; sets the live EV gate's backtest window
                        # (declared in strategy_params.toml; executed as of 2026-08-06)
min_trades = 12         # global fallback — applied to directional trade count (longs for LONG alerts, shorts for SHORT)
min_trades_4h  = 5      # per-TF overrides; calibrated from DB p25 directional counts
min_trades_1d  = 2
min_trades_1wk = 1
min_avg_r = 0.0         # hard mode: suppress alert if directional avg_r < this (positive EV gate)
min_avg_r_z = 1.64      # ...and only when the shortfall is this many standard errors below, i.e.
                        # distinguishable from zero. 0.0 = block on any shortfall (legacy). The
                        # gate FAILS OPEN, so a larger min_trades suppresses LESS, not more.
fee_pct = 0.0005        # flat fee applied to inline backtest (falls back to top-level fee_pct)
```

**`[strategy_params]`** overrides `tp_r`, `sl_pct`, and volume/ADR gates per strategy, per TF, and per symbol.
Resolution order: **symbol+TF → symbol → TF → strategy → global**.

```toml
[strategy_params.engulfing]
tp_r = 3.0          # all symbols, all TFs

[strategy_params.engulfing.NVDA]
tp_r_4h = 4.0       # NVDA 4h only; other NVDA TFs fall back to strategy-wide 3.0

[strategy_params.doji]
tp_r = 3.0          # all symbols fallback

[strategy_params.doji.AAPL]
tp_r_1d = 3.5       # AAPL 1d only

[strategy_params.doji.MSFT]
tp_r_1d = 4.5       # MSFT 1d only — diverges from AAPL
```

Per-symbol blocks use `[strategy_params.STRATEGY.SYMBOL]` sub-table syntax, placed after their
parent `[strategy_params.STRATEGY]` block. Any symbol not listed falls through to TF-level or
strategy-wide.

Two boolean flags are also supported per strategy block:

- **`adr_exempt = true`** — skip the ADR bias gate for this strategy (use for breakout/continuation strategies that need range momentum)
- **`volume_suppress = true/false`** — override the global `[backtest].volume_suppress` for this strategy. `true` drops signals on candles with volume < 1.5× the 20-candle rolling mean; `false` explicitly keeps them even when the global flag is on. Omit to inherit the global default (off). Decision is data-driven: run `make wifey-backtest` and check the "Volume Impact" table for each strategy — suppress when normal-vol avg R clearly exceeds low-vol avg R (Δ > 0.05R). **Two preconditions, both added 2026-08-06 after all four shipped flags turned out to be wrong:**
  - **`true` requires `adr_exempt = true` on the same strategy** — `load_signal_config` raises otherwise. The ADR gate keeps quiet, small-range bars while this flag keeps high-volume bars, and range/volume correlate at +0.61 (1d) / +0.67 (4h), so the conjunction discards ~99% of signals *silently*. It left `doji × 1d` at zero signals from 1,247 raw detector fires and inverted the measured sign on two other strategies.
  - **A raw Δ is a point estimate on a noisy sample — test it.** Split the unsuppressed trades on `Trade.low_volume` and run a significance test. A Δ of +0.11R justified discarding 94% of `bos`'s signals for months; retested, every cell came back p ≥ 0.113 with the 95% CI straddling zero. Never compare flag-ON against flag-OFF — ON is a strict subset of OFF, so the arms are dependent and no test applies.

  As of 2026-08-06 **no strategy in either shipped config sets `volume_suppress`.** Full analysis: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

The inline backtest (computed each scan cycle per firing signal) respects all config values:
`fee_pct`, `day_filter`, `sl_pct`, and `cooldown_seconds` are now all read from TOML and
applied correctly — results stored in `backtest_runs` match what the live filter uses.

**`[bias]`** — bias chain applied between detector fan-out and Telegram dispatch.
Order: `regime` (Step −1) → `htf_ema` / F8 (Step 0) → `adr_suppress_threshold` → `dow_soft_suppress`.

```toml
[bias]
# ADR directional gate: when today's range has consumed >= this fraction of ADR-14,
# suppresses only the chasing direction (LONGs when move was up, SHORTs when move was
# down). Reversal signals at the extreme still fire. Falls back to blanket suppress when
# move direction is unknown.
adr_suppress_threshold = 0.80   # e.g. 0.80 = suppress chasing direction when 80%+ consumed
# INTRADAY TIMEFRAMES ONLY (2026-08-06). "Range consumed UP TO this candle" needs >1 bar
# per calendar day to be a partial quantity; `1d` and `1wk` have exactly one, so the gate
# no-ops there via `adr_gate_applies()`. On a one-bar day the ratio silently became a
# high-range-day filter and its direction guard was true by construction, costing 28-82%
# of every strategy's signals for an effect that survived correction in 1 of 17 cells.
# See docs/audits/2026-08-06-adr-gate-timeframe-degeneracy.md.

# DOW soft suppress: reduce confidence by 1 star when signal direction opposes today's
# historical DOW avg return (from stats_lib). Signal still fires but shows lower conviction.
dow_soft_suppress = false
dow_suppress_min_abs_return = 0.005  # dead-band: ±0.5% to avoid noise from near-zero days

# F8 HTF EMA directional gate — suppresses signals fighting the HTF trend.
# See `config/strategy_params.toml` for the live anchor mix and per-strategy overrides.
[bias.htf_ema]
enabled = true
mode = "hard"                   # "soft" = log only; "hard" = drop opposing signals
default_tf = "4h"               # default anchor TF; per_strategy entries can override
default_period = 50
default_slope_lookback = 10
deadband_pct = 0.003            # |slope| < 0.3% over slope_lookback bars → allow

# v2 Phase 2 regime gate (per redesign §6) — Step −1, runs before F8.
# Drops signals whose strategy type is not enabled in the current 4h regime.
# `unknown` regime and cache misses always fall open.
[bias.regime]
enabled = true
mode = "soft"                   # ship soft first; flip to "hard" after ≥2 weeks observation
htf_tf = "4h"                   # regime classified off 4h candles

[bias.regime.enabled_regimes]
trend         = ["trend"]                       # continuation only in trend
fib           = ["trend"]                       # BOS-anchored continuation
flow          = ["trend", "range", "high_vol"]
structural    = ["trend", "range", "high_vol"]
price_action  = ["trend", "range", "high_vol"]
candlestick   = ["trend", "range", "high_vol"]
session       = ["trend", "range", "high_vol"]

[bias.regime.per_strategy]
bos = ["high_vol", "range"]     # routing-audit-corrected (PR #366); trend regime was bos's worst

# T2c per-strategy directional suppress — Step −0.5 of the bias chain.
# Drops signals matching [strategy_params.<name>].suppress_long / .suppress_short.
# Cheapest filter — pure per-event flag, no HTF / regime data.
[bias.direction_filter]
enabled = true
mode = "soft"                   # flip to "hard" after ≥2 weeks of soft-mode logs

# No strategy sets suppress_long / suppress_short as of 2026-08-06 (PR #143). `bos` carried
# suppress_long until then, but it arrived from the crypto parent one day before the fork
# (parent PR #367) citing n=34,767 long trades — more than this repo's entire backtest_trades
# table (24,196 rows), so it was never equity data. Re-derived on equities the claim INVERTS:
# long is bos's BETTER leg on both timeframes. The gate below still reads these keys; it just
# has nothing to act on. Re-adding one must break
# tests/test_signal_config.py::test_no_shipped_strategy_sets_direction_suppress.
```

ADR + DOW gates read from the per-symbol `StatsContext` computed each cycle (same data shown
in the Telegram stats footer). F8 reads from a slope cache pre-computed once per cycle from
HTF candles. Regime reads from a `dict[symbol, Regime]` classified once per cycle off the
`htf_tf` candles. If any data is unavailable for a symbol, the corresponding gate is silently
skipped (fall-open).

**Example alert (Telegram, soft mode)** — primary channel, as rendered:

```text
SIGNAL — $AAPL 4h  ·  LONG 🟢
ema · pullback  ★★★☆☆

333.85  ·  31-Jul 01:30 MYT
🏛️ RTH

SL: 324.26  (2.9%)
TP: 348.24  (4.3%  ·  1.5R)

⚠️ Low volume — weaker conviction

📊 Backtest 365d [↑]: 62% win · avg +1.4R (18 longs)

📐 Tue closes bullish 58% (+0.3% avg) · Low still ahead 50% of Tuesdays · ADR [█████░░░░░] 53% of 2.0%
🎯 TP window: high ~21:00 MYT on Tuesdays · Weekly low: 46% still ahead
```

The header is `strategy · variant`: the detector's reason contributes only what the
alert does not already state, so `ema_pullback_long@333.85` renders as `ema · pullback`.

The **wife channel** carries the same trade, condensed — BUY for long, WAIT for short
(no levels, since a short means "take no action"):

```text
BUY — $AAPL 4h  ★★★☆☆              WAIT — $AAPL 4h
Entry 333.85  ·  31-Jul 01:30 MYT  306.47  ·  10-Aug 21:30 MYT
                                   Sit tight — conditions look weak
Stop 324.26 (−2.9%)  ·  Target 348.24 (+4.3%)

⚠️ Low volume — weaker conviction
```

Both renders print on every `wifey signal test`, so either can be reviewed without
dispatching anything.

Two-layer dedup prevents alert spam:

- **Candle watermark** — won't re-alert the same candle after a restart
- **Cooldown timer** — 1-hour cooldown per `(symbol, strategy, direction)`

State is persisted to `signal_state.json` so dedup survives container restarts.

> **Note:** Run `analytics backfill` + `analytics sync` first. The daemon auto-backfills
> symbols with no data on first boot, but pre-loading data is faster.

### Go Live (Phase A) — Daily Runbook

The Phase-A signal-alert bot is feature-complete; "live" = it runs against the equity
watchlist and fires Telegram alerts. Each scan cycle **self-syncs OHLCV via yfinance** before
detecting, so a single run is always current — no separate sync step. Two Makefile targets
wrap the routine:

```bash
# 0. One-time (fresh box / wiped DB / data gap): backfill the watchlist OHLCV.
make go-live-prep                       # SINCE=2023-01-01 by default; override with SINCE=...

# 1. Confirm Telegram creds in .env:
#      TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID            (primary long+short channel)
#      TELEGRAM_BOT_TOKEN_2 / TELEGRAM_CHAT_ID_2        (wife BUY/WAIT channel)
#      TELEGRAM_WIFE_DRY_RUN=0                          (flip from 1 to actually send to wife)

# 2. Run ONE scan cycle and exit (Telegram ON). Run it once a day, pre-market
#    (before the US open). A daily/weekly bar is treated as closed only once its
#    full period has elapsed, so a same-evening-after-close run would still see
#    the *prior* day's bar — run the next morning to alert on the latest close.
make go-live                            # uses config/signal_watch.toml
make go-live GO_LIVE_CONFIG=config/signal_watch_weekdays.toml   # weekday day-filter variant
```

`make go-live` runs a **single cycle** (`signal watch --once`) and exits — ideal for a manual
once-a-day run or a scheduled one; the candle-watermark dedup in `signal_state.json` prevents
re-alerting candles already seen.

**Scheduling it (recommended).** `deploy/systemd/user/wifey-signal-watch.{service,timer}` fire
`make go-live CATCH_UP=1` once each weekday at **08:30 UTC**, pre-open under both EDT (13:30 bell)
and EST (14:30). It is **opt-in like every other unit here — nothing installs it**, and it sends
Telegram, so installing it is an operator decision:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user/wifey-*.{service,timer} ~/.config/systemd/user/   # glob also picks up wifey-alert@
systemd-analyze --user verify ~/.config/systemd/user/wifey-signal-watch.service   # parse before trusting
systemctl --user daemon-reload
systemctl --user enable --now wifey-signal-watch.timer

systemctl --user list-timers wifey-signal-watch.timer
systemctl --user start wifey-signal-watch.service    # fire one now — ⚠ this SENDS
journalctl --user -t wifey-signal-watch -n 50        # -t, not -u: the unit sets SyslogIdentifier
```

Why 08:30 rather than any pre-open time — three constraints, and it is the only slot meeting all
three. **Market:** after the 1d bar closes (04:00 UTC EDT / 05:00 UTC EST) and before the bell,
clearing the EST close by 3h30m and the EDT bell by 5h. **Alert window:** it keeps the previous
session's *first* 4h bar (13:30 UTC open, 17:30 UTC close) at 15h against the 24h
`max_alert_age_hours` window, with 9h to spare — a materially later fire ages that bar out and
silently restores the old newest-candle-only rule. **Uptime:** the host is not always on, and
`Persistent=true` recovers a missed *run* but never its alerts, so the hour was picked from measured
uptime (14/14 weekdays at hour 08 UTC over 2026-08-06→08-25) rather than from the market clock
alone. Re-measure before moving it.

Without systemd, the equivalent cron entry (same time, same flags):

```cron
30 8 * * 1-5   cd /path/to/repo && make go-live CATCH_UP=1 >> go-live.log 2>&1
```

A single-cycle run fires on only the latest closed candle, so a **skipped run-day** (missed cron,
host down) permanently loses that day's signals. Add `--catch-up` to replay every un-alerted
candle since the last run instead — e.g. `make wifey-signal-watch ONCE=1 TELEGRAM=1 CATCH_UP=1`
(or `wifey signal watch --once --catch-up --telegram`). A recovered candle **does** reach Telegram
while its close is inside `max_alert_age_hours`; older ones land in the DB and outcome ledger only.
(This sentence used to say recovered candles are *never* sent — that was the pre-#260 rule, and it
contradicted the clause beside it.) Leaving `CATCH_UP=1` on every run is safe because the window is
a **calendar** bound: a stale candle ages out rather than arriving late. The first run for a fresh
state file only seeds the latest candle, so enabling it on an established deployment is safe (no
burst). ⚠ `max_alert_age_hours` is **inert** without it — only catch-up emits an event for a
non-latest candle in the first place, which is why the scheduled unit below passes it.

**`max_alert_age_hours`** (shared base, `24.0`) sets how stale a candle may be and still alert,
measured from its close. The newest closed candle always alerts; older ones alert only while
they are this fresh. It exists because "newest closed candle only" is arbitrary under a fixed
cadence: with one pre-open run a day the session's **first** 4h bar can never *be* the newest
closed candle, so it was structurally undeliverable — 120 of 351 ledger candles (34%), all of
them the 13:30 UTC bar, dropped at 19.3h stale while the 15.3h bar shipped. Set `0.0` to restore
the newest-only rule; a negative value is refused at load.

Two things it deliberately does **not** do. It cannot **replay history** — a candle whose
watermark a previous backfill consumed stays consumed, so raising it never re-sends old alerts.
And it does not recover a **skipped run day**: at a Monday run, Thursday's bars are ~87h old and
stay ledger-only. That one is a cadence habit, and under `day_filter = "tue_thu"` the alerting
days are **Wed/Thu/Fri** — a Monday or Tuesday pre-open run sees Friday/Monday bars, which the
day filter discards, so it can never alert (it still does useful sync and ledger work).
Running Friday is worth 66% vs 44% of the alert surface.
See `docs/audits/2026-08-25-dispatch-recency-window.md`.

To run as a **continuous daemon** instead (self-syncs every cycle and sleeps to the next
candle boundary — keep it alive with tmux / systemd), drop the once flag:

```bash
make wifey-signal-watch CONFIG=config/signal_watch.toml TELEGRAM=1
```

### Backup — Snapshot the Irreplaceable State

Three trees are single-copy and unreachable by git, so git protects none of them:

- `analytics.db` (~153MB) holds `signal_alert_outcomes`, the live out-of-sample ledger.
  yfinance will not re-serve a historical signal fire, and restarting the ledger yields a
  differently-*biased* sample rather than an equivalent one.
- `docs/plans/` (~1MB) holds the entire research pipeline's output — pundit ledger, routing
  watermark, Streams A/B, video notes, parent-sync triage, measurement scripts, the handoff.
- The **memory tree** (~1MB) holds `project_todo_master.md` — the single source of truth
  to-do, carrying the north star and gates G1–G4 — plus `MEMORY.md` and ~70 topic files.
  ⚠ It lives *outside* the repo, at `~/.claude-personal/projects/<repo-path-slug>/memory`,
  which is why it went uncovered until 2026-08-18: the backup's tree and file lists both
  resolve against the repo root, so anything above it was invisible by construction. It is
  now carried by the script's `EXTERNAL_ROOTS` section.

`.gitignore` excludes `docs/plans/` wholesale, so `git ls-files docs/plans/ | wc -l` returns
**0** and a `git clean -xdf` deletes all of it with no prompt. `analytics.db.bak` in the repo
root is an undated, unverified byte copy — it is not a backup.

```bash
make backup           # verified snapshot → ~/backups/wifey (weekly parquet if >=7 days old)
make backup-dry-run   # report what would be captured, write nothing
make backup-check     # how old is the newest snapshot? (advisory, never in CI)
```

The snapshot is row-count verified against the source, re-opened standalone to prove it
restores, refused outright if `signal_alert_outcomes` comes back empty, and published by
atomic rename so a snapshot at the final path is never half-written. If something else holds
the database it retries, then falls back to a lock-free byte copy — it never kills the
process holding the lock.

That is the **likely-failure** leg only (fat-finger delete, `git clean`, a bad script), and
it does not survive disk death or a lost laptop. `make backup-offsite` is the leg that does:
an `rclone sync` of that one directory to `gdrive-wifey:snapshots`, on wifey's **own** rclone
remote pinned to its own Drive folder so it cannot reach the crypto parent's backups.
⚠ `sync` mirrors deletions in both directions — read the off-site section before setting
`WIFEY_BACKUP_REMOTE`, and use `make backup-offsite-dry-run` first.

Both legs ship **opt-in** systemd user timers — nothing installs them, exactly like the
signal-watch timer above. Every wifey unit is `Type=oneshot`, so there is still no wifey daemon. ⚠ A green timer is not a current backup: alerting is failure-only, so a timer that
silently stopped and one with nothing to report look identical. `make backup-check` dates the
newest snapshot from its own manifest, which is the *input* the off-site leg copies rather than
that copy's exit code. Full rationale, setup, coverage policy, and restore procedure:
[`deploy/README.md`](deploy/README.md).

### Signal Test — Fire a Test Alert From Historical Data

Runs a detector against real historical OHLCV data and prints (or sends) the formatted alert.
Useful for testing alert formatting changes without waiting for a live signal.
No DB writes, no cooldown state, no latest-candle-only restriction.

```bash
# Most recent BOS signal for AAPL 4h — print only
poetry run python wifey.py signal test --strategy bos --symbol AAPL --timeframe 4h

# Pin to a specific candle (UTC)
poetry run python wifey.py signal test --strategy bos --symbol AAPL --timeframe 4h \
  --at 2026-04-07T02:00:00

# Use MYT offset (+08:00)
poetry run python wifey.py signal test --strategy bos --symbol AAPL --timeframe 4h \
  --at 2026-04-07T10:00:00+08:00

# Inherit symbol/TF/tp_r from TOML and send to Telegram
poetry run python wifey.py signal test --config config/signal_watch.toml \
  --strategy marubozu --timeframe 4h --telegram

# Filter to shorts only, wider lookback
poetry run python wifey.py signal test --strategy fvg --symbol MSFT --timeframe 1d \
  --direction short --lookback 500
```

Or via Makefile:

```bash
make wifey-signal-test STRATEGY=bos SYMBOL=AAPL TIMEFRAME=4h
make wifey-signal-test STRATEGY=bos SYMBOL=AAPL TIMEFRAME=4h AT=2026-04-07T02:00:00
make wifey-signal-test CONFIG=config/signal_watch.toml STRATEGY=marubozu TIMEFRAME=4h TELEGRAM=1
```

**Options:**

- `--strategy` *(required)* — strategy to test (e.g. `bos`, `fvg`, `marubozu`)
- `--symbol` — symbol (required unless `--config` provides one)
- `--timeframe` — candle timeframe (required unless `--config` provides one)
- `--at` — pin to a specific candle; ISO datetime (naive = UTC, or with `+08:00` for MYT) or Unix ms integer; defaults to latest available candle
- `--lookback` — number of candles to load ending at `--at` (default: `200`)
- `--direction` — filter to `long` or `short` signals only
- `--tp-r` — TP risk:reward for formatting (default: `2.0` or from `--config`)
- `--min-sl-pct` — minimum SL distance as fraction of price (default: `0` or from `--config`)
- `--config` — TOML file to inherit symbol/TF/tp_r/sl_pct defaults
- `--telegram` — send the alert via Telegram (in addition to printing)

### Web API — FastAPI Backend

A JSON REST API and SSE streaming backend for the Phase 5 Svelte frontend (or any HTTP client).

```bash
# Start the API server (default: http://127.0.0.1:8000)
poetry run python wifey.py web

# Pass a signal-watch TOML so the UI auto-populates defaults from it
poetry run python wifey.py web --config config/signal_watch.toml

# Custom host/port with auto-reload for development
poetry run python wifey.py web --host 0.0.0.0 --port 8000 --reload

# Or via Makefile (override PORT and/or CONFIG)
make wifey-web
make wifey-web PORT=8080
make wifey-web CONFIG=config/signal_watch.toml
make web-full CONFIG=config/signal_watch.toml   # build UI then start server
```

**Authentication:** All endpoints except `/api/health` require a Bearer token. Set `API_TOKEN` in `.env`.

**Endpoints:**

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/api/health` | Health check — no auth required |
| `GET` | `/api/config` | Per-symbol config from `stocks.json` |
| `GET` | `/api/active-config` | Active TOML config the server was started with (empty defaults when no `--config` passed) |
| `GET` | `/api/universe-policy` | Active universe policy + survivorship caveat (+ watchlist size) |
| `GET` | `/api/strategies` | All strategy specs with params and confidence (auto-uses active config's star ratings) |
| `GET` | `/api/ohlcv` | OHLCV candles (`?symbol=&timeframe=&start_ms=&end_ms=`) |
| `POST` | `/api/signals` | Detect strategy signals on historical data |
| `GET` | `/api/backtest/runs` | All saved backtest runs from DB, newest first |
| `POST` | `/api/backtest` | Run a backtest (auto-saved to DB) for a symbol/timeframe/strategy |
| `GET` | `/api/stats/{symbol}` | Computed stats bundle (P1/P2, ADR, DOW, session, weekly) for a symbol |
| `GET` | `/api/live-outcomes` | Cross-symbol roll-up of fired-alert outcomes from `signal_alert_outcomes` (win/loss/avg-R per strategy×tf×direction); never cached |
| `GET` | `/api/zones` | Structural zones for a symbol+timeframe (FVG, OB, EQH/EQL, BOS, Fib, OTE, swings) |

Phase A (signals-only) does not ship `/api/positions`, `/api/prices`, or `/api/stream/*` — the Binance-Futures variants were removed in T16-full and Phase B will re-introduce equivalents against the chosen equities broker.

**CORS:** Defaults to `http://localhost:5173` (Vite dev server). Override with `CORS_ORIGINS` env var (comma-separated). If you change `DEV_PORT`, update `CORS_ORIGINS` accordingly (e.g. `CORS_ORIGINS=http://localhost:3000`).

**Notes:**

- The web server opens the DB in **read-only** mode. The signal daemon holds the write lock.
- Requires `analytics backfill` to have been run first for OHLCV/signals/backtest endpoints.
- In production, the API server serves the built Svelte UI from `web/ui/dist/` as static files.

### Web Frontend — Svelte 5

A single-page trading terminal UI. Dark theme, no component library, no SSR.
Pages: Chart (candlesticks + signal markers + structural zone overlays), Backtest (DB-backed sortable/filterable results table + collapsible run form), Signal Feed (poll + filters), Stats.

Chart overlays include EMA 20/50/200, RSI sub-panel, Range Levels (MO/DO/WO + PDH/PDL/PWH/PWL/Mon H·L), overnight/CME Gap (1h only), Fibonacci retracement, and **Structural Zones** (7 toggles: FVG boxes, Order Block boxes, EQH·EQL lines, BOS levels, Fib Golden Zone box, OTE box, swing pivot dots — powered by `GET /api/zones`).

```bash
# Install frontend dependencies (first time)
make web-install

# Start dev server with API proxy (http://localhost:5173)
# Set VITE_API_TOKEN in web/ui/.env.local
make web-dev

# Build for production (output to web/ui/dist/)
make web-build

# Build + start API server serving the built UI
make web-full
```

**Dev environment:** Set `VITE_API_TOKEN=<your API_TOKEN>` in `web/ui/.env.local`.
**Production:** `make web-build` then `make wifey-web` — FastAPI serves the UI from `/`.

---

## Makefile Usage

The Makefile provides easy commands for all major actions:

**Lint, Format, Typecheck:**

```bash
make lint           # Lint Markdown and Python (excludes venv)
make lint-py        # Lint + format Python with ruff
make typecheck      # Type check with mypy
make docs-index     # Regenerate the audit + spec indexes
```

`docs/audits/INDEX.md` and `docs/superpowers/specs/INDEX.md` are **generated** —
edit the docs, then run `make docs-index`. `tests/test_docs_index.py` fails when a
new audit or spec lands unindexed, so the indexes cannot drift silently.

It also fails when **a new audit states no verdict a machine can read**. Put the
verdict in prose under a `## Headline verdict:` heading — a table, blockquote or
`**Date:**` line under that heading is rejected on purpose, because each renders
as a plausible-but-wrong verdict. This is not style: the generated index renders
each verdict in a column, so an unreadable one shows up as an empty cell that
reads like an audit which reached no conclusion.

**Install/Update dependencies:**

```bash
make poetry-install
make poetry-update
```

**Backup:**

```bash
make backup          # Verified snapshot of analytics.db + docs/plans + memory → ~/backups/wifey
make backup-dry-run  # Report what would be captured; writes nothing
make backup-check    # Age of the newest verified snapshot; advisory, never gates CI
```

**Analytics:**

```bash
make wifey-analytics-backfill              # Backfill from 2023-01-01 (default)
make wifey-analytics-backfill SINCE=2024-01-01   # Backfill from custom date
make wifey-analytics-sync                  # Incremental sync
make wifey-universe-backfill               # Backfill the research breadth universe
make wifey-xasset-backfill                 # Backfill the cross-asset TSMOM ETF basket (1d, 2007+)
make wifey-pead-backfill                    # Ingest EDGAR earnings facts (edge-hunt #4, one-shot)
make wifey-insider-backfill                 # Ingest EDGAR Form 4 insider transactions (H-024, one-shot)
make universe-coverage                     # OHLCV coverage report over the universe
make wifey-forecast-audit                  # G2 audit — EWMAC trend sleeve (read-only)
make wifey-xsmom-audit                     # G3 audit — XS-momentum sleeve (read-only)
make wifey-xsmom-residual-audit            # Experiment #1 — residualized XS-mom 2x2 (read-only)
make wifey-lowvol-audit                     # Edge-hunt #2 — low-beta/BAB 2x2 (read-only)
make wifey-xasset-audit                     # Edge-hunt #3 — cross-asset TSMOM 2x2 (read-only)
make wifey-pead-audit                       # Edge-hunt #4 — PEAD-lite 2x2 (read-only)
make wifey-exit-audit                      # Exit MFE/MAE diagnostic — live ledger (read-only)
make wifey-n-eff                           # Effective independent series (n_eff) for a pooled panel (read-only)
```

**Backtest:**

```bash
make wifey-backtest                                          # SPY fvg 4h 90d (defaults)
make wifey-backtest SYMBOL=MSFT STRATEGY=bos                 # Override symbol and strategy
make wifey-backtest SYMBOL=AAPL STRATEGY=fvg INTERVAL=1d DAYS=30 SL_PCT=0.015 TP_R=3.0
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1  # Full sweep + persist to DB
make wifey-backtest SYMBOL=AAPL STRATEGY=bos SAVE=1         # Single-combo + persist to DB

# Co-firing confluence backtest (D10)
make wifey-combo-backtest CONFIG=config/signal_watch.toml SINCE=2025-09-12 SAVE=1
make wifey-combo-backtest CONFIG=config/signal_watch.toml WINDOW=3 MIN_TRADES=5
make wifey-combo-backtest CONFIG=config/signal_watch.toml WORKERS=2  # light mode when other processes running

# Recalibrate confidence star ratings (per-config)
make wifey-recalibrate CONFIG=config/signal_watch.toml          # dry-run
make wifey-recalibrate CONFIG=config/signal_watch.toml APPLY=1  # write to DB
make wifey-recalibrate MIN_TRADES=20 CONFIG=config/signal_watch.toml APPLY=1

# Digest: aggregated analysis over saved backtest runs
make wifey-digest QUERY=strategy           # strategy leaderboard (default)
make wifey-digest QUERY=symbol             # symbol leaderboard
make wifey-digest QUERY=direction_bias     # long vs short avg R per strategy
make wifey-digest QUERY=adr_ab             # ADR gate A/B delta — see caveat below
make wifey-digest QUERY=volume_ab          # volume suppress A/B delta
make wifey-digest QUERY=day_filter_ab      # day filter A/B delta
make wifey-digest QUERY=consistency        # edge breadth across symbol×TF combos
make wifey-digest QUERY=recovery_factor    # risk-adjusted ranking
make wifey-digest QUERY=tf                 # timeframe ranking
make wifey-digest QUERY=combos TOP_N=20    # best combos top-N
make wifey-digest QUERY=co_firing          # co-firing confluence pair leaderboard
make wifey-digest QUERY=cross_tf_combos   # cross-TF co-firing pair leaderboard (HTF→LTF)
make wifey-digest MIN_TRADES=10            # raise min-trades threshold
```

Defaults: `SYMBOL=SPY`, `STRATEGY=fvg`, `INTERVAL=4h`, `DAYS=90`.
Optional overrides: `SL_PCT`, `TP_R`, `FEE_PCT`, `SAVE=1` (persist to DB).

> **`QUERY=adr_ab` returns nothing, and that is now the honest answer.** The column it splits
> on records **what the gate executed** since 2026-08-11 (it recorded the declared config value
> before, so `adr_exempt` strategies and every `1d` / `1wk` row landed on the "on" side while
> the gate never ran). Within one config the executed threshold is a pure function of
> `(strategy, timeframe)`, so the card's join — which requires the same strategy *and*
> timeframe on both sides — can never find a genuine on/off pair from the routine sweep. It
> needs two runs **over the same window** that differ only in the ADR setting, which nothing in
> the pipeline produces; `make db-update` will not populate it. The join also requires matching
> `data_start_ms` / `data_end_ms`, without which the corrected column pairs a pre-2026-08-06 row
> against its post-fix twin and reports 661 fabricated deltas comparing measurements two months
> apart.

To populate both `day_filter` variants for complete coverage:

```bash
# day_filter = false
poetry run python wifey.py backtest --config config/signal_watch.toml --save

# day_filter = true
poetry run python wifey.py backtest --config config/signal_watch.toml --day-filter --save
```

**Persisting results for confidence score recalibration:**

Add `--save` (or `SAVE=1` via make) to store aggregate results in `analytics.db`:

```text
backtest_runs        — one row per (symbol, tf, strategy, param combo):
                       win_rate, avg_r, total_r, max_drawdown_r, all params used;
                       long_win_rate, long_avg_r, short_win_rate, short_avg_r (direction split)
backtest_trades      — one row per simulated trade, linked to backtest_runs
signal_alert_outcomes — live forward-test outcomes (renamed from signal_outcomes)
```

Re-running with the same params replaces existing rows (deterministic `run_id` hash),
so you can re-run sweeps freely without accumulating duplicates.

**Query win rate per strategy** (foundation for confidence score recalibration):

```python
import duckdb
from analytics.data_store import get_win_rate_by_strategy

conn = duckdb.connect("analytics.db", read_only=True)
print(get_win_rate_by_strategy(conn))
# strategy  total_closed  total_wins  win_rate_pct  mean_avg_r  combos_run
# fvg              312         198          63.5       +0.42         8
# bos              287         168          58.5       +0.31         8
# ...
```

Only includes combos with ≥ 20 closed trades. Use this to compare against the
current editorial star ratings in `SIGNAL_REGISTRY` and adjust `confidence` values.

**Persistence is opt-OUT, and currently ON.** `save_results` defaults to `True`
(`analytics/signal_config.py`), and no config overrides it — so the live gate already
writes a `backtest_runs` row on every scan. To disable it, add the key to the
`[backtest]` table. It is read from `data["backtest"]`, so a **top-level key is silently
ignored**:

```toml
[backtest]
save_results = false
```

Those live rows are safe to leave on: they carry `sweep_id IS NULL` and a separate
`origin="live_gate"` `run_id` namespace, so they cannot overwrite a swept row, and
`recalibrate` rates sweep rows only.

**Web frontend:**

```bash
make web-install                    # npm install in web/ui/
make web-dev                        # Vite dev server (http://localhost:5173, proxies /api to :8000)
make web-dev DEV_PORT=3000          # Override Vite port
make web-build                      # Build Svelte app → web/ui/dist/
make web-preview                    # Preview production build locally
make web-full                       # Build + start FastAPI serving the UI
make wifey-web PORT=8080           # FastAPI on a custom port
```

**Signal watch:**

```bash
make wifey-signal-watch                                              # All symbols, 4h, all strategies
make wifey-signal-watch CONFIG=config/signal_watch.toml             # Load from config file
make wifey-signal-watch CONFIG=config/signal_watch.toml TELEGRAM=1  # Config file + override flag
make wifey-signal-watch SYMBOLS="AAPL MSFT"                         # Specific symbols
make wifey-signal-watch STRATEGIES="fvg bos" TELEGRAM=1             # Specific strategies + Telegram
make wifey-signal-watch TIMEFRAMES="4h 1d 1wk" MIN_SL_PCT=0.003 TELEGRAM=1  # SL floor
make wifey-signal-test STRATEGY=bos SYMBOL=AAPL TIMEFRAME=4h          # test alert, print only
make wifey-signal-test STRATEGY=bos SYMBOL=AAPL TIMEFRAME=4h AT=2026-04-07T02:00:00  # pin candle
make wifey-signal-test CONFIG=config/signal_watch.toml STRATEGY=marubozu TIMEFRAME=4h TELEGRAM=1
```

The daemon wakes at clock-aligned candle boundaries (e.g. 04:00:10, 08:00:10 for `4h`),
so alerts arrive within seconds of the candle close. Optional overrides: `SYMBOLS`,
`TIMEFRAMES`, `STRATEGIES`, `MIN_SL_PCT`, `TELEGRAM=1` (flag).

All commands use your `.env` file for secrets and config.

---

## Docker

You can use Docker to run the bot and analytics tools in a consistent environment.
`config/stocks.json` and `.env` are excluded from the image via `.dockerignore` and
bind-mounted at runtime.

### Makefile targets

```bash
make docker-build                  # Build the image

# Analytics — analytics.db is bind-mounted from the host
make docker-analytics-backfill                       # Backfill from 2023-01-01
make docker-analytics-backfill SINCE=2024-01-01      # Backfill from custom date
make docker-analytics-sync                           # Incremental sync

# Backtest
make docker-backtest                                          # SPY fvg 4h 90d (defaults)
make docker-backtest SYMBOL=MSFT STRATEGY=bos
make docker-backtest SYMBOL=AAPL STRATEGY=fvg INTERVAL=1d

# Signal watch daemon (interactive, Ctrl+C to stop)
make docker-signal-watch                                      # All symbols, 4h, no Telegram
make docker-signal-watch TELEGRAM=1                           # With Telegram alerts
make docker-signal-watch STRATEGIES="fvg bos"
```

> **First run:** Before running analytics, backtest, or signal-watch Docker commands,
> create the bind-mount files on the host so Docker mounts files (not directories):
>
> ```bash
> touch analytics.db signal_state.json
> ```

### Docker Compose

`docker-compose.yml` is provided for long-running services. Analytics services use the
`analytics` profile and are run with `docker-compose run` (one-shot, not `up`).

```bash
# Long-running services (restart: unless-stopped)
docker-compose up signal-watch      # Signal daemon with --telegram enabled

# One-shot analytics (requires touch analytics.db on first use)
touch analytics.db signal_state.json
docker-compose run --rm analytics-backfill
SINCE=2024-01-01 docker-compose run --rm analytics-backfill
docker-compose run --rm analytics-sync
```

Make sure `config/stocks.json`, `.env`, `analytics.db`, and `signal_state.json` exist before
running signal-watch or analytics services.

---

## GitHub Actions

Three workflows run automatically on every push and pull request. All three carry a
`concurrency` group, so a superseded PR run is cancelled rather than burning to
completion — scoped to `pull_request` only, because cancelling a `main` run would
destroy the record of whether `main` is green. Every job has a `timeout-minutes`.

### `lint.yaml` — CI (always active)

Runs on every push to `main` and every PR. Uses path filters so only relevant jobs run:

| Job | Triggers on | Steps |
| --- | --- | --- |
| `markdownlint` | `*.md` changes; `.claude/skills/**` | markdownlint-cli2 across all Markdown files, plus `SKILL.md` frontmatter validation |
| `lint-typecheck-test` | `*.py` / `pyproject.toml` / `poetry.lock` changes | ruff check, ruff format, mypy, pytest; uploads `test-results.xml` as an artifact |
| `regression` | `analytics/**/*.py` / TOML / fixture / golden JSON changes | runs `make test-regression` against committed golden files; fails with a diff report if metrics drift |
| `frontend-check` | `web/ui/**` changes | `npm ci`, production build, `svelte-check` |

**No coverage in CI.** Nothing consumes it — there is no codecov/coveralls step and no
`fail_under` gate, so `coverage.xml` was an artifact nobody downloaded while
`pytest-cov`'s tracer ran on every line of every test. Run `make test-cov` locally when
you actually want to read it.

The `regression` filter is deliberately narrower than `**/*.py`: `tests/test_regression.py`
imports from `analytics.*` only, so a `tools/` or `web/` change cannot move the goldens.
`pyproject.toml` and `poetry.lock` stay in on purpose — a pandas or numpy bump *does*
move them, and finding that out silently is exactly what the suite exists to prevent.

### `docker-build.yaml` — Docker build check

Builds the Docker image when something that can affect it changes (`Dockerfile`,
`.dockerignore`, `docker-compose.yml`, `pyproject.toml`, `poetry.lock`, or the workflow
itself). The path filter sits on the `on:` **trigger**, not inside a step, so an
unaffected PR does not start the workflow at all — a step-level filter still spins up a
runner to check out the repo and evaluate the filter before deciding to skip.

### `security-scan.yaml` — Trivy filesystem scan

Two Trivy steps that answer different questions and so carry different exit codes:

| Scanner | Exit code | Why |
| --- | --- | --- |
| `secret` | `'1'` — **gates** | A committed credential is a property of *this diff*, always the author's to fix, and fixable in the same PR. `.env`, `config/stocks.json` and all of `docs/plans/` are gitignored, so a `git add -f` under `docs/plans/` is the realistic path to leaking one |
| `vuln` | `'0'` — advisory | A `CRITICAL,HIGH` CVE appears because the outside world changed, not the repo. Gating on it reddens whichever unrelated PR happens to be open and forces a dependency bump at an arbitrary moment |

The advisory step carries `if: always()` so its report still appears when the secret gate
has failed. The action is pinned to a release tag (`@v0.36.0`) rather than `@master`:
this is the workflow whose job is to catch supply-chain problems, and a mutable ref means
the scanner itself is unreviewed code that can change between two runs of the same commit.

---

## Linting and Type Checking

This project uses:

- **ruff** for Python linting and formatting
- **mypy** for static type checking
- **markdownlint-cli2** for Markdown linting
- **pre-commit** for automated checks on every commit

To check formatting and types locally:

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy .
poetry run pytest tests/ -q --durations=10
```

Coverage is not part of the default run — see the CI notes above. `make test-cov`
produces a report on demand.

---

## Coming Soon / Ideas

- Auto-close on global SL or high-risk warning
- Telegram command handler (`/price`, `/position`)
