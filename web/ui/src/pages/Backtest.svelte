<script lang="ts">
  import { onMount } from "svelte";
  import {
    runBacktest,
    getBacktestRuns,
    getUniversePolicy,
    type BacktestResponse,
    type BacktestRunSummary,
    type UniversePolicyResponse,
  } from "../api";
  import { symbols } from "../stores/config";
  import { strategiesStore, strategyNames } from "../stores/strategies";
  import { activeConfigStore } from "../stores/activeConfig";
  import BacktestResultCmp from "../components/BacktestResult.svelte";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import AnalysisCard from "../components/AnalysisCard.svelte";

  const TIMEFRAMES = ["15m", "1h", "4h", "1d"];

  let activeTab = $state<"runs" | "analysis">("runs");
  let analysisMinTrades = $state(5);
  let analysisUseConfig = $state(false);

  // ── DB runs view ─────────────────────────────────────────────────────────────
  let runs = $state<BacktestRunSummary[]>([]);
  let runsLoading = $state(true);
  let runsError = $state<string | null>(null);
  let universePolicy = $state<UniversePolicyResponse | null>(null);

  // Multi-select filters — empty Set = no filter (all pass)
  let selSymbols = $state(new Set<string>());
  let selTfs = $state(new Set<string>());
  let selStrategies = $state(new Set<string>());
  let selDayFilters = $state(new Set<string>());
  let selAdrFilters = $state(new Set<string>());  // "none" | "0.80" etc.
  let minStarsFilter = $state(0);         // 0 = any
  let minLongStarsFilter = $state(0);    // 0 = any
  let minShortStarsFilter = $state(0);   // 0 = any
  let minTrades = $state(0);              // 0 = no minimum
  let minAvgRText = $state("");           // "" = no minimum
  let minLongWinRateText = $state("");    // "" = no minimum
  let minShortWinRateText = $state("");   // "" = no minimum
  let minLongAvgRText = $state("");       // "" = no minimum
  let minShortAvgRText = $state("");      // "" = no minimum
  let minLongTotalRText = $state("");     // "" = no minimum
  let minShortTotalRText = $state("");    // "" = no minimum
  let minWinRateText = $state("");        // "" = no minimum
  let minTotalRText = $state("");         // "" = no minimum
  let maxDrawdownRText = $state("");      // "" = no maximum
  let minRecoveryFactorText = $state(""); // "" = no minimum

  type SortCol = keyof BacktestRunSummary | "stars" | "long_stars" | "short_stars";
  let sortCol = $state<SortCol>("avg_r");
  let sortDir = $state<"asc" | "desc">("desc");

  async function loadRuns(): Promise<void> {
    runsError = null;
    try {
      runs = await getBacktestRuns();
    } catch (e) {
      runsError = e instanceof Error ? e.message : String(e);
    } finally {
      runsLoading = false;
    }
  }

  onMount(() => {
    void loadRuns();
    // Best-effort: the caveat banner is informational, never blocks the page
    getUniversePolicy()
      .then((p) => { universePolicy = p; })
      .catch(() => {});
  });

  const availableDayFilters = $derived(
    [...new Set(runs.map((r) => r.day_filter))].sort(),
  );

  // "none" for NULL rows, 2dp threshold for non-null rows
  const availableAdrFilters = $derived(
    [...new Set(runs.map((r) => r.adr_suppress_threshold === null ? "none" : r.adr_suppress_threshold.toFixed(2)))].sort(),
  );

  const hasActiveFilters = $derived(
    selSymbols.size > 0 ||
    selTfs.size > 0 ||
    selStrategies.size > 0 ||
    selDayFilters.size > 0 ||
    selAdrFilters.size > 0 ||
    minStarsFilter > 0 ||
    minLongStarsFilter > 0 ||
    minShortStarsFilter > 0 ||
    minTrades > 0 ||
    minAvgRText !== "" ||
    minLongWinRateText !== "" ||
    minShortWinRateText !== "" ||
    minLongAvgRText !== "" ||
    minShortAvgRText !== "" ||
    minLongTotalRText !== "" ||
    minShortTotalRText !== "" ||
    minWinRateText !== "" ||
    minTotalRText !== "" ||
    maxDrawdownRText !== "" ||
    minRecoveryFactorText !== "",
  );

  type FilterToken = { label: string; clear: () => void };
  const activeFilterTokens = $derived.by((): FilterToken[] => {
    const tokens: FilterToken[] = [];
    selSymbols.forEach(s => tokens.push({ label: s.replace("USDT", ""), clear: () => { selSymbols = tog(selSymbols, s); } }));
    selTfs.forEach(tf => tokens.push({ label: tf, clear: () => { selTfs = tog(selTfs, tf); } }));
    selStrategies.forEach(s => tokens.push({ label: s, clear: () => { selStrategies = tog(selStrategies, s); } }));
    selDayFilters.forEach(df => tokens.push({ label: `day:${df}`, clear: () => { selDayFilters = tog(selDayFilters, df); } }));
    selAdrFilters.forEach(af => tokens.push({ label: `adr:${af === "none" ? "off" : af}`, clear: () => { selAdrFilters = tog(selAdrFilters, af); } }));
    if (minStarsFilter > 0) tokens.push({ label: `★≥${"★".repeat(minStarsFilter)}`, clear: () => { minStarsFilter = 0; } });
    if (minLongStarsFilter > 0) tokens.push({ label: `↑★≥${"★".repeat(minLongStarsFilter)}`, clear: () => { minLongStarsFilter = 0; } });
    if (minShortStarsFilter > 0) tokens.push({ label: `↓★≥${"★".repeat(minShortStarsFilter)}`, clear: () => { minShortStarsFilter = 0; } });
    if (minTrades > 0) tokens.push({ label: `trades≥${minTrades}`, clear: () => { minTrades = 0; } });
    if (minWinRateText !== "") tokens.push({ label: `win%≥${minWinRateText}`, clear: () => { minWinRateText = ""; } });
    if (minAvgRText !== "") tokens.push({ label: `avgR≥${minAvgRText}`, clear: () => { minAvgRText = ""; } });
    if (minTotalRText !== "") tokens.push({ label: `totalR≥${minTotalRText}`, clear: () => { minTotalRText = ""; } });
    if (maxDrawdownRText !== "") tokens.push({ label: `maxDD≤${maxDrawdownRText}`, clear: () => { maxDrawdownRText = ""; } });
    if (minRecoveryFactorText !== "") tokens.push({ label: `RF≥${minRecoveryFactorText}`, clear: () => { minRecoveryFactorText = ""; } });
    if (minLongWinRateText !== "") tokens.push({ label: `↑win%≥${minLongWinRateText}`, clear: () => { minLongWinRateText = ""; } });
    if (minShortWinRateText !== "") tokens.push({ label: `↓win%≥${minShortWinRateText}`, clear: () => { minShortWinRateText = ""; } });
    if (minLongAvgRText !== "") tokens.push({ label: `↑avgR≥${minLongAvgRText}`, clear: () => { minLongAvgRText = ""; } });
    if (minShortAvgRText !== "") tokens.push({ label: `↓avgR≥${minShortAvgRText}`, clear: () => { minShortAvgRText = ""; } });
    if (minLongTotalRText !== "") tokens.push({ label: `↑totalR≥${minLongTotalRText}`, clear: () => { minLongTotalRText = ""; } });
    if (minShortTotalRText !== "") tokens.push({ label: `↓totalR≥${minShortTotalRText}`, clear: () => { minShortTotalRText = ""; } });
    return tokens;
  });

  const filteredRuns = $derived.by(() => {
    const col = sortCol;
    const dir = sortDir;
    const mss  = minStarsFilter;
    const mlss = minLongStarsFilter;
    const msss = minShortStarsFilter;
    const mt = minTrades;
    const mar   = minAvgRText        === "" ? -Infinity : (parseFloat(minAvgRText)        || -Infinity);
    const mlwr  = minLongWinRateText  === "" ? -Infinity : (parseFloat(minLongWinRateText)  / 100 || -Infinity);
    const mswr  = minShortWinRateText === "" ? -Infinity : (parseFloat(minShortWinRateText) / 100 || -Infinity);
    const mlar  = minLongAvgRText     === "" ? -Infinity : (parseFloat(minLongAvgRText)      || -Infinity);
    const msar  = minShortAvgRText    === "" ? -Infinity : (parseFloat(minShortAvgRText)     || -Infinity);
    const mltr  = minLongTotalRText   === "" ? -Infinity : (parseFloat(minLongTotalRText)    || -Infinity);
    const mstr  = minShortTotalRText  === "" ? -Infinity : (parseFloat(minShortTotalRText)   || -Infinity);
    const mwr   = minWinRateText      === "" ? -Infinity : (parseFloat(minWinRateText)  / 100 || -Infinity);
    const mtr   = minTotalRText       === "" ? -Infinity : (parseFloat(minTotalRText)        || -Infinity);
    // user types negative (e.g. -10); stored value is positive — negate to compare
    const mxdd  = maxDrawdownRText       === "" ? Infinity  : (-parseFloat(maxDrawdownRText)       || Infinity);
    const mrf   = minRecoveryFactorText  === "" ? -Infinity : (parseFloat(minRecoveryFactorText)   || -Infinity);

    const filtered = runs.filter(
      (r) => {
        const adrKey = r.adr_suppress_threshold === null ? "none" : r.adr_suppress_threshold.toFixed(2);
        return (selSymbols.size === 0 || selSymbols.has(r.symbol)) &&
        (selTfs.size === 0 || selTfs.has(r.timeframe)) &&
        (selStrategies.size === 0 || selStrategies.has(r.strategy)) &&
        (selDayFilters.size === 0 || selDayFilters.has(r.day_filter)) &&
        (selAdrFilters.size === 0 || selAdrFilters.has(adrKey)) &&
        (mss  === 0 || starsFor(r) >= mss) &&
        (mlss === 0 || (r.long_stars !== null && r.long_stars >= mlss)) &&
        (msss === 0 || (r.short_stars !== null && r.short_stars >= msss)) &&
        r.closed_trades >= mt &&
        r.avg_r >= mar &&
        r.win_rate >= mwr &&
        r.total_r >= mtr &&
        r.max_drawdown_r <= mxdd &&
        (mlwr === -Infinity || (r.long_win_rate !== null && r.long_win_rate >= mlwr)) &&
        (mswr === -Infinity || (r.short_win_rate !== null && r.short_win_rate >= mswr)) &&
        (mlar === -Infinity || (r.long_avg_r !== null && r.long_avg_r >= mlar)) &&
        (msar === -Infinity || (r.short_avg_r !== null && r.short_avg_r >= msar)) &&
        (mltr === -Infinity || (r.long_total_r !== null && r.long_total_r >= mltr)) &&
        (mstr === -Infinity || (r.short_total_r !== null && r.short_total_r >= mstr)) &&
        (mrf  === -Infinity || (r.recovery_factor !== null && r.recovery_factor >= mrf));
      },
    );

    filtered.sort((a, b) => {
      const av: number | string =
        col === "stars" ? starsFor(a)
        : col === "long_stars" ? (a.long_stars ?? -1)
        : col === "short_stars" ? (a.short_stars ?? -1)
        : (a[col as keyof BacktestRunSummary] as number | string | null) ?? "";
      const bv: number | string =
        col === "stars" ? starsFor(b)
        : col === "long_stars" ? (b.long_stars ?? -1)
        : col === "short_stars" ? (b.short_stars ?? -1)
        : (b[col as keyof BacktestRunSummary] as number | string | null) ?? "";
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return dir === "desc" ? -cmp : cmp;
    });
    return filtered;
  });

  // Svelte 5: must reassign $state variable — in-place Set mutation is not tracked
  function tog(current: Set<string>, val: string): Set<string> {
    const next = new Set(current);
    if (next.has(val)) next.delete(val);
    else next.add(val);
    return next;
  }

  function resetFilters(): void {
    selSymbols = new Set();
    selTfs = new Set();
    selStrategies = new Set();
    selDayFilters = new Set();
    selAdrFilters = new Set();
    minStarsFilter = 0;
    minLongStarsFilter = 0;
    minShortStarsFilter = 0;
    minTrades = 0;
    minAvgRText = "";
    minLongWinRateText = "";
    minShortWinRateText = "";
    minLongAvgRText = "";
    minShortAvgRText = "";
    minLongTotalRText = "";
    minShortTotalRText = "";
    minWinRateText = "";
    minTotalRText = "";
    maxDrawdownRText = "";
    minRecoveryFactorText = "";
  }

  function setSort(col: SortCol): void {
    if (sortCol === col) {
      sortDir = sortDir === "desc" ? "asc" : "desc";
    } else {
      sortCol = col;
      sortDir = "desc";
    }
  }

  function sortIcon(col: string): string {
    if (sortCol !== col) return "⇅";
    return sortDir === "desc" ? "↓" : "↑";
  }

  function starsFor(run: BacktestRunSummary): number {
    // Prefer per-config calibrated stars joined from confidence_ratings at query time
    if (run.stars !== null && run.stars !== undefined) return run.stars;
    // Fallback: resolve from global registry for rows without calibrated stars yet
    const conf = $strategiesStore[run.strategy]?.confidence;
    if (conf === undefined || conf === null) return 0;
    if (typeof conf === "number") return conf;
    if (run.timeframe in conf) return conf[run.timeframe];
    return conf["default"] ?? 3;
  }

  function renderStars(n: number): string {
    return "★".repeat(n) + "☆".repeat(5 - n);
  }

  function fmtDate(ms: number): string {
    return new Intl.DateTimeFormat("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(ms));
  }

  function fmtR(r: number): string {
    return (r >= 0 ? "+" : "") + r.toFixed(2) + "R";
  }

  function fmtWinPct(w: number): string {
    return (w * 100).toFixed(1) + "%";
  }

  function fmtDirWinPct(w: number | null): string {
    return w === null ? "—" : (w * 100).toFixed(1) + "%";
  }

  function fmtDirR(r: number | null): string {
    return r === null ? "—" : (r >= 0 ? "+" : "") + r.toFixed(2) + "R";
  }

  // ── Run form ──────────────────────────────────────────────────────────────────
  let showForm = $state(false);
  let symbol = $state("BTCUSDT");
  let timeframe = $state("4h");
  let strategy = $state("bos");
  let days = $state(200);
  let sl_pct = $state(2.0);
  let tp_r = $state(2.0);
  let fee_pct = $state(0.05);
  let extraParams = $state<Record<string, number>>({});
  let result = $state<BacktestResponse | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    const spec = $strategiesStore[strategy];
    if (!spec) return;
    extraParams = Object.fromEntries(spec.params.map((p) => [p.name, p.default]));
  });

  const existingRun = $derived(
    runs.find(
      (r) => r.symbol === symbol && r.timeframe === timeframe && r.strategy === strategy,
    ) ?? null,
  );

  async function submit(): Promise<void> {
    loading = true;
    error = null;
    result = null;
    try {
      result = await runBacktest({
        symbol,
        timeframe,
        strategy,
        days,
        sl_pct: sl_pct / 100,
        tp_r,
        fee_pct: fee_pct / 100,
        ...extraParams,
      });
      runsLoading = true;
      await loadRuns();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  const currentSpec = $derived($strategiesStore[strategy]);

  // Whether an active config is loaded (controls chip visibility)
  const hasActiveConfig = $derived($activeConfigStore?.config_name != null);

  function applyConfigDefaults(): void {
    const cfg = $activeConfigStore;
    if (!cfg) return;

    // Table filters: scope to config symbols, TFs, strategies
    if (cfg.symbols && cfg.symbols.length > 0) {
      selSymbols = new Set(cfg.symbols);
    }
    if (cfg.timeframes.length > 0) {
      selTfs = new Set(cfg.timeframes);
    }
    if (cfg.strategies && cfg.strategies.length > 0) {
      selStrategies = new Set(cfg.strategies);
    }

    // Day filter — include "off" so all.toml scopes to runs with no day filter
    if (cfg.day_filter) {
      selDayFilters = new Set([cfg.day_filter]);
    } else {
      selDayFilters = new Set();
    }

    // ADR gate
    if (cfg.adr_suppress_threshold !== null) {
      selAdrFilters = new Set([cfg.adr_suppress_threshold.toFixed(2)]);
    } else {
      selAdrFilters = new Set(["none"]);
    }

    // Run form defaults from config
    fee_pct = cfg.fee_pct * 100;
    tp_r = cfg.tp_r;
    sl_pct = cfg.sl_pct * 100;
  }
</script>

<div class="page">
  <div class="page-header">
    <h2>Backtest</h2>
    {#if hasActiveConfig}
      <button class="config-btn" onclick={applyConfigDefaults}
        title="Pre-fill filters and run form from the active TOML config">
        ◈ {$activeConfigStore?.config_name}
      </button>
    {/if}
    <button class="toggle-btn" class:active={showForm} onclick={() => { showForm = !showForm; }}>
      {showForm ? "✕ Close" : "▶ Run Backtest"}
    </button>
  </div>

  {#if universePolicy}
    <p class="universe-caveat">
      Universe: {universePolicy.scope} (as_of={universePolicy.as_of}{universePolicy.n_symbols !== null ? `, ${universePolicy.n_symbols} symbols` : ""})
      — ⚠ {universePolicy.survivorship_note}
    </p>
  {/if}

  <!-- ── Sub-tab bar ──────────────────────────────────────────────────────── -->
  <div class="sub-tabs">
    <button class="sub-tab" class:active={activeTab === "runs"} onclick={() => { activeTab = "runs"; }}>Runs</button>
    <button class="sub-tab" class:active={activeTab === "analysis"} onclick={() => { activeTab = "analysis"; }}>Analysis</button>
  </div>

  {#if activeTab === "runs"}

  <!-- ── Filters ──────────────────────────────────────────────────────────── -->
  <div class="filters">

    <!-- Section 1: Categorical -->
    <div class="filter-row">
      <span class="section-tag">CATEGORY</span>

      <span class="flabel">Symbol</span>
      <div class="chips">
        {#each $symbols as s}
          <button class="chip" class:on={selSymbols.has(s)}
            onclick={() => { selSymbols = tog(selSymbols, s); }}>{s.replace("USDT", "")}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">TF</span>
      <div class="chips">
        {#each TIMEFRAMES as tf}
          <button class="chip" class:on={selTfs.has(tf)}
            onclick={() => { selTfs = tog(selTfs, tf); }}>{tf}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">Strategy</span>
      <details class="strat-picker">
        <summary class="strat-summary">
          {selStrategies.size === 0 ? "all" : `${selStrategies.size} selected`}
          <span class="summary-arrow">▾</span>
        </summary>
        <div class="strat-grid">
          {#each $strategyNames as s}
            <button class="chip" class:on={selStrategies.has(s)}
              onclick={() => { selStrategies = tog(selStrategies, s); }}>{s}</button>
          {/each}
        </div>
      </details>

      <span class="fsep"></span>

      <span class="flabel">Day Filter</span>
      <div class="chips">
        {#each availableDayFilters as df}
          <button class="chip" class:on={selDayFilters.has(df)}
            onclick={() => { selDayFilters = tog(selDayFilters, df); }}>{df}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">ADR Gate</span>
      <div class="chips">
        {#each availableAdrFilters as af}
          <button class="chip" class:on={selAdrFilters.has(af)}
            onclick={() => { selAdrFilters = tog(selAdrFilters, af); }}>{af === "none" ? "off" : af}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel">★ ≥</span>
      <div class="chips">
        <button class="chip" class:on={minStarsFilter === 0}
          onclick={() => { minStarsFilter = 0; }}>any</button>
        {#each [1, 2, 3, 4, 5] as n}
          <button class="chip star-chip" class:on={minStarsFilter === n}
            onclick={() => { minStarsFilter = n; }}>{"★".repeat(n)}</button>
        {/each}
      </div>

      <span class="fsep"></span>

      <span class="flabel long-label">↑★ ≥</span>
      <div class="chips">
        <button class="chip" class:on={minLongStarsFilter === 0}
          onclick={() => { minLongStarsFilter = 0; }}>any</button>
        {#each [1, 2, 3, 4, 5] as n}
          <button class="chip star-chip" class:on={minLongStarsFilter === n}
            onclick={() => { minLongStarsFilter = n; }}>{"★".repeat(n)}</button>
        {/each}
      </div>

      <span class="flabel short-label">↓★ ≥</span>
      <div class="chips">
        <button class="chip" class:on={minShortStarsFilter === 0}
          onclick={() => { minShortStarsFilter = 0; }}>any</button>
        {#each [1, 2, 3, 4, 5] as n}
          <button class="chip star-chip" class:on={minShortStarsFilter === n}
            onclick={() => { minShortStarsFilter = n; }}>{"★".repeat(n)}</button>
        {/each}
      </div>
    </div>

    <!-- Section 2: Combined performance -->
    <div class="filter-row">
      <span class="section-tag">PERF</span>

      <span class="flabel">Win% ≥</span>
      <input type="number" class="filter-num" bind:value={minWinRateText}
        min="0" max="100" step="1" placeholder="any"
        title="Minimum win rate % (empty = no minimum)" />

      <span class="fsep"></span>

      <span class="flabel">Trades ≥</span>
      <input type="number" class="filter-num" bind:value={minTrades}
        min="0" step="1"
        title="Minimum closed trades (0 = no minimum)" />

      <span class="fsep"></span>

      <span class="flabel">Avg R ≥</span>
      <input type="number" class="filter-num" bind:value={minAvgRText}
        step="0.01" placeholder="any"
        title="Minimum avg R (empty = no minimum)" />

      <span class="fsep"></span>

      <span class="flabel">Total R ≥</span>
      <input type="number" class="filter-num" bind:value={minTotalRText}
        step="0.1" placeholder="any"
        title="Minimum total R (empty = no minimum)" />

      <span class="fsep"></span>

      <span class="flabel">Max DD ≤</span>
      <input type="number" class="filter-num" bind:value={maxDrawdownRText}
        max="0" step="0.5" placeholder="any"
        title="Maximum drawdown e.g. -10 = no worse than -10R (empty = no limit)" />

      <span class="fsep"></span>

      <span class="flabel">RF ≥</span>
      <input type="number" class="filter-num" bind:value={minRecoveryFactorText}
        step="0.5" placeholder="any"
        title="Minimum recovery factor (total R / max DD). ≥3 good, 2–3 acceptable" />
    </div>

    <!-- Section 3: Directional (long / short splits) -->
    <div class="filter-row">
      <span class="section-tag">DIR</span>

      <span class="flabel long-label">↑ Long% ≥</span>
      <input type="number" class="filter-num" bind:value={minLongWinRateText}
        min="0" max="100" step="1" placeholder="any"
        title="Minimum long win rate % (empty = no minimum)" />

      <span class="flabel short-label">↓ Short% ≥</span>
      <input type="number" class="filter-num" bind:value={minShortWinRateText}
        min="0" max="100" step="1" placeholder="any"
        title="Minimum short win rate % (empty = no minimum)" />

      <span class="fsep"></span>

      <span class="flabel long-label">↑ L Avg R ≥</span>
      <input type="number" class="filter-num" bind:value={minLongAvgRText}
        step="0.01" placeholder="any"
        title="Minimum long avg R (empty = no minimum)" />

      <span class="flabel short-label">↓ S Avg R ≥</span>
      <input type="number" class="filter-num" bind:value={minShortAvgRText}
        step="0.01" placeholder="any"
        title="Minimum short avg R (empty = no minimum)" />


      <span class="fsep"></span>

      <span class="flabel long-label">↑ L Total R ≥</span>
      <input type="number" class="filter-num" bind:value={minLongTotalRText}
        step="0.1" placeholder="any"
        title="Minimum long total R (empty = no minimum)" />

      <span class="flabel short-label">↓ S Total R ≥</span>
      <input type="number" class="filter-num" bind:value={minShortTotalRText}
        step="0.1" placeholder="any"
        title="Minimum short total R (empty = no minimum)" />


    </div>

    <!-- Active filter tokens bar -->
    <div class="filter-tokens">
      {#if activeFilterTokens.length > 0}
        {#each activeFilterTokens as tok (tok.label)}
          <button class="filter-token" onclick={tok.clear}>
            {tok.label} <span class="tok-x">✕</span>
          </button>
        {/each}
        <button class="reset-btn" onclick={resetFilters}>clear all</button>
      {/if}
      <span class="count">
        {#if runsLoading}loading…{:else}{filteredRuns.length} / {runs.length}{/if}
      </span>
    </div>

  </div>

  {#if runsError}
    <ErrorBanner error={runsError} />
  {/if}

  <!-- ── Results Table ─────────────────────────────────────────────────── -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="sortable" onclick={() => setSort("symbol")}>Symbol <span class="si">{sortIcon("symbol")}</span></th>
          <th class="sortable" onclick={() => setSort("timeframe")}>TF <span class="si">{sortIcon("timeframe")}</span></th>
          <th class="sortable" onclick={() => setSort("strategy")}>Strategy <span class="si">{sortIcon("strategy")}</span></th>
          <th class="sortable" onclick={() => setSort("stars")}>★ <span class="si">{sortIcon("stars")}</span></th>
          <th class="sortable long-col" onclick={() => setSort("long_stars")}>↑★ <span class="si">{sortIcon("long_stars")}</span></th>
          <th class="sortable short-col" onclick={() => setSort("short_stars")}>↓★ <span class="si">{sortIcon("short_stars")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("win_rate")}>Win% <span class="si">{sortIcon("win_rate")}</span></th>
          <th class="sortable num-col long-col" onclick={() => setSort("long_win_rate")}>↑ Long% <span class="si">{sortIcon("long_win_rate")}</span></th>
          <th class="sortable num-col long-col" onclick={() => setSort("long_avg_r")}>↑ L Avg R <span class="si">{sortIcon("long_avg_r")}</span></th>
          <th class="sortable num-col long-col" onclick={() => setSort("long_total_r")}>↑ L Total R <span class="si">{sortIcon("long_total_r")}</span></th>
          <th class="sortable num-col short-col" onclick={() => setSort("short_win_rate")}>↓ Short% <span class="si">{sortIcon("short_win_rate")}</span></th>
          <th class="sortable num-col short-col" onclick={() => setSort("short_avg_r")}>↓ S Avg R <span class="si">{sortIcon("short_avg_r")}</span></th>
          <th class="sortable num-col short-col" onclick={() => setSort("short_total_r")}>↓ S Total R <span class="si">{sortIcon("short_total_r")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("closed_trades")}>Trades <span class="si">{sortIcon("closed_trades")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("avg_r")}>Avg R <span class="si">{sortIcon("avg_r")}</span></th>
          <th class="sortable num-col" onclick={() => setSort("total_r")}>Total R <span class="si">{sortIcon("total_r")}</span></th>
          <th class="sortable num-col dd-col" onclick={() => setSort("max_drawdown_r")}>Max DD <span class="si">{sortIcon("max_drawdown_r")}</span></th>
          <th class="sortable num-col rf-col" onclick={() => setSort("recovery_factor")}>RF <span class="si">{sortIcon("recovery_factor")}</span></th>
          <th class="sortable" onclick={() => setSort("day_filter")}>Day Filter <span class="si">{sortIcon("day_filter")}</span></th>
          <th class="sortable" onclick={() => setSort("adr_suppress_threshold")}>ADR Gate <span class="si">{sortIcon("adr_suppress_threshold")}</span></th>
          <th class="sortable" onclick={() => setSort("run_at_ms")}>Date <span class="si">{sortIcon("run_at_ms")}</span></th>
        </tr>
      </thead>
      <tbody>
        {#if runsLoading}
          <tr><td colspan="23" class="msg-cell">Loading…</td></tr>
        {:else if filteredRuns.length === 0}
          <tr>
            <td colspan="23" class="msg-cell">
              {hasActiveFilters ? "No results match current filters." : "No runs saved — run a backtest first."}
            </td>
          </tr>
        {:else}
          {#each filteredRuns as run (run.run_id)}
            <tr>
              <td class="sym">{run.symbol.replace("USDT", "")}</td>
              <td class="muted">{run.timeframe}</td>
              <td class="strat-name">{run.strategy}</td>
              <td class="stars">{renderStars(starsFor(run))}</td>
              <td class="stars dir-long" class:dir-nil={run.long_stars === null}>{run.long_stars !== null ? renderStars(run.long_stars) : "—"}</td>
              <td class="stars dir-short" class:dir-nil={run.short_stars === null}>{run.short_stars !== null ? renderStars(run.short_stars) : "—"}</td>
              <td class="num">{fmtWinPct(run.win_rate)}</td>
              <td class="num dir-long" class:dir-pos={run.long_win_rate !== null && run.long_win_rate > 0.5} class:dir-nil={run.long_win_rate === null}>{fmtDirWinPct(run.long_win_rate)}</td>
              <td class="num dir-long" class:pos={run.long_avg_r !== null && run.long_avg_r > 0} class:neg={run.long_avg_r !== null && run.long_avg_r < 0} class:dir-nil={run.long_avg_r === null}>{fmtDirR(run.long_avg_r)}</td>
              <td class="num dir-long" class:pos={run.long_total_r !== null && run.long_total_r > 0} class:neg={run.long_total_r !== null && run.long_total_r < 0} class:dir-nil={run.long_total_r === null}>{fmtDirR(run.long_total_r)}</td>
              <td class="num dir-short" class:dir-pos={run.short_win_rate !== null && run.short_win_rate > 0.5} class:dir-nil={run.short_win_rate === null}>{fmtDirWinPct(run.short_win_rate)}</td>
              <td class="num dir-short" class:pos={run.short_avg_r !== null && run.short_avg_r > 0} class:neg={run.short_avg_r !== null && run.short_avg_r < 0} class:dir-nil={run.short_avg_r === null}>{fmtDirR(run.short_avg_r)}</td>
              <td class="num dir-short" class:pos={run.short_total_r !== null && run.short_total_r > 0} class:neg={run.short_total_r !== null && run.short_total_r < 0} class:dir-nil={run.short_total_r === null}>{fmtDirR(run.short_total_r)}</td>
              <td class="num muted">{run.closed_trades}</td>
              <td class="num" class:pos={run.avg_r > 0} class:neg={run.avg_r < 0}>{fmtR(run.avg_r)}</td>
              <td class="num" class:pos={run.total_r > 0} class:neg={run.total_r < 0}>{fmtR(run.total_r)}</td>
              <td class="num dd-val" class:dd-bad={run.max_drawdown_r > 10}>-{run.max_drawdown_r.toFixed(1)}R</td>
              <td class="num rf-val"
                class:rf-good={run.recovery_factor !== null && run.recovery_factor >= 3}
                class:rf-ok={run.recovery_factor !== null && run.recovery_factor >= 2 && run.recovery_factor < 3}
                class:rf-bad={run.recovery_factor !== null && run.recovery_factor < 2}
              >{run.recovery_factor !== null ? run.recovery_factor.toFixed(2) : "—"}</td>
              <td class="muted small">{run.day_filter}</td>
              <td class="muted small">{run.adr_suppress_threshold === null ? "—" : run.adr_suppress_threshold.toFixed(2)}</td>
              <td class="muted small nowrap">{fmtDate(run.run_at_ms)}</td>
            </tr>
          {/each}
        {/if}
      </tbody>
    </table>
  </div>

  <!-- ── Run Form ──────────────────────────────────────────────────────── -->
  {#if showForm}
    <div class="run-section">
      <div class="run-header">Run New Backtest</div>

      {#if error}<ErrorBanner {error} />{/if}

      <div class="form-section">
        <div class="form-row">
          <label>Symbol
            <select bind:value={symbol}>
              {#each $symbols as s}<option>{s}</option>{/each}
            </select>
          </label>
          <label>Timeframe
            <select bind:value={timeframe}>
              {#each TIMEFRAMES as tf}<option>{tf}</option>{/each}
            </select>
          </label>
          <label>Strategy
            <select bind:value={strategy}>
              {#each $strategyNames as s}<option>{s}</option>{/each}
            </select>
          </label>
        </div>

        <div class="form-row">
          <label>Days <input type="number" bind:value={days} min="7" max="365" /></label>
          <label title="Stop-loss distance from entry, e.g. 2 = 2%">SL % <input type="number" bind:value={sl_pct} min="0.5" max="10" step="0.5" /></label>
          <label title="Take-profit as a multiple of the SL distance">TP (R) <input type="number" bind:value={tp_r} min="0.5" max="10" step="0.5" /></label>
          <label title="Taker fee per side, e.g. 0.05 = 0.05%">Fee % <input type="number" bind:value={fee_pct} min="0" max="0.5" step="0.01" /></label>
        </div>

        {#if currentSpec && currentSpec.params.length > 0}
          <div class="form-row">
            {#each currentSpec.params as p}
              <label>{p.name}
                <input
                  type="number"
                  bind:value={extraParams[p.name]}
                  min={p.min_val}
                  max={p.max_val}
                  step={p.param_type === "int" ? 1 : 0.01}
                  title={p.description}
                />
              </label>
            {/each}
          </div>
        {/if}

        <div class="form-row">
          <button disabled={loading} onclick={() => void submit()}>
            {loading ? "Running…" : existingRun ? "↺ Re-run & Update" : "▶ Run Backtest"}
          </button>
          {#if existingRun}
            <span class="update-hint">Will update existing run from {fmtDate(existingRun.run_at_ms)}</span>
          {/if}
          {#if currentSpec}
            <span class="strat-desc">{currentSpec.description}</span>
          {/if}
        </div>
      </div>

      {#if result}
        <BacktestResultCmp {result} />
      {/if}
    </div>
  {/if}

  {:else}

  <!-- ── Analysis Tab ──────────────────────────────────────────────────── -->
  <div class="analysis-controls">
    <label class="min-trades-label">
      Min trades
      <input type="number" class="min-trades-input" bind:value={analysisMinTrades} min="1" max="100"
        disabled={analysisUseConfig} />
    </label>
    {#if hasActiveConfig}
      <button
        class="scope-btn"
        class:active={analysisUseConfig}
        onclick={() => { analysisUseConfig = !analysisUseConfig; }}
        title="Scope to active config: applies day_filter, fee_pct, symbols, and per-TF min_trades from {$activeConfigStore?.config_name}"
      >
        ◈ {analysisUseConfig ? $activeConfigStore?.config_name : "Scope to config"}
      </button>
    {/if}
  </div>
  {#if analysisUseConfig && $activeConfigStore}
    {@const cfg = $activeConfigStore}
    {@const perTf = Object.entries(cfg.min_trades_per_tf ?? {}).map(([tf, n]) => `${tf}: ${n}`).join(", ")}
    <div class="scope-summary">
      <span class="scope-pill">Min trades: {cfg.min_trades}{perTf ? ` · ${perTf}` : ""}</span>
      {#if cfg.day_filter}
        <span class="scope-pill">Day filter: {cfg.day_filter}</span>
      {/if}
      {#if cfg.fee_pct > 0}
        <span class="scope-pill">Fee: {(cfg.fee_pct * 100).toFixed(3)}%</span>
      {/if}
      {#if cfg.symbols?.length}
        <span class="scope-pill">Symbols: {cfg.symbols.join(", ")}</span>
      {/if}
    </div>
  {/if}
  <div class="analysis-grid">
    <AnalysisCard query="strategy" title="Strategy Leaderboard"
      description="Trade-weighted avg R per strategy across all symbols & TFs"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="consistency" title="Edge Breadth"
      description="% of symbol×TF combos with positive avg R — broad vs niche strategies"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="direction_bias" title="Direction Bias"
      description="Long vs short avg R per strategy — reveals directional asymmetry"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="recovery_factor" title="Recovery Factor Ranking"
      description="Avg recovery factor (total R ÷ max drawdown) — risk-adjusted view"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="adr_ab" title="ADR Gate A/B"
      description="Δavg R with vs without the ADR bias gate per strategy × TF"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="volume_ab" title="Volume Suppress A/B"
      description="Δavg R with vs without low-volume candle suppression"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="day_filter_ab" title="Day Filter A/B"
      description="Δavg R with day filter ON vs OFF — shows weekday filtering impact"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="symbol" title="Symbol Leaderboard"
      description="Total R and avg R per symbol — which market has the most edge"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="tf" title="Timeframe Ranking"
      description="Trade-weighted avg R per timeframe across all strategies"
      minTrades={analysisMinTrades} useConfig={analysisUseConfig} />
    <AnalysisCard query="combos" title="Best Combos"
      description="Top symbol × strategy × TF combinations ranked by avg R"
      minTrades={analysisMinTrades} topN={20} useConfig={analysisUseConfig} />
  </div>

  {/if}

</div>

<style>
  /* ── Sub-tabs ────────────────────────────────────────────────────────── */
  .sub-tabs {
    display: flex;
    gap: 2px;
    margin: 8px 0 12px;
    border-bottom: 1px solid var(--border-dim);
  }

  .sub-tab {
    padding: 5px 16px;
    font-size: 0.78rem;
    background: transparent;
    color: var(--fg-dim);
    border: none;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: -1px;
  }

  .sub-tab:hover { color: var(--fg); }

  .sub-tab.active {
    color: var(--fg);
    border-bottom-color: var(--accent, #6060cc);
  }

  /* ── Analysis grid ───────────────────────────────────────────────────── */
  .analysis-controls {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 12px;
    font-size: 0.78rem;
    color: var(--fg-dim);
  }

  .min-trades-label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }

  .min-trades-input {
    width: 60px;
    padding: 2px 6px;
    background: var(--bg-input, #1a1a2a);
    border: 1px solid var(--border, #2a2a2a);
    border-radius: 3px;
    color: var(--fg);
    font-size: 0.78rem;
  }

  .min-trades-input:disabled {
    opacity: 0.4;
  }

  .scope-btn {
    padding: 3px 10px;
    font-size: 0.75rem;
    background: transparent;
    color: var(--fg-dim);
    border: 1px solid var(--border, #2a2a2a);
    border-radius: 4px;
    cursor: pointer;
  }

  .scope-btn:hover { color: var(--fg); border-color: var(--accent, #6060cc); }

  .scope-btn.active {
    background: var(--accent-dim, #2a2a4a);
    color: var(--fg);
    border-color: var(--accent, #6060cc);
  }

  .scope-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.4rem 0 0.6rem;
  }

  .scope-pill {
    font-size: 0.7rem;
    color: var(--fg-dim, #888);
    background: var(--bg-card, #1a1a1a);
    border: 1px solid var(--border, #2a2a2a);
    border-radius: 3px;
    padding: 0.15rem 0.45rem;
    white-space: nowrap;
  }

  .analysis-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
    padding-bottom: 2rem;
  }

  @media (max-width: 900px) {
    .analysis-grid { grid-template-columns: 1fr; }
  }

  /* ── Page header ─────────────────────────────────────────────────────── */
  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-dim);
    margin-bottom: 14px;
  }

  .page-header h2 {
    border-bottom: none;
    margin-bottom: 0;
  }

  .toggle-btn {
    font-size: 10px;
    padding: 4px 12px;
  }

  .universe-caveat {
    font-size: 10px;
    color: var(--text-dim);
    margin: -6px 0 12px;
    line-height: 1.5;
  }

  .toggle-btn.active {
    background: var(--accent-dim);
    color: var(--accent-bright);
  }

  .config-btn {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 4px 10px;
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--accent);
    border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
    border-radius: 3px;
    cursor: pointer;
    transition: background 120ms;
    margin-right: 6px;
  }

  .config-btn:hover {
    background: color-mix(in srgb, var(--accent) 18%, transparent);
  }

  /* ── Filters ─────────────────────────────────────────────────────────── */
  .section-tag {
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--muted);
    opacity: 0.45;
    padding: 2px 5px;
    border: 1px solid var(--border);
    border-radius: 3px;
    flex-shrink: 0;
    align-self: center;
    min-width: 46px;
    text-align: center;
  }

  .filters {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 12px;
  }

  .filter-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  .flabel {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    white-space: nowrap;
    flex-shrink: 0;
  }

  .fsep {
    width: 1px;
    height: 14px;
    background: var(--border);
    flex-shrink: 0;
    margin: 0 2px;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.04em;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    transition: background 80ms, color 80ms, border-color 80ms;
  }

  .chip:hover:not(.on) {
    border-color: var(--border-dim);
    color: var(--text);
    background: var(--bg-panel);
  }

  .chip.on {
    background: var(--accent-dim);
    border-color: var(--accent);
    color: var(--accent-bright);
  }

  .star-chip.on {
    color: var(--yellow);
    border-color: var(--yellow);
    background: rgba(240, 192, 64, 0.1);
  }

  /* ── Strategy picker ─────────────────────────────────────────────────── */
  .strat-picker {
    position: relative;
  }

  .strat-summary {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.04em;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 2px;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 4px;
    transition: background 80ms, border-color 80ms;
    user-select: none;
  }

  .strat-summary::-webkit-details-marker { display: none; }

  .strat-picker[open] .strat-summary {
    border-color: var(--accent);
    color: var(--accent-bright);
    background: var(--accent-dim);
  }

  .summary-arrow {
    font-size: 8px;
    color: var(--muted);
  }

  .strat-grid {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 10;
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px;
    display: grid;
    grid-template-columns: repeat(4, auto);
    gap: 4px;
    min-width: 360px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }

  /* ── Numeric filters ─────────────────────────────────────────────────── */
  .filter-num {
    width: 70px;
    font-size: 11px;
    padding: 3px 6px;
  }

  /* ── Active filter token bar ─────────────────────────────────────────── */
  .filter-tokens {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
    min-height: 22px;
    padding: 2px 0;
  }

  .filter-token {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 2px 7px;
    border: 1px solid var(--accent);
    color: var(--accent);
    border-radius: 3px;
    background: transparent;
    cursor: pointer;
    transition: background 0.12s;
  }

  .filter-token:hover {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
  }

  .tok-x {
    font-size: 8px;
    opacity: 0.7;
  }

  .reset-btn {
    font-size: 9px;
    padding: 2px 8px;
    color: var(--red);
    border-color: var(--red);
  }

  .reset-btn:hover:not(:disabled) {
    background: var(--red-dim);
    color: var(--red);
  }

  .count {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
    white-space: nowrap;
  }

  /* ── Table ───────────────────────────────────────────────────────────── */
  .table-wrap {
    overflow-x: auto;
    margin-bottom: 4px;
  }

  th.sortable {
    cursor: pointer;
    user-select: none;
  }

  th.sortable:hover { color: var(--text-dim); }

  th.num-col { text-align: right; }

  .si {
    font-size: 9px;
    color: var(--muted);
    margin-left: 2px;
  }

  .sym { font-weight: 600; }

  .strat-name {
    color: var(--text-dim);
    font-size: 11.5px;
  }

  .stars {
    color: var(--yellow);
    font-size: 10px;
    letter-spacing: -1px;
    white-space: nowrap;
  }

  td.num {
    text-align: right;
    font-feature-settings: "tnum" 1;
  }

  td.pos { color: var(--green); }
  td.neg { color: var(--red); }

  td.small { font-size: 11px; }
  td.nowrap { white-space: nowrap; }

  .msg-cell {
    text-align: center;
    color: var(--muted);
    padding: 24px;
    font-size: 11px;
  }

  /* ── Run section ─────────────────────────────────────────────────────── */
  .run-section {
    margin-top: 20px;
    border-top: 1px solid var(--border-dim);
    padding-top: 16px;
  }

  .run-header {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
  }

  .run-header::before {
    content: "//  ";
    color: var(--accent);
  }

  .form-section {
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 16px;
    margin-bottom: 4px;
  }

  .update-hint {
    font-size: 10px;
    color: var(--yellow);
    padding-left: 4px;
  }

  .strat-desc {
    font-size: 11px;
    color: var(--muted);
    padding-left: 4px;
    font-style: italic;
  }

  /* ── Direction split columns ──────────────────────────────────────────── */
  .long-label { color: var(--green); }
  .short-label { color: var(--red); }

  th.long-col  { color: var(--green); opacity: 0.75; }
  th.short-col { color: var(--red);   opacity: 0.75; }

  td.dir-long,
  td.dir-short {
    color: var(--muted);
    font-feature-settings: "tnum" 1;
  }

  td.dir-long.dir-pos  { color: var(--green); }
  td.dir-short.dir-pos { color: var(--green); }
  td.dir-nil           { color: var(--border); letter-spacing: 0.05em; }

  /* ── Max DD column ─────────────────────────────────────────────────────── */
  th.dd-col  { color: var(--red); opacity: 0.65; }
  td.dd-val  { color: var(--muted); font-feature-settings: "tnum" 1; }
  td.dd-bad  { color: var(--red); }

  /* ── Recovery Factor column ─────────────────────────────────────────────── */
  th.rf-col   { opacity: 0.75; }
  td.rf-val   { font-feature-settings: "tnum" 1; }
  td.rf-good  { color: var(--green); }
  td.rf-ok    { color: var(--yellow, #f0b429); }
  td.rf-bad   { color: var(--red); }
</style>
