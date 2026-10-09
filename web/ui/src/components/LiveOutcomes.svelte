<script lang="ts">
  import { onMount } from "svelte";
  import {
    getLiveOutcomes,
    getLiveOutcomesOpen,
    type LiveOutcomesResponse,
    type LiveOpenPositionsResponse,
  } from "../api";

  let liveOutcomes = $state<LiveOutcomesResponse | null>(null);
  let loLoading = $state(false);
  let loError = $state<string | null>(null);
  let loDays = $state(30); // 0 = all time
  let loMinN = $state(1);
  let loSymbol = $state<string | null>(null); // null = ALL

  // The ALL chip's count must stay global even while a symbol is selected, so it
  // sums the chip list (always global) rather than reading the filtered roll-up.
  const loTotalAllSymbols = $derived(
    liveOutcomes ? liveOutcomes.symbols.reduce((acc, s) => acc + s.n, 0) : 0,
  );

  let openExpanded = $state(false);
  let openData = $state<LiveOpenPositionsResponse | null>(null);
  let openError = $state<string | null>(null);
  let openTimer: ReturnType<typeof setInterval> | null = null;
  let nowMs = $state(Date.now());
  let clockTimer: ReturnType<typeof setInterval> | null = null;

  onMount(() => {
    void loadLiveOutcomes();
    clockTimer = setInterval(() => (nowMs = Date.now()), 60_000);
    return () => {
      if (clockTimer !== null) clearInterval(clockTimer);
      if (openTimer !== null) clearInterval(openTimer);
    };
  });

  const formatPct = (v: number) => (v * 100).toFixed(1) + "%";

  async function loadLiveOutcomes(): Promise<void> {
    loLoading = true;
    loError = null;
    try {
      liveOutcomes = await getLiveOutcomes(loDays, loMinN, loSymbol);
    } catch (e) {
      loError = e instanceof Error ? e.message : String(e);
    } finally {
      loLoading = false;
    }
  }

  function setLoDays(d: number): void {
    if (loDays === d) return;
    loDays = d;
    void loadLiveOutcomes();
  }

  function setLoMinN(n: number): void {
    if (loMinN === n) return;
    loMinN = n;
    void loadLiveOutcomes();
  }

  function setLoSymbol(s: string | null): void {
    if (loSymbol === s) return;
    loSymbol = s;
    void loadLiveOutcomes();
    if (openExpanded) void loadOpenPositions();
  }

  async function loadOpenPositions(): Promise<void> {
    try {
      openData = await getLiveOutcomesOpen(loSymbol);
      openError = null;
    } catch (e) {
      // Keep the last good data and mark it stale rather than blanking the panel
      // — a transient DuckDB lock (the signal daemon writing) must not flash an
      // error banner on every poll.
      openError = e instanceof Error ? e.message : String(e);
    }
  }

  function toggleOpenPanel(): void {
    openExpanded = !openExpanded;
    if (openExpanded) {
      void loadOpenPositions();
      openTimer = setInterval(() => void loadOpenPositions(), 30_000);
    } else if (openTimer !== null) {
      clearInterval(openTimer);
      openTimer = null;
    }
  }

  function fmtAge(firedAtMs: number): string {
    const mins = Math.max(0, Math.floor((nowMs - firedAtMs) / 60_000));
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h${String(mins % 60).padStart(2, "0")}m`;
    return `${Math.floor(hours / 24)}d${String(hours % 24).padStart(2, "0")}h`;
  }

  const fmtR = (v: number | null) => (v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(3));
  // Adaptive price formatter — the open-panel ledger mixes MSTR (~1500) with
  // low-priced names, so a fixed decimal count either loses precision on the
  // small side or floods the column on the large side.
  const fmtPrice = (v: number | null): string => {
    if (v === null) return "—";
    return v >= 1000
      ? v.toLocaleString("en-US", { maximumFractionDigits: 0 })
      : v >= 1
        ? v.toFixed(2)
        : v.toFixed(4);
  };
  // Largest |avg_r| among visible cells — used to scale the diverging bars.
  const loMaxAbsR = $derived(
    liveOutcomes
      ? Math.max(
          0.01,
          ...liveOutcomes.cells.map((c) => Math.abs(c.avg_r ?? 0)),
          ...liveOutcomes.by_strategy.map((s) => Math.abs(s.avg_r ?? 0)),
        )
      : 1
  );

  type SortDir = "asc" | "desc";
  let stratSort = $state<{ key: string; dir: SortDir } | null>(null);
  let cellSort = $state<{ key: string; dir: SortDir } | null>(null);

  function toggleSort(
    current: { key: string; dir: SortDir } | null,
    key: string,
  ): { key: string; dir: SortDir } {
    if (current && current.key === key) {
      return { key, dir: current.dir === "desc" ? "asc" : "desc" };
    }
    return { key, dir: "desc" };
  }

  function sortRows<T>(
    rows: T[],
    sort: { key: string; dir: SortDir } | null,
  ): T[] {
    if (!sort) return rows;
    const sign = sort.dir === "desc" ? -1 : 1;
    return [...rows].sort((a, b) => {
      const av = (a as Record<string, unknown>)[sort.key];
      const bv = (b as Record<string, unknown>)[sort.key];
      // Nulls always last, whichever way the column is sorted.
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === "number" && typeof bv === "number") return sign * (av - bv);
      return sign * String(av).localeCompare(String(bv));
    });
  }

  const sortedStrategies = $derived(
    liveOutcomes ? sortRows(liveOutcomes.by_strategy, stratSort) : [],
  );
  const sortedCells = $derived(
    liveOutcomes ? sortRows(liveOutcomes.cells, cellSort) : [],
  );
</script>

{#snippet rbar(v: number | null)}
  <span class="lo-bar">
    <span class="lo-bar-mid"></span>
    {#if v !== null && v !== 0}
      {@const w = Math.min(Math.abs(v) / loMaxAbsR, 1) * 50}
      <span
        class="lo-bar-fill"
        class:pos={v > 0}
        class:neg={v < 0}
        style="width:{w.toFixed(1)}%; {v > 0 ? 'left:50%' : 'right:50%'}"
      ></span>
    {/if}
  </span>
{/snippet}

<div class="lo-controls">
  <div class="lo-chip-group">
    <button class="lo-chip" class:active={loDays === 30} onclick={() => setLoDays(30)}>30D</button>
    <button class="lo-chip" class:active={loDays === 90} onclick={() => setLoDays(90)}>90D</button>
    <button class="lo-chip" class:active={loDays === 0} onclick={() => setLoDays(0)}>All</button>
  </div>
  <div class="lo-chip-group">
    <button class="lo-chip" class:active={loMinN === 1} onclick={() => setLoMinN(1)}>n≥1</button>
    <button class="lo-chip" class:active={loMinN === 10} onclick={() => setLoMinN(10)}>n≥10</button>
  </div>
  {#if liveOutcomes}
    <div class="lo-chip-group">
      <button class="lo-chip" class:active={loSymbol === null} onclick={() => setLoSymbol(null)}>
        ALL <span class="lo-chip-n">{loTotalAllSymbols.toLocaleString()}</span>
      </button>
      {#each liveOutcomes.symbols as s}
        <button class="lo-chip" class:active={loSymbol === s.symbol} onclick={() => setLoSymbol(s.symbol)}>
          {s.symbol} <span class="lo-chip-n">{s.n.toLocaleString()}</span>
        </button>
      {/each}
    </div>
  {/if}
</div>

{#if loError}
  <div class="lo-msg val-red">Failed to load outcomes: {loError}</div>
{:else if !liveOutcomes}
  <div class="lo-msg muted">Loading live outcomes…</div>
{:else if liveOutcomes.rollup.total_rows === 0}
  <div class="lo-msg muted">No alerts fired yet — the ledger is empty.</div>
{:else}
  {@const rollup = liveOutcomes.rollup}
  <div class="lo-rollup">
    <div class="lo-stat">
      <span class="lo-stat-val">{rollup.total_rows.toLocaleString()}</span>
      <span class="lo-stat-label">fired</span>
    </div>
    <div class="lo-stat">
      <span class="lo-stat-val">{rollup.resolved.toLocaleString()}</span>
      <span class="lo-stat-label">resolved</span>
    </div>
    <button class="lo-stat lo-stat-btn" onclick={toggleOpenPanel}>
      <span class="lo-stat-val">{rollup.open.toLocaleString()} {openExpanded ? "▾" : "▸"}</span>
      <span class="lo-stat-label">open</span>
    </button>
    <div class="lo-stat lo-stat-integrity" class:val-green={rollup.open_no_tp === 0} class:val-red={rollup.open_no_tp > 0}>
      <span class="lo-stat-val">{rollup.open_no_tp === 0 ? "✓ 0" : rollup.open_no_tp.toLocaleString()}</span>
      <span class="lo-stat-label">no-TP hole</span>
    </div>
    <div class="lo-wle">
      <span class="val-green">{rollup.wins.toLocaleString()} W</span>
      <span class="lo-sep">·</span>
      <span class="val-red">{rollup.losses.toLocaleString()} L</span>
      <span class="lo-sep">·</span>
      <span class="muted">{rollup.expired.toLocaleString()} exp</span>
    </div>
  </div>
  <div class="lo-scope muted">
    {loSymbol ?? "all symbols"} · all-time roll-up · tables show
    {loDays === 0 ? "all time" : `last ${loDays}d`}, min n {loMinN}
  </div>

  {#if openExpanded}
    <div class="lo-open">
      {#if openError && !openData}
        <div class="lo-msg val-red">Failed to load open positions: {openError}</div>
      {:else if !openData}
        <div class="lo-msg muted">Loading open positions…</div>
      {:else if openData.positions.length === 0}
        <div class="lo-msg muted">No open alerts{loSymbol ? ` on ${loSymbol}` : ""}.</div>
      {:else}
        {#if !openData.marks_ok}
          <div class="lo-note muted">Live prices unavailable — showing ledger values only.</div>
        {:else if openError}
          <div class="lo-note muted">Prices may be stale — last refresh failed.</div>
        {/if}
        <div class="lo-table lo-open-table">
          <div class="lo-row lo-open-row lo-head">
            <span>sym</span><span>strat</span><span>tf</span><span>dir</span>
            <span class="num">age</span><span class="num">entry</span>
            <span class="num">mark</span><span class="num">uR*</span>
            <span class="num">→SL</span><span class="num">→TP</span>
          </div>
          {#each openData.positions as p}
            <div class="lo-row lo-open-row">
              <span>{p.symbol}</span>
              <span class="lo-strat">{p.strategy}</span>
              <span class="muted">{p.tf}</span>
              <span class:val-green={p.direction === "long"} class:val-red={p.direction === "short"}>
                {p.direction === "long" ? "▲ L" : "▼ S"}
              </span>
              <span class="num muted">{fmtAge(p.fired_at_ms)}</span>
              <span class="num muted">{fmtPrice(p.entry_price)}</span>
              <span class="num">{fmtPrice(p.mark)}</span>
              <span class="num" class:val-green={(p.unrealized_r ?? 0) > 0} class:val-red={(p.unrealized_r ?? 0) < 0}>
                {fmtR(p.unrealized_r)}
              </span>
              <span class="num muted">{p.dist_sl_pct === null ? "—" : formatPct(p.dist_sl_pct)}</span>
              <span class="num muted">{p.dist_tp_pct === null ? "—" : formatPct(p.dist_tp_pct)}</span>
            </div>
          {/each}
        </div>
        <div class="lo-note muted">
          *uR marks to the newest stored close (as fresh as the last sync) — same gross R scale as the tables.
        </div>
      {/if}
    </div>
  {/if}

  {#if liveOutcomes.by_strategy.length === 0}
    <div class="lo-msg muted">No resolved trades in this window — widen the period or lower min n.</div>
  {:else}
    <div class="lo-cols">
      <div class="lo-block">
        <div class="lo-block-title">
          By strategy <span class="lo-title-note">(n≥{liveOutcomes.min_n} total)</span>
        </div>
        <div class="lo-table">
          <div class="lo-row lo-head">
            <button class="lo-th" onclick={() => (stratSort = toggleSort(stratSort, "strategy"))}>
              strategy{stratSort?.key === "strategy" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "n"))}>
              n{stratSort?.key === "n" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "expired"))}>
              exp{stratSort?.key === "expired" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "win_rate"))}>
              win{stratSort?.key === "win_rate" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (stratSort = toggleSort(stratSort, "avg_r"))}>
              avg R{stratSort?.key === "avg_r" ? (stratSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <span></span>
          </div>
          {#each sortedStrategies as s}
            <div class="lo-row">
              <span class="lo-strat">{s.strategy}</span>
              <span class="num muted">{s.n}</span>
              <span class="num muted">{s.expired}</span>
              <span class="num">{s.win_rate === null ? "—" : formatPct(s.win_rate)}</span>
              <span class="num" class:val-green={(s.avg_r ?? 0) > 0} class:val-red={(s.avg_r ?? 0) < 0}>{fmtR(s.avg_r)}</span>
              <span class="lo-bar-cell">{@render rbar(s.avg_r)}</span>
            </div>
          {/each}
        </div>
      </div>

      <div class="lo-block">
        <div class="lo-block-title">
          By strategy · tf · direction
          <span class="lo-title-note">(n≥{liveOutcomes.min_n} per cell)</span>
        </div>
        <div class="lo-table lo-scroll">
          <div class="lo-row lo-cell-row lo-head">
            <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "strategy"))}>
              strat{cellSort?.key === "strategy" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "tf"))}>
              tf{cellSort?.key === "tf" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th" onclick={() => (cellSort = toggleSort(cellSort, "direction"))}>
              dir{cellSort?.key === "direction" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "n"))}>
              n{cellSort?.key === "n" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "expired"))}>
              exp{cellSort?.key === "expired" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "win_rate"))}>
              win{cellSort?.key === "win_rate" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <button class="lo-th num" onclick={() => (cellSort = toggleSort(cellSort, "avg_r"))}>
              avg R{cellSort?.key === "avg_r" ? (cellSort.dir === "desc" ? " ▾" : " ▴") : ""}
            </button>
            <span></span>
          </div>
          {#each sortedCells as c}
            <div class="lo-row lo-cell-row">
              <span class="lo-strat">{c.strategy}</span>
              <span class="muted">{c.tf}</span>
              <span class:val-green={c.direction === "long"} class:val-red={c.direction === "short"}>{c.direction === "long" ? "▲ L" : "▼ S"}</span>
              <span class="num muted">{c.n}</span>
              <span class="num muted">{c.expired}</span>
              <span class="num">{c.win_rate === null ? "—" : formatPct(c.win_rate)}</span>
              <span class="num" class:val-green={(c.avg_r ?? 0) > 0} class:val-red={(c.avg_r ?? 0) < 0}>{fmtR(c.avg_r)}</span>
              <span class="lo-bar-cell">{@render rbar(c.avg_r)}</span>
            </div>
          {/each}
          {#if sortedCells.length === 0}
            <div class="lo-msg muted">
              no cell clears n≥{liveOutcomes.min_n} — lower min n to see the breakdown
            </div>
          {/if}
        </div>
      </div>
    </div>
    <div class="lo-note muted">
      win% = wins/(wins+losses); expired excluded. avg R is gross (win=+rr, loss=−1, expired=MTM) and includes expired.
    </div>
  {/if}
{/if}

<style>
  /* Chrome colours — Stats.svelte defines these too (Svelte scopes styles per
     component, so the parent's rule doesn't reach here); keep in sync. */
  .val-green { color: var(--green, #4caf81); }
  .val-red { color: var(--red, #e05c5c); }

  /* Control row — relocated from the card header (was `.pill-toggle`); styled
     to match PathCone.svelte's `.chip-group` / `.chip`. */
  .lo-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 10px 0 4px;
  }
  .lo-chip-group {
    display: flex;
    flex-wrap: wrap;
    min-width: 0;
    gap: 4px;
  }
  .lo-chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    white-space: nowrap;
    cursor: pointer;
  }
  .lo-chip.active {
    border-color: #60a5fa;
    color: #e5e7eb;
  }
  .lo-chip-n {
    opacity: 0.55;
    margin-left: 4px;
  }

  .lo-msg {
    font-size: 12px;
    padding: 10px 2px;
  }

  /* Roll-up chips */
  .lo-rollup {
    display: flex;
    align-items: stretch;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 6px;
  }

  .lo-stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 7px 14px;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: color-mix(in srgb, var(--accent) 4%, transparent);
    min-width: 72px;
  }

  .lo-stat-val {
    font-size: 18px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
  }

  .lo-stat-label {
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .lo-stat-integrity {
    background: color-mix(in srgb, currentColor 8%, transparent);
    border-color: color-mix(in srgb, currentColor 30%, var(--border));
  }

  .lo-stat-btn {
    background: none;
    border: none;
    font: inherit;
    color: inherit;
    cursor: pointer;
    text-align: left;
    padding: 0;
  }

  .lo-wle {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
    padding: 7px 4px;
    font-size: 13px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .lo-wle .lo-sep { color: var(--muted); font-weight: 400; }

  .lo-scope {
    font-size: 10px;
    margin-bottom: 12px;
  }

  .lo-open {
    margin: 10px 0 4px;
    max-height: 360px;
    overflow-y: auto;
  }
  .lo-note {
    font-size: 11px;
    margin: 6px 0 0;
  }

  /* Two-column block layout */
  .lo-cols {
    display: grid;
    grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
    gap: 18px;
  }

  @media (max-width: 760px) {
    .lo-cols { grid-template-columns: 1fr; }
  }

  .lo-block-title {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 6px;
  }

  .lo-title-note {
    font-size: 9px;
    font-weight: 400;
    letter-spacing: 0.02em;
    text-transform: none;
    color: #777;
  }

  .lo-table { display: flex; flex-direction: column; }

  .lo-scroll {
    max-height: 360px;
    overflow-y: auto;
  }

  .lo-row {
    display: grid;
    grid-template-columns: 1fr 34px 34px 46px 56px 64px;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    padding: 3px 4px;
    border-radius: 3px;
    margin: 0 -4px;
  }

  .lo-cell-row {
    grid-template-columns: 1fr 34px 34px 30px 34px 44px 56px 60px;
  }

  .lo-open-row {
    grid-template-columns: 78px minmax(0, 1fr) 34px 34px 52px 76px 76px 58px 52px 52px;
  }

  .lo-row:not(.lo-head):hover {
    background: color-mix(in srgb, var(--accent) 6%, transparent);
  }

  .lo-head {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 5px;
    margin-bottom: 2px;
    position: sticky;
    top: 0;
    background: var(--bg-panel);
    z-index: 1;
  }

  .lo-row .num { text-align: right; }

  .lo-th {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }
  .lo-th.num {
    text-align: right;
  }
  .lo-th:hover {
    color: var(--fg, #d5dde6);
  }

  .lo-strat {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* Diverging avg-R bar (red left / green right of centre) */
  .lo-bar-cell { display: flex; }
  .lo-bar {
    position: relative;
    width: 100%;
    height: 8px;
    background: color-mix(in srgb, var(--border) 50%, transparent);
    border-radius: 2px;
  }

  .lo-bar-mid {
    position: absolute;
    left: 50%;
    top: -1px;
    bottom: -1px;
    width: 1px;
    background: var(--muted);
    opacity: 0.5;
  }

  .lo-bar-fill {
    position: absolute;
    top: 0;
    bottom: 0;
    border-radius: 2px;
  }

  .lo-bar-fill.pos { background: var(--green, #4caf81); }
  .lo-bar-fill.neg { background: var(--red, #e05c5c); }
</style>
