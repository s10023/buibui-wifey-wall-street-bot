/**
 * Shared path-cone SVG math — pure functions, no Svelte/DOM.
 *
 * Extracted from PathCone.svelte (the daily cone) so the weekly cone
 * (WeeklyCone.svelte) can reuse the same geometry/formatting instead of
 * forking it. The daily cone passes totalSteps=7 (hourly RTH bars); the
 * weekly cone passes its own step count. Everything else — viewBox
 * geometry, percentile band/line construction, normalized-value
 * formatting — is period-agnostic.
 */

export interface ConeGeometry {
  W: number;
  H: number;
  PL: number;
  PR: number;
  PT: number;
  PB: number;
}

export interface YDomain {
  min: number;
  max: number;
}

/** Step index (0…totalSteps) → viewBox x. `totalSteps` is 7 daily. */
export function coneX(step: number, totalSteps: number, g: ConeGeometry): number {
  return g.PL + (step / totalSteps) * (g.W - g.PL - g.PR);
}

/** Value (×ADR or ×AWR) → viewBox y. */
export function coneY(v: number, dom: YDomain, g: ConeGeometry): number {
  return g.PT + ((dom.max - v) / (dom.max - dom.min)) * (g.H - g.PT - g.PB);
}

/** Filled polygon between percentile columns loIdx/hiIdx (0=p10 … 4=p90). */
export function bandPath(
  bands: number[][],
  loIdx: number,
  hiIdx: number,
  totalSteps: number,
  dom: YDomain,
  g: ConeGeometry
): string {
  if (bands.length === 0) return "";
  const pts = [`M ${coneX(0, totalSteps, g)} ${coneY(0, dom, g)}`];
  bands.forEach((row, i) =>
    pts.push(`L ${coneX(i + 1, totalSteps, g)} ${coneY(row[hiIdx], dom, g)}`)
  );
  for (let i = bands.length - 1; i >= 0; i--)
    pts.push(`L ${coneX(i + 1, totalSteps, g)} ${coneY(bands[i][loIdx], dom, g)}`);
  pts.push("Z");
  return pts.join(" ");
}

/** Single percentile line. */
export function linePath(
  bands: number[][],
  idx: number,
  totalSteps: number,
  dom: YDomain,
  g: ConeGeometry
): string {
  if (bands.length === 0) return "";
  return [
    `M ${coneX(0, totalSteps, g)} ${coneY(0, dom, g)}`,
    ...bands.map((row, i) => `L ${coneX(i + 1, totalSteps, g)} ${coneY(row[idx], dom, g)}`),
  ].join(" ");
}

/** Overlay path for a partial period's normalized points. */
export function overlayPath(
  points: number[],
  totalSteps: number,
  dom: YDomain,
  g: ConeGeometry
): string {
  if (points.length === 0) return "";
  return [
    `M ${coneX(0, totalSteps, g)} ${coneY(0, dom, g)}`,
    ...points.map((v, i) => `L ${coneX(i + 1, totalSteps, g)} ${coneY(v, dom, g)}`),
  ].join(" ");
}

/**
 * Normalized magnitude → absolute price. `open` and `normalizer` are the
 * period's open and its ADR14 (daily) or AWR14 (weekly).
 */
export function toPrice(
  mag: number,
  side: 1 | -1,
  open: number | null,
  normalizer: number | null
): number | null {
  if (open === null || normalizer === null) return null;
  return open * (1 + side * mag * normalizer);
}

/** Signed ×ADR/×AWR label, e.g. "+0.42". */
export function fmtNorm(v: number): string {
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

/**
 * Price label. Decimals scale with magnitude: 0 decimals ≥1000, 2 decimals
 * ≥1, 4 decimals below 1 — so a mid-priced stock's hover readout doesn't
 * spell out four decimals five times in one line.
 */
export function fmtPrice(p: number | null): string {
  if (p === null) return "—";
  const abs = Math.abs(p);
  if (abs >= 1000) return p.toFixed(0);
  if (abs >= 1) return p.toFixed(2);
  return p.toFixed(4);
}
