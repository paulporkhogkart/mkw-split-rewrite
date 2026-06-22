<script>
  import { onMount } from "svelte";
  import { territoryTimelineUrl } from "./lib/api.js";
  import { manifestUrl } from "./lib/map.js";
  import { heatRows } from "./lib/heat.js";
  import { E0 as E0_LOCKED, K as K_LOCKED } from "./lib/fireModel.js";

  // Geometry, ported from tools/fire-model-explorer.html.
  const LEADX = 8.0; // x-axis span: lead over #2, % of WR
  const OFFY = 20.0; // y-axis span: how far off the WR, %
  const GUT = 172; // right gutter (px) for the course-label column
  const H = 440; // plot height (px)
  const COLS = 24,
    ROWS = 20;

  let rows = []; // heatRows(): one entry per qualifying course (locked metrics)
  let loaded = false,
    error = false;
  let sceneW = 720; // measured plot width (bind:clientWidth)

  // Live, slider-driven tuning knobs, initialised to the LOCKED model so the first paint matches
  // the territory map's flames; dragging is a local what-if (never touches the map or the model).
  let E0 = E0_LOCKED;
  let K = K_LOCKED;

  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const bar = (off) => E0 * Math.exp(off / K); // slider-driven fire bar at a given off%

  // Warm -> hot colour ramp for the lit region of the heatmap.
  const STOPS = [
    [0, [20, 22, 26]],
    [0.25, [64, 22, 16]],
    [0.45, [150, 52, 20]],
    [0.65, [222, 110, 30]],
    [0.85, [245, 182, 44]],
    [1, [253, 230, 162]],
  ];
  function ramp(d) {
    d = clamp01(d);
    for (let i = 1; i < STOPS.length; i++) {
      if (d <= STOPS[i][0]) {
        const d0 = STOPS[i - 1][0],
          c0 = STOPS[i - 1][1],
          d1 = STOPS[i][0],
          c1 = STOPS[i][1],
          t = (d - d0) / (d1 - d0);
        return `rgb(${Math.round(lerp(c0[0], c1[0], t))},${Math.round(lerp(c0[1], c1[1], t))},${Math.round(lerp(c0[2], c1[2], t))})`;
      }
    }
    return "rgb(253,230,162)";
  }
  const fmt = (ms) => {
    const s = ms / 1000,
      m = Math.floor(s / 60),
      r = s - m * 60;
    return `${m}:${r < 10 ? "0" : ""}${r.toFixed(3)}`;
  };

  // Heatmap cells: lit where lead clears the bar at that row's off%.
  $: cells = (() => {
    const out = [];
    for (let r = 0; r < ROWS; r++) {
      const off = (OFFY * (r + 0.5)) / ROWS,
        b = bar(off);
      for (let c = 0; c < COLS; c++) {
        const lead = (LEADX * (c + 0.5)) / COLS;
        out.push(lead >= b ? ramp(0.35 + 0.65 * Math.exp(-off / K)) : null);
      }
    }
    return out;
  })();

  $: cw = Math.max(80, sceneW - GUT); // plot width minus the label gutter

  // Per-course plotted points; `lit` is recomputed from the LIVE sliders (not the locked row.fire).
  $: pts = rows.map((r) => ({
    ...r,
    lit: r.leadPct >= bar(r.offPct),
    x: clamp01(r.leadPct / LEADX) * cw,
    y: clamp01(r.offPct / OFFY) * H,
  }));

  // Label column: stack top-down, push each down to avoid overlap, lift back if it overflows the
  // bottom (ported from the explorer's de-collision).
  $: labels = (() => {
    const ls = pts.map((p) => ({ ...p })).sort((a, b) => a.y - b.y);
    const minG = 14;
    let last = -1e9;
    ls.forEach((s) => {
      s.ly = Math.max(s.y, last + minG);
      last = s.ly;
    });
    const over = last - (H - 6);
    if (over > 0) ls.forEach((s) => (s.ly -= over));
    return ls;
  })();

  // The exponential bar as an SVG polyline across the plot.
  $: barPts = (() => {
    const p = [];
    for (let o = 0; o <= OFFY; o += 0.4) {
      p.push(`${clamp01(bar(o) / LEADX) * cw},${(o / OFFY) * H}`);
      if (bar(o) > LEADX) break;
    }
    return p.join(" ");
  })();

  $: lit = pts.filter((p) => p.lit).sort((a, b) => a.offPct - b.offPct);

  // Floor readout: the lead a rival must beat (at WR pace) on the shortest vs longest WR.
  $: floor = (() => {
    if (!rows.length) return null;
    const wrSorted = rows.map((r) => r.wr).sort((a, b) => a - b);
    const sec = (wr) => ((E0 / 100) * wr / 1000).toFixed(2);
    return { lo: sec(wrSorted[0]), hi: sec(wrSorted[wrSorted.length - 1]) };
  })();

  onMount(async () => {
    try {
      const [mf, tl] = await Promise.all([
        fetch(manifestUrl(), { cache: "no-store" }).then((r) => {
          if (!r.ok) throw new Error(`manifest ${r.status}`);
          return r.json();
        }),
        fetch(territoryTimelineUrl(150)).then((r) => {
          if (!r.ok) throw new Error(`timeline ${r.status}`);
          return r.json();
        }),
      ]);
      const { events, colors, wrHistory } = tl;
      rows = heatRows({ courses: mf.courses, events: events || [], wrHistory: wrHistory || {}, colors: colors || {}, t: Infinity });
      loaded = true;
    } catch (e) {
      console.error("heat graph load failed", e);
      error = true;
    }
  });
