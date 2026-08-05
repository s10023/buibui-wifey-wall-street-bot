<script lang="ts">
  import { onMount } from "svelte";
  import { get } from "svelte/store";
  import { getSignalsHistory } from "../api";
  import { symbols } from "../stores/config";
  import { strategyNames } from "../stores/strategies";
  import {
    signalsStore,
    signalsLoading,
    signalsError,
    signalsLastRefresh,
    type SignalWithMeta,
  } from "../stores/signals";
  import ErrorBanner from "../components/ErrorBanner.svelte";
  import LoadingSpinner from "../components/LoadingSpinner.svelte";

  const TIMEFRAMES = ["1h", "4h", "1d", "1wk"];
  const POLL_MS = 60_000;
  const LS_KEY = "signal_trade_state";

  type TradeStatus = "taken" | "missed";
  type TradeStateMap = Record<string, TradeStatus>;

  function sigKey(s: SignalWithMeta): string {
    return `${s.symbol}|${s.timeframe}|${s.strategy}|${s.open_time}`;
  }

  // ── localStorage trade state ──────────────────────────────────────────────

  function loadTradeState(): TradeStateMap {
    try {
      const raw = localStorage.getItem(LS_KEY);
      return raw ? (JSON.parse(raw) as TradeStateMap) : {};
    } catch {
      return {};
    }
  }

  function saveTradeState(state: TradeStateMap): void {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(state));
    } catch {
      /* ignore quota errors */
    }
  }

  let tradeState = $state<TradeStateMap>(loadTradeState());

  function setStatus(sig: SignalWithMeta, status: TradeStatus | null): void {
    const key = sigKey(sig);
    const next = { ...tradeState };
    if (status === null) {
      delete next[key];
    } else {
      next[key] = status;
    }
    tradeState = next;
    saveTradeState(next);
    dismissPrompt();
  }

  // ── Conflict prompt (active trade vs new signal) ──────────────────────────

  type ConflictPrompt = {
    existing: SignalWithMeta; // the "taken" signal already on record
    incoming: SignalWithMeta; // the new conflicting signal
  };

  let conflictPrompt = $state<ConflictPrompt | null>(null);
  let dismissedConflicts = $state(new Set<string>());

  function conflictPairKey(a: SignalWithMeta, b: SignalWithMeta): string {
    return [sigKey(a), sigKey(b)].sort().join("||");
  }

  function dismissPrompt(): void {
    if (conflictPrompt !== null) {
      dismissedConflicts = new Set([
        ...dismissedConflicts,
        conflictPairKey(conflictPrompt.existing, conflictPrompt.incoming),
      ]);
    }
    conflictPrompt = null;
  }

  function closeTradeAndMark(prompt: ConflictPrompt): void {
    // Mark existing taken trade as missed (closed), leave incoming unmarked
    setStatus(prompt.existing, "missed");
    conflictPrompt = null;
  }

  // ── Window / filter state ─────────────────────────────────────────────────

  type WindowOption = { label: string; days: number };
  const WINDOW_OPTIONS: WindowOption[] = [
    { label: "24h", days: 1 },
    { label: "7d", days: 7 },
    { label: "30d", days: 30 },
    { label: "90d", days: 90 },
  ];
  let selectedDays = $state(7);
  let filterSymbol = $state("");
  let filterStrategy = $state("");
  let filterDirection = $state<"" | "long" | "short">("");

  const nowMs = () => Date.now();
  const startMs = () => nowMs() - selectedDays * 24 * 60 * 60 * 1000;

  async function fetchAll(): Promise<void> {
    signalsError.set(null);
    try {
      const allSymbols = get(symbols);
      if (allSymbols.length === 0) return;

      const settled = await Promise.allSettled(
        allSymbols.flatMap((sym) =>
          TIMEFRAMES.map(async (tf) => {
            const resp = await getSignalsHistory({
              symbol: sym,
              timeframe: tf,
              start_ms: startMs(),
              end_ms: nowMs(),
            });
            return resp.signals.map((s): SignalWithMeta => ({ ...s, symbol: sym, timeframe: tf }));
          })
        )
      );
      // 404 = no OHLCV data for that symbol/tf — skip silently
      signalsStore.set(
        settled
          .flatMap((r) => (r.status === "fulfilled" ? r.value : []))
          .sort((a, b) => b.open_time - a.open_time)
          .slice(0, 100)
      );
      signalsLastRefresh.set(new Date());
    } catch (e) {
      signalsError.set(String(e));
    } finally {
      signalsLoading.set(false);
    }
  }

  let interval: ReturnType<typeof setInterval>;
  onMount(() => {
    const unsub = symbols.subscribe((syms) => {
      if (syms.length > 0 && get(signalsLoading)) void fetchAll();
    });
    interval = setInterval(() => void fetchAll(), POLL_MS);
    return () => {
      unsub();
      clearInterval(interval);
    };
  });

  // ── Conflict detection (B3) ───────────────────────────────────────────────
  // For each (symbol, timeframe) group: if both LONG and SHORT signals exist,
  // tag the lower-confidence one as conflicting. Equal confidence → both tagged.

  type SignalWithConflict = SignalWithMeta & { isConflict: boolean };

  function tagConflicts(sigs: SignalWithMeta[]): SignalWithConflict[] {
    // group by symbol+tf
    const groups = new Map<string, SignalWithMeta[]>();
    for (const s of sigs) {
      const gk = `${s.symbol}|${s.timeframe}`;
      const arr = groups.get(gk) ?? [];
      arr.push(s);
      groups.set(gk, arr);
    }

    const conflictKeys = new Set<string>();
    for (const group of groups.values()) {
      const longs = group.filter((s) => s.direction === "long");
      const shorts = group.filter((s) => s.direction === "short");
      if (longs.length === 0 || shorts.length === 0) continue;

      const maxLong = Math.max(...longs.map((s) => s.confidence));
      const maxShort = Math.max(...shorts.map((s) => s.confidence));

      if (maxLong > maxShort) {
        // shorts are conflicting
        for (const s of shorts) conflictKeys.add(sigKey(s));
      } else if (maxShort > maxLong) {
        // longs are conflicting
        for (const s of longs) conflictKeys.add(sigKey(s));
      } else {
        // equal — both sides conflicting
        for (const s of [...longs, ...shorts]) conflictKeys.add(sigKey(s));
      }
    }

    return sigs.map((s) => ({ ...s, isConflict: conflictKeys.has(sigKey(s)) }));
  }

  // ── Derived: filtered + conflict-tagged signals ───────────────────────────

  const filtered = $derived(
    tagConflicts(
      $signalsStore.filter((s) => {
        if (filterSymbol && s.symbol !== filterSymbol) return false;
        if (filterStrategy && s.strategy !== filterStrategy) return false;
        if (filterDirection && s.direction !== filterDirection) return false;
        return true;
      })
    )
  );

  const activeTrades = $derived(
    filtered.filter((s) => tradeState[sigKey(s)] === "taken")
  );

  const feedSignals = $derived(
    filtered.filter((s) => tradeState[sigKey(s)] !== "taken")
  );

  // ── Check new signals against active trades ───────────────────────────────
  // When signals refresh, see if any new signal conflicts with an active trade.
  // Show at most one prompt at a time.

  $effect(() => {
    if (conflictPrompt !== null) return; // already showing one
    const taken = filtered.filter((s) => tradeState[sigKey(s)] === "taken");
    for (const active of taken) {
      const conflict = filtered.find(
        (s) =>
          s.symbol === active.symbol &&
          s.timeframe === active.timeframe &&
          s.direction !== active.direction &&
          sigKey(s) !== sigKey(active) &&
          tradeState[sigKey(s)] !== "missed"
      );
      if (conflict && !dismissedConflicts.has(conflictPairKey(active, conflict))) {
        conflictPrompt = { existing: active, incoming: conflict };
        break;
      }
    }
  });

  // ── Formatting helpers ────────────────────────────────────────────────────

  const fmtTime = (ms: number) =>
    new Date(ms).toLocaleString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

  const stars = (n: number) => "★".repeat(n) + "☆".repeat(5 - n);

  const refreshTime = $derived(
    $signalsLastRefresh
      ? $signalsLastRefresh.toLocaleTimeString("en-MY", { timeZone: "Asia/Kuala_Lumpur", hour12: false })
      : null
  );
