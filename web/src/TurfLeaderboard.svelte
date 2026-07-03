<script>
  // Stylised turf-ownership leaderboard column. Cards render in a FIXED roster
  // order and are positioned/animated imperatively (transform + Web Animations),
  // driven by two reactive inputs: the shown frame index, and a live play-progress
  // object so the numbers tick in lockstep with the map's territory sweep.
  // Look + motion are the committed references in docs/design/turf-leaderboard/.
  import { onMount } from "svelte";
  import { figureFor } from "../../src/lib/playerFigures.js";
  import { playerKey } from "../../src/lib/playerKey.js";
  import { turfStandings, cardConfig, digitJank } from "./lib/turf.js";

  export let snapshots = [];
  export let colors = {};
  export let courseCount = 30;
  export let frameIndex = 0;
  export let anim = { active: false, from: 0, to: 0, tau: 0 };
  export let scale = 1;   // whole-column scale (set by WorldMap so its height matches the map)

  const STEP = 120, SWAP = 420;

  $: roster = Object.keys(colors).sort();               // stable DOM order
  $: cfg = roster.map((n, i) => cardConfig(playerKey(n), i));

  let cardEls = [], mounted = false;
  const elOf = (name) => cardEls[roster.indexOf(name)];
  let curSlot = {}, curSide = {}, curPct = {};

  function setNum(name, pct) {
    const card = elOf(name); if (!card) return;
    const num = card.querySelector(".num"); if (!num) return;
    const s = String(pct);
    let h = "";
    for (let i = 0; i < s.length; i++) {
      const j = digitJank(i);
      h += `<span class="d" style="transform:rotate(${j.rot}deg) translateY(${j.ty}px)">${s[i]}</span>`;
    }
    h += `<span class="pc" style="transform:rotate(2deg)">%</span>`;
    num.innerHTML = h;
    num.animate([{ transform: "scale(1.16)" }, { transform: "scale(1)" }],
      { duration: 230, easing: "cubic-bezier(.3,1.6,.4,1)" });
  }

  function slide(node, dx) {
    if (Math.abs(dx) < 1) return;
    // dx is measured in scaled screen px; the node lives inside the scaled column, so divide by
    // the scale to move exactly dx on screen (otherwise the slam overshoots by the scale factor).
    node.animate([{ transform: `translateX(${dx / scale}px)` }, { transform: "translateX(0)" }],
      { duration: SWAP, easing: "cubic-bezier(.3,1.55,.35,1)" });
  }

  // colour border sits on the figure's side; mirrors (and slides) on a side-swap
  function setBorder(card, c, side, animate) {
    const ck = card.querySelector(".ck"); if (!ck) return;
    const ax = Math.abs(c.ox);
    const nx = side === "L" ? ax : -ax, nt = `translate(${nx}px,${c.oy}px)`;
    if (animate) {
      const ot = ck.style.transform || nt;
      ck.animate([{ transform: ot }, { transform: nt }],
        { duration: SWAP, easing: "cubic-bezier(.3,1.55,.35,1)" });
    }
    ck.style.transform = nt;
  }

  // kinetic side-swap: header + figure slam across (FLIP) + a colour streak. No fade.
  function doSwap(card, side) {
    const hdr = card.querySelector(".header"), img = card.querySelector(".figmask img");
    const h0 = hdr.getBoundingClientRect(), i0 = img.getBoundingClientRect();
    card.classList.remove("L", "R"); card.classList.add(side);
    const h1 = hdr.getBoundingClientRect(), i1 = img.getBoundingClientRect();
    slide(hdr, h0.left - h1.left); slide(img, i0.left - i1.left);
    const bar = card.querySelector(".streak b");
    if (bar) bar.animate(
      [{ transform: "translateX(-160%) skewX(-14deg)", opacity: 0.9 },
       { transform: "translateX(360%) skewX(-14deg)", opacity: 0 }],
      { duration: SWAP, easing: "ease-out" });
  }

  function place(counts, animated) {
    const order = roster.slice().sort((a, b) =>
      counts[b] - counts[a] || (a < b ? -1 : a > b ? 1 : 0));
    order.forEach((name, slot) => {
      const card = elOf(name); if (!card) return;
      const c = cfg[roster.indexOf(name)], side = slot % 2 === 0 ? "L" : "R";
      if (curSlot[name] !== slot) {
        const first = curSlot[name] === undefined;
        if (!animated) card.style.transition = "none";
        card.style.transform = `translateY(${slot * STEP}px) rotate(${c.rot}deg)`;
        if (!animated) { void card.offsetWidth; card.style.transition = ""; } // flush, re-enable
        curSlot[name] = slot;
        // z by slot (lower player on top); on a live reorder flip z at the slide midpoint (unseen)
        if (animated && !first) setTimeout(() => { if (curSlot[name] === slot) card.style.zIndex = 10 + slot; }, 220);
        else card.style.zIndex = 10 + slot;
      }
      if (curSide[name] !== side) {
        const swap = animated && curSide[name] !== undefined;
        if (swap) doSwap(card, side); else { card.classList.remove("L", "R"); card.classList.add(side); }
        setBorder(card, c, side, swap);
        curSide[name] = side;
      }
      card.classList.toggle("zero", Math.round(counts[name]) <= 0);
    });
  }

  function countsAt(i) {
    const out = {};
    const idx = Math.max(0, Math.min(i, snapshots.length - 1));
    turfStandings(snapshots[idx], colors, courseCount).forEach((r) => (out[r.player] = r.courses));
    return out;
  }

  function drive(fi, a) {
    if (!mounted || !snapshots.length) return;
    let counts;
    if (a && a.active) {
      const f = countsAt(a.from), t = countsAt(a.to);
      counts = {};
      roster.forEach((n) => (counts[n] = (f[n] || 0) + ((t[n] || 0) - (f[n] || 0)) * a.tau));
    } else {
      counts = countsAt(fi);
    }
    roster.forEach((n) => {
      const pct = Math.round((counts[n] / courseCount) * 100);
      if (pct !== curPct[n]) { setNum(n, pct); curPct[n] = pct; }
    });
    place(counts, !!(a && a.active));
  }

  onMount(() => { mounted = true; });
  // redraw on any input change (frame scrub, play tau, or a late data arrival);
  // roster/cfg are referenced so this runs AFTER they recompute.
  $: mounted, snapshots, colors, courseCount, roster, cfg, drive(frameIndex, anim);
