<script lang="ts">
  import type { CurrentWeekPathResponse, WeeklyConeResponse, WeeklyConeComboResponse } from "../api";
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
    weeklyCone,
    currentWeekPath,
  }: {
    weeklyCone: WeeklyConeResponse | null;
    currentWeekPath: CurrentWeekPathResponse | null;
  } = $props();

  // "all" is never a selectable alternative — it is always the unconditional
  // reference band drawn underneath whichever conditional cone is selected.
  const DIRS: { key: "bull" | "bear"; label: string }[] = [
    { key: "bull", label: "Bull" },
    { key: "bear", label: "Bear" },
  ];

  const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  // True session boundaries (Mon occupies x(0)…x(7), Tue x(7)…x(14), …).
  // 0 and 35 are the chart edges and need no gridline.
  const DAY_BOUNDARIES = [7, 14, 21, 28];

  let dir = $state<"bull" | "bear">("bull");

  const totalWeeks = $derived(weeklyCone?.total_weeks ?? 0);
  const allCombo = $derived<WeeklyConeComboResponse | null>(weeklyCone?.combos.all ?? null);
  const combo = $derived<WeeklyConeComboResponse | null>(weeklyCone?.combos[dir] ?? null);
  const hasData = $derived(totalWeeks > 0 && !!combo && combo.n > 0);

  // ── SVG geometry (viewBox units) — same size as the daily cone ──
  // 35 steps = 5 RTH sessions × 7 hourly bars (Mon–Fri).
  const TOTAL_STEPS = 35;
  const geo: ConeGeometry = { W: 760, H: 280, PL: 46, PR: 30, PT: 12, PB: 26 };
  const { W, H, PL, PR, PT, PB } = geo;

  const yDomain = $derived.by<YDomain>(() => {
    const vals: number[] = [0];
    if (allCombo && allCombo.n > 0) for (const row of allCombo.bands) vals.push(row[0], row[4]);
    if (combo && combo.n > 0) for (const row of combo.bands) vals.push(row[0], row[4]);
    if (currentWeekPath) vals.push(...currentWeekPath.points);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 0.1);
    return { min: lo - pad, max: hi + pad };
  });

  const x = (step: number) => coneX(step, TOTAL_STEPS, geo);
  const y = (v: number) => coneY(v, yDomain, geo);

  // Reference band — the unconditional "all" population, subordinate to the
  // selected conditional cone (thinner, lower opacity, outline-only fill).
  const refBandD = $derived(allCombo ? bandPath(allCombo.bands, 0, 4, TOTAL_STEPS, yDomain, geo) : "");
  const refMedianD = $derived(allCombo ? linePath(allCombo.bands, 2, TOTAL_STEPS, yDomain, geo) : "");

  // Selected conditional cone (bull/bear), color-coded to match the rest of
  // the dashboard's win/loss vocabulary.
  const bandOuterD = $derived(combo ? bandPath(combo.bands, 0, 4, TOTAL_STEPS, yDomain, geo) : "");
  const bandInnerD = $derived(combo ? bandPath(combo.bands, 1, 3, TOTAL_STEPS, yDomain, geo) : "");
  const medianD = $derived(combo ? linePath(combo.bands, 2, TOTAL_STEPS, yDomain, geo) : "");

  const weekD = $derived(
    currentWeekPath ? overlayPath(currentWeekPath.points, TOTAL_STEPS, yDomain, geo) : ""
  );

  // Day label centers — one per session, at the midpoint of its 7-bar span.
  const dayTicks = DAY_LABELS.map((label, i) => ({
    center: i * 7 + 3.5,
    label,
  }));

  const fmtAwr = fmtNorm;
  const px = (mag: number, side: 1 | -1) =>
    toPrice(mag, side, currentWeekPath?.week_open ?? null, currentWeekPath?.awr14_current ?? null);
  const fmtPx = fmtPrice;

  const elapsed = $derived(currentWeekPath?.elapsed_h ?? 0);
  const lowInNow = $derived(
    hasData && elapsed >= 1 ? combo!.low_in_by[Math.min(elapsed, TOTAL_STEPS) - 1] : null
  );
  const highInNow = $derived(
    hasData && elapsed >= 1 ? combo!.high_in_by[Math.min(elapsed, TOTAL_STEPS) - 1] : null
  );

  let hoverStep = $state<number | null>(null);

  function onPointerMove(ev: PointerEvent): void {
    const svg = (ev.currentTarget as SVGGraphicsElement).ownerSVGElement;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    const vbX = ((ev.clientX - rect.left) / rect.width) * W;
    const raw = Math.round(((vbX - PL) / (W - PL - PR)) * TOTAL_STEPS);
    hoverStep = Math.max(0, Math.min(TOTAL_STEPS, raw));
  }

  // Bands are indexed 0…34 for steps 1…35; step 0 is the week open, where
  // every percentile is 0 by construction.
  const hoverRow = $derived.by(() => {
    if (hoverStep === null || !combo || combo.n === 0) return null;
    const step = hoverStep;
    const vals = step === 0 ? [0, 0, 0, 0, 0] : combo.bands[step - 1];
    if (!vals) return null;
    const week =
      currentWeekPath && step >= 1 && step <= currentWeekPath.points.length
        ? currentWeekPath.points[step - 1]
        : null;
    return { step, vals, week };
  });

  const fmtVal = (v: number): string => {
    const p = px(v, 1);
    return p === null ? fmtAwr(v) + "×" : fmtPx(p);
  };

  // Step → session day + ET bar-close label, e.g. "Wed 14:30 ET". Step 0 is
  // the Monday open (09:30 ET); step s ≥ 1 is the close of bar s. ET is
  // DST-stable relative to the session, matching the daily cone's axis.
  const BAR_CLOSE_ET = ["10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:00"];
  const hourLabel = (step: number): string => {
    if (step === 0) return "Mon 09:30 ET";
    const dayIdx = Math.floor((step - 1) / 7);
    return `${DAY_LABELS[dayIdx]} ${BAR_CLOSE_ET[(step - 1) % 7]} ET`;
  };
