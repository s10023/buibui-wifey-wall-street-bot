---
name: stats-dashboard
description: >
  Stats page architecture: card inventory, adding new stat cards, timezone /
  caching constraints, and the `stats_lib.py` ↔ router ↔ Svelte UI flow.
  Invoke when the user says "/stats-dashboard", touches `stats_lib.py`,
  `web/api/routers/stats.py`, `Stats.svelte`, or any P1/P2, ADR, DOW, session,
  or weekly-timing data — even for small fixes.
allowed-tools: "*"
---

# Stats Dashboard Skill

Use when working on the Stats page or its backend — adding new stat cards, fixing data, changing layout, or debugging the API.

---

## Architecture

```text
analytics/stats_lib.py          ← pure computation (DuckDB queries, returns StatsBundle)
web/api/routers/stats.py        ← GET /api/stats/{symbol}?days=180 (cached in stats_cache table)
web/api/models/                 ← Pydantic response models (if any)
web/ui/src/pages/Stats.svelte   ← hero cones + card grid UI
web/ui/src/api.ts               ← getStats(symbol, days) typed client
```

## The 11 Cards

### Cached in StatsBundle (`compute_all` → `stats_cache` table)

| Card | stats_lib fn | Data key | Notes |
| ------ | ------------- | ---------- | ------- |
| Daily Path Cone (hero) | `compute_path_cone` | `path_cone` | M5: ADR14-normalized hourly paths over complete 7-bar RTH sessions; 18 direction × Mon–Fri combos; all-history (ignores `days`); ET axis labels; `PathCone.svelte` over `lib/cone.ts` |
| Weekly Path Cone (hero) | `compute_weekly_cone` | `weekly_cone` | M5: AWR14-normalized 35-bar Monday-anchored trading weeks (holiday weeks drop); all/bull/bear with "all" as gray reference band; `WeeklyCone.svelte` |
| P1/P2 Daily | `compute_p1p2_daily` | `p1p2` | overall + per-DOW bars; `p1_strong_pct` = fraction where P1-direction wick < 20% range; Low First = green, High First = red |
| Average Daily Range | `compute_adr` | `adr` | ADR(14), ADR(30) + `adr_14_median`/`adr_30_median` rendered `avg / med` at **2dp** (1dp collapses the pair — AAPL 30d ties at 2.19%), today_range_pct, today_consumed_pct (**÷ ADR14 MEAN — never the median**; `test_today_consumed_still_divides_by_the_MEAN` guards it), today_move_up |
| Hourly Extreme Distribution | `compute_hourly_extremes` | `hourly_extremes` | 24 bars, MYT; `peak_high/low_hour_by_dow` per-DOW MODE |
| Day-of-Week Patterns | `compute_dow_patterns` | `dow_patterns` | avg_range_pct, bull_pct, avg_return_pct + `median_return_pct` + `return_stderr_pct`, `strong_high_pct`/`strong_low_pct` (Str H/L = rejection wick < 20% range) per DOW. **Both return columns DIM when the mean sits inside `2.576 × SE`** — see the noise rule below |
| Session Breakdown | `compute_session_breakdown` | `sessions` | Asia (08–13 MYT)/London (14–21)/NY (20–03); 04–07 dead zone; London/NY overlap double-counted |
| Weekly P1/P2 | `compute_weekly_p1p2` | `weekly_p1p2` | raw DOW distribution (not cumulative); use P2 Timing for "is extreme in yet?" |
| Weekly P2 Timing | `compute_weekly_p2_timing` + `compute_weekly_flip_risk_conditioned` | `weekly_p2_timing` + `weekly_flip_risk_conditioned` | All: unconditional still-ahead % + flip risk; Bullish/Bearish P1 toggle: P(P2 still ahead \| p1_direction, DOW); live "This week" banner |

### Live — never cached (injected after cache hit via `_inject_live_fields()`)

