<script lang="ts">
  import type {
    ConeComboResponse,
    PathConeResponse,
    TodayPathResponse,
  } from "../api";
  import {
    bandPath,
    coneX,
    coneY,
    fmtNorm,
    fmtPrice,
    linePath,
    overlayPath,
    toPrice,
    type ConeGeometry,
    type YDomain,
  } from "../lib/cone";

  let {
    pathCone,
    todayPath,
  }: {
    pathCone: PathConeResponse;
    todayPath: TodayPathResponse | null;
  } = $props();

  const DIRS = [
    { key: "all", label: "All" },
    { key: "bull", label: "Bull" },
    { key: "bear", label: "Bear" },
  ];
  const DAYS = [
    { key: "all", label: "All days" },
    { key: "mon", label: "Mon" },
    { key: "tue", label: "Tue" },
    { key: "wed", label: "Wed" },
    { key: "thu", label: "Thu" },
    { key: "fri", label: "Fri" },
  ];

  let dir = $state("all");
  let wday = $state("all");

  const combo = $derived<ConeComboResponse | null>(
    pathCone.combos[`${dir}|${wday}`] ?? null
  );
  const hasData = $derived(!!combo && combo.n > 0);

  // ── SVG geometry (viewBox units) ──
  // 7 hourly RTH bars per session (09:30–16:00 ET; the last bar is the
  // half-hour 15:30 stub). Step 0 is the open; steps 1…7 are bar closes.
  const TOTAL_STEPS = 7;
  const geo: ConeGeometry = { W: 760, H: 280, PL: 46, PR: 30, PT: 12, PB: 26 };
  const { W, H, PL, PR, PT, PB } = geo;

  const yDomain = $derived.by<YDomain>(() => {
    const vals: number[] = [0];
    if (combo && combo.n > 0)
      for (const row of combo.bands) vals.push(row[0], row[4]);
    if (todayPath) vals.push(...todayPath.points);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 0.1);
    return { min: lo - pad, max: hi + pad };
  });

  const x = (step: number) => coneX(step, TOTAL_STEPS, geo);
  const y = (v: number) => coneY(v, yDomain, geo);

  const bandD = (loIdx: number, hiIdx: number): string =>
    combo ? bandPath(combo.bands, loIdx, hiIdx, TOTAL_STEPS, yDomain, geo) : "";

  const lineD = (idx: number): string =>
    combo ? linePath(combo.bands, idx, TOTAL_STEPS, yDomain, geo) : "";

  const todayD = $derived(
    todayPath ? overlayPath(todayPath.points, TOTAL_STEPS, yDomain, geo) : ""
  );

  // Axis: step index → ET wall-clock label (DST-stable relative to the session)
  const ET_LABELS = [
    "09:30",
    "10:30",
    "11:30",
    "12:30",
    "13:30",
    "14:30",
    "15:30",
    "16:00",
  ];
  const ticks = ET_LABELS.map((label, step) => ({ step, label }));

  const fmtAdr = fmtNorm;
  const px = (mag: number, side: 1 | -1) =>
    toPrice(mag, side, todayPath?.today_open ?? null, todayPath?.adr14_today ?? null);
  const fmtPx = fmtPrice;

  const elapsed = $derived(todayPath?.elapsed_h ?? 0);
  const lowInNow = $derived(
    hasData && elapsed >= 1
      ? combo!.low_in_by[Math.min(elapsed, TOTAL_STEPS) - 1]
      : null
  );
  const highInNow = $derived(
    hasData && elapsed >= 1
      ? combo!.high_in_by[Math.min(elapsed, TOTAL_STEPS) - 1]
      : null
  );

  let hoverStep = $state<number | null>(null);

  // Pointer x → elapsed step. Reading the SVG's client rect keeps this correct
  // at any rendered width, since the chart scales through its viewBox.
  function onPointerMove(ev: PointerEvent): void {
    const svg = (ev.currentTarget as SVGGraphicsElement).ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    const vbX = ((ev.clientX - rect.left) / rect.width) * W;
    const raw = Math.round(((vbX - PL) / (W - PL - PR)) * TOTAL_STEPS);
    hoverStep = Math.max(0, Math.min(TOTAL_STEPS, raw));
  }

  // Bands are indexed 0…6 for steps 1…7; step 0 is the open, where every
  // percentile is 0 by construction.
  const hoverRow = $derived.by(() => {
    if (hoverStep === null || !combo || combo.n === 0) return null;
    const step = hoverStep;
    const vals = step === 0 ? [0, 0, 0, 0, 0] : combo.bands[step - 1];
    // A short bands array would leave vals undefined and crash on vals[4].
    if (!vals) return null;
    const today =
      todayPath && step >= 1 && step <= todayPath.points.length
        ? todayPath.points[step - 1]
        : null;
    return { step, vals, today };
  });

  // Price when the live overlay gives us today_open + adr14; the cone's native
  // ADR multiple otherwise.
  const fmtVal = (v: number): string => {
    const p = px(v, 1);
    return p === null ? fmtAdr(v) + "×" : fmtPx(p);
  };
</script>

