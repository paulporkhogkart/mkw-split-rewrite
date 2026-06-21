<script>
  // Wide "on-fire" overlay that wraps the whole wordmark (navbar hover). Reuses the card Fire's
  // engine — metaball ellipses through an SVG goo filter, a 4-stop hue ramp off the player's
  // colour, one rAF loop while `active` — but with a WIDE layout authored for the logo box:
  //   BACK  : a curtain rising from behind across the full width, crowns licking up over the letters.
  //   FRONT : a low trough across the bottom + arms climbing the left & right edges (screen-blended).
  // Sibling to components/Fire.svelte (left untouched) so the player card can't regress.
  import { onMount, onDestroy } from "svelte";

  export let color = "#888";
  export let active = false;

  const NS = "http://www.w3.org/2000/svg";
  const SPEED = 0.70;
  const rnd = Math.random().toString(36).slice(2, 8);
  const uidB = "wfgooB-" + rnd, uidF = "wfgooF-" + rnd;

  // viewBox (preserveAspectRatio=none -> matches the render box aspect, set in CSS to avoid distortion).
  const W = 260, H = 116;
  const EDGE = 10;              // x of the left arm; right arm mirrors at W-EDGE

  let bkSvg, frSvg, glowEl, blobs = [], raf = 0, running = false, t = 0;

  function hexToHsl(hex) {
    hex = (hex || "#888888").replace("#", "");
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    const r = parseInt(hex.substr(0,2),16)/255, g = parseInt(hex.substr(2,2),16)/255, b = parseInt(hex.substr(4,2),16)/255;
    const mx = Math.max(r,g,b), mn = Math.min(r,g,b); let h, s, l = (mx+mn)/2;
    if (mx===mn){ h=s=0; } else { const d=mx-mn; s=l>0.5?d/(2-mx-mn):d/(mx+mn);
      h = mx===r ? (g-b)/d+(g<b?6:0) : mx===g ? (b-r)/d+2 : (r-g)/d+4; h*=60; }
    return { h: Math.round(h), s: Math.round(s*100), l: Math.round(l*100) };
  }
  const css = (o, a = 1) => `hsl(${o.h} ${o.s}% ${o.l}% / ${a})`;
  function palette(hex) {
    const { h, s, l } = hexToHsl(hex);
    return {
      outer: { h, s: Math.min(100, s+14), l: Math.max(24, l-20) },
      mid:   { h, s: Math.min(100, s+8),  l: Math.max(44, l-2) },
      inner: { h, s: Math.min(100, s),    l: Math.min(76, l+16) },
      core:  { h, s: Math.max(20, s-42),  l: Math.min(95, l+38) },
    };
  }

  // Layers. `xRange` = spawn base-x uniformly across that span (full-width curtain / trough);
  // otherwise `cx` ± `spread` (the edge arms). baseY/top are fractions of H.
  const span = [EDGE, W - EDGE];
  // Tongues, not a cloud: lower counts (gaps show between flames), tall + narrow (high stretch /
  // high thin), and the bright inner/core ride near the top of each tongue. Tuned live.
  const BACK = (P) => [
    { color: P.outer, n: 20, xRange: span, rise:[0.55,0.95], r0:[8,12], spread:0, sway:5, top:0.06, stretch:1.9, thin:0.50, baseY:[0.64,0.94] },
    { color: P.mid,   n: 15, xRange: span, rise:[0.6,1.05],  r0:[6,9],  spread:0, sway:5, top:0.12, stretch:2.0, thin:0.54, baseY:[0.58,0.86] },
    { color: P.inner, n: 11, xRange: span, rise:[0.65,1.1],  r0:[4,7],  spread:0, sway:4, top:0.20, stretch:2.0, thin:0.58, baseY:[0.52,0.78] },
    { color: P.core,  n: 7,  xRange: span, rise:[0.65,1.1],  r0:[3,5],  spread:0, sway:4, top:0.28, stretch:2.0, thin:0.60, baseY:[0.48,0.70] },
    { color: P.inner, n: 10, xRange: span, rise:[0.8,1.25],  r0:[3,6],  spread:0, sway:7, top:0.00, stretch:3.2, thin:0.74, baseY:[0.34,0.56] },
  ];
  const FRONT = (P) => [
    // low trough across the bottom (short stubs)
    { color: P.outer, n: 18, xRange: span, rise:[0.5,0.85], r0:[6,9], spread:0, sway:2, top:0.64, stretch:1.0, thin:0.28, baseY:[0.84,1.0] },
    { color: P.core,  n: 11, xRange: span, rise:[0.55,0.9], r0:[3,5], spread:0, sway:2, top:0.70, stretch:1.1, thin:0.34, baseY:[0.84,1.0] },
    // left + right arms, pinned at the edges, climbing tall
    { color: P.outer, n: 8, cx: EDGE,     rise:[0.55,1.0], r0:[5,8], spread:5, sway:2, top:0.24, stretch:1.9, thin:0.46, baseY:[0.72,1.0] },
    { color: P.core,  n: 5, cx: EDGE,     rise:[0.6,1.1],  r0:[3,5], spread:4, sway:2, top:0.30, stretch:2.0, thin:0.50, baseY:[0.72,1.0] },
    { color: P.outer, n: 8, cx: W - EDGE, rise:[0.55,1.0], r0:[5,8], spread:5, sway:2, top:0.24, stretch:1.9, thin:0.46, baseY:[0.72,1.0] },
    { color: P.core,  n: 5, cx: W - EDGE, rise:[0.6,1.1],  r0:[3,5], spread:4, sway:2, top:0.30, stretch:2.0, thin:0.50, baseY:[0.72,1.0] },
  ];

  function resetBlob(b, L, scatter) {
    const lcx = L.cx != null ? L.cx : (L.xRange[0] + L.xRange[1]) / 2; b.lcx = lcx;
    b.base = L.xRange ? (L.xRange[0] + Math.random() * (L.xRange[1] - L.xRange[0]))
                      : lcx + (Math.random() * 2 - 1) * L.spread;
    b.vy = (L.rise[0] + Math.random() * (L.rise[1] - L.rise[0])) * SPEED;
    b.r0 = L.r0[0] + Math.random() * (L.r0[1] - L.r0[0]);
    b.phase = Math.random() * 6.28; b.sway = L.sway * (0.5 + Math.random() * 0.9);
    b.stretch = L.stretch; b.thin = L.thin; b.L = L; b.topY = H * L.top;
    const baseLo = H * L.baseY[0], baseHi = H * L.baseY[1];
    b.y = scatter ? (b.topY + Math.random() * (baseHi - b.topY)) : baseLo + Math.random() * (baseHi - baseLo);
    b.spawnY = b.y;
  }
  function buildLayer(svg, layers, filterId) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("filter", `url(#${filterId})`); svg.appendChild(g);
    for (const L of layers) for (let i = 0; i < L.n; i++) {
      const e = document.createElementNS(NS, "ellipse"); e.setAttribute("fill", css(L.color)); g.appendChild(e);
      const b = { el: e }; resetBlob(b, L, true); blobs.push(b);
    }
  }
  function build() {
    const P = palette(color); blobs = [];
    if (bkSvg) { bkSvg.querySelectorAll("g").forEach((g) => g.remove()); buildLayer(bkSvg, BACK(P), uidB); }
    if (frSvg) { frSvg.querySelectorAll("g").forEach((g) => g.remove()); buildLayer(frSvg, FRONT(P), uidF); }
    if (glowEl) glowEl.style.background =
      `radial-gradient(75% 80% at 50% 78%, ${css(P.mid, 0.42)}, ${css(P.outer, 0.13)} 60%, transparent 82%)`;
  }
  function frame() {
    t += 0.016 * SPEED;
    for (const b of blobs) {
      b.y -= b.vy;
      const sp = Math.max(6, b.spawnY - b.topY), pr = Math.min(1, Math.max(0, (b.spawnY - b.y) / sp));
      const rx = Math.max(0, b.r0 * (1 - pr * b.thin)), ry = b.r0 * (0.7 + pr * b.stretch);
      const x = b.base + (b.lcx - b.base) * pr * 0.18 + Math.sin(t * 2.4 + b.phase) * b.sway * (0.3 + pr);
      if (b.y < b.topY || rx < 0.4) { resetBlob(b, b.L, false); continue; }
      b.el.setAttribute("cx", x.toFixed(1)); b.el.setAttribute("cy", b.y.toFixed(1));
      b.el.setAttribute("rx", rx.toFixed(1)); b.el.setAttribute("ry", ry.toFixed(1));
    }
    raf = requestAnimationFrame(frame);
  }
  function start() { if (running) return; running = true; raf = requestAnimationFrame(frame); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  onMount(() => { build(); if (active && !reduced) start(); });
  onDestroy(stop);
  $: if (bkSvg && frSvg) { color; build(); }
  $: if (bkSvg && frSvg && !reduced) { active ? start() : stop(); }
</script>

<div class="wf-back" class:on={active} aria-hidden="true">
  <div class="wf-glow" bind:this={glowEl}></div>
  <svg bind:this={bkSvg} class="wf-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    <defs>
      <filter id={uidB} x="-20%" y="-30%" width="140%" height="170%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="3.0" result="b" />
        <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
      </filter>
    </defs>
  </svg>
</div>
<div class="wf-front" class:on={active} aria-hidden="true">
  <svg bind:this={frSvg} class="wf-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    <defs>
      <filter id={uidF} x="-20%" y="-30%" width="140%" height="170%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="3.0" result="b" />
        <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
      </filter>
    </defs>
  </svg>
</div>

<style>
  /* The fire box covers the wordmark + extends above/sides for the licks. Inset is tuned in the
     consumer via custom props so the box aspect matches the viewBox (no metaball distortion). */
  .wf-back, .wf-front {
    position: absolute;
    left: var(--wf-x, -14px); right: var(--wf-x, -14px);
    top: var(--wf-top, -34px); bottom: var(--wf-bottom, -8px);
    pointer-events: none; opacity: 0;
    transition: opacity 0.1s ease;   /* fade OUT — fast, so un-hover feels instant */
  }
  .wf-back { z-index: 0; }
  .wf-front { z-index: 3; mix-blend-mode: screen; }
  .wf-back.on  { opacity: 1;   transition: opacity 0.22s ease; }   /* ignite — a touch slower */
  .wf-front.on { opacity: 0.6; transition: opacity 0.22s ease; }
  .wf-glow {
    position: absolute; inset: 0; mix-blend-mode: screen; filter: blur(7px);
    animation: wf-flick 3.4s ease-in-out infinite;
  }
  @keyframes wf-flick { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
  .wf-svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  @media (prefers-reduced-motion: reduce) {
    .wf-back.on, .wf-front.on { opacity: 0.5; }
  }
</style>