</script>

<!-- ── Conflict prompt overlay ──────────────────────────────────────────── -->
{#if conflictPrompt !== null}
  {@const p = conflictPrompt}
  <div class="overlay" role="dialog" aria-modal="true" aria-label="Conflict alert">
    <div class="prompt-box">
      <div class="prompt-title">Active trade conflict</div>
      <div class="prompt-body">
        You have an active <span class={p.existing.direction === "long" ? "green" : "red"}>{p.existing.direction.toUpperCase()}</span>
        on <strong>{p.existing.symbol}</strong> ({p.existing.timeframe}) via <em>{p.existing.strategy}</em>.
        <br /><br />
        {#if p.incoming.open_time > p.existing.open_time}
          A newer <span class={p.incoming.direction === "long" ? "green" : "red"}>{p.incoming.direction.toUpperCase()}</span>
          signal fired (<em>{p.incoming.strategy}</em>).
          Does this mean your active trade should be closed?
        {:else}
          There is an older <span class={p.incoming.direction === "long" ? "green" : "red"}>{p.incoming.direction.toUpperCase()}</span>
          signal on the same pair (<em>{p.incoming.strategy}</em>) that you chose to ignore.
          Are you sure you want to take this trade?
        {/if}
      </div>
      <div class="prompt-actions">
        <button class="btn-close-trade" onclick={() => closeTradeAndMark(p)}>
          {p.incoming.open_time > p.existing.open_time ? "Yes — close trade" : "Cancel — undo taken"}
        </button>
        <button class="btn-dismiss" onclick={dismissPrompt}>
          {p.incoming.open_time > p.existing.open_time ? "No — keep it open" : "Yes — take it anyway"}
        </button>
      </div>
    </div>
  </div>
{/if}

<div class="page">
  <div class="page-header">
    <h2>Signal Feed</h2>
    {#if refreshTime}
      <span class="refresh-ts">updated {refreshTime} MYT</span>
    {/if}
  </div>

  {#if $signalsError}<ErrorBanner error={$signalsError} />{/if}

  <div class="filter-bar">
    <label>Symbol
      <select bind:value={filterSymbol}>
        <option value="">All</option>
        {#each $symbols as sym}<option>{sym}</option>{/each}
      </select>
    </label>
    <label>Strategy
      <select bind:value={filterStrategy}>
        <option value="">All</option>
        {#each $strategyNames as s}<option>{s}</option>{/each}
      </select>
    </label>
    <label>Direction
      <select bind:value={filterDirection}>
        <option value="">All</option>
        <option value="long">Long</option>
        <option value="short">Short</option>
      </select>
    </label>
    <div class="window-group">
      {#each WINDOW_OPTIONS as opt}
        <button
          class="window-btn"
          class:active={selectedDays === opt.days}
          onclick={() => { selectedDays = opt.days; void fetchAll(); }}
        >{opt.label}</button>
      {/each}
    </div>
    <button onclick={() => void fetchAll()}>Refresh</button>
    <span class="result-count">
      {#if !$signalsLoading}{filtered.length} result{filtered.length !== 1 ? "s" : ""}{/if}
    </span>
  </div>

  {#if $signalsLoading}
    <LoadingSpinner label="Loading signals..." />
  {:else}

    <!-- ── Active Trades section ──────────────────────────────────────────── -->
    {#if activeTrades.length > 0}
      <div class="section-label">Active trades <span class="count-badge">{activeTrades.length}</span></div>
      <div class="card-grid">
        {#each activeTrades as sig (sigKey(sig))}
          <div class="sig-card taken" class:conflict={sig.isConflict}>
            <div class="card-header">
              <span class="sym">{sig.symbol}</span>
              <span class="tf muted">{sig.timeframe}</span>
              <span class="dir-badge" class:long={sig.direction === "long"} class:short={sig.direction === "short"}>
                {sig.direction.toUpperCase()}
              </span>
              {#if sig.isConflict}
                <span class="conflict-tag" title="Opposing signal exists with higher or equal confidence">⚠ conflict</span>
              {/if}
              <span class="status-badge taken-badge">taken</span>
            </div>
            <div class="card-body">
              <span class="strategy">{sig.strategy}</span>
              <span class="stars">{stars(sig.confidence)}</span>
              <span class="sl mono">{sig.sl_price > 0 ? `SL ${sig.sl_price.toLocaleString()}` : ""}</span>
            </div>
            <div class="card-reason muted">{sig.reason}</div>
            <div class="card-meta muted">{fmtTime(sig.open_time)}</div>
            <div class="card-actions">
              <button class="action-btn btn-undo" onclick={() => setStatus(sig, null)}>Undo</button>
              <button class="action-btn btn-missed" onclick={() => setStatus(sig, "missed")}>Closed</button>
            </div>
          </div>
        {/each}
      </div>
    {/if}

    <!-- ── Signal feed section ──────────────────────────────────────────── -->
    {#if activeTrades.length > 0}
      <div class="section-label">Signal feed</div>
    {/if}

    <table>
      <thead>
        <tr>
          <th>Time (MYT)</th><th>Symbol</th><th>TF</th><th>Strategy</th>
          <th>Dir</th><th>Conf</th><th>SL</th><th>Reason</th><th>Action</th>
        </tr>
      </thead>
      <tbody>
        {#each feedSignals as sig (sigKey(sig))}
          {@const status = tradeState[sigKey(sig)] as TradeStatus | undefined}
          <tr class:row-missed={status === "missed"}>
            <td class="mono muted">{fmtTime(sig.open_time)}</td>
            <td class="sym">{sig.symbol}</td>
            <td class="muted">{sig.timeframe}</td>
            <td>
              {sig.strategy}
              {#if sig.isConflict}
                <span class="conflict-tag inline" title="Opposing signal exists with higher or equal confidence">⚠ conflict</span>
              {/if}
            </td>
            <td class={sig.direction === "long" ? "green" : "red"}>
              {sig.direction.toUpperCase()}
            </td>
            <td class="stars">{stars(sig.confidence)}</td>
            <td class="mono">{sig.sl_price > 0 ? sig.sl_price.toLocaleString() : "—"}</td>
            <td class="reason muted">{sig.reason}</td>
            <td class="actions-cell">
              {#if status === "missed"}
                <span class="status-badge missed-badge">missed</span>
                <button class="action-btn btn-undo small" onclick={() => setStatus(sig, null)}>Undo</button>
              {:else}
                <button class="action-btn btn-taken small" onclick={() => setStatus(sig, "taken")}>✓ Taken</button>
                <button class="action-btn btn-missed small" onclick={() => setStatus(sig, "missed")}>✗ Missed</button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if filtered.length === 0}
      <p class="empty muted">// no signals match current filters</p>
    {:else if feedSignals.length === 0 && activeTrades.length > 0}
      <p class="empty muted">// all signals marked — active trades shown above</p>
    {/if}
  {/if}
</div>

<style>
  /* ── Header ──────────────────────────────────────────────────────────────── */
  .page-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }

  .refresh-ts {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.05em;
    padding-bottom: 14px;
  }

  /* ── Filter bar ──────────────────────────────────────────────────────────── */
  .filter-bar {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: flex-end;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    border-radius: 4px;
    padding: 12px 16px;
    margin-bottom: 14px;
  }

  .result-count {
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    align-self: center;
    margin-left: 4px;
  }

  .window-group {
    display: flex;
    gap: 2px;
    align-items: flex-end;
  }

  .window-btn {
    padding: 4px 10px;
    font-size: 11px;
    letter-spacing: 0.04em;
    background: var(--bg-panel);
    border: 1px solid var(--border-dim);
    color: var(--muted);
    border-radius: 3px;
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }

  .window-btn:hover {
    border-color: var(--accent);
    color: var(--fg);
  }

  .window-btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--bg);
    font-weight: 600;
  }

  /* ── Section labels ──────────────────────────────────────────────────────── */
  .section-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .count-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    border-radius: 9px;
    color: var(--accent);
    font-size: 10px;
    font-weight: 700;
  }

  /* ── Active trade cards ──────────────────────────────────────────────────── */
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 10px;
    margin-bottom: 20px;
  }

  .sig-card {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: border-color 120ms;
  }

  .sig-card.taken {
    border-left: 3px solid var(--accent);
  }

  .sig-card.conflict {
    border-left: 3px solid var(--yellow);
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .card-body {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .card-reason {
    font-size: 11px;
    line-height: 1.4;
  }

  .card-meta {
    font-size: 10px;
    letter-spacing: 0.04em;
  }

  .card-actions {
    display: flex;
    gap: 6px;
    margin-top: 2px;
  }

  /* ── Badges ──────────────────────────────────────────────────────────────── */
  .dir-badge {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    padding: 1px 6px;
    border-radius: 3px;
  }

  .dir-badge.long {
    background: var(--green-dim);
    color: var(--green);
    border: 1px solid #0d3d20;
  }

  .dir-badge.short {
    background: var(--red-dim);
    color: var(--red);
    border: 1px solid #4a1515;
  }

  .status-badge {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 1px 7px;
    border-radius: 3px;
  }

  .taken-badge {
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid var(--accent);
  }

  .missed-badge {
    background: #1a1a1a;
    color: var(--muted);
    border: 1px solid var(--border-dim);
  }

  .conflict-tag {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    color: var(--yellow);
    background: #2a2000;
    border: 1px solid #3d3000;
    border-radius: 3px;
    padding: 1px 6px;
  }

  .conflict-tag.inline {
    display: inline-block;
    margin-left: 4px;
    vertical-align: middle;
  }

  /* ── Action buttons ──────────────────────────────────────────────────────── */
  .action-btn {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 3px;
    cursor: pointer;
    transition: background 120ms, color 120ms;
    border: 1px solid;
  }

  .action-btn.small {
    font-size: 9px;
    padding: 2px 7px;
  }

  .btn-taken {
    color: var(--accent);
    border-color: var(--accent);
    background: transparent;
  }

  .btn-taken:hover {
    background: var(--accent-dim);
    color: var(--accent-bright);
  }

  .btn-missed {
    color: var(--muted);
    border-color: var(--border-dim);
    background: transparent;
  }

  .btn-missed:hover {
    background: #1a1a1a;
    color: var(--text);
    border-color: var(--border);
  }

  .btn-undo {
    color: var(--muted);
    border-color: var(--border-dim);
    background: transparent;
  }

  .btn-undo:hover {
    color: var(--text);
    border-color: var(--border);
  }

  .btn-close-trade {
    color: var(--red);
    border-color: #4a1515;
    background: var(--red-dim);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 5px 14px;
    border-radius: 3px;
    cursor: pointer;
    transition: background 120ms;
  }

  .btn-close-trade:hover {
    background: #3d1515;
  }

  .btn-dismiss {
    color: var(--muted);
    border-color: var(--border-dim);
    background: transparent;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 5px 14px;
    border-radius: 3px;
    cursor: pointer;
    transition: background 120ms, color 120ms;
    border: 1px solid;
  }

  .btn-dismiss:hover {
    background: #1a1a1a;
    color: var(--text);
  }

  /* ── Table extras ────────────────────────────────────────────────────────── */
  .actions-cell {
    white-space: nowrap;
    display: flex;
    gap: 4px;
    align-items: center;
    padding: 4px 8px;
  }

  .row-missed td {
    opacity: 0.45;
  }

  .row-missed td:last-child {
    opacity: 1;
  }

  /* ── Misc ────────────────────────────────────────────────────────────────── */
  .sym { font-weight: 500; }
  .strategy { font-size: 11px; }
  .stars { color: var(--yellow); letter-spacing: -1px; font-size: 11px; }
  .sl { font-size: 10px; color: var(--muted); }
  .reason { font-size: 11px; max-width: 200px; }
  .empty { padding: 20px 0; font-size: 12px; letter-spacing: 0.05em; }

  /* ── Conflict prompt overlay ─────────────────────────────────────────────── */
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .prompt-box {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-top: 3px solid var(--yellow);
    border-radius: 5px;
    padding: 24px 28px;
    max-width: 420px;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .prompt-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--yellow);
  }

  .prompt-body {
    font-size: 12px;
    line-height: 1.7;
    color: var(--text);
  }

  .prompt-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
  }
</style>
