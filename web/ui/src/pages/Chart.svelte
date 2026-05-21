<script lang="ts">
  import { getOhlcv, getSignals, getZones, type CandleRow, type SignalRow, type ZonesResponse } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import { selectedSymbol, selectSymbol } from "../stores/watchlist";
  import { configDefaultSymbol } from "../stores/activeConfig";
  import CandleChart from "../components/CandleChart.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  // B6: Short display labels for strategy pills
  const STRATEGY_LABELS: Record<string, string> = {
    bos:                  "BOS",
    fvg:                  "FVG",
    orb:                  "ORB",
    doji:                 "Doji",
    engulfing:            "Engulfing",
    pin_bar:              "Pin Bar",
    inside_bar:           "Inside Bar",
    marubozu:             "Marubozu",
    wick_fill:            "Wick Fill",
    trend_day:            "Trend Day",
    eqh_eql:              "EQH/EQL",
    ote_entry:            "OTE Entry",
    fib_golden_zone:      "Fib Zone",
    order_block:          "Ord Block",
    hammer_hanging_man:   "Hammer/HM",
    morning_evening_star: "M/E Star",
    ema:                  "EMA Pullback",
  };

  // Strategy taxonomy — reviewed against detector logic in indicators_lib.py
  const STRATEGY_GROUPS: Record<string, string[]> = {
    Structure:    ["bos", "eqh_eql", "order_block", "fvg"],
    Fibonacci:    ["fib_golden_zone", "ote_entry"],
    "Price Action": ["wick_fill", "marubozu", "inside_bar", "trend_day"],
    Candlestick:  ["engulfing", "pin_bar", "hammer_hanging_man", "doji", "morning_evening_star"],
    Session:      ["orb", "seasonality"],
    Trend:        ["ema"],
  };

  function stratLabel(name: string): string {
    return STRATEGY_LABELS[name] ?? name;
  }

  let expandedGroups = $state<Record<string, boolean>>({
    Structure: false, Fibonacci: false, "Price Action": false,
    Candlestick: false, Flow: false, Session: false, Trend: false,
  });

  let symbol = $state($selectedSymbol);
  let timeframe = $state("4h");
  let days = $state(90);
  let selectedStrategies = $state<string[]>([]);

  // Indicator toggles (C2)
  let showEMA20 = $state(false);
  let showEMA50 = $state(false);
  let showEMA200 = $state(false);
  let showRSI = $state(false);

  // Range levels toggle (C11)
  let showRangeLevels = $state(false);

  // CME gap toggle
  let showCMEGaps = $state(false);

  // C6: Structural zone toggles
  let showFVG = $state(false);
  let showOB = $state(false);
  let showEQHEQL = $state(false);
  let showBOS = $state(false);
  let showFibZone = $state(false);
  let showOTE = $state(false);
  let showSwings = $state(false);

  let candles = $state<CandleRow[]>([]);
  let signals = $state<SignalRow[]>([]);
  let zones = $state<ZonesResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let loaded = $state(false);

  // Keep localStorage in sync when symbol changes programmatically
  $effect(() => {
    selectSymbol(symbol);
  });

  // Auto-select + auto-load on mount:
  // - If no saved symbol, prefer first config symbol, then first from coins.json
  // - If symbol is set but chart not yet loaded, trigger load
  $effect(() => {
    const syms = $symbols;
    if (syms.length === 0) return;
    if (!symbol) symbol = $configDefaultSymbol ?? syms[0];
    if (symbol && !loaded && !loading) void load();
  });

  const anyZoneActive = () =>
    showFVG || showOB || showEQHEQL || showBOS || showFibZone || showOTE || showSwings;

  async function load(): Promise<void> {
    loading = true;
    error = null;
    const end_ms = Date.now();
    const start_ms = end_ms - days * 24 * 60 * 60 * 1000;
    try {
      const [ohlcvResp, sigResp, zonesResp] = await Promise.all([
        getOhlcv({ symbol, timeframe, start_ms, end_ms }),
        selectedStrategies.length > 0
          ? getSignals({ symbol, timeframe, start_ms, end_ms, strategies: selectedStrategies })
          : Promise.resolve({ signals: [] }),
        anyZoneActive()
          ? getZones({ symbol, timeframe, start_ms, end_ms }).catch(() => null)
          : Promise.resolve(null),
      ]);
      candles = ohlcvResp.candles;
      signals = sigResp.signals;
      zones = zonesResp ?? null;
      loaded = true;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  // Clicking a watchlist symbol immediately loads the chart
  function handleSymbolClick(sym: string): void {
    symbol = sym;
    void load();
  }

  function toggleStrategy(name: string): void {
    selectedStrategies = selectedStrategies.includes(name)
      ? selectedStrategies.filter((s) => s !== name)
      : [...selectedStrategies, name];
    void load();
  }
</script>

<div class="chart-page">
  <!-- B2: Watchlist sidebar -->
  <aside class="watchlist">
    <div class="watchlist-header">Watchlist</div>
    {#each $symbols as sym}
      <button
        class="watchlist-item"
        class:active={symbol === sym}
        onclick={() => handleSymbolClick(sym)}
      >{sym.replace("USDT", "")}</button>
    {/each}
  </aside>

  <!-- Main chart area -->
  <div class="main">
    {#if error}<ErrorBanner {error} />{/if}

    <div class="controls">
      <div class="form-row">
        <label>Timeframe
          <select bind:value={timeframe} onchange={() => { if (timeframe !== "15m" && timeframe !== "1h") showCMEGaps = false; void load(); }}>
            {#each TIMEFRAMES as tf}<option>{tf}</option>{/each}
          </select>
        </label>
        <label>Days
          <input type="number" bind:value={days} min="7" max="365" onchange={() => void load()} />
        </label>
      </div>

      <!-- C2: Indicator toggles — pill buttons matching strategy pill style -->
      <div class="form-row indicators-row">
        <span class="section-label">Indicators</span>
        <button class="pill" class:active={showEMA20} onclick={() => showEMA20 = !showEMA20}>EMA 20</button>
        <button class="pill" class:active={showEMA50} onclick={() => showEMA50 = !showEMA50}>EMA 50</button>
        <button class="pill" class:active={showEMA200} onclick={() => showEMA200 = !showEMA200}>EMA 200</button>
        <button class="pill" class:active={showRSI} onclick={() => showRSI = !showRSI}>RSI 14</button>
        <button class="pill" class:active={showRangeLevels} onclick={() => showRangeLevels = !showRangeLevels}>Range Levels</button>
        {#if timeframe === "15m" || timeframe === "1h"}
          <button class="pill" class:active={showCMEGaps} onclick={() => showCMEGaps = !showCMEGaps}>CME Gap</button>
        {/if}
      </div>

      <!-- C6: Structural zone overlays -->
      <div class="form-row indicators-row">
        <span class="section-label">Zones</span>
        <button class="pill zone-pill" class:active={showFVG} onclick={() => { showFVG = !showFVG; void load(); }}>FVG</button>
        <button class="pill zone-pill" class:active={showOB} onclick={() => { showOB = !showOB; void load(); }}>Ord Block</button>
        <button class="pill zone-pill" class:active={showEQHEQL} onclick={() => { showEQHEQL = !showEQHEQL; void load(); }}>EQH·EQL</button>
        <button class="pill zone-pill" class:active={showBOS} onclick={() => { showBOS = !showBOS; void load(); }}>BOS Levels</button>
        <button class="pill zone-pill" class:active={showFibZone} onclick={() => { showFibZone = !showFibZone; void load(); }}>0.5–0.618</button>
        <button class="pill zone-pill" class:active={showOTE} onclick={() => { showOTE = !showOTE; void load(); }}>OTE</button>
        <button class="pill zone-pill" class:active={showSwings} onclick={() => { showSwings = !showSwings; void load(); }}>Swings</button>
      </div>

      {#if $strategyNames.length > 0}
        <div class="form-row strategies-row">
          <span class="section-label">Strategies</span>
          {#each Object.entries(STRATEGY_GROUPS) as [group, members]}
            {@const available = members.filter((n) => $strategyNames.includes(n))}
            {@const activeCount = available.filter((n) => selectedStrategies.includes(n)).length}
            {#if available.length > 0}
              <button
                class="pill group-toggle"
                class:active={expandedGroups[group]}
                class:has-active={activeCount > 0 && !expandedGroups[group]}
                onclick={() => { expandedGroups[group] = !expandedGroups[group]; }}
              >{group}{activeCount > 0 ? ` · ${activeCount}` : ""} {expandedGroups[group] ? "▾" : "▸"}</button>
            {/if}
          {/each}
        </div>
        {#each Object.entries(STRATEGY_GROUPS) as [group, members]}
          {@const available = members.filter((n) => $strategyNames.includes(n))}
          {#if expandedGroups[group] && available.length > 0}
            <div class="pill-row pill-group-inner">
              <span class="group-inner-label">{group}</span>
              {#each available as name}
                <button
                  class="pill"
                  class:active={selectedStrategies.includes(name)}
                  onclick={() => toggleStrategy(name)}
                >{stratLabel(name)}</button>
              {/each}
            </div>
          {/if}
        {/each}
      {/if}
    </div>

    {#if loading}
      <LoadingSpinner label="Loading chart data..." />
    {:else if loaded}
      <div class="chart-frame">
        <CandleChart
          {candles}
          {signals}
          {symbol}
          {timeframe}
          {showEMA20}
          {showEMA50}
          {showEMA200}
          {showRSI}
          {showRangeLevels}
          {showCMEGaps}
          {zones}
          {showFVG}
          {showOB}
          {showEQHEQL}
          {showBOS}
          {showFibZone}
          {showOTE}
          {showSwings}
        />
      </div>
      <div class="chart-meta">
        <span class="sym-label">{symbol}</span>
        <span class="sep">·</span>
        <span>{candles.length} candles</span>
        <span class="sep">·</span>
        <span class={signals.length > 0 ? "accent" : "muted"}>{signals.length} signals</span>
      </div>
    {:else}
      <p class="hint">// select a symbol from the watchlist</p>
    {/if}
  </div>
</div>

<style>
  .chart-page {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }

  /* ── Watchlist sidebar ── */
  .watchlist {
    width: 108px;
    flex-shrink: 0;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .watchlist-header {
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    padding: 8px 10px 6px;
    border-bottom: 1px solid var(--border-dim);
  }

  .watchlist-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 7px 10px;
    font-size: 11px;
    font-family: inherit;
    letter-spacing: 0.04em;
    color: var(--text);
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border-dim);
    cursor: pointer;
    transition: background 80ms, color 80ms;
  }

  .watchlist-item:last-child { border-bottom: none; }

  .watchlist-item:hover {
    background: rgba(88, 166, 255, 0.06);
    color: var(--accent);
  }

  .watchlist-item.active {
    box-shadow: inset 2px 0 0 var(--accent);
    color: var(--accent);
    font-weight: 600;
    background: rgba(0, 212, 170, 0.04);
  }

  /* ── Main area ── */
  .main { flex: 1; min-width: 0; }

  .controls {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 14px 16px;
    margin-bottom: 14px;
  }

  .controls .form-row { margin-bottom: 10px; }
  .controls .form-row:last-child { margin-bottom: 0; }

  .section-label {
    font-size: 9px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
    align-self: center;
    margin-right: 2px;
  }

  .indicators-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }

  .pill-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .pill {
    font-size: 10px;
    padding: 3px 10px;
    border-radius: 2px;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: none;
    font-weight: 400;
    transition: all 100ms;
  }

  .pill:hover { border-color: var(--accent); color: var(--text); background: transparent; }

  .pill.active {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent);
  }

  .strategies-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .group-toggle {
    opacity: 0.7;
    letter-spacing: 0.04em;
  }

  .group-toggle.active { opacity: 1; }

  .group-toggle.has-active {
    opacity: 1;
    border-color: var(--accent);
    color: var(--accent);
  }

  .pill-group-inner {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: 4px;
    padding: 6px 10px;
    border-left: 2px solid var(--border-dim);
    background: rgba(255,255,255,0.015);
    border-radius: 0 2px 2px 0;
  }

  .group-inner-label {
    font-size: 8px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--muted);
    opacity: 0.6;
    margin-right: 4px;
    align-self: center;
    white-space: nowrap;
  }

  .chart-frame {
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    overflow: hidden;
  }

  .chart-meta {
    padding: 6px 2px;
    font-size: 10px;
    color: var(--muted);
    display: flex;
    gap: 6px;
    letter-spacing: 0.05em;
  }

  .sym-label { color: var(--text); font-weight: 600; }
  .accent { color: var(--accent); }
  .sep { opacity: 0.4; }

  .hint {
    color: var(--muted);
    font-size: 12px;
    letter-spacing: 0.05em;
    padding: 20px 0;
  }
</style>