</script>

<div class="wcone">
  <div class="wcone-controls">
    <div class="wcone-chip-group">
      {#each DIRS as d}
        <button
          class="wcone-chip"
          class:active={dir === d.key}
          class:bull={d.key === "bull"}
          class:bear={d.key === "bear"}
          onclick={() => (dir = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    {#if hasData && combo}
      <span class="wcone-caption"
        >Paths of weeks that closed {dir} (n={combo.n}{combo.n < 30
          ? " ⚠ thin sample"
          : ""}) · gray reference = all {totalWeeks} weeks</span
      >
    {/if}
  </div>

  {#if hasData && combo}
    <svg viewBox="0 0 {W} {H}" class="wcone-svg" role="img" aria-label="Weekly path cone">
      {#if refBandD}
        <path d={refBandD} class="wcone-ref-band" />
      {/if}
      {#if refMedianD}
        <path d={refMedianD} class="wcone-ref-median" />
      {/if}
      <path d={bandOuterD} class="wcone-band-outer" class:bull={dir === "bull"} class:bear={dir === "bear"} />
      <path d={bandInnerD} class="wcone-band-inner" class:bull={dir === "bull"} class:bear={dir === "bear"} />
      <line x1={x(0)} y1={y(0)} x2={x(TOTAL_STEPS)} y2={y(0)} class="wcone-zero-line" />
      {#each DAY_BOUNDARIES as b}
        <line x1={x(b)} y1={PT} x2={x(b)} y2={H - PB} class="wcone-day-gridline" />
      {/each}
      <path d={medianD} class="wcone-median" class:bull={dir === "bull"} class:bear={dir === "bear"} />
      {#if weekD && currentWeekPath}
        <path d={weekD} class="wcone-week-line" />
        <circle
          cx={x(currentWeekPath.points.length)}
          cy={y(currentWeekPath.points[currentWeekPath.points.length - 1])}
          r="3.5"
          class="wcone-week-dot"
        />
      {/if}
      {#if hoverStep !== null}
        <line
          x1={x(hoverStep)}
          y1={PT}
          x2={x(hoverStep)}
          y2={H - PB}
          class="wcone-guide"
        />
      {/if}
      {#each dayTicks as t}
        <text x={x(t.center)} y={H - 8} class="wcone-axis-label" text-anchor="middle"
          >{t.label}</text
        >
      {/each}
      <text x={PL - 6} y={PT + 8} class="wcone-axis-label" text-anchor="end"
        >{fmtAwr(yDomain.max)}×</text
      >
      <text x={PL - 6} y={y(0) + 3} class="wcone-axis-label" text-anchor="end">0</text>
      <text x={PL - 6} y={H - PB} class="wcone-axis-label" text-anchor="end"
        >{fmtAwr(yDomain.min)}×</text
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

    <div class="wcone-readout">
      {#if hoverRow}
        <span class="wcone-readout-hour">{hourLabel(hoverRow.step)} (bar {hoverRow.step}/{TOTAL_STEPS})</span>
        <span
          >p90 {fmtVal(hoverRow.vals[4])} · p75 {fmtVal(hoverRow.vals[3])} · p50
          {fmtVal(hoverRow.vals[2])} · p25 {fmtVal(hoverRow.vals[1])} · p10
          {fmtVal(hoverRow.vals[0])}</span
        >
        {#if hoverRow.week !== null}
          <span class="wcone-readout-week"
            >this week {fmtVal(hoverRow.week)} ({fmtAwr(hoverRow.week)}×)</span
          >
        {/if}
      {:else}
        <span class="wcone-muted">hover the chart for hourly percentiles</span>
      {/if}
    </div>

    <div class="wcone-footer">
      {#if lowInNow !== null && highInNow !== null}
        <span class="wcone-stat"
          >by now: low in {(lowInNow * 100).toFixed(0)}% · high in {(
            highInNow * 100
          ).toFixed(0)}% of {dir} weeks</span
        >
      {/if}
      {#if currentWeekPath}
        <span class="wcone-stat"
          >pivots: H p50 {fmtPx(px(combo.high_piv[0], 1))} · H p80 {fmtPx(
            px(combo.high_piv[1], 1)
          )} · L p50 {fmtPx(px(combo.low_piv[0], -1))} · L p80 {fmtPx(
            px(combo.low_piv[1], -1)
          )}</span
        >
      {/if}
      <span class="wcone-muted"
        >median week: dip {fmtAwr(combo.mae_p[1])}× / peak {fmtAwr(
          combo.mfe_p[1]
        )}× AWR</span
      >
      {#if currentWeekPath}
        <span class="wcone-muted"
          >1× AWR ≈ {(currentWeekPath.awr14_current * 100).toFixed(2)}%</span
        >
      {/if}
    </div>
  {:else}
    <div class="wcone-empty wcone-muted">not enough complete weeks yet</div>
  {/if}
</div>

<style>
  .wcone {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .wcone-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    align-items: center;
  }
  .wcone-chip-group {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  .wcone-chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .wcone-chip.active.bull {
    border-color: var(--green);
    color: var(--green);
  }
  .wcone-chip.active.bear {
    border-color: var(--red);
    color: var(--red);
  }
  .wcone-caption {
    font-size: 0.75rem;
    color: #888;
  }
  .wcone-svg {
    width: 100%;
    height: auto;
  }
  .wcone-ref-band {
    fill: color-mix(in srgb, var(--muted) 12%, transparent);
    stroke: color-mix(in srgb, var(--muted) 45%, transparent);
    stroke-width: 1;
  }
  .wcone-ref-median {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1;
    stroke-dasharray: 3 2;
  }
  .wcone-band-outer.bull {
    fill: color-mix(in srgb, var(--green) 14%, transparent);
  }
  .wcone-band-outer.bear {
    fill: color-mix(in srgb, var(--red) 14%, transparent);
  }
  .wcone-band-inner.bull {
    fill: color-mix(in srgb, var(--green) 26%, transparent);
  }
  .wcone-band-inner.bear {
    fill: color-mix(in srgb, var(--red) 26%, transparent);
  }
  .wcone-zero-line {
    stroke: #444;
    stroke-dasharray: 2 3;
  }
  .wcone-day-gridline {
    stroke: color-mix(in srgb, var(--muted) 55%, transparent);
    stroke-dasharray: 1 3;
  }
  .wcone-median {
    fill: none;
    stroke-width: 1.5;
  }
  .wcone-median.bull {
    stroke: var(--green);
  }
  .wcone-median.bear {
    stroke: var(--red);
  }
  .wcone-week-line {
    fill: none;
    stroke: #f59e0b;
    stroke-width: 1.5;
    stroke-dasharray: 5 3;
  }
  .wcone-week-dot {
    fill: #f59e0b;
  }
  .wcone-axis-label {
    fill: #777;
    font-size: 10px;
  }
  .wcone-footer {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1.2rem;
    font-size: 0.8rem;
  }
  .wcone-stat {
    color: #cbd5e1;
  }
  .wcone-muted {
    color: #777;
  }
  .wcone-empty {
    padding: 2rem 0;
    text-align: center;
  }
  .wcone-guide {
    stroke: #666;
    stroke-width: 1;
    stroke-dasharray: 3 3;
  }
  .wcone-readout {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem 1rem;
    min-height: 2.4em;
    padding-top: 4px;
    font-size: 0.78rem;
    color: #bbb;
  }
  .wcone-readout-hour {
    color: #e5e7eb;
    font-variant-numeric: tabular-nums;
  }
  .wcone-readout-week {
    color: #f59e0b;
  }
</style>