<div class="cone">
  <div class="cone-controls">
    <div class="chip-group">
      {#each DIRS as d}
        <button
          class="chip"
          class:active={dir === d.key}
          onclick={() => (dir = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    <div class="chip-group">
      {#each DAYS as d}
        <button
          class="chip"
          class:active={wday === d.key}
          onclick={() => (wday = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    {#if combo}
      <span class="n-badge" class:thin={combo.n < 30}
        >n={combo.n}{combo.n < 30 ? " ⚠ thin sample" : ""}</span
      >
    {/if}
  </div>

  {#if hasData && combo}
    <svg viewBox="0 0 {W} {H}" class="cone-svg" role="img" aria-label="Daily path cone">
      <path d={bandD(0, 4)} class="band-outer" />
      <path d={bandD(1, 3)} class="band-inner" />
      <line x1={x(0)} y1={y(0)} x2={x(TOTAL_STEPS)} y2={y(0)} class="zero-line" />
      <path d={lineD(2)} class="median-line" />
      {#if todayD && todayPath}
        <path d={todayD} class="today-line" />
        <circle
          cx={x(todayPath.points.length)}
          cy={y(todayPath.points[todayPath.points.length - 1])}
          r="3.5"
          class="today-dot"
        />
      {/if}
      {#if hoverStep !== null}
        <line
          x1={x(hoverStep)}
          y1={PT}
          x2={x(hoverStep)}
          y2={H - PB}
          class="cone-guide"
        />
      {/if}
      {#each ticks as t}
        <text x={x(t.step)} y={H - 8} class="axis-label" text-anchor="middle"
          >{t.label}</text
        >
      {/each}
      <text x={PL - 6} y={PT + 8} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.max)}×</text
      >
      <text x={PL - 6} y={y(0) + 3} class="axis-label" text-anchor="end">0</text>
      <text x={PL - 6} y={H - PB} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.min)}×</text
      >
      <rect
        x={PL}
        y={PT}
        width={W - PL - PR}
        height={H - PT - PB}
        fill="transparent"
        role="presentation"
        onpointermove={onPointerMove}
        onpointerleave={() => (hoverStep = null)}
      />
    </svg>

    <div class="cone-readout">
      {#if hoverRow}
        <span class="cone-readout-hour"
          >{ET_LABELS[hoverRow.step]} ET (bar {hoverRow.step}/{TOTAL_STEPS})</span
        >
        <span
          >p90 {fmtVal(hoverRow.vals[4])} · p75 {fmtVal(hoverRow.vals[3])} · p50
          {fmtVal(hoverRow.vals[2])} · p25 {fmtVal(hoverRow.vals[1])} · p10
          {fmtVal(hoverRow.vals[0])}</span
        >
        {#if hoverRow.today !== null}
          <span class="cone-readout-today"
            >today {fmtVal(hoverRow.today)} ({fmtAdr(hoverRow.today)}×)</span
          >
        {/if}
      {:else}
        <span class="cone-muted">hover the chart for hourly percentiles</span>
      {/if}
    </div>

    <div class="cone-footer">
      {#if lowInNow !== null && highInNow !== null}
        <span class="cone-stat"
          >by now: low in {(lowInNow * 100).toFixed(0)}% · high in {(
            highInNow * 100
          ).toFixed(0)}% of matching days</span
        >
      {/if}
      {#if todayPath}
        <span class="cone-stat"
          >pivots: H p50 {fmtPx(px(combo.high_piv[0], 1))} · H p80 {fmtPx(
            px(combo.high_piv[1], 1)
          )} · L p50 {fmtPx(px(combo.low_piv[0], -1))} · L p80 {fmtPx(
            px(combo.low_piv[1], -1)
          )}</span
        >
      {/if}
      <span class="cone-muted"
        >median day: dip {fmtAdr(combo.mae_p[1])}× / peak {fmtAdr(
          combo.mfe_p[1]
        )}× ADR</span
      >
      {#if todayPath}
        <span class="cone-muted"
          >1× ADR ≈ {(todayPath.adr14_today * 100).toFixed(2)}%</span
        >
      {/if}
    </div>
  {:else}
    <div class="cone-empty cone-muted">insufficient data for this filter</div>
  {/if}
</div>

<style>
  .cone {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .cone-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    align-items: center;
  }
  .chip-group {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  .chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .chip.active {
    border-color: #60a5fa;
    color: #e5e7eb;
  }
  .n-badge {
    font-size: 0.75rem;
    color: #888;
  }
  .n-badge.thin {
    color: #f59e0b;
  }
  .cone-svg {
    width: 100%;
    height: auto;
  }
  .band-outer {
    fill: rgba(96, 165, 250, 0.1);
  }
  .band-inner {
    fill: rgba(96, 165, 250, 0.18);
  }
  .zero-line {
    stroke: #444;
    stroke-dasharray: 2 3;
  }
  .median-line {
    fill: none;
    stroke: #60a5fa;
    stroke-width: 1.5;
  }
  .today-line {
    fill: none;
    stroke: #f59e0b;
    stroke-width: 1.5;
    stroke-dasharray: 5 3;
  }
  .today-dot {
    fill: #f59e0b;
  }
  .axis-label {
    fill: #777;
    font-size: 10px;
  }
  .cone-footer {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1.2rem;
    font-size: 0.8rem;
  }
  .cone-stat {
    color: #cbd5e1;
  }
  .cone-muted {
    color: #777;
  }
  .cone-empty {
    padding: 2rem 0;
    text-align: center;
  }
  .cone-guide {
    stroke: #666;
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .cone-readout {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem 1rem;
    min-height: 2.4em;
    padding-top: 4px;
    font-size: 0.78rem;
    color: #bbb;
  }
  .cone-readout-hour {
    color: #e5e7eb;
    font-variant-numeric: tabular-nums;
  }
  .cone-readout-today {
    color: #60a5fa;
  }
</style>
