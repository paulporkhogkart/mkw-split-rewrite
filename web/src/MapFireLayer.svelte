<script>
  // Shared-rAF "on fire" flames for dominated course icons on the territory map. ONE rAF loop
  // animates compact metaball flame columns for ALL on-fire courses (reusing the card/logo Fire
  // technique: ellipses through an SVG goo filter, hued per leader colour). Rendered as TWO passes
  // by the map: a BACK pass behind the icon (a wide rising column) and a FRONT pass in front of it
  // (a low trough + arms climbing the icon's sides, screen-blended) so the icon sits INSIDE the
  // fire. Self-contained sibling of Fire.svelte / WordmarkFire.svelte (own inline colour helpers,
  // by that established pattern). Shape/density tuned live. Binary on/off via Svelte fade;
  // honours reduced-motion.
  import { onDestroy, tick } from "svelte";
  import { fade } from "svelte/transition";

  export let courses = [];   // [{ slug, hit:{x,y,w,h}, color }] - the on-fire set
  export let front = false;  // false = back column (behind icon); true = front wrap (over icon)

  const NS = "http://www.w3.org/2000/svg";
  const SPEED = 0.7;
  const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const uid = "mfgoo-" + Math.random().toString(36).slice(2, 8);

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

  const VW = 56, VH = 96, CX = VW / 2;

  // BACK: a wide column rising behind the icon, broad tongues filling the box.
  const BACK = (P) => [
    { color: P.outer, n: 16, rise: [0.5, 0.95],  r0: [10, 16], spread: 24, sway: 5, top: 0.10, stretch: 1.9, thin: 0.46, baseY: [0.70, 1.00] },
    { color: P.mid,   n: 13, rise: [0.55, 1.0],  r0: [8, 13],  spread: 20, sway: 5, top: 0.16, stretch: 2.0, thin: 0.50, baseY: [0.64, 0.94] },
    { color: P.inner, n: 10, rise: [0.6, 1.05],  r0: [6, 10],  spread: 15, sway: 4, top: 0.24, stretch: 2.0, thin: 0.54, baseY: [0.58, 0.86] },
    { color: P.core,  n: 7,  rise: [0.6, 1.05],  r0: [4, 7],   spread: 10, sway: 4, top: 0.32, stretch: 2.0, thin: 0.58, baseY: [0.52, 0.78] },
  ];
  // FRONT: a low trough across the icon's foot + arms climbing both sides (cx-pinned), in front
  // of the icon (screen-blended) so the flames wrap over its edges.
  const FRONT = (P) => [
    { color: P.outer, n: 12, rise: [0.45, 0.85], r0: [8, 12], spread: 24, sway: 2, top: 0.66, stretch: 0.9, thin: 0.22, baseY: [0.86, 1.04] },
    { color: P.inner, n: 8,  rise: [0.5, 0.9],   r0: [5, 8],  spread: 19, sway: 2, top: 0.70, stretch: 1.0, thin: 0.26, baseY: [0.86, 1.04] },
    { color: P.outer, n: 8,  cx: 4,  rise: [0.55, 1.0], r0: [6, 10], spread: 6, sway: 3, top: 0.30, stretch: 1.7, thin: 0.46, baseY: [0.74, 1.02] },
    { color: P.inner, n: 5,  cx: 5,  rise: [0.6, 1.05], r0: [4, 7],  spread: 5, sway: 3, top: 0.36, stretch: 1.8, thin: 0.50, baseY: [0.74, 1.02] },
    { color: P.outer, n: 8,  cx: 52, rise: [0.55, 1.0], r0: [6, 10], spread: 6, sway: 3, top: 0.30, stretch: 1.7, thin: 0.46, baseY: [0.74, 1.02] },
    { color: P.inner, n: 5,  cx: 51, rise: [0.6, 1.05], r0: [4, 7],  spread: 5, sway: 3, top: 0.36, stretch: 1.8, thin: 0.50, baseY: [0.74, 1.02] },
  ];

  // Flame box (% of the stage). BACK is wide + tall behind the icon; FRONT hugs the icon so its
  // trough sits at the foot and its arms climb the icon's sides. hit is fractions. Tuned live.
  function boxStyle(hit) {
    const p = (v) => (v * 100).toFixed(3) + "%";
    let w, h, top;
    if (front) { w = hit.w * 1.4;  h = hit.h * 1.7;  top = hit.y + hit.h * 1.45 - h; }
    else       { w = hit.w * 2.2;  h = hit.h * 2.5;  top = hit.y + hit.h - h + hit.h * 0.70; }
    const left = hit.x + hit.w / 2 - w / 2;
    return `left:${p(left)};top:${p(top)};width:${p(w)};height:${p(h)}`;
  }

  let groups = {};       // slug -> <g> element (bound per course)
  let blobs = [];        // { el, slug, ... } across all courses
  let raf = 0, running = false, t = 0;

  function resetBlob(b, L, scatter) {
    const lcx = L.cx != null ? L.cx : CX; b.lcx = lcx;
    b.base = lcx + (Math.random() * 2 - 1) * L.spread;
    b.vy = (L.rise[0] + Math.random() * (L.rise[1] - L.rise[0])) * SPEED;
    b.r0 = L.r0[0] + Math.random() * (L.r0[1] - L.r0[0]);
    b.phase = Math.random() * 6.28; b.sway = L.sway * (0.5 + Math.random() * 0.9);
    b.stretch = L.stretch; b.thin = L.thin; b.L = L; b.topY = VH * L.top;
    const baseLo = VH * L.baseY[0], baseHi = VH * L.baseY[1];
    b.y = scatter ? (b.topY + Math.random() * (baseHi - b.topY)) : baseLo + Math.random() * (baseHi - baseLo);
    b.spawnY = b.y;
  }
  function applyBlob(b, pr, x) {
    const rx = Math.max(0, b.r0 * (1 - pr * b.thin)), ry = b.r0 * (0.7 + pr * b.stretch);
    b.el.setAttribute("cx", x.toFixed(1)); b.el.setAttribute("cy", b.y.toFixed(1));
    b.el.setAttribute("rx", rx.toFixed(1)); b.el.setAttribute("ry", ry.toFixed(1));
  }
  function buildBlobs() {
    blobs = [];
    for (const c of courses) {                  // only current on-fire courses (leaving ones keep their frozen <g> during fade-out)
      const g = groups[c.slug]; if (!g) continue;
      while (g.firstChild) g.removeChild(g.firstChild);
      const P = palette(c.color);
      for (const L of (front ? FRONT(P) : BACK(P))) {
        for (let i = 0; i < L.n; i++) {
          const e = document.createElementNS(NS, "ellipse"); e.setAttribute("fill", css(L.color)); g.appendChild(e);
          const b = { el: e, slug: c.slug }; resetBlob(b, L, true); blobs.push(b);
          if (reduced) applyBlob(b, 0.5, b.base);   // static mid-rise frame
        }
      }
    }
  }
  function frame() {
    t += 0.016 * SPEED;
    for (const b of blobs) {
      b.y -= b.vy;
      const span = Math.max(6, b.spawnY - b.topY), pr = Math.min(1, Math.max(0, (b.spawnY - b.y) / span));
      const x = b.base + (b.lcx - b.base) * pr * 0.2 + Math.sin(t * 2.4 + b.phase) * b.sway * (0.3 + pr);
      if (b.y < b.topY || (b.r0 * (1 - pr * b.thin)) < 0.4) { resetBlob(b, b.L, false); continue; }
      applyBlob(b, pr, x);
    }
    raf = requestAnimationFrame(frame);
  }
  function start() { if (running || reduced) return; running = true; raf = requestAnimationFrame(frame); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  // Rebuild blobs whenever the on-fire set (slug or colour) changes, after the new <g> nodes mount.
  let prevKey = "";
  $: {
    const key = courses.map((c) => c.slug + ":" + c.color).join(",");
    if (key !== prevKey) {
      prevKey = key;
      tick().then(() => {
        buildBlobs();
        if (reduced) stop();
        else if (blobs.length) start();
        else stop();
      });
    }
  }
  onDestroy(stop);
</script>

{#each courses as c (c.slug)}
  <div class="flame" class:front style={boxStyle(c.hit)} transition:fade={{ duration: 260 }} aria-hidden="true">
    {#if !front}
      <div class="glow" style="background: radial-gradient(60% 60% at 50% 78%, {css(palette(c.color).mid, 0.5)}, {css(palette(c.color).outer, 0.12)} 60%, transparent 82%)"></div>
    {/if}
    <svg class="svg" viewBox="0 0 {VW} {VH}" preserveAspectRatio="none">
      <defs>
        <filter id="{uid}-{c.slug}" x="-60%" y="-30%" width="220%" height="170%" color-interpolation-filters="sRGB">
          <feGaussianBlur stdDeviation="2.6" result="b" />
          <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 17 -6" />
        </filter>
      </defs>
      <g bind:this={groups[c.slug]} filter="url(#{uid}-{c.slug})"></g>
    </svg>
  </div>
{/each}

<style>
  .flame { position: absolute; pointer-events: none; }
  .glow { position: absolute; inset: 0; mix-blend-mode: screen; filter: blur(6px); animation: mf-flick 3.4s ease-in-out infinite; }
  @keyframes mf-flick { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
  .svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  /* The front pass licks OVER the icon: screen-blended + a touch dimmer so the icon reads through. */
  .flame.front .svg { mix-blend-mode: screen; opacity: 0.62; }
  @media (prefers-reduced-motion: reduce) { .glow { animation: none; } }
</style>
