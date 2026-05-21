<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    ColorType,
    createChart,
    type CandlestickData,
    type HistogramData,
    type IChartApi,
    type ISeriesApi,
    type LineData,
    type SeriesMarker,
    type Time,
  } from "lightweight-charts";
  import type { CandleRow, SignalRow, ZonesResponse } from "../api";

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
  };

  function stratLabel(name: string): string {
    return STRATEGY_LABELS[name] ?? name;
  }

  let {
    candles,
    signals,
    symbol,
    timeframe,
    showEMA20 = false,
    showEMA50 = false,
    showEMA200 = false,
    showRSI = false,
    showRangeLevels = false,
    showCMEGaps = false,
    zones = null,
    showFVG = false,
    showOB = false,
    showEQHEQL = false,
    showBOS = false,
    showFibZone = false,
    showOTE = false,
    showSwings = false,
  }: {
    candles: CandleRow[];
    signals: SignalRow[];
    symbol: string;
    timeframe: string;
    showEMA20?: boolean;
    showEMA50?: boolean;
    showEMA200?: boolean;
    showRSI?: boolean;
    showRangeLevels?: boolean;
    showCMEGaps?: boolean;
    zones?: ZonesResponse | null;
    showFVG?: boolean;
    showOB?: boolean;
    showEQHEQL?: boolean;
    showBOS?: boolean;
    showFibZone?: boolean;
    showOTE?: boolean;
    showSwings?: boolean;
  } = $props();

  let container: HTMLDivElement;
  let chart: IChartApi;
  let candleSeries: ISeriesApi<"Candlestick">;
  let volumeSeries: ISeriesApi<"Histogram">;
  // EMA series
  let ema20Series: ISeriesApi<"Line"> | null = null;
  let ema50Series: ISeriesApi<"Line"> | null = null;
  let ema200Series: ISeriesApi<"Line"> | null = null;

  // RSI series
  let rsiSeries: ISeriesApi<"Line"> | null = null;

  // Range level series (C11)
  let rangeSeries: ISeriesApi<"Line">[] = [];
  let rangeLabelContainer: HTMLDivElement;
  let rangeLabelPrices: number[] = [];
  let rangeLabelEndTimes: number[] = [];

  // CME gap overlay
  let cmeGapContainer: HTMLDivElement;
  let cmeGapDiv: HTMLDivElement | null = null;
  let cmeGapData: CMEGap | null = null;

  // Structural zone overlay (C6)
  let zonesContainer: HTMLDivElement;
  let zoneBoxDivs: HTMLDivElement[] = [];
  interface ZoneBoxData { low: number; high: number; startSec: number; endSec: number; }
  let zoneBoxData: ZoneBoxData[] = [];
  let zoneLineSeries: ISeriesApi<"Line">[] = [];
  let zoneSwingDots: HTMLDivElement[] = [];
  let zoneSwingPrices: number[] = [];
  let zoneSwingTimes: number[] = [];

  // ── Indicator computation ─────────────────────────────────────────────────────

  function computeEMA(closes: number[], period: number): number[] {
    const result: number[] = [];
    if (closes.length === 0) return result;
    const k = 2 / (period + 1);
    let ema = closes[0];
    for (let i = 0; i < closes.length; i++) {
      if (i < period - 1) {
        // Not enough data yet — fill with NaN so we can skip
        result.push(NaN);
        // Accumulate SMA for seed
        if (i === 0) {
          ema = closes[0];
        } else {
          ema = ema + closes[i]; // sum accumulator
        }
      } else if (i === period - 1) {
        // Seed with SMA
        ema = (ema + closes[i]) / period;
        result.push(ema);
      } else {
        ema = closes[i] * k + ema * (1 - k);
        result.push(ema);
      }
    }
    return result;
  }

  function computeRSI(closes: number[], period: number): number[] {
    const result: number[] = [];
    if (closes.length < period + 1) return result;
    for (let i = 0; i < period; i++) result.push(NaN);

    let avgGain = 0;
    let avgLoss = 0;
    for (let i = 1; i <= period; i++) {
      const diff = closes[i] - closes[i - 1];
      if (diff >= 0) avgGain += diff;
      else avgLoss -= diff;
    }
    avgGain /= period;
    avgLoss /= period;

    const rsi = (g: number, l: number) => (l === 0 ? 100 : l === 0 && g === 0 ? 50 : 100 - 100 / (1 + g / l));
    result.push(rsi(avgGain, avgLoss));

    for (let i = period + 1; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1];
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      result.push(rsi(avgGain, avgLoss));
    }
    return result;
  }

  // ── Range levels (C11) ───────────────────────────────────────────────────────

  interface RangeLevel {
    label: string;
    price: number;
    originTimeSec: number;
    color: string;
  }

  function computeRangeLevels(data: CandleRow[]): RangeLevel[] {
    if (data.length < 2) return [];

    const DAY = 86400;
    const nowSec = Date.now() / 1000;

    // All boundaries align to UTC midnight — Binance candles open on UTC boundaries.
    // (Monday 00:00 UTC = Monday 08:00 MYT, daily candle opens 08:00 MYT, etc.)
    const todayUTC = Math.floor(nowSec / DAY) * DAY;
    const ydayUTC  = todayUTC - DAY;

    // Monday 00:00 UTC of this week
    const utcDow = new Date(nowSec * 1000).getUTCDay(); // 0=Sun, 1=Mon
    const daysSinceMon = (utcDow + 6) % 7;
    const thisMonUTC = todayUTC - daysSinceMon * DAY;
    const lastMonUTC = thisMonUTC - 7 * DAY;

    // 1st of month 00:00 UTC
    const utcNow = new Date(nowSec * 1000);
    const monthUTC = Date.UTC(utcNow.getUTCFullYear(), utcNow.getUTCMonth(), 1) / 1000;

    const between = (c: CandleRow, s: number, e: number) => {
      const t = c.open_time / 1000;
      return t >= s && t < e;
    };

    const levels: RangeLevel[] = [];

    // Monthly Open — first candle of this month (1st 00:00 UTC = 08:00 MYT)
    const monthCandles = data.filter(c => c.open_time / 1000 >= monthUTC);
    if (monthCandles.length > 0) {
      const c = monthCandles[0];
      levels.push({ label: "MO", price: c.open, originTimeSec: c.open_time / 1000, color: "#f0883e" });
    }

    // Daily Open — first candle of today (00:00 UTC = 08:00 MYT)
    const todayCandles = data.filter(c => between(c, todayUTC, todayUTC + DAY));
    if (todayCandles.length > 0) {
      const c = todayCandles[0];
      levels.push({ label: "DO", price: c.open, originTimeSec: c.open_time / 1000, color: "#bc8cff" });
    }

    // PDH / PDL — previous day
    const ydayCandles = data.filter(c => between(c, ydayUTC, todayUTC));
    if (ydayCandles.length > 0) {
      const pdhC = ydayCandles.reduce((a, b) => b.high > a.high ? b : a);
      const pdlC = ydayCandles.reduce((a, b) => b.low  < a.low  ? b : a);
      levels.push({ label: "PDH", price: pdhC.high, originTimeSec: pdhC.open_time / 1000, color: "#56d364" });
      levels.push({ label: "PDL", price: pdlC.low,  originTimeSec: pdlC.open_time / 1000, color: "#f85149" });
    }

    // Weekly Open — first candle on or after Monday 00:00 UTC (= Mon 08:00 MYT)
    const thisWeekCandles = data.filter(c => between(c, thisMonUTC, thisMonUTC + 7 * DAY));
    if (thisWeekCandles.length > 0) {
      const c = thisWeekCandles[0];
      levels.push({ label: "WO", price: c.open, originTimeSec: c.open_time / 1000, color: "#58a6ff" });
    }

    // Monday H / Monday L — all candles on Monday UTC (show all week including Monday)
    const monCandles = data.filter(c => between(c, thisMonUTC, thisMonUTC + DAY));
    if (monCandles.length > 0) {
      const mhC = monCandles.reduce((a, b) => b.high > a.high ? b : a);
      const mlC = monCandles.reduce((a, b) => b.low  < a.low  ? b : a);
      levels.push({ label: "Mon H", price: mhC.high, originTimeSec: mhC.open_time / 1000, color: "#e3b341" });
      levels.push({ label: "Mon L", price: mlC.low,  originTimeSec: mlC.open_time / 1000, color: "#e3b341" });
    }

    // PWH / PWL — previous week
    const lastWeekCandles = data.filter(c => between(c, lastMonUTC, thisMonUTC));
    if (lastWeekCandles.length > 0) {
      const pwhC = lastWeekCandles.reduce((a, b) => b.high > a.high ? b : a);
      const pwlC = lastWeekCandles.reduce((a, b) => b.low  < a.low  ? b : a);
      levels.push({ label: "PWH", price: pwhC.high, originTimeSec: pwhC.open_time / 1000, color: "rgba(86, 211, 100, 0.55)" });
      levels.push({ label: "PWL", price: pwlC.low,  originTimeSec: pwlC.open_time / 1000, color: "rgba(248, 81, 73, 0.55)" });
    }

    return levels;
  }

  function updateRangeLabelPositions(): void {
    if (!chart || !candleSeries || rangeLabelPrices.length === 0) return;
    const labels = rangeLabelContainer?.querySelectorAll<HTMLSpanElement>(".range-label");
    if (!labels) return;
    const overlayWidth = rangeLabelContainer.clientWidth;
    labels.forEach((el, i) => {
      const y = candleSeries.priceToCoordinate(rangeLabelPrices[i]);
      if (y === null) { el.style.display = "none"; return; }
      const rawX = chart.timeScale().timeToCoordinate(rangeLabelEndTimes[i] as Time);
      const x = rawX !== null ? Math.min(rawX, overlayWidth - 4) : overlayWidth - 4;
      el.style.display = "block";
      el.style.top    = `${y - 8}px`;
      el.style.left   = `${x - el.offsetWidth - 4}px`;
    });
  }

  function drawRangeLines(): void {
    clearRangeLines();
    if (!chart || candles.length < 2) return;

    const last = candles[candles.length - 1];
    const prev = candles[candles.length - 2];
    const intervalMs = last.open_time - prev.open_time;
    const endTimeSec = (last.open_time + 200 * intervalMs) / 1000;

    rangeLabelPrices = [];
    rangeLabelEndTimes = [];

    for (const { label, price, originTimeSec, color } of computeRangeLevels(candles)) {
      const series = chart.addLineSeries({
        color,
        lineWidth: 1,
        priceScaleId: "right",
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title: "",
      });
      series.setData([
        { time: originTimeSec as Time, value: price },
        { time: endTimeSec    as Time, value: price },
      ]);
      rangeSeries.push(series);
      rangeLabelPrices.push(price);
      rangeLabelEndTimes.push(endTimeSec);

      const el = document.createElement("span");
      el.className = "range-label";
      el.textContent = label;
      el.style.color = color;
      rangeLabelContainer.appendChild(el);
    }

    requestAnimationFrame(updateRangeLabelPositions);
    chart.timeScale().subscribeVisibleLogicalRangeChange(updateRangeLabelPositions);
  }

  function clearRangeLines(): void {
    for (const s of rangeSeries) {
      try { chart.removeSeries(s); } catch { /* already removed */ }
    }
    rangeSeries = [];
    rangeLabelPrices = [];
    rangeLabelEndTimes = [];
    rangeLabelContainer?.replaceChildren();
    chart?.timeScale().unsubscribeVisibleLogicalRangeChange(updateRangeLabelPositions);
  }

  // ── CME gap overlay ──────────────────────────────────────────────────────────

  interface CMEGap {
    startSec: number;        // fridayCandle.open_time/1000 — aligns to actual candle for reliable timeToCoordinate
    endSec: number;          // mondayCandle.open_time/1000 (or nowSec if partial)
    displayEndSec: number;   // extended right edge for chart visualisation
    top: number;             // max(fridayClose, mondayOpen)
    bottom: number;          // min(fridayClose, mondayOpen)
    gapUp: boolean;
    partial: boolean;
  }

  function computeCMEGap(data: CandleRow[]): CMEGap | null {
    if (data.length < 2) return null;

    const nowSec = Date.now() / 1000;
    const DAY = 86400;
    const WEEK = 7 * DAY;

    // Most recent Friday 21:00 UTC (CME close = Sat 05:00 MYT)
    const utcDow = new Date(nowSec * 1000).getUTCDay(); // 0=Sun
    const todayUTC = Math.floor(nowSec / DAY) * DAY;
    const daysSinceFri = (utcDow + 7 - 5) % 7; // Fri=0, Sat=1, Sun=2, Mon=3…
    const lastFriUTC = todayUTC - daysSinceFri * DAY;
    let cmeCloseSec = lastFriUTC + 21 * 3600;
    if (cmeCloseSec > nowSec) cmeCloseSec -= WEEK; // haven't hit this Friday yet

    const cmeOpenSec = cmeCloseSec + 49 * 3600; // +49h = Sun 22:00 UTC (Mon 06:00 MYT)

    // Last candle whose open_time is before CME close → Friday close price
    const fridayCandle = [...data].reverse().find(c => c.open_time / 1000 < cmeCloseSec);
    if (!fridayCandle) return null;

    // First candle at/after CME open → Monday open price
    const mondayCandle = data.find(c => c.open_time / 1000 >= cmeOpenSec);

    const fridayClose = fridayCandle.close;

    if (!mondayCandle) {
      // Currently inside CME closure window — show band up to now
      return {
        startSec: fridayCandle.open_time / 1000,
        endSec: Math.min(nowSec, cmeOpenSec),
        displayEndSec: 0,
        top: fridayClose,
        bottom: fridayClose,
        gapUp: false,
        partial: true,
      };
    }

    const mondayOpen = mondayCandle.open;
    return {
      startSec: fridayCandle.open_time / 1000,
      endSec: mondayCandle.open_time / 1000,
      displayEndSec: 0,
      top: Math.max(fridayClose, mondayOpen),
      bottom: Math.min(fridayClose, mondayOpen),
      gapUp: mondayOpen > fridayClose,
      partial: false,
    };
  }

  function updateCMEGapPosition(): void {
    if (!cmeGapDiv || !chart || !candleSeries) return;
    const gap = cmeGapData;
    if (!gap) return;

    const containerWidth = cmeGapContainer.clientWidth;
    const rawX1 = chart.timeScale().timeToCoordinate(gap.startSec as Time);
    // Start off-screen left → don't render; box must begin at origin candle
    if (rawX1 === null) { cmeGapDiv.style.display = "none"; return; }
    const rawX2 = chart.timeScale().timeToCoordinate(gap.displayEndSec as Time);
    // End off-screen right → clamp to container edge
    const clampedX1 = rawX1;
    const clampedX2 = Math.min(containerWidth, rawX2 ?? containerWidth);
    if (clampedX2 <= clampedX1) {
      cmeGapDiv.style.display = "none";
      return;
    }

    let y1: number, y2: number;
    if (gap.partial || gap.top === gap.bottom) {
      // Just a vertical band — full height of chart (500px)
      y1 = 0;
      y2 = 500;
    } else {
      const py1 = candleSeries.priceToCoordinate(gap.top);
      const py2 = candleSeries.priceToCoordinate(gap.bottom);
      if (py1 === null || py2 === null) { cmeGapDiv.style.display = "none"; return; }
      y1 = Math.min(py1, py2);
      y2 = Math.max(py1, py2);
    }

    const minHeight = 2;
    const boxHeight = Math.max(minHeight, y2 - y1);

    cmeGapDiv.style.display  = "block";
    cmeGapDiv.style.left     = `${clampedX1}px`;
    cmeGapDiv.style.width    = `${clampedX2 - clampedX1}px`;
    cmeGapDiv.style.top      = `${y1}px`;
    cmeGapDiv.style.height   = `${boxHeight}px`;
  }

  function drawCMEGap(): void {
    clearCMEGap();
    if (!chart || !cmeGapContainer) return;

    const gap = computeCMEGap(candles);
    if (!gap) return;

    // Extend right edge past the gap window so it stays visible against recent candles
    const last = candles[candles.length - 1];
    const prev = candles[candles.length - 2];
    const intervalMs = last.open_time - prev.open_time;
    gap.displayEndSec = Math.max(gap.endSec, (last.open_time + 200 * intervalMs) / 1000);

    const div = document.createElement("div");
    div.className = gap.partial
      ? "cme-gap-box cme-gap-partial"
      : gap.gapUp ? "cme-gap-box cme-gap-up" : "cme-gap-box cme-gap-down";

    cmeGapData = gap;

    // Label inside box
    const label = document.createElement("span");
    label.className = "cme-gap-label";
    if (gap.partial) {
      label.textContent = "CME closed";
    } else {
      const pct = gap.top !== 0
        ? (((gap.gapUp ? gap.top : gap.bottom) - (gap.gapUp ? gap.bottom : gap.top)) / (gap.gapUp ? gap.bottom : gap.top) * 100).toFixed(2)
        : "0.00";
      label.textContent = `CME Gap ${gap.gapUp ? "▲" : "▼"} ${pct}%`;
    }
    div.appendChild(label);
    cmeGapContainer.appendChild(div);
    cmeGapDiv = div;

    requestAnimationFrame(updateCMEGapPosition);
    chart.timeScale().subscribeVisibleLogicalRangeChange(updateCMEGapPosition);
  }

  function clearCMEGap(): void {
    if (cmeGapDiv) {
      cmeGapDiv.remove();
      cmeGapDiv = null;
    }
    cmeGapData = null;
    chart?.timeScale().unsubscribeVisibleLogicalRangeChange(updateCMEGapPosition);
    cmeGapContainer?.replaceChildren();
  }

  // ── Structural zone overlay (C6) ─────────────────────────────────────────────

  // Visual config per zone type+direction
  const ZONE_STYLES: Record<string, { bg: string; border: string; labelColor: string; labelText: string }> = {
    fvg_bull:      { bg: "rgba(86,211,100,0.10)",  border: "rgba(86,211,100,0.40)",  labelColor: "rgba(86,211,100,0.75)",  labelText: "FVG" },
    fvg_bear:      { bg: "rgba(248,81,73,0.10)",   border: "rgba(248,81,73,0.40)",   labelColor: "rgba(248,81,73,0.75)",   labelText: "FVG" },
    ob_bull:       { bg: "rgba(86,211,100,0.08)",  border: "rgba(86,211,100,0.50)",  labelColor: "rgba(86,211,100,0.75)",  labelText: "OB" },
    ob_bear:       { bg: "rgba(248,81,73,0.08)",   border: "rgba(248,81,73,0.50)",   labelColor: "rgba(248,81,73,0.75)",   labelText: "OB" },
    fib_zone_bull: { bg: "rgba(227,179,65,0.09)",  border: "rgba(227,179,65,0.45)",  labelColor: "rgba(227,179,65,0.75)",  labelText: "0.5–0.618" },
    fib_zone_bear: { bg: "rgba(227,179,65,0.09)",  border: "rgba(227,179,65,0.45)",  labelColor: "rgba(227,179,65,0.75)",  labelText: "0.5–0.618" },
    ote_bull:      { bg: "rgba(240,136,62,0.09)",  border: "rgba(240,136,62,0.45)",  labelColor: "rgba(240,136,62,0.75)",  labelText: "OTE" },
    ote_bear:      { bg: "rgba(240,136,62,0.09)",  border: "rgba(240,136,62,0.45)",  labelColor: "rgba(240,136,62,0.75)",  labelText: "OTE" },
  };

  function updateZoneBoxPositions(): void {
    if (!chart || !candleSeries || zoneBoxDivs.length === 0) return;
    const containerWidth = zonesContainer.clientWidth;
    zoneBoxDivs.forEach((div, i) => {
      const { low, high, startSec, endSec } = zoneBoxData[i];
      const rawX1 = chart.timeScale().timeToCoordinate(startSec as Time);
      if (rawX1 === null) { div.style.display = "none"; return; }
      const rawX2 = chart.timeScale().timeToCoordinate(endSec as Time);
      const clampedX2 = Math.min(containerWidth, rawX2 ?? containerWidth);
      if (clampedX2 <= rawX1) { div.style.display = "none"; return; }
      const py1 = candleSeries.priceToCoordinate(high);
      const py2 = candleSeries.priceToCoordinate(low);
      if (py1 === null || py2 === null) { div.style.display = "none"; return; }
      const y1 = Math.min(py1, py2);
      const y2 = Math.max(py1, py2);
      div.style.display = "block";
      div.style.left    = `${rawX1}px`;
      div.style.width   = `${clampedX2 - rawX1}px`;
      div.style.top     = `${y1}px`;
      div.style.height  = `${Math.max(2, y2 - y1)}px`;
    });
  }

  function updateZoneSwingPositions(): void {
    if (!chart || !candleSeries || zoneSwingDots.length === 0) return;
    const containerWidth = zonesContainer.clientWidth;
    zoneSwingDots.forEach((dot, i) => {
      const x = chart.timeScale().timeToCoordinate(zoneSwingTimes[i] as Time);
      const y = candleSeries.priceToCoordinate(zoneSwingPrices[i]);
      if (x === null || y === null || x < 0 || x > containerWidth) { dot.style.display = "none"; return; }
      dot.style.display = "block";
      dot.style.left = `${x - 3}px`;
      dot.style.top  = `${y - 3}px`;
    });
  }

  function updateZonePositions(): void {
    updateZoneBoxPositions();
    updateZoneSwingPositions();
  }

  function drawZones(): void {
    clearZones();
    if (!chart || !zonesContainer || !zones || candles.length < 2) return;

    const last = candles[candles.length - 1];
    const prev = candles[candles.length - 2];
    const intervalMs = last.open_time - prev.open_time;
    const rightEdgeSec = (last.open_time + 200 * intervalMs) / 1000; // for lines only
    const lastSec = last.open_time / 1000; // box active zones end here

    // ── Box zones ──────────────────────────────────────────────────────────────
    const activeBoxTypes = new Set<string>();
    if (showFVG)     activeBoxTypes.add("fvg");
    if (showOB)      activeBoxTypes.add("ob");
    if (showFibZone) activeBoxTypes.add("fib_zone");
    if (showOTE)     activeBoxTypes.add("ote");

    for (const box of zones.boxes) {
      if (!activeBoxTypes.has(box.zone_type)) continue;
      const styleKey = `${box.zone_type}_${box.direction}`;
      const s = ZONE_STYLES[styleKey] ?? ZONE_STYLES["fvg_bull"];
      const opacity = box.active ? 1 : 0.35;

      const div = document.createElement("div");
      div.className = "zone-box";
      div.style.cssText = `background:${s.bg};border:1px solid ${s.border};opacity:${opacity};`;

      const lbl = document.createElement("span");
      lbl.className = "zone-box-label";
      lbl.style.color = s.labelColor;
      lbl.textContent = s.labelText;
      div.appendChild(lbl);

      zonesContainer.appendChild(div);
      zoneBoxDivs.push(div);
      // Active: end at last candle. Inactive: end at fill/mitigation candle.
      const boxEndSec = box.active ? lastSec : (box.close_ms ? box.close_ms / 1000 : lastSec);
      zoneBoxData.push({ low: box.zone_low, high: box.zone_high, startSec: box.start_ms / 1000, endSec: boxEndSec });
    }

    // ── Line zones (EQH/EQL/BOS) — line series from start_ms to right edge ──────
    for (const line of zones.lines) {
      const isEQH = line.zone_type === "eqh" || line.zone_type === "eql";
      const isBOS = line.zone_type === "bos";
      if (isEQH && !showEQHEQL) continue;
      if (isBOS && !showBOS) continue;
      if (!isEQH && !isBOS) continue;

      const color = line.direction === "bull"
        ? (isBOS ? "#56d36460" : "#56d36488")
        : (isBOS ? "#f8514960" : "#f8514988");
      const lineStyle = isBOS ? 3 : 2; // dotted for BOS, dashed for EQH/EQL
      // Active → extends to right edge; inactive → ends at the break/sweep candle
      const lineEndSec = line.active
        ? rightEdgeSec
        : (line.close_ms ? line.close_ms / 1000 : lastSec);
      const opacity = line.active ? 1 : 0.4;

      const s = chart.addLineSeries({
        color,
        lineWidth: 1,
        lineStyle,
        priceScaleId: "right",
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        title: "",
      });
      s.applyOptions({ visible: opacity > 0.5 || !line.active });
      s.setData([
        { time: (line.start_ms / 1000) as Time, value: line.price },
        { time: lineEndSec as Time, value: line.price },
      ]);
      // Mute inactive via alpha on the series color directly
      if (!line.active) {
        s.applyOptions({ color: color.replace(/[\d.]+\)$/, "0.35)") });
      }
      zoneLineSeries.push(s);
    }

    // ── Swing dots ─────────────────────────────────────────────────────────────
    if (showSwings) {
      for (const pt of zones.swings) {
        const dot = document.createElement("div");
        dot.className = pt.swing_type === "high" ? "swing-dot swing-dot-high" : "swing-dot swing-dot-low";
        zonesContainer.appendChild(dot);
        zoneSwingDots.push(dot);
        zoneSwingPrices.push(pt.price);
        zoneSwingTimes.push(pt.time_ms / 1000);
      }
    }

    requestAnimationFrame(updateZonePositions);
    chart.timeScale().subscribeVisibleLogicalRangeChange(updateZonePositions);
  }

  function clearZones(): void {
    zonesContainer?.replaceChildren();
    zoneBoxDivs = [];
    zoneBoxData = [];
    for (const s of zoneLineSeries) {
      try { chart?.removeSeries(s); } catch { /* already removed */ }
    }
    zoneLineSeries = [];
    zoneSwingDots = [];
    zoneSwingPrices = [];
    zoneSwingTimes = [];
    chart?.timeScale().unsubscribeVisibleLogicalRangeChange(updateZonePositions);
  }

  // ── Chart init ───────────────────────────────────────────────────────────────

  onMount(() => {
    chart = createChart(container, {
      width: container.clientWidth,
      height: 500,
      layout: {
        background: { type: ColorType.Solid, color: "#0d1117" },
        textColor: "#c9d1d9",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: {
        vertLine: { color: "#58a6ff44", labelBackgroundColor: "#161b22" },
        horzLine: { color: "#58a6ff44", labelBackgroundColor: "#161b22" },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#30363d" },
      localization: {
        timeFormatter: (t: number) => {
          // t is UTC seconds from lightweight-charts; shift to MYT (UTC+8)
          const d = new Date((t + 28800) * 1000);
          const day = d.getUTCDate().toString().padStart(2, "0");
          const mon = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][d.getUTCMonth()];
          const h   = d.getUTCHours().toString().padStart(2, "0");
          const m   = d.getUTCMinutes().toString().padStart(2, "0");
          return `${day} ${mon} ${h}:${m}`;
        },
      },
      rightPriceScale: { borderColor: "#30363d" },
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
      priceScaleId: "right",
    });

    volumeSeries = chart.addHistogramSeries({
      color: "#30363d",
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // EMA overlays — on main price scale
    ema20Series = chart.addLineSeries({
      color: "#f0883e",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA20",
    });

    ema50Series = chart.addLineSeries({
      color: "#58a6ff",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA50",
    });

    ema200Series = chart.addLineSeries({
      color: "#bc8cff",
      lineWidth: 1,
      priceScaleId: "right",
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      title: "EMA200",
    });

    // RSI sub-panel
    rsiSeries = chart.addLineSeries({
      color: "#e3b341",
      lineWidth: 1,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      priceScaleId: "rsi",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("rsi").applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const ro = new ResizeObserver(() =>
      chart.applyOptions({ width: container.clientWidth })
    );
    ro.observe(container);
    return () => ro.disconnect();
  });

  // ── Candle data effect ────────────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    const data: CandlestickData[] = candles.map((c) => ({
      time: (c.open_time / 1000) as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    candleSeries.setData(data);

    const volData: HistogramData[] = candles.map((c) => ({
      time: (c.open_time / 1000) as Time,
      value: c.volume,
      color: c.close >= c.open ? "#3fb95044" : "#f8514944",
    }));
    volumeSeries.setData(volData);
  });

  // ── Signal markers effect ─────────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    const markers: SeriesMarker<Time>[] = signals
      .map((s) => ({
        time: (s.open_time / 1000) as Time,
        position:
          s.direction === "long"
            ? ("belowBar" as const)
            : ("aboveBar" as const),
        color: s.direction === "long" ? "#3fb950" : "#f85149",
        shape:
          s.direction === "long"
            ? ("arrowUp" as const)
            : ("arrowDown" as const),
        text: `${stratLabel(s.strategy)} (${s.direction})`,
        size: 1,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);
  });

  // ── EMA effects ───────────────────────────────────────────────────────────────

  $effect(() => {
    if (!ema20Series) return;
    if (!showEMA20 || candles.length === 0) {
      ema20Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 20);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema20Series.setData(data);
  });

  $effect(() => {
    if (!ema50Series) return;
    if (!showEMA50 || candles.length === 0) {
      ema50Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 50);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema50Series.setData(data);
  });

  $effect(() => {
    if (!ema200Series) return;
    if (!showEMA200 || candles.length === 0) {
      ema200Series.setData([]);
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeEMA(closes, 200);
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => !isNaN(d.value));
    ema200Series.setData(data);
  });

  // ── RSI effect ────────────────────────────────────────────────────────────────

  $effect(() => {
    if (!rsiSeries) return;
    if (!showRSI || candles.length === 0) {
      rsiSeries.setData([]);
      chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      return;
    }
    const closes = candles.map((c) => c.close);
    const values = computeRSI(closes, 14);
    // values array starts at index 0 of closes; first 14 entries are NaN (warm-up)
    const data: LineData[] = candles
      .map((c, i) => ({ time: (c.open_time / 1000) as Time, value: values[i] }))
      .filter((d) => d.value !== undefined && !isNaN(d.value));
    rsiSeries.setData(data);
    // Give RSI panel a visible height
    chart.priceScale("rsi").applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
  });

  // ── Range levels effect (C11) ────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    void candles;
    if (showRangeLevels) {
      drawRangeLines();
    } else {
      clearRangeLines();
    }
  });

  // ── CME gap effect ───────────────────────────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    void candles;
    if (showCMEGaps) {
      drawCMEGap();
    } else {
      clearCMEGap();
    }
  });

  // ── Structural zone overlay effect (C6) ──────────────────────────────────────

  $effect(() => {
    if (!candleSeries) return;
    void zones;
    void candles;
    const anyActive =
      showFVG || showOB || showEQHEQL || showBOS || showFibZone || showOTE || showSwings;
    if (anyActive && zones) {
      drawZones();
    } else {
      clearZones();
    }
  });

  onDestroy(() => {
    chart?.remove();
  });
