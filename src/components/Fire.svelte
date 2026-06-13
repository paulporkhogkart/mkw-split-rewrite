<script>
  // "On fire" overlay for a player card on PB pace. Two layers of fluid cartoon
  // flame, hued from the player's brand colour: a wide column BEHIND the figure
  // (crown raised into the visible zone above the head) and a short, stubby
  // U-wrap IN FRONT (screen-blended, low, arms climbing both borders). Both are
  // clipped to the portrait column. Metaball ellipses through an SVG goo filter;
  // one rAF loop, only while `active`. Shape constants were tuned live in the
  // brainstorm companion (see the on-fire design spec).
  import { onMount, onDestroy } from "svelte";

  export let color = "#888";   // the player's --pc
  export let active = false;   // fire lit (PB pace) — drives the rAF + fade

  const NS = "http://www.w3.org/2000/svg";
  const SPEED = 0.70;
  const rnd = Math.random().toString(36).slice(2, 8);
  const uidB = "fbgooB-" + rnd, uidF = "fbgooF-" + rnd;   // one goo filter per svg (valid, self-contained)

  let bkSvg, frSvg, glowEl;
  let blobs = [];
  let raf = 0, running = false, t = 0;

  function hexToHsl(hex) {
    hex = (hex || "#888888").replace("#", "");
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    const r = parseInt(hex.substr(0, 2), 16) / 255, g = parseInt(hex.substr(2, 2), 16) / 255, b = parseInt(hex.substr(4, 2), 16) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b); let h, s, l = (mx + mn) / 2;
    if (mx === mn) { h = s = 0; } else {
      const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60;
    }
    return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
  }
  const css = (o, a = 1) => `hsl(${o.h} ${o.s}% ${o.l}% / ${a})`;
  function palette(hex) {
    const { h, s, l } = hexToHsl(hex);
    return {
      outer: { h, s: Math.min(100, s + 14), l: Math.max(24, l - 20) },
      mid:   { h, s: Math.min(100, s + 8),  l: Math.max(44, l - 2) },
      inner: { h, s: Math.min(100, s),      l: Math.min(76, l + 16) },
      core:  { h, s: Math.max(20, s - 42),  l: Math.min(95, l + 38) },
    };
  }

  // BACK: wide column raised to the head line + flame licks on top.
  const BACK = (P) => [
    { color: P.outer, n: 13, rise: [0.5, 0.9],  r0: [12, 18], spread: 30, sway: 4, top: 0.15, stretch: 1.2, thin: 0.30, baseY: [70, 104] },
    { color: P.mid,   n: 13, rise: [0.55, 1.0], r0: [10, 15], spread: 27, sway: 4, top: 0.19, stretch: 1.3, thin: 0.32, baseY: [66, 100] },
    { color: P.inner, n: 9,  rise: [0.6, 1.0],  r0: [7, 11],  spread: 19, sway: 3, top: 0.27, stretch: 1.3, thin: 0.36, baseY: [60, 88] },
    { color: P.core,  n: 6,  rise: [0.6, 1.0],  r0: [5, 9],   spread: 13, sway: 3, top: 0.35, stretch: 1.3, thin: 0.38, baseY: [56, 80] },
    { color: P.outer, n: 6,  rise: [0.7, 1.1],  r0: [6, 10],  spread: 22, sway: 6, top: 0.11, stretch: 2.4, thin: 0.62, baseY: [44, 66] },
    { color: P.mid,   n: 5,  rise: [0.7, 1.1],  r0: [5, 8],   spread: 18, sway: 6, top: 0.09, stretch: 2.5, thin: 0.64, baseY: [42, 62] },
  ];
  // FRONT: wide low trough + bold arms pinned at (and clipping into) both borders.
  const FRONT = (P) => [
    { color: P.outer, n: 13, rise: [0.45, 0.8],  r0: [9, 13], spread: 27, sway: 2, top: 0.78, stretch: 0.7, thin: 0.18 },
    { color: P.inner, n: 10, rise: [0.5, 0.85],  r0: [6, 10], spread: 22, sway: 2, top: 0.80, stretch: 0.8, thin: 0.22 },
    { color: P.core,  n: 5,  rise: [0.5, 0.85],  r0: [4, 6],  spread: 16, sway: 2, top: 0.79, stretch: 0.9, thin: 0.26 },
    { color: P.outer, n: 7,  cx: 1,  rise: [0.5, 0.9],   r0: [7, 11], spread: 6, sway: 2, top: 0.60, stretch: 1.2, thin: 0.34 },
    { color: P.inner, n: 5,  cx: 2,  rise: [0.55, 0.95], r0: [4, 8],  spread: 5, sway: 2, top: 0.64, stretch: 1.3, thin: 0.38 },
    { color: P.outer, n: 7,  cx: 55, rise: [0.5, 0.9],   r0: [7, 11], spread: 6, sway: 2, top: 0.60, stretch: 1.2, thin: 0.34 },
    { color: P.inner, n: 5,  cx: 54, rise: [0.55, 0.95], r0: [4, 8],  spread: 5, sway: 2, top: 0.64, stretch: 1.3, thin: 0.38 },
  ];

  const H = 150, CX = 28;
  function resetBlob(b, L, scatter) {
    const lcx = L.cx != null ? L.cx : CX; b.lcx = lcx;
    b.base = lcx + (Math.random() * 2 - 1) * L.spread;
    b.vy = (L.rise[0] + Math.random() * (L.rise[1] - L.rise[0])) * SPEED;
    b.r0 = L.r0[0] + Math.random() * (L.r0[1] - L.r0[0]);
    b.phase = Math.random() * 6.28; b.sway = L.sway * (0.5 + Math.random() * 0.9);
    b.stretch = L.stretch; b.thin = L.thin; b.L = L; b.topY = H * L.top;
    const baseLo = L.baseY ? L.baseY[0] : H, baseHi = L.baseY ? L.baseY[1] : H + 8;
    b.y = scatter ? (b.topY + Math.random() * (baseHi - b.topY)) : baseLo + Math.random() * (baseHi - baseLo);
    b.spawnY = b.y;
  }
  function buildLayer(svg, layers, filterId) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("filter", `url(#${filterId})`);
    svg.appendChild(g);
    for (const L of layers) {
      for (let i = 0; i < L.n; i++) {
        const e = document.createElementNS(NS, "ellipse");
        e.setAttribute("fill", css(L.color));
        g.appendChild(e);
        const b = { el: e }; resetBlob(b, L, true); blobs.push(b);
      }
    }
  }
  function build() {
    const P = palette(color);
    blobs = [];
    if (bkSvg) { bkSvg.querySelectorAll("g").forEach((g) => g.remove()); buildLayer(bkSvg, BACK(P), uidB); }
    if (frSvg) { frSvg.querySelectorAll("g").forEach((g) => g.remove()); buildLayer(frSvg, FRONT(P), uidF); }
    if (glowEl) glowEl.style.background =
      `radial-gradient(68% 64% at 50% 70%, ${css(P.mid, 0.46)}, ${css(P.outer, 0.14)} 58%, transparent 80%)`;
  }
  function frame() {
    t += 0.016 * SPEED;
    for (const b of blobs) {
      b.y -= b.vy;
      const span = Math.max(6, b.spawnY - b.topY), pr = Math.min(1, Math.max(0, (b.spawnY - b.y) / span));
      const rx = Math.max(0, b.r0 * (1 - pr * b.thin)), ry = b.r0 * (0.7 + pr * b.stretch);
      const x = b.base + (b.lcx - b.base) * pr * 0.22 + Math.sin(t * 2.4 + b.phase) * b.sway * (0.3 + pr);
      if (b.y < b.topY || rx < 0.4) { resetBlob(b, b.L, false); continue; }
      b.el.setAttribute("cx", x.toFixed(1)); b.el.setAttribute("cy", b.y.toFixed(1));
      b.el.setAttribute("rx", rx.toFixed(1)); b.el.setAttribute("ry", ry.toFixed(1));
    }
    raf = requestAnimationFrame(frame);
  }
  function start() { if (running) return; running = true; raf = requestAnimationFrame(frame); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  onMount(() => { build(); if (active) start(); });
  onDestroy(stop);
  // Rebuild palette when the player colour changes; (re)start/stop with `active`.
  $: if (bkSvg && frSvg) { color; build(); }
  $: if (bkSvg && frSvg) { active ? start() : stop(); }
</script>

<div class="fb-back" class:on={active} aria-hidden="true">
  <div class="fb-glow" bind:this={glowEl}></div>
  <svg bind:this={bkSvg} class="fb-svg" viewBox="0 0 56 150" preserveAspectRatio="none">
    <defs>
      <filter id={uidB} x="-80%" y="-30%" width="260%" height="170%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="3.1" result="b" />
        <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
      </filter>
    </defs>
  </svg>
</div>
<div class="fb-front" class:on={active} aria-hidden="true">
  <svg bind:this={frSvg} class="fb-svg" viewBox="0 0 56 150" preserveAspectRatio="none">
    <defs>
      <filter id={uidF} x="-80%" y="-30%" width="260%" height="170%" color-interpolation-filters="sRGB">
        <feGaussianBlur stdDeviation="3.1" result="b" />
        <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
      </filter>
    </defs>
  </svg>
</div>

<style>
  /* The portrait column: spine (3px) + fig margin-left (2px) => x[5..61], width 56. */
  .fb-back, .fb-front {
    position: absolute; left: 5px; top: 0; width: 56px; height: 100%;
    overflow: hidden; pointer-events: none; opacity: 0; transition: opacity 0.45s ease;
  }
  .fb-back { z-index: 1; }
  .fb-front { z-index: 3; mix-blend-mode: screen; }
  .fb-back.on { opacity: 1; }
  .fb-front.on { opacity: 0.62; }
  .fb-glow {
    position: absolute; left: 0; top: 0; width: 56px; height: 100%;
    mix-blend-mode: screen; filter: blur(6px); animation: fb-flick 3.4s ease-in-out infinite;
  }
  @keyframes fb-flick { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
  .fb-svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
</style>