</script>

<div class="turfwrap" style="width:{172 * scale}px;height:{roster.length * STEP * scale}px">
  <div class="turfcol" style="height:{roster.length * STEP}px;transform:scale({scale})">
  {#each roster as name, i (name)}
    <div class="rp" bind:this={cardEls[i]} style="--c:{colors[name]};--fx:{cfg[i].fx}px">
      <div class="inner">
        <div class="ck p{cfg[i].shape}"></div>
        <div class="cf dot p{cfg[i].shape}"></div>
        <div class="figmask m{cfg[i].shape}"><img src={figureFor(name, true)} alt="" /></div>
        <div class="header"><span class="num"></span><span class="name">{name}</span></div>
        <div class="streak p{cfg[i].shape}"><b></b></div>
      </div>
    </div>
  {/each}
  </div>
</div>

<style>
  /* Card CSS is copied verbatim from docs/design/turf-leaderboard/turf-animation-prototype.html.
     The subtree is styled with `.turfcol :global(...)` because classes/elements are managed
     imperatively (.L/.R/.zero toggles, JS-built .d/.pc digit spans) — scoped selectors would
     not match them. Bounding under the unique `.turfcol` root keeps it collision-safe. */
  /* .turfwrap reserves the SCALED footprint in WorldMap's flex row; .turfcol renders at its
     base 172px width and is scaled up via transform (so figures/text/gaps all scale together). */
  .turfwrap { position: relative; flex: 0 0 auto; }
  .turfcol { position: absolute; top: 0; left: 0; width: 172px; transform-origin: top left; }
  .turfcol :global(.rp) { position: absolute; top: 0; left: 12px; width: 160px; height: 110px; overflow: visible;
    transition: transform .44s cubic-bezier(.5,.05,.15,1), filter .45s ease; will-change: transform; }
  .turfcol :global(.rp.zero) { filter: saturate(.32) brightness(.72); }
  .turfcol :global(.inner) { position: absolute; inset: 0; }
  .turfcol :global(.ck), .turfcol :global(.cf) { position: absolute; inset: 0; }
  .turfcol :global(.ck) { background: var(--c); opacity: .92; }
  .turfcol :global(.cf) { background: #191a1d; }
  .turfcol :global(.cf.dot::after) { content: ""; position: absolute; inset: 0; opacity: .16;
    background-image: radial-gradient(circle, var(--c) 1px, transparent 1.5px); background-size: 7px 7px; }
  .turfcol :global(.figmask) { position: absolute; inset: 0; z-index: 3; }
  .turfcol :global(.figmask img) { position: absolute; bottom: -6px; height: 138px; filter: drop-shadow(-1px 3px 4px rgba(0,0,0,.45)); }
  .turfcol :global(.rp.L .figmask img) { right: calc(0px - var(--fx, 0px)); }
  .turfcol :global(.rp.R .figmask img) { left: calc(0px - var(--fx, 0px)); }
  .turfcol :global(.header) { position: absolute; top: 9px; z-index: 6; width: auto; max-width: 130px; }
  .turfcol :global(.rp.L .header) { left: 11px; text-align: left; }
  .turfcol :global(.rp.R .header) { right: 11px; text-align: right; }
  .turfcol :global(.num) { display: block; font-weight: 900; font-style: italic; color: #fff; font-size: 44px;
    line-height: .66; letter-spacing: -.045em; -webkit-text-stroke: 2.2px #101114; paint-order: stroke fill;
    text-shadow: 3px 3px 0 var(--c), 1px 2px 0 rgba(0,0,0,.5); white-space: nowrap; }
  .turfcol :global(.num .d) { display: inline-block; }
  .turfcol :global(.num .pc) { display: inline-block; color: var(--c); font-size: .5em;
    -webkit-text-stroke: 1.2px #101114; text-shadow: 2px 2px 0 rgba(0,0,0,.5); }
  .turfcol :global(.name) { display: inline-block; margin-top: 4px; font-weight: 900; font-style: italic;
    background: var(--c); color: #101114; padding: 2px 7px 3px; font-size: 11px;
    box-shadow: 1.5px 1.5px 0 rgba(0,0,0,.45); white-space: nowrap; }
  .turfcol :global(.streak) { position: absolute; inset: 0; overflow: hidden; pointer-events: none; z-index: 5; }
  .turfcol :global(.streak b) { position: absolute; top: -12%; height: 124%; width: 30px;
    transform: translateX(-160%) skewX(-14deg);
    background: linear-gradient(90deg, transparent, var(--c), #fff, transparent); opacity: 0; mix-blend-mode: screen; }
  .turfcol :global(.p1) { clip-path: polygon(3% 2%,30% 0,66% 4%,100% 0,96% 46%,99% 84%,70% 96%,40% 85%,14% 98%,0 82%,4% 44%); }
  .turfcol :global(.p2) { clip-path: polygon(0 5%,34% 0,70% 6%,100% 2%,95% 50%,100% 88%,66% 94%,36% 88%,8% 97%,3% 52%); }
  .turfcol :global(.p3) { clip-path: polygon(5% 0,40% 5%,72% 0,100% 6%,94% 84%,58% 100%,24% 88%,0 96%,6% 40%); }
  .turfcol :global(.p4) { clip-path: polygon(6% 3%,100% 0,96% 40%,100% 92%,46% 100%,8% 90%,0 30%,5% 12%); }
  .turfcol :global(.p5) { clip-path: polygon(2% 0,100% 6%,96% 52%,88% 100%,0 92%,6% 40%); }
  .turfcol :global(.m1) { clip-path: polygon(0% -40%,100% -40%,96% 46%,99% 84%,70% 96%,40% 85%,14% 98%,0 82%,4% 44%); }
  .turfcol :global(.m2) { clip-path: polygon(0% -40%,100% -40%,95% 50%,100% 88%,66% 94%,36% 88%,8% 97%,3% 52%); }
  .turfcol :global(.m3) { clip-path: polygon(0% -40%,100% -40%,94% 84%,58% 100%,24% 88%,0 96%,6% 40%); }
  .turfcol :global(.m4) { clip-path: polygon(0% -40%,100% -40%,96% 40%,100% 92%,46% 100%,8% 90%,0 30%,5% 12%); }
  .turfcol :global(.m5) { clip-path: polygon(0% -40%,100% -40%,96% 52%,88% 100%,0 92%,6% 40%); }
  @media (max-width: 760px) { .turfwrap { margin: 0 auto; } }
</style>