</script>

<section class="heat">
  <h2>"on fire" model — live</h2>
  <p class="sub">
    A course burns while its leader's margin over #2 clears the exponential bar
    <b>fireBar(off) = E₀ · e^(off/K)</b> (lead &amp; bar in % of WR). Live Season PBs vs current WRs.
  </p>

  <div class="knobs">
    <label
      >floor E₀ <b>{E0.toFixed(2)}</b>% of WR
      <input type="range" min="0.05" max="1.0" step="0.05" bind:value={E0} /></label
    >
    <label
      >steepness K <b>{K.toFixed(1)}</b>
      <input type="range" min="3" max="10" step="0.5" bind:value={K} /></label
    >
    <button class="reset" on:click={() => { E0 = E0_LOCKED; K = K_LOCKED; }}>reset to locked</button>
  </div>

  {#if error}
    <p class="msg">Couldn't load live data.</p>
  {:else if !loaded}
    <p class="msg">Loading…</p>
  {:else if !rows.length}
    <p class="msg">No courses have a #1, a #2, and a current WR yet.</p>
  {:else}
    <div class="chartrow">
      <div class="yax"><b>≈ WR pace</b><span>off the WR ↓</span><b>{OFFY}% off</b></div>
      <div class="scene" bind:clientWidth={sceneW}>
        <div class="chart" style="right:{GUT}px">
          <div class="cells" style="grid-template-columns:repeat({COLS},1fr);grid-template-rows:repeat({ROWS},1fr)">
            {#each cells as c}<div style={c ? `background:${c}` : "background:#15171b;opacity:.5"}></div>{/each}
          </div>
        </div>
        <svg class="svg" viewBox="0 0 {sceneW} {H}" preserveAspectRatio="none">
          <polyline points={barPts} fill="none" stroke="#f5b62c" stroke-width="2" opacity="0.9" />
          {#each labels as s}
            <line x1={s.x} y1={s.y} x2={cw + 6} y2={s.ly} stroke={s.lit ? "#7a4a1e" : "#26292f"} stroke-width="1" />
          {/each}
        </svg>
        {#each pts as s}
          <div
            class="dot"
            class:fire={s.lit}
            style="left:{s.x}px;top:{s.y}px;background:{s.color}"
            title="{s.name} — {s.leader}, lead {((s.t2 - s.t1) / 1000).toFixed(2)}s ({s.leadPct.toFixed(2)}% WR), {s.offPct.toFixed(1)}% off WR{s.lit ? '  ON FIRE' : ''}"
          ></div>
        {/each}
        {#each labels as s}
          <div class="lab" class:fire={s.lit} style="left:{cw + 10}px;top:{s.ly}px">
            {s.name} <span class="d">{s.offPct.toFixed(1)}% off</span>
          </div>
        {/each}
      </div>
    </div>
    <div class="xax" style="padding-right:{GUT}px"><span>0%</span><span>2%</span><span>4%</span><span>6%</span><span>8%</span></div>
    <div class="xtitle" style="padding-right:{GUT}px">lead over #2, as % of WR →</div>

    {#if floor}
      <div class="floor">
        At WR pace, a rival must get within <b>{E0.toFixed(2)}% of WR</b> to snuff —
        <span class="mono">{floor.lo}s</span> on the shortest track, <span class="mono">{floor.hi}s</span> on the longest.
      </div>
    {/if}

    <div class="firelist">
      <div class="hd">{lit.length} on fire — sorted by closeness to WR:</div>
      {#if !lit.length}<div class="frow dim">Nothing lit.</div>{/if}
      {#each lit as s}
        <div class="frow">
          🔥 <b>{s.name}</b> — {s.leader} <span class="mono">{fmt(s.t1)}</span>
          ({s.offPct.toFixed(1)}% off), leads <span class="mono">{((s.t2 - s.t1) / 1000).toFixed(2)}s</span>. Snuffed only if a rival is
          within <span class="mono out">{((bar(s.offPct) / 100) * s.wr / 1000).toFixed(2)}s</span>
          (under <span class="mono out">{fmt(s.t1 + (bar(s.offPct) / 100) * s.wr)}</span>).
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .heat {
    max-width: 1040px;
    margin: 0 auto;
    padding: 22px 24px;
    color: #c7ccd2;
    font-family: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h2 {
    color: #e8eaed;
    font-size: 18px;
    margin: 0 0 4px;
  }
  .sub {
    color: #8a8f98;
    font-size: 13px;
    margin: 0 0 12px;
    max-width: 760px;
  }
  .sub b {
    color: #cfd3d8;
    font-weight: 600;
  }
  .knobs {
    display: flex;
    gap: 22px;
    flex-wrap: wrap;
    align-items: flex-end;
    margin-bottom: 14px;
    font-size: 11.5px;
  }
  .knobs label {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 220px;
    color: #8a8f98;
  }
  .knobs b {
    color: #f5b62c;
  }
  .knobs input {
    width: 100%;
    accent-color: #3d7cc2;
  }
  .reset {
    background: #16181d;
    color: #aeb4bc;
    border: 1px solid #2a2e35;
    border-radius: 4px;
    padding: 5px 9px;
    font-size: 11px;
    cursor: pointer;
  }
  .reset:hover {
    color: #f3f4f6;
    border-color: #3a3f48;
  }
  .msg {
    color: #8a8f98;
    font-size: 13px;
    padding: 24px 0;
  }
  .chartrow {
    display: flex;
    gap: 10px;
    align-items: stretch;
  }
  .yax {
    width: 62px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    text-align: right;
    font-size: 10px;
    color: #7a818b;
    padding: 1px 0;
    line-height: 1.2;
  }
  .yax b {
    color: #cfd3d8;
    font-weight: 600;
  }
  .scene {
    position: relative;
    flex: 1 1 auto;
    min-width: 0;
    height: 440px;
  }
  .chart {
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    border: 1px solid #23262b;
    border-radius: 5px;
    overflow: hidden;
  }
  .cells {
    position: absolute;
    inset: 0;
    display: grid;
  }
  .cells > div {
    min-width: 0;
  }
  .svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
  .dot {
    position: absolute;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    transform: translate(-50%, -50%);
    border: 1.5px solid #0b0c0e;
    box-shadow: 0 0 0 1px #000;
    z-index: 4;
  }
  .dot.fire {
    box-shadow: 0 0 0 2px #fff, 0 0 11px 3px #f5912c;
    z-index: 6;
  }
  .lab {
    position: absolute;
    transform: translateY(-50%);
    font-size: 9.5px;
    white-space: nowrap;
    color: #aeb4bc;
    z-index: 5;
  }
  .lab.fire {
    color: #ffce8a;
    font-weight: 600;
  }
  .lab .d {
    color: #6f7782;
    font-variant-numeric: tabular-nums;
  }
  .xax {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: #7a818b;
    margin-top: 5px;
    padding-left: 72px;
  }
  .xtitle {
    text-align: center;
    font-size: 10.5px;
    color: #8a8f98;
    margin-top: 2px;
    padding-left: 72px;
  }
  .floor {
    margin-top: 12px;
    font-size: 12px;
    color: #cdd3da;
    background: #0e1014;
    border: 1px solid #23262b;
    border-radius: 5px;
    padding: 8px 11px;
  }
  .floor b {
    color: #f5b62c;
  }
  .mono {
    font-family: ui-monospace, Menlo, monospace;
    font-variant-numeric: tabular-nums;
  }
  .firelist {
    margin-top: 13px;
    font-size: 12px;
    line-height: 1.7;
  }
  .firelist .hd {
    color: #f7a13a;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .frow b {
    color: #fff;
  }
  .frow .out {
    color: #f7a13a;
  }
  .frow.dim {
    color: #7a818b;
  }
</style>
