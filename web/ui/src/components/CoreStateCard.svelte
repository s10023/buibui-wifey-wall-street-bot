<script lang="ts">
  import { onMount } from "svelte";
  import { getCoreState, type CoreStateResponse } from "../api";

  // Read-only view of analytics/overlay/live.py::core_state (#432). Every number
  // comes from /api/core-state; this component adds no rule of its own.

  let core = $state<CoreStateResponse | null>(null);
  let error = $state<string | null>(null);

  onMount(() => {
    void load();
  });

  async function load(): Promise<void> {
    try {
      core = await getCoreState();
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const pct0 = (v: number) => (v * 100).toFixed(0) + "%";
  const pct1 = (v: number) => (v * 100).toFixed(1) + "%";
  const signed = (v: number) => (v >= 0 ? "+" : "−") + pct1(Math.abs(v));
  const level = (v: number) =>
    v.toLocaleString("en-US", { maximumFractionDigits: 0 });
</script>

{#if error}
  <div class="core-error">Core state unavailable — {error}</div>
{:else if !core}
  <div class="core-muted">Loading…</div>
{:else}
  <div class="core-row">
    <div class="core-headline">
      <span class="core-label">Exposure</span>
      <span class="core-exposure" class:zero={core.exposure === 0}>{pct0(core.exposure)}</span>
      <span class="core-muted">next session</span>
    </div>

    <div class="core-leg">
      <span class="core-label">OV-1 · 200-session MA</span>
      <span>
        <span class="badge" class:in={core.ma_in} class:out={!core.ma_in}>{core.ma_in ? "IN" : "OUT"}</span>
        {core.sessions_in_state} sessions
      </span>
      <span class="core-detail">
        ^GSPC {level(core.close)} vs SMA {level(core.sma)} ({signed(core.sma_distance)})
      </span>
      <span class="core-detail">
        flips {core.ma_in ? "below" : "above"} {level(core.flip_level)} ({signed(core.flip_distance)})
      </span>
    </div>

    <div class="core-leg">
      <span class="core-label">VM · vol target</span>
      <span>w {core.vm_weight.toFixed(2)}</span>
      <span class="core-detail">σ̂20 {pct1(core.sigma_ann)} annualised</span>
    </div>

    <div class="core-leg core-asof">
      <span class="core-label">As of close</span>
      <span>{core.as_of}</span>
      {#if core.missing_sessions > 0}
        <span class="badge stale" title="run make core-sync">
          STALE · {core.missing_sessions} session{core.missing_sessions === 1 ? "" : "s"} missing
        </span>
      {/if}
    </div>
  </div>
{/if}

<style>
  .core-row {
    display: grid;
    grid-template-columns: auto 1fr 1fr auto;
    gap: 24px;
    align-items: start;
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--text);
  }

  @media (max-width: 700px) {
    .core-row { grid-template-columns: 1fr 1fr; gap: 16px; }
  }

  .core-headline,
  .core-leg {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .core-label {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .core-exposure {
    font-size: 28px;
    font-weight: 600;
    line-height: 1.1;
    color: var(--accent);
  }

  .core-exposure.zero { color: var(--text-dim); }

  .core-detail { color: var(--text-dim); font-size: 12px; }
  .core-muted { color: var(--muted); font-size: 12px; }
  .core-error { color: var(--red); font-size: 12px; }

  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
  }

  .badge.in { background: var(--green-dim); color: var(--green); }
  .badge.out { background: var(--red-dim); color: var(--red); }
  .badge.stale {
    align-self: flex-start;
    border: 1px solid var(--yellow);
    color: var(--yellow);
  }
</style>