| Card | stats_lib fn | Notes |
| ------ | ------------- | ------- |
| Daily Path Cone overlay | `compute_today_path(conn, symbol)` | Today's ADR14-normalized partial path (dotted amber line on the cached cone); None on short history |
| Weekly Path Cone overlay | `compute_current_week_path(conn, symbol)` | The forming week's AWR14-normalized partial path; None on short history |
| P1 Wick Rank | `compute_weekly_wick_percentile(conn, symbol, adr_14, days)` | Current week's P1 wick exceedance vs historical P1 wicks; "P1 not yet set" when only one weekly extreme has formed; fresh every request |
| Weekly Current State | `compute_weekly_current_state(conn, symbol, adr_14, days)` | Live banner: current DOW, move% from weekly open, distance bucket, conditioned low/high-still-ahead probabilities |

**Why split?** The cached bundle is safe to serve stale for a day. The live cards must reflect the current candle's position, so they bypass the cache entirely.

## Key constraints

- **Timezone**: DuckDB on this host requires `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP` for MYT — NOT `AT TIME ZONE`.
- **Empty data**: `stats_lib.py` raises `ValueError` on empty data; API must return 422/404 not 500.
- **Caching**: cached in `stats_cache` table keyed by `(symbol, days, date)`. Live fields never enter the cache.
- **Sessions**: London (14–21 MYT) and NY (20–03 MYT) overlap — one candle can count in both.
- **DOW order**: `["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]` — enforce this in Svelte, not API.
  **US equities yield only the first five**; `compute_dow_patterns` does not filter weekends, the
  data simply has none. That row count is load-bearing — see the next bullet.
- **The DOW return noise rule (`DOW_NOISE_SE = 2.576`, ported from parent #598).** Both return
  columns render in a neutral grey when the mean sits inside `2.576 × return_stderr_pct`, and a
  dimmed `+` and a dimmed `−` use the **same** grey on purpose: the sign of a value inside its own
  error bar carries no information, so colouring it re-introduces the false read the dimming
  removes. Values that round to zero print **without a sign** (`0.0%`, never `-0.0%`).
  **2.576 is Bonferroni over FIVE weekdays** (0.05/5 = 0.01 two-sided) — **not** the parent's
  **2.69**, which is the k=7 crypto constant. Re-derive it if the row count ever changes, and keep
  it in step with `tests/test_stats_dow_return.py::DOW_NOISE_SE`.
  **It is a display threshold, not a gate** — nothing sizes or trades off it. On this repo's data
  no cell survives it: measured 2026-08-12 across SPY/QQQ/NVDA/AAPL/MSFT at 365d, **25 of 25** fell
  inside the bar, the largest `|t|` anywhere was **1.76**, and mean and median disagreed on sign in
  **5 of 25**. Script: `docs/plans/scripts/dow_return_noise.py` — it calls the production functions
  rather than reading `1d` bars, because **the two sources disagree** (both `compute_adr` and
  `compute_dow_patterns` aggregate `1h`).
- **Str H/L definition**: Str H = large upper wick (rejection), Str L = large lower wick (rejection). NOT continuation (closed near extreme). `strong_high_pct` = fraction of days where upper wick < 20% of range.

## Adding a new stat card

Decide: is the card **cacheable** (uses only historical OHLCV, same answer all day) or **live** (depends on today's candle position)?

**Cacheable card:**

1. Add `compute_<name>(conn, df)` to `stats_lib.py` → return typed dataclass
2. Add to `StatsBundle` + call from `compute_all`
3. Add field to `StatsResponse` in the API model
4. Add TypeScript type to `StatsResponse` in `web/ui/src/api.ts`
5. Add `<div class="card">` block in `Stats.svelte`
6. Add test in `tests/` using `duckdb.connect(":memory:")`

**Live card:**

1. Add `compute_<name>(conn, symbol, adr_14, days)` to `stats_lib.py` (takes conn directly, not df)
2. Add to `_inject_live_fields()` in `web/api/routers/stats.py` — called after cache hit/miss
3. Everything else same as above (step 3–6)

## Checks after any change

```bash
make lint-py
make typecheck
make test
# For UI changes: make web-build
```

## UI style rules

Load `/frontend-design` before any CSS/layout changes.

- Card title: `font-size: 11px; text-transform: uppercase; color: var(--accent)`
- Accent values: `color: var(--accent)`. Green/red: `var(--green)` / `var(--red)`
- Help button: small `?` circle, toggles inline `help-panel` below card title
- Wide cards (full row): add class `card-wide` (`grid-column: 1 / -1`)