</script>

<div class="chart-wrap">
  <div bind:this={container} class="chart-container"></div>
  <div bind:this={cmeGapContainer} class="cme-gap-overlay"></div>
  <div bind:this={zonesContainer} class="zones-overlay"></div>
  <div bind:this={rangeLabelContainer} class="range-label-overlay"></div>
  <img src="/buibui-logo.svg" alt="buibui" class="chart-logo" />
</div>

<style>
  .chart-wrap { position: relative; width: 100%; }
  .chart-container { width: 100%; }

  .range-label-overlay {
    position: absolute;
    top: 0;
    left: 0;
    width: calc(100% - 70px);
    height: 100%;
    pointer-events: none;
    overflow: hidden;
    z-index: 5;
  }

  :global(.range-label) {
    position: absolute;
    font-size: 9px;
    font-family: monospace;
    letter-spacing: 0.04em;
    white-space: nowrap;
    background: rgba(13, 17, 23, 0.72);
    padding: 1px 4px;
    border-radius: 2px;
  }

  .chart-logo {
    position: absolute;
    bottom: 10px;
    left: 14px;
    height: 34px;
    opacity: 0.75;
    pointer-events: none;
    z-index: 10;
  }
  /* Hide the injected TradingView attribution */
  :global(.chart-container a) {
    display: none !important;
  }

  .cme-gap-overlay {
    position: absolute;
    top: 0;
    left: 0;
    width: calc(100% - 70px);
    height: 100%;
    pointer-events: none;
    overflow: hidden;
    z-index: 3;
  }

  :global(.cme-gap-box) {
    position: absolute;
    pointer-events: none;
  }

  :global(.cme-gap-up) {
    background: rgba(86, 211, 100, 0.08);
    border-top: 1px solid rgba(86, 211, 100, 0.35);
    border-bottom: 1px solid rgba(86, 211, 100, 0.35);
    border-left: 1px solid rgba(86, 211, 100, 0.20);
    border-right: 1px solid rgba(86, 211, 100, 0.20);
  }

  :global(.cme-gap-down) {
    background: rgba(248, 81, 73, 0.08);
    border-top: 1px solid rgba(248, 81, 73, 0.35);
    border-bottom: 1px solid rgba(248, 81, 73, 0.35);
    border-left: 1px solid rgba(248, 81, 73, 0.20);
    border-right: 1px solid rgba(248, 81, 73, 0.20);
  }

  :global(.cme-gap-partial) {
    background: rgba(88, 166, 255, 0.05);
    border-left: 1px solid rgba(88, 166, 255, 0.25);
    border-right: 1px dashed rgba(88, 166, 255, 0.20);
  }

  :global(.cme-gap-label) {
    position: absolute;
    top: 4px;
    left: 4px;
    font-size: 9px;
    font-family: monospace;
    letter-spacing: 0.04em;
    white-space: nowrap;
    color: rgba(201, 209, 217, 0.55);
  }

  /* ── Structural zone overlay (C6) ── */
  .zones-overlay {
    position: absolute;
    top: 0;
    left: 0;
    width: calc(100% - 70px);
    height: 100%;
    pointer-events: none;
    overflow: hidden;
    z-index: 2;
  }

  :global(.zone-box) {
    position: absolute;
    pointer-events: none;
    border-radius: 1px;
    min-height: 2px;
  }

  :global(.zone-box-label) {
    position: absolute;
    top: 2px;
    left: 4px;
    font-size: 8px;
    font-family: monospace;
    letter-spacing: 0.05em;
    white-space: nowrap;
    font-weight: 600;
  }

  :global(.swing-dot) {
    position: absolute;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    pointer-events: none;
  }

  :global(.swing-dot-high) {
    background: rgba(240, 136, 62, 0.8);
    border: 1px solid rgba(240, 136, 62, 0.5);
  }

  :global(.swing-dot-low) {
    background: rgba(86, 211, 100, 0.8);
    border: 1px solid rgba(86, 211, 100, 0.5);
  }
</style>
