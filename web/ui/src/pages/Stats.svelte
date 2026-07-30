<script lang="ts">
  import { onMount } from "svelte";
  import { getStats, type StatsResponse } from "../api";
  import { symbols } from "../stores/config";
  import { configDefaultSymbol } from "../stores/activeConfig";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import PathCone from "../components/PathCone.svelte";
  import LiveOutcomes from "../components/LiveOutcomes.svelte";

  const TIMEFRAMES_DAYS = [30, 90, 180, 365];
  const DOW_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  let symbol = $state($configDefaultSymbol ?? "BTCUSDT");
  let days = $state(365);
  let stats = $state<StatsResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let openHelp = $state<string | null>(null);

  // P1/P2 card: "low_first" = bullish context (low forms first); "high_first" = bearish context
  let p1p2Mode = $state<"low_first" | "high_first">("low_first");
  // Weekly card: default "high_first" (bearish — when does weekly high form?)
  let weeklyMode = $state<"low_first" | "high_first">("high_first");
  // Weekly P2 Timing toggle: "all" = unconditional, "low" = bullish P1, "high" = bearish P1
  let p1FilterMode = $state<"all" | "low" | "high">("all");

  // Live MYT clock — ticks every minute
  let now = $state(Date.now());

  onMount(() => {
    void loadStats();
    const t = setInterval(() => { now = Date.now(); }, 60_000);
    return () => clearInterval(t);
  });

  // MYT = UTC+8: shift timestamp then read UTC fields
  const currentMYTHour = $derived(new Date(now + 8 * 3600 * 1000).getUTCHours());
  const todayDOW = $derived(
    ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(now + 8 * 3600 * 1000).getUTCDay()]
  );

  // Active equity session based on current ET wall-clock (DST-aware via toLocaleString).
  // Half-open windows mirror the T15 alert formatter:
  //   Pre-Market  [04:00, 09:30)
  //   RTH         [09:30, 15:00)
  //   Power Hour  [15:00, 16:00)
  //   After Hours [16:00, 20:00)
  const currentETMinutes = $derived.by(() => {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const parts = fmt.formatToParts(new Date(now));
    const h = parseInt(parts.find((p) => p.type === "hour")?.value ?? "0", 10);
    const m = parseInt(parts.find((p) => p.type === "minute")?.value ?? "0", 10);
    return h * 60 + m;
  });
  const activeSessions = $derived(
    [
      currentETMinutes >= 240 && currentETMinutes < 570 ? "Pre-Market" : null,
      currentETMinutes >= 570 && currentETMinutes < 900 ? "RTH" : null,
      currentETMinutes >= 900 && currentETMinutes < 960 ? "Power Hour" : null,
      currentETMinutes >= 960 && currentETMinutes < 1200 ? "After Hours" : null,
    ].filter(Boolean) as string[]
  );

  const CARD_HELP: Record<string, { what: string; value: string; example: string }> = {
    p1p2: {
      what: "For each historical day, was the daily LOW or HIGH made first? Toggle 'Low First' (bullish context — dip then recovery) vs 'High First' (bearish — pump then dump). Today's day of week is highlighted.",
      value: "Before entering a long on a dip, check if today's DOW historically makes the low first. If P1=Low is 62% on Thursdays, the day structure is behind you. If you're shorting a Thursday rally, you're fighting historical bias.",
      example: "Thursday P1=Low 62% → low forms first then recovers. Favour longs on Thursday dips.",
    },
    adr: {
      what: "ADR(14) = 2-week rolling average range — short-term volatility. ADR(30) = monthly baseline. Both are (daily high − low) / open. The 'Today' gauge shows how much of the typical daily range is already consumed. Note: DOW Patterns 'Avg Range' is the historical average for that specific weekday type across the full lookback — a different metric.",
      value: "If today's gauge is 90%+ consumed, the move is done — don't chase entries. If ADR(14) >> ADR(30), the market is recently more volatile than usual — widen your stops.",
      example: "ADR(14) 2.8%, today 1.2% → 43% consumed. Room to run.",
    },
    hourly: {
      what: "24-column chart: fraction of days each MYT hour made the daily high (green) vs daily low (red). Current hour is highlighted with a border. Peaks are labelled.",
      value: "Your empirically-derived kill zone for this specific asset — not fixed ICT windows. If the high peak is at 14:00 and it's only 08:00, the high hasn't formed yet. If the high peak was at 14:00 and it's now 20:00, the high is likely already in.",
      example: "High peak 14:00, low peak 02:00 MYT → buy the 02:00 dip, target 14:00 expansion.",
    },
    dow: {
      what: "Per weekday: avg daily range, bull/bear split, avg directional return (green=bullish, red=bearish), and N (sample count). Today's row is highlighted.",
      value: "Size up on wide-range directional days. Combine Avg Return with Direction split for a fuller bias picture.",
      example: "Wed 3.1% range, 58% bull, +1.8% avg → lean longs, size normally. Fri 1.8%, 49%, −0.6% → reduce exposure.",
    },
    session: {
      what: "US equity sessions (America/New_York wall-clock, DST-aware): Pre-Market (04:00–09:30), RTH (09:30–15:00), Power Hour (15:00–16:00), After Hours (16:00–20:00). Overnight 20:00–04:00 ET is excluded. Each column = fraction of trading days that session made the daily high or low. The active session is shown with ●.",
      value: "If RTH makes the daily low 41% of the time and price is falling during RTH, there's meaningful probability you're watching the daily low form.",
      example: "RTH Lo 41% + price dropping in RTH → probable daily low forming. Watch for reversal.",
    },
    weekly: {
      what: "Which day of the week most commonly forms the weekly extreme. Bear context shows when the weekly HIGH forms (useful for shorts). Bull context shows when the weekly LOW forms (useful for longs). Bars show % of weeks each DOW made that extreme, normalized to the dominant day.",
      value: "Shows the raw distribution — which day is most common — but not cumulative probability. To ask 'is the high/low likely already set by today?', use the Weekly P2 Timing card, which gives cumulative still-ahead probabilities per DOW.",
      example: "Weekly High: Tue 34% — Tuesday is the single most common high day. Check P2 Timing to see cumulative P(high still ahead) from today.",
    },
    weeklyTiming: {
      what: "For each day of week, P(weekly LOW or HIGH still forms after that day). 'All' shows unconditional still-ahead % for both extremes, plus flip risk (P the running extreme gets undercut later in the week, even if one already exists). 'Bullish P1' = only bullish weeks (low set first) — shows P(weekly high still ahead | DOW). 'Bearish P1' = bearish weeks — shows P(weekly low still ahead | DOW).",
      value: "High % = that extreme likely hasn't formed yet. Low % = it's likely already in. Riskier for a long when 'Low still ahead' is HIGH (e.g. 70%) — the weekly low is probably still below, so entering long now risks catching the drop. Flip risk shows the chance the running extreme gets beaten later in the week. Use the Bullish P1 filter once a weekly low is confirmed forming: it shows whether the high (P2) is still statistically expected.",
      example: "Today Mon, Bullish P1 filter → 71% of bullish weeks still set the high after Mon → weekly high very likely still ahead → good timing window for longs targeting the weekly high.",
    },
    pathCone: {
      what: "Historical intraday paths — hourly closes as ×ADR14 from the session open — pooled into percentile bands: p10–p90 outer, p25–p75 inner, median line. Filter by session direction (bull/bear at close) and weekday; the dotted amber line is today so far on the same scale. 'By now' row: fraction of matching sessions whose eventual low/high was already set by the current bar. Pivots: typical (p50) and extended (p80) high/low excursions mapped to today's prices.",
      value: "Read where today sits inside the historical envelope: hugging p90 = extended vs the template — chasing here is late; near the median = nothing unusual yet. The 'by now' row says whether the session extreme is statistically already in. Pivots give price targets/invalidation for the day. Thin-sample combos (n<30, amber ⚠) are directional hints, not statistics.",
      example: "Bull + Tue at 14:30 ET: today riding p75, low-in-by 81% → the dip is likely in; H p80 pivot 1.6% above → remaining upside bounded. Fade extension, don't chase.",
    },
    wickPercentile: {
      what: "For this week's P1 candle (the 1h candle that first set the weekly extreme), how does its wick size compare to all historical P1 candles? Wick is measured in the P1 direction (lower wick for P1=low, upper wick for P1=high), normalised by the candle's open price and ADR14. Exceedance % = fraction of historical P1 weeks with a BIGGER wick than this week's.",
      value: "Low exceedance (e.g. 15%) means this week's P1 wick is unusually large — only 15% of historical weeks had a bigger wick. A large wick indicates a sharp sweep-and-reverse. High exceedance means the wick is small relative to history — the extreme may not hold.",
      example: "Exceedance 20% → this week's P1 wick is larger than 80% of historical weeks. Strong sweep-and-reverse signal — wait for 1h close to confirm before entering.",
    },
    liveOutcomes: {
      what: "REAL outcomes of every Telegram alert the live daemon fired, scored from the signal_alert_outcomes ledger (cross-symbol). Roll-up is all-time: resolved = TP/SL touched or held to expiry; No-TP should read 0 (every fired alert now persists a stop/target). The tables window by the selected period. Win rate excludes expired trades; avg R averages outcome_r over all resolved rows.",
      value: "This is ground truth — what actually happened live, not a backtest. Use it to confirm or kill an edge: if a (strategy, tf, direction) cell is positive live with enough N, it earns its place; if it bleeds, it's a hard-flip candidate. Note retro-backfilled rows use a pct-fallback stop (best-effort R), while forward rows are exact.",
      example: "bos 4h short +1.46R (70% win, n=40) vs bos 4h long −0.50R → shorts win, longs lose. Evidence for suppressing bos longs.",
    },
  };

  function toggleHelp(card: string): void {
    openHelp = openHelp === card ? null : card;
  }

  const formatPct = (v: number) => (v * 100).toFixed(1) + "%";
  const fmtHour = (h: number) => String(h).padStart(2, "0") + ":00";

  async function loadStats(): Promise<void> {
    loading = true;
    error = null;
    try {
      stats = await getStats(symbol, days);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const hourlyRows = $derived(stats ? stats.hourly_extremes : []);
  const maxHighPct = $derived(hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.high_pct)) : 1);
  const maxLowPct  = $derived(hourlyRows.length ? Math.max(...hourlyRows.map((r) => r.low_pct)) : 1);

  const p1p2Rows = $derived(
    stats
      ? DOW_ORDER.map((d) => stats!.p1p2.by_dow.find((r) => r.dow === d)).filter(Boolean)
      : []
  );

  const maxDOWRange = $derived(
    stats ? Math.max(...stats.dow_patterns.map((r) => r.avg_range_pct)) : 1
  );

  const weeklyBars = $derived(
    stats
      ? DOW_ORDER.map((d) => ({
          dow: d,
          pct: weeklyMode === "high_first"
            ? (stats!.weekly_p1p2.high_by_dow?.[d] ?? 0)
            : (stats!.weekly_p1p2.low_by_dow?.[d] ?? 0),
        }))
      : []
  );

  const weeklyMaxPct = $derived(
    weeklyBars.length ? (Math.max(...weeklyBars.map((r) => r.pct)) || 1) : 1
  );

  // Conditioned flip risk lookup: { [dow_label]: flip_pct } filtered by p1FilterMode
  const conditionedFlipByDow = $derived((): Record<string, { pct: number; n: number }> => {
    if (!stats?.weekly_flip_risk_conditioned || p1FilterMode === "all") return {};
    const dir = p1FilterMode; // "low" | "high"
    return Object.fromEntries(
      stats.weekly_flip_risk_conditioned.rows
        .filter((r) => r.p1_direction === dir)
        .map((r) => [r.dow_label, { pct: r.flip_pct, n: r.sample_count }])
    );
  });
</script>

<div class="page">
  <div class="page-header">
    <h2>Stats</h2>

    <div class="controls">
      <select bind:value={symbol}>
        {#each $symbols as sym}
          <option value={sym}>{sym}</option>
        {/each}
      </select>

      <div class="days-group">
        <select bind:value={days}>
          {#each TIMEFRAMES_DAYS as d}
            <option value={d}>{d}d</option>
          {/each}
        </select>
        <span class="days-note">lookback (ADR always uses last 14/30d)</span>
      </div>

      <button onclick={() => void loadStats()} disabled={loading} class="btn-load">
        {loading ? "Loading…" : "Load"}
      </button>
    </div>
  </div>

  {#if error}
    <ErrorBanner error={error} />
  {/if}

  {#if loading}
    <LoadingSpinner label="Computing statistics…" />
  {:else if stats}
    <!-- Daily Path Cone — hero (M5) -->
    <div class="card hero-card">
      <div class="card-header">
        <span class="card-title">Daily Path Cone</span>
        <button class="help-btn" class:active={openHelp === "pathCone"} onclick={() => toggleHelp("pathCone")} aria-label="Help">?</button>
      </div>
      {#if openHelp === "pathCone"}
        <div class="help-panel">
          <div class="help-section"><span class="help-label">What</span>{CARD_HELP.pathCone.what}</div>
          <div class="help-section"><span class="help-label">Use</span>{CARD_HELP.pathCone.value}</div>
          <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.pathCone.example}</div>
        </div>
      {/if}
      <PathCone pathCone={stats.path_cone} todayPath={stats.today_path} />
    </div>

    <div class="grid">

      <!-- P1/P2 Daily -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">P1/P2 Daily</span>
          <div class="header-actions">
            <div class="pill-toggle">
              <button class:active={p1p2Mode === "low_first"} onclick={() => { p1p2Mode = "low_first"; }}>Low First</button>
              <button class:active={p1p2Mode === "high_first"} onclick={() => { p1p2Mode = "high_first"; }}>High First</button>
            </div>
            <button class="help-btn" class:active={openHelp === "p1p2"} onclick={() => toggleHelp("p1p2")} aria-label="Help">?</button>
          </div>
        </div>
        {#if openHelp === "p1p2"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.p1p2.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.p1p2.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.p1p2.example}</div>
          </div>
        {/if}
        <div class="card-subtitle">
          {#if p1p2Mode === "low_first"}
            Low made before High: <span class="val-accent">{formatPct(stats.p1p2.overall_p1_low_pct)}</span>
          {:else}
            High made before Low: <span class="val-accent">{formatPct(1 - stats.p1p2.overall_p1_low_pct)}</span>
          {/if}
          <span class="muted"> ({stats.p1p2.sample_days}d sampled)</span>
          · P1 strong: <span class="val-accent">{formatPct(stats.p1p2.p1_strong_pct)}</span>
          <span class="p1-strong-hint muted">(closed near P2 extreme)</span>
        </div>
        <div class="dow-bars">
          {#each p1p2Rows as row}
            {#if row}
              {@const pct = p1p2Mode === "low_first" ? row.p1_low_pct : 1 - row.p1_low_pct}
              <div class="dow-bar-row" class:today-row={row.dow === todayDOW}>
                <span class="dow-label">{row.dow}</span>
                <div class="bar-track">
                  <div class="bar-fill" class:bar-fill-bear={p1p2Mode === "high_first"} style="width: {(pct * 100).toFixed(1)}%"></div>
                </div>
                <span class="bar-pct">{formatPct(pct)}</span>
              </div>
            {/if}
          {/each}
        </div>
      </div>

      <!-- ADR -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Average Daily Range</span>
          <button class="help-btn" class:active={openHelp === "adr"} onclick={() => toggleHelp("adr")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "adr"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.adr.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.adr.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.adr.example}</div>
          </div>
        {/if}
        <div class="adr-rows">
          <div class="adr-row">
            <span class="adr-label">ADR(14) <span class="adr-sublabel">2-week</span></span>
            <span class="val-accent">{formatPct(stats.adr.adr_14)}</span>
          </div>
          <div class="adr-row">
            <span class="adr-label">ADR(30) <span class="adr-sublabel">monthly</span></span>
            <span class="val-accent">{formatPct(stats.adr.adr_30)}</span>
          </div>
          {#if stats.adr.today_range_pct !== null}
            <div class="adr-row">
              <span class="adr-label">Today</span>
              <span class="val-accent">
                {formatPct(stats.adr.today_range_pct)}
                {#if stats.adr.today_consumed_pct !== null}
                  <span class="muted">({formatPct(stats.adr.today_consumed_pct)} used)</span>
                {/if}
              </span>
            </div>
            {#if stats.adr.today_consumed_pct !== null}
              {@const consumed = stats.adr.today_consumed_pct}
              <div class="adr-gauge-track">
                <div
                  class="adr-gauge-fill"
                  class:adr-gauge-warn={consumed >= 0.8}
                  style="width: {Math.min(consumed * 100, 100).toFixed(1)}%"
                ></div>
              </div>
              {#if consumed >= 0.8}
                <div class="adr-warn">Range nearly consumed — avoid chasing entries</div>
              {/if}
            {/if}
          {/if}
        </div>
      </div>

      <!-- Hourly Extreme Distribution -->
      <div class="card card-wide">
        <div class="card-header">
          <span class="card-title">Hourly Extreme Distribution (MYT)</span>
          <button class="help-btn" class:active={openHelp === "hourly"} onclick={() => toggleHelp("hourly")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "hourly"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.hourly.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.hourly.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.hourly.example}</div>
          </div>
        {/if}
        <div class="card-subtitle muted">
          High peak: <span class="val-green">{fmtHour(stats.hourly_extremes.reduce((a, b) => a.high_pct > b.high_pct ? a : b).hour_myt)} MYT</span>
          &nbsp;·&nbsp;
          Low peak: <span class="val-red">{fmtHour(stats.hourly_extremes.reduce((a, b) => a.low_pct > b.low_pct ? a : b).hour_myt)} MYT</span>
          &nbsp;·&nbsp;
          Now: <span class="val-accent">{fmtHour(currentMYTHour)} MYT</span>
        </div>
        <div class="hourly-chart">
          {#each hourlyRows as row}
            <div class="hour-col" class:hour-now={row.hour_myt === currentMYTHour}>
              <div class="hour-bars">
                <div
                  class="hour-bar high"
                  style="height: {maxHighPct > 0 ? (row.high_pct / maxHighPct * 60).toFixed(1) : 0}px"
                  title="{fmtHour(row.hour_myt)} High {formatPct(row.high_pct)}"
                ></div>
                <div
                  class="hour-bar low"
                  style="height: {maxLowPct > 0 ? (row.low_pct / maxLowPct * 60).toFixed(1) : 0}px"
                  title="{fmtHour(row.hour_myt)} Low {formatPct(row.low_pct)}"
                ></div>
              </div>
              <span class="hour-label">{row.hour_myt}</span>
            </div>
          {/each}
        </div>
        <div class="hourly-legend">
          <span class="legend-high">■ High</span>
          <span class="legend-low">■ Low</span>
          <span class="legend-now">▼ Now</span>
        </div>
      </div>

      <!-- DOW Patterns -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Day-of-Week Patterns</span>
          <button class="help-btn" class:active={openHelp === "dow"} onclick={() => toggleHelp("dow")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "dow"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.dow.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.dow.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.dow.example}</div>
          </div>
        {/if}
        <table class="stat-table">
          <thead>
            <tr>
              <th>Day</th>
              <th>Avg Range</th>
              <th>Direction</th>
              <th>Avg Return</th>
              <th title="Strong high: close in bottom 20% of range — high strongly rejected, likely to hold">Str H</th>
              <th title="Strong low: close in top 20% of range — low strongly rejected, likely to hold">Str L</th>
              <th title="Number of that weekday in the lookback window">N</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.dow_patterns as row}
              {@const ret = row.avg_return_pct}
              {@const isPos = ret >= 0}
              <tr class:today-row={row.dow === todayDOW}>
                <td class="dow-name">{row.dow}</td>
                <td>
                  <div class="range-cell">
                    <div class="range-mini">
                      <div class="range-mini-fill" style="width: {(row.avg_range_pct / maxDOWRange * 100).toFixed(0)}%"></div>
                    </div>
                    <span>{formatPct(row.avg_range_pct)}</span>
                  </div>
                </td>
                <td>
                  <div class="bias-cell">
                    <div class="bias-split">
                      <div class="bias-bull" style="width: {(row.bull_pct * 100).toFixed(0)}%"></div>
                      <div class="bias-bear" style="width: {((1 - row.bull_pct) * 100).toFixed(0)}%"></div>
                    </div>
                    <span class:bull={row.bull_pct >= 0.5} class:bear={row.bull_pct < 0.5}>
                      {formatPct(row.bull_pct)}
                    </span>
                  </div>
                </td>
                <td class:val-green={isPos} class:val-red={!isPos}>
                  {isPos ? "+" : ""}{(ret * 100).toFixed(1)}%
                </td>
                <td class="strong-cell" class:strong-hi={row.strong_high_pct >= 0.6} class:strong-lo-dim={row.strong_high_pct < 0.4}>
                  {(row.strong_high_pct * 100).toFixed(0)}%
                </td>
                <td class="strong-cell" class:strong-lo={row.strong_low_pct >= 0.6} class:strong-lo-dim={row.strong_low_pct < 0.4}>
                  {(row.strong_low_pct * 100).toFixed(0)}%
                </td>
                <td class="val-muted">{row.sample_days}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <!-- Session Breakdown -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Session Breakdown</span>
          <button class="help-btn" class:active={openHelp === "session"} onclick={() => toggleHelp("session")} aria-label="Help">?</button>
        </div>
        {#if openHelp === "session"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.session.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.session.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.session.example}</div>
          </div>
        {/if}
        <table class="stat-table">
          <thead>
            <tr>
              <th>Session (MYT)</th>
              <th>Hi %</th>
              <th>Lo %</th>
            </tr>
          </thead>
          <tbody>
            {#each stats.sessions as row}
              {@const isActive = activeSessions.includes(row.session)}
              <tr class:session-active={isActive}>
                <td class="session-name-cell">
                  {row.session}
                  {#if isActive}<span class="live-dot" title="Active now">●</span>{/if}
                </td>
                <td>
                  <div class="session-bar-cell">
                    <div class="session-bar-track">
                      <div class="session-bar-fill session-bar-high" style="width: {(row.high_pct * 100).toFixed(0)}%"></div>
                    </div>
                    <span class="val-green">{formatPct(row.high_pct)}</span>
                  </div>
                </td>
                <td>
                  <div class="session-bar-cell">
                    <div class="session-bar-track">
                      <div class="session-bar-fill session-bar-low" style="width: {(row.low_pct * 100).toFixed(0)}%"></div>
                    </div>
                    <span class="val-red">{formatPct(row.low_pct)}</span>
                  </div>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        <div class="session-note muted">Pre-Market 04:00–09:30 · RTH 09:30–15:00 · Power Hour 15:00–16:00 · After Hours 16:00–20:00 (America/New_York)</div>
      </div>

      <!-- Weekly P1/P2 -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Weekly P1/P2</span>
          <div class="header-actions">
            <div class="pill-toggle">
              <button class:active={weeklyMode === "high_first"} onclick={() => { weeklyMode = "high_first"; }}>Bear</button>
              <button class:active={weeklyMode === "low_first"} onclick={() => { weeklyMode = "low_first"; }}>Bull</button>
            </div>
            <button class="help-btn" class:active={openHelp === "weekly"} onclick={() => toggleHelp("weekly")} aria-label="Help">?</button>
          </div>
        </div>
        {#if openHelp === "weekly"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.weekly.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.weekly.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.weekly.example}</div>
          </div>
        {/if}
        <div class="card-subtitle">
          {#if weeklyMode === "high_first"}
            Weekly high before low: <span class="val-accent">{formatPct(1 - stats.weekly_p1p2.overall_p1_low_pct)}</span>
            · Most common: <span class="val-red">{stats.weekly_p1p2.high_day}</span>
          {:else}
            Weekly low before high: <span class="val-accent">{formatPct(stats.weekly_p1p2.overall_p1_low_pct)}</span>
            · Most common: <span class="val-green">{stats.weekly_p1p2.low_day}</span>
          {/if}
          <span class="muted"> ({stats.weekly_p1p2.sample_weeks} wks)</span>
        </div>
        {#if stats.weekly_p1p2.sample_weeks < 26}
          <div class="low-sample-warn">
            ⚠ Only {stats.weekly_p1p2.sample_weeks} weeks of data — days with 0% may simply not have occurred yet. Sync more history (180d+ recommended) for reliable distribution.
          </div>
        {/if}
        <div class="dow-bars">
          {#each weeklyBars as row}
            <div class="dow-bar-row" class:today-row={row.dow === todayDOW}>
              <span class="dow-label">{row.dow}</span>
              <div class="bar-track">
                <div
                  class="bar-fill"
                  class:bar-fill-bear={weeklyMode === "high_first"}
                  style="width: {weeklyMaxPct > 0 ? (row.pct / weeklyMaxPct * 100).toFixed(1) : 0}%"
                ></div>
              </div>
              <span class="bar-pct">{formatPct(row.pct)}</span>
            </div>
          {/each}
        </div>
      </div>

      <!-- Weekly P2 Timing -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Weekly P2 Timing</span>
          <div class="header-actions">
            <div class="pill-toggle">
              <button class:active={p1FilterMode === "all"} onclick={() => { p1FilterMode = "all"; }}>All</button>
              <button class:active={p1FilterMode === "low"} onclick={() => { p1FilterMode = "low"; }}>Bullish P1</button>
              <button class:active={p1FilterMode === "high"} onclick={() => { p1FilterMode = "high"; }}>Bearish P1</button>
            </div>
            <button class="help-btn" class:active={openHelp === "weeklyTiming"} onclick={() => toggleHelp("weeklyTiming")} aria-label="Help">?</button>
          </div>
        </div>
        {#if openHelp === "weeklyTiming"}
          <div class="help-panel">
            <div class="help-section"><span class="help-label">What</span>{CARD_HELP.weeklyTiming.what}</div>
            <div class="help-section"><span class="help-label">Value</span>{CARD_HELP.weeklyTiming.value}</div>
            <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.weeklyTiming.example}</div>
          </div>
        {/if}

        {#if p1FilterMode === "all"}
          <div class="p2-timing-grid">
            <div class="p2-timing-header"></div>
            <div class="p2-timing-header val-green">Low still ahead</div>
            <div class="p2-timing-header flip-col">Low flip risk</div>
            <div class="p2-timing-header flip-col">High flip risk</div>
            <div class="p2-timing-header val-red">High still ahead</div>
            {#each DOW_ORDER as dow}
              {@const lowAhead = stats.weekly_p2_timing.low_still_ahead_by_dow[dow] ?? null}
              {@const highAhead = stats.weekly_p2_timing.high_still_ahead_by_dow[dow] ?? null}
              {@const lowFlip = stats.weekly_p2_timing.low_flip_risk_by_dow[dow] ?? null}
              {@const highFlip = stats.weekly_p2_timing.high_flip_risk_by_dow[dow] ?? null}
              <div class="p2-timing-dow" class:today-cell={dow === todayDOW}>{dow}</div>
              <div class="p2-timing-val" class:today-cell={dow === todayDOW}>
                {#if lowAhead !== null}
                  <div class="p2-bar-track">
                    <div class="p2-bar-fill p2-bar-bull" style="width: {(lowAhead * 100).toFixed(0)}%"></div>
                  </div>
                  <span class:val-green={lowAhead >= 0.5} class:val-muted={lowAhead < 0.5}>{formatPct(lowAhead)}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
              <div class="p2-timing-val flip-col" class:today-cell={dow === todayDOW}>
                {#if lowFlip !== null}
                  <span class:val-amber={lowFlip >= 0.3} class:val-muted={lowFlip < 0.3}>{formatPct(lowFlip)}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
              <div class="p2-timing-val flip-col" class:today-cell={dow === todayDOW}>
                {#if highFlip !== null}
                  <span class:val-amber={highFlip >= 0.3} class:val-muted={highFlip < 0.3}>{formatPct(highFlip)}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
              <div class="p2-timing-val" class:today-cell={dow === todayDOW}>
                {#if highAhead !== null}
                  <div class="p2-bar-track">
                    <div class="p2-bar-fill p2-bar-bear" style="width: {(highAhead * 100).toFixed(0)}%"></div>
                  </div>
                  <span class:val-red={highAhead >= 0.5} class:val-muted={highAhead < 0.5}>{formatPct(highAhead)}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
            {/each}
          </div>
        {:else}
          {@const condLabel = p1FilterMode === "low" ? "P2 (High) still ahead" : "P2 (Low) still ahead"}
          {@const condColor = p1FilterMode === "low" ? "val-red" : "val-green"}
          {@const condFill = p1FilterMode === "low" ? "p2-bar-bear" : "p2-bar-bull"}
          <div class="p2-conditioned-note muted">
            {p1FilterMode === "low" ? "Bullish P1 weeks (low set first)" : "Bearish P1 weeks (high set first)"}
            — {condLabel} per day-of-week
          </div>
          <div class="p2-cond-grid">
            <div class="p2-timing-header"></div>
            <div class="p2-timing-header {condColor}">{condLabel}</div>
            <div class="p2-timing-header muted">n</div>
            {#each DOW_ORDER as dow}
              {@const entry = conditionedFlipByDow()[dow] ?? null}
              <div class="p2-timing-dow" class:today-cell={dow === todayDOW}>{dow}</div>
              <div class="p2-timing-val" class:today-cell={dow === todayDOW}>
                {#if entry !== null}
                  <div class="p2-bar-track">
                    <div class="p2-bar-fill {condFill}" style="width: {(entry.pct * 100).toFixed(0)}%"></div>
                  </div>
                  <span class="{condColor}" class:val-muted={entry.pct < 0.5}>{formatPct(entry.pct)}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
              <div class="p2-timing-val flip-col" class:today-cell={dow === todayDOW}>
                {#if entry !== null}
                  <span class="val-muted">{entry.n}</span>
                {:else}
                  <span class="val-muted">—</span>
                {/if}
              </div>
            {/each}
          </div>
        {/if}

        {#if stats.weekly_current_state}
          {@const wcs = stats.weekly_current_state}
          <div class="wcs-row">
            <span class="wcs-label">This week</span>
            <span class="wcs-dow">{wcs.current_dow}</span>
            <span class:val-green={wcs.move_pct >= 0} class:val-red={wcs.move_pct < 0}>
              {wcs.move_pct >= 0 ? "+" : ""}{(wcs.move_pct * 100).toFixed(1)}% from wk open
            </span>
            <span class="wcs-bucket">({wcs.move_bucket})</span>
            {#if wcs.low_still_ahead_conditioned !== null && wcs.high_still_ahead_conditioned !== null}
              <span class="wcs-sep">·</span>
              <span class="wcs-conditioned">
                Low: <span class:val-green={wcs.low_still_ahead_conditioned >= 0.5} class:val-muted={wcs.low_still_ahead_conditioned < 0.5}>{formatPct(wcs.low_still_ahead_conditioned)}</span>
                &nbsp;·&nbsp;
                High: <span class:val-red={wcs.high_still_ahead_conditioned >= 0.5} class:val-muted={wcs.high_still_ahead_conditioned < 0.5}>{formatPct(wcs.high_still_ahead_conditioned)}</span>
                <span class="wcs-conditioned-label">conditioned</span>
              </span>
            {/if}
          </div>
        {/if}
        <div class="p2-timing-note muted">
          Typical weekly low: <span class="val-green">{stats.weekly_p1p2.low_day}</span>
          &nbsp;·&nbsp;
          Typical weekly high: <span class="val-red">{stats.weekly_p1p2.high_day}</span>
          <span class="muted"> ({stats.weekly_p1p2.sample_weeks} wks)</span>
        </div>
      </div>

      <!-- Weekly P1 Wick Rank — current week vs historical distribution -->
      {#if stats.weekly_wick_percentile}
        {@const wwp = stats.weekly_wick_percentile}
        <div class="card">
          <div class="card-header">
            <span class="card-title">P1 Wick Rank</span>
            <button class="help-btn" class:active={openHelp === "wickPercentile"} onclick={() => toggleHelp("wickPercentile")} aria-label="Help">?</button>
          </div>
          {#if openHelp === "wickPercentile"}
            <div class="help-panel">
              <div class="help-section"><span class="help-label">What</span>{CARD_HELP.wickPercentile.what}</div>
              <div class="help-section"><span class="help-label">Use</span>{CARD_HELP.wickPercentile.value}</div>
              <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.wickPercentile.example}</div>
            </div>
          {/if}

          {#if wwp.exceedance_pct !== null && wwp.current_wick_of_adr !== null}
            <div class="dist-main-row">
              <div class="dist-exceedance" class:val-green={wwp.exceedance_pct <= 0.25} class:val-amber={wwp.exceedance_pct <= 0.5 && wwp.exceedance_pct > 0.25} class:val-muted={wwp.exceedance_pct > 0.5}>
                {formatPct(1 - wwp.exceedance_pct)}
              </div>
              <div class="dist-exceedance-label muted">of historical P1 wicks were smaller</div>
            </div>
            <div class="wwp-detail-row">
              <span class="wwp-direction" class:val-green={wwp.p1_direction === "low"} class:val-red={wwp.p1_direction === "high"}>
                {wwp.p1_direction === "low" ? "▼ Bullish P1" : "▲ Bearish P1"}
              </span>
              <span class="wwp-wick muted">wick {wwp.current_wick_of_adr.toFixed(3)}× ADR</span>
            </div>
            <div class="dist-bar-wrap" style="margin-top:8px">
              <div class="dist-bar-track">
                <!-- exceedance_pct = P(hist > current), so rank from bottom = 1 - exceedance_pct -->
                <div class="dist-bar-fill" style="width: {((1 - wwp.exceedance_pct) * 100).toFixed(1)}%"></div>
              </div>
            </div>
            <div class="dist-note muted" style="margin-top:6px">{wwp.sample_count} historical P1 weeks</div>
          {:else}
            <div class="wwp-pending muted">P1 not yet set this week — waiting for both extremes</div>
          {/if}
        </div>
      {/if}

    </div>
  {/if}

  <!-- Live Alert Outcomes — REAL fired-alert results (cross-symbol ledger) -->
  <div class="grid lo-grid">
    <div class="card card-wide lo-card">
      <div class="card-header">
        <span class="card-title">Live Alert Outcomes</span>
        <div class="header-actions">
          <button class="help-btn" class:active={openHelp === "liveOutcomes"} onclick={() => toggleHelp("liveOutcomes")} aria-label="Help">?</button>
        </div>
      </div>
      {#if openHelp === "liveOutcomes"}
        <div class="help-panel">
          <div class="help-section"><span class="help-label">What</span>{CARD_HELP.liveOutcomes.what}</div>
          <div class="help-section"><span class="help-label">Use</span>{CARD_HELP.liveOutcomes.value}</div>
          <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.liveOutcomes.example}</div>
        </div>
      {/if}

      <LiveOutcomes />
    </div>
  </div>
</div>

<style>
  .page {
    padding: 20px;
    max-width: 1200px;
    margin: 0 auto;
  }

  .page-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }

  .page-header h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text);
  }

  .controls {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .days-group {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .days-note {
    font-size: 10px;
    color: var(--muted);
  }

  select {
    background: var(--bg-input, var(--bg-panel));
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 10px;
    font-size: 12px;
    border-radius: 4px;
    cursor: pointer;
  }

  .btn-load {
    background: var(--accent);
    color: #000;
    border: none;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 600;
    border-radius: 4px;
    cursor: pointer;
    letter-spacing: 0.05em;
    transition: opacity 120ms;
  }

  .btn-load:disabled { opacity: 0.5; cursor: default; }

  /* Grid layout */
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .card-wide { grid-column: 1 / -1; }

  @media (max-width: 700px) {
    .grid { grid-template-columns: 1fr; }
    .card-wide { grid-column: 1; }
  }

  .card {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
  }

  /* Card header */
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
  }

  .card-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--accent);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  /* Pill toggle */
  .pill-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
  }

  .pill-toggle button {
    background: transparent;
    border: none;
    color: var(--muted);
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 3px 7px;
    cursor: pointer;
    transition: background 120ms, color 120ms;
  }

  .pill-toggle button + button {
    border-left: 1px solid var(--border);
  }

  .pill-toggle button.active {
    background: color-mix(in srgb, var(--accent) 15%, transparent);
    color: var(--accent);
  }

  .pill-toggle button:hover:not(.active) {
    color: var(--text);
  }

  /* Help button */
  .help-btn {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: border-color 120ms, color 120ms, background 120ms;
    flex-shrink: 0;
  }

  .help-btn:hover { border-color: var(--accent); color: var(--accent); }
  .help-btn.active {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: var(--accent);
    color: var(--accent);
  }

  /* Help panel */
  .help-panel {
    background: color-mix(in srgb, var(--accent) 5%, var(--bg, #111));
    border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
    border-radius: 4px;
    padding: 10px 12px;
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
    gap: 7px;
    animation: help-in 150ms ease;
  }

  @keyframes help-in {
    from { opacity: 0; transform: translateY(-4px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .help-section {
    font-size: 11px;
    color: var(--text-dim);
    line-height: 1.5;
    display: grid;
    grid-template-columns: 38px 1fr;
    gap: 6px;
  }

  .help-label {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    padding-top: 1px;
  }

  .help-example {
    color: color-mix(in srgb, var(--accent) 70%, var(--text-dim));
    font-style: italic;
  }

  .card-subtitle {
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 12px;
  }

  .muted { color: var(--muted); }
  .val-accent { color: var(--accent); }
  .val-green { color: var(--green, #4caf81); }
  .val-red { color: var(--red, #e05c5c); }
  .val-amber { color: #d4a843; }
  .val-muted { color: var(--text-dim); }

  /* P1/P2 + Weekly DOW bars */
  .dow-bars { display: flex; flex-direction: column; gap: 5px; }

  .dow-bar-row {
    display: grid;
    grid-template-columns: 32px 1fr 44px;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    border-radius: 3px;
    padding: 2px 4px;
    margin: 0 -4px;
    transition: background 150ms;
  }

  .dow-bar-row.today-row {
    background: color-mix(in srgb, var(--accent) 7%, transparent);
  }

  .dow-label {
    color: var(--text-dim);
    font-size: 11px;
  }

  .dow-bar-row.today-row .dow-label {
    color: var(--accent);
    font-weight: 700;
  }

  .bar-track {
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    height: 6px;
    overflow: hidden;
  }

  .bar-fill {
    background: var(--accent);
    height: 100%;
    border-radius: 2px;
    transition: width 300ms ease;
  }

  .bar-fill.bar-fill-bear {
    background: var(--red, #e05c5c);
  }

  .bar-pct { font-size: 11px; color: var(--text-dim); text-align: right; }

  /* ADR */
  .adr-rows { display: flex; flex-direction: column; gap: 8px; }

  .adr-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 12px;
  }

  .adr-label { color: var(--text-dim); }

  .adr-sublabel {
    font-size: 9px;
    color: var(--muted);
    font-style: normal;
    margin-left: 4px;
  }

  .adr-gauge-track {
    background: var(--bg, #1a1a1a);
    border-radius: 3px;
    height: 8px;
    overflow: hidden;
    margin-top: 4px;
  }

  .adr-gauge-fill {
    background: linear-gradient(90deg, var(--accent) 0%, #e0a030 100%);
    height: 100%;
    border-radius: 3px;
    transition: width 300ms ease;
  }

  .adr-gauge-fill.adr-gauge-warn {
    background: linear-gradient(90deg, #e0a030 0%, var(--red, #e05c5c) 100%);
  }

  .adr-warn {
    font-size: 10px;
    color: var(--red, #e05c5c);
    margin-top: 4px;
  }

  /* Hourly chart */
  .hourly-chart {
    display: flex;
    align-items: flex-end;
    gap: 2px;
    padding: 8px 0 0;
    overflow-x: auto;
  }

  .hour-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    min-width: 20px;
    flex: 1;
    border-radius: 3px;
    padding: 2px 1px;
  }

  .hour-col.hour-now {
    outline: 1px solid color-mix(in srgb, var(--accent) 50%, transparent);
    background: color-mix(in srgb, var(--accent) 7%, transparent);
  }

  .hour-bars {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    gap: 1px;
    height: 62px;
  }

  .hour-bar {
    width: 8px;
    border-radius: 1px 1px 0 0;
    min-height: 2px;
    transition: height 300ms ease;
  }

  .hour-bar.high { background: var(--green, #4caf81); }
  .hour-bar.low  { background: var(--red, #e05c5c); }

  .hour-label {
    font-size: 9px;
    color: var(--muted);
    font-feature-settings: "tnum" 1;
  }

  .hour-col.hour-now .hour-label {
    color: var(--accent);
    font-weight: 700;
  }

  .hourly-legend {
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 11px;
  }

  .legend-high { color: var(--green, #4caf81); }
  .legend-low  { color: var(--red, #e05c5c); }
  .legend-now  { color: var(--accent); }

  /* Tables */
  .stat-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  .stat-table th {
    text-align: left;
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.08em;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding: 4px 6px;
  }

  .stat-table td {
    padding: 5px 6px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent);
    color: var(--text);
  }

  .stat-table tr:last-child td { border-bottom: none; }

  /* DOW today highlight in table */
  tr.today-row td { background: color-mix(in srgb, var(--accent) 6%, transparent); }
  tr.today-row .dow-name { color: var(--accent); font-weight: 700; }

  /* DOW range mini bar */
  .range-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .range-mini {
    width: 36px;
    height: 4px;
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .range-mini-fill {
    height: 100%;
    background: color-mix(in srgb, var(--accent) 60%, transparent);
    border-radius: 2px;
  }

  /* DOW direction split bar */
  .bias-cell {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .bias-split {
    display: flex;
    width: 36px;
    height: 4px;
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .bias-bull { background: var(--green, #4caf81); }
  .bias-bear { background: var(--red, #e05c5c); }

  .bull { color: var(--green, #4caf81); }
  .bear { color: var(--red, #e05c5c); }

  /* Session */
  tr.session-active td { background: color-mix(in srgb, var(--accent) 5%, transparent); }
  tr.session-active .session-name-cell { color: var(--accent); }

  .session-name-cell { color: var(--text-dim); }

  .live-dot {
    color: var(--accent);
    font-size: 8px;
    margin-left: 4px;
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }

  .session-note {
    font-size: 10px;
    margin-top: 8px;
  }

  .session-bar-cell {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
  }

  .session-bar-track {
    width: 50px;
    height: 5px;
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .session-bar-fill {
    height: 100%;
    border-radius: 2px;
  }

  .session-bar-high { background: var(--green, #4caf81); }
  .session-bar-low  { background: var(--red, #e05c5c); }

  .low-sample-warn {
    font-size: 10px;
    color: #e0a030;
    background: color-mix(in srgb, #e0a030 8%, transparent);
    border: 1px solid color-mix(in srgb, #e0a030 25%, transparent);
    border-radius: 3px;
    padding: 5px 8px;
    margin-bottom: 10px;
    line-height: 1.4;
  }

  /* Weekly P2 Timing grid */
  .p2-timing-grid {
    display: grid;
    grid-template-columns: 32px 1fr 52px 52px 1fr;
    gap: 3px 8px;
    font-size: 11px;
    margin-top: 4px;
  }

  .flip-col {
    justify-content: center;
    text-align: center;
  }

  .p2-timing-header {
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.07em;
    color: var(--muted);
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2px;
  }

  .p2-timing-dow {
    color: var(--text-dim);
    display: flex;
    align-items: center;
    padding: 2px 0;
  }

  .p2-timing-dow.today-cell {
    color: var(--accent);
    font-weight: 700;
  }

  .p2-timing-val {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 0;
  }

  .p2-timing-val.today-cell {
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    border-radius: 2px;
    padding: 2px 4px;
    margin: 0 -4px;
  }

  .p2-bar-track {
    width: 36px;
    height: 4px;
    background: var(--bg, #1a1a1a);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .p2-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 300ms ease;
  }

  .p2-bar-bull { background: color-mix(in srgb, var(--green, #4caf81) 70%, transparent); }
  .p2-bar-bear { background: color-mix(in srgb, var(--red, #e05c5c) 70%, transparent); }

  .p2-timing-note {
    font-size: 10px;
    margin-top: 10px;
  }

  .wcs-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
    padding: 6px 8px;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 11px;
  }

  .wcs-label {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent);
    margin-right: 2px;
  }

  .wcs-dow {
    font-weight: 600;
    color: var(--text);
  }

  .wcs-bucket {
    color: var(--muted);
    font-size: 10px;
  }

  .wcs-sep {
    color: var(--muted);
  }

  .wcs-conditioned {
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }

  .wcs-conditioned-label {
    font-size: 9px;
    color: var(--muted);
    margin-left: 4px;
  }

  /* Conditioned P2 timing grid (2-column) */
  .p2-conditioned-note {
    font-size: 10px;
    margin-bottom: 8px;
  }

  .p2-cond-grid {
    display: grid;
    grid-template-columns: 32px 1fr 40px;
    gap: 2px 8px;
    align-items: center;
  }

  /* Daily Path Cone hero (M5) */
  .hero-card {
    margin-bottom: 1rem;
  }

  /* P1 Wick Rank card */
  .dist-main-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 8px 0 4px;
  }

  .dist-exceedance {
    font-size: 28px;
    font-weight: 700;
    font-feature-settings: "tnum" 1;
    letter-spacing: -0.02em;
  }

  .dist-exceedance-label {
    font-size: 11px;
  }

  .dist-bar-wrap {
    margin-bottom: 4px;
  }

  .dist-bar-track {
    position: relative;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: visible;
  }

  .dist-bar-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width 0.4s ease;
  }

  .dist-note {
    font-size: 10px;
    margin-top: 2px;
  }

  /* P1 Wick Rank specifics */
  .wwp-detail-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 4px 0 6px;
    font-size: 11px;
  }

  .wwp-direction {
    font-weight: 600;
  }

  .wwp-wick {
    font-feature-settings: "tnum" 1;
  }

  .wwp-pending {
    font-size: 11px;
    margin-top: 8px;
    font-style: italic;
  }

  .val-accent {
    color: var(--accent);
  }

  .val-amber {
    color: var(--amber, #f59e0b);
  }

  /* Strong H/L table cells */
  .strong-cell {
    font-size: 11px;
    font-feature-settings: "tnum" 1;
    text-align: right;
    color: var(--muted);
  }

  .strong-hi {
    color: var(--accent);
  }

  .strong-lo {
    color: var(--green, #22c55e);
  }

  .strong-lo-dim {
    opacity: 0.5;
  }

  /* P1 strong hint in P1/P2 card subtitle */
  .p1-strong-hint {
    font-size: 10px;
    margin-left: 2px;
  }

  /* ── Live Alert Outcomes ─────────────────────────────────────────── */
  .lo-grid { margin-top: 16px; }

</style>
