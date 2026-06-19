<script>
  import { onMount, onDestroy, tick } from "svelte";
  import { baseUrl, manifestUrl, spriteUrl, hitStyle, spriteStyle } from "./lib/map.js";
  import CoursePopup from "./CoursePopup.svelte";
  import { fetchCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
  import { API_BASE, territoryUrl, territoryTimelineUrl } from "./lib/api.js";
  import { buildSnapshots } from "./lib/timeline.js";

  let manifest = null;
  let error = false;

  let terrCanvas;   // the .territory <canvas>

  // --- Timeline (SP4): historical ownership snapshots, lazily rendered + capped cache ---
  // N can be ~200; full-res layers (2200x1775, ~15 MB each) x N would OOM, so historical frames
  // render at a reduced internal width and only a bounded window is kept (re-rendered on demand).
  // The present view stays the canonical high-res renderTerritory (= /v1/territory).
  const TL_RENDER_W = 1100;   // internal render width for scrub frames (display res, not 2200)
  const TL_CACHE_CAP = 32;    // max cached scrub bitmaps (~32 x 3.9 MB, ~125 MB ceiling)
  let snapshots = [];
  let tlIndex = 0;
  let timelineReady = false;
  let tlWorker = null, tlCov = null, tlBase = null, tlW = 0, tlH = 0, tlPending = null;
  const tlCache = [];         // index -> ImageBitmap (sparse)
  const tlOrder = [];         // FIFO of cached indices, for eviction

  async function renderTerritory() {
    if (!terrCanvas || !manifest) return;
    try {
      const [rows, cov, base] = await Promise.all([
        fetch(territoryUrl(150)).then((r) => r.json()),
        createImageBitmap(await (await fetch(`/map/island.png`)).blob()),
        createImageBitmap(await (await fetch(`/map/base.jpg`)).blob()),
      ]);
      const W = cov.width, H = cov.height;
      const worker = new Worker(new URL("./lib/territoryWorker.js", import.meta.url), { type: "module" });
      worker.onerror = (ev) => console.error("territory worker error", ev.message || ev);
      worker.onmessage = (e) => {
        try {
          const dw = 1100, dh = Math.round((dw * H) / W);
          terrCanvas.width = dw; terrCanvas.height = dh;
          const ctx = terrCanvas.getContext("2d");
          ctx.clearRect(0, 0, dw, dh);
          ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
          ctx.drawImage(e.data.bitmap, 0, 0, W, H, 0, 0, dw, dh);   // 2x -> display = AA
        } catch (err) { console.error("territory draw failed", err); } finally { worker.terminate(); }
      };
      worker.postMessage({ coverageBitmap: cov, baseBitmap: base, W, H,
        manifestCourses: manifest.courses, territoryRows: rows }, [cov, base]);
    } catch (e) { console.error("territory render failed", e); }
  }

  // Render one snapshot's territory (reduced res) via a persistent worker. One render is in
  // flight at a time (callers await / coalesce), so a single pending resolver suffices. The
  // source bitmaps are NOT transferred, so they survive for the next snapshot.
  function tlRenderViaWorker(territoryRows) {
    return new Promise((resolve, reject) => {
      tlPending = { resolve, reject };
      tlWorker.postMessage({
        coverageBitmap: tlCov, baseBitmap: tlBase, W: tlW, H: tlH,
        manifestCourses: manifest.courses, territoryRows,
      });
    });
  }

  // Lazily render + cache snapshot i's bitmap, evicting the oldest beyond the cap (never the
  // current index or the one just produced). Evicted frames re-render on demand.
  async function ensureBitmap(i) {
    if (tlCache[i]) return tlCache[i];
    const rows = Object.entries(snapshots[i].owners).map(([slug, o]) => ({ slug, color: o.color }));
    const bmp = await tlRenderViaWorker(rows);
    tlCache[i] = bmp;
    tlOrder.push(i);
    while (tlOrder.length > TL_CACHE_CAP) {
      const old = tlOrder.shift();
      if (old !== tlIndex && old !== i && tlCache[old]) { tlCache[old].close?.(); tlCache[old] = undefined; }
    }
    return bmp;
  }

  // Fetch the merged run history, build ownership snapshots, and prepare the reduced-res worker
  // + source bitmaps. Additive: the canonical present render stays as-is; on any failure (e.g.
  // endpoint unavailable) we simply keep the live one-shot territory and no timeline appears.
  async function loadTimeline() {
    try {
      const res = await fetch(territoryTimelineUrl(150));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { events, colors } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return;
      const [cov, base] = await Promise.all([
        createImageBitmap(await (await fetch(`/map/island.png`)).blob(), { resizeWidth: TL_RENDER_W, resizeQuality: "high" }),
        createImageBitmap(await (await fetch(`/map/base.jpg`)).blob(), { resizeWidth: TL_RENDER_W, resizeQuality: "high" }),
      ]);
      tlCov = cov; tlBase = base; tlW = cov.width; tlH = cov.height;
      tlWorker = new Worker(new URL("./lib/territoryWorker.js", import.meta.url), { type: "module" });
      tlWorker.onmessage = (e) => { const p = tlPending; tlPending = null; p?.resolve(e.data.bitmap); };
      tlWorker.onerror = (ev) => { const p = tlPending; tlPending = null; console.error("timeline worker error", ev.message || ev); p?.reject(new Error("worker error")); };
      snapshots = snaps;
      tlIndex = snaps.length - 1;
      timelineReady = true;
    } catch (e) {
      console.error("timeline load failed (keeping live territory):", e);
    }
  }

  // Hover popup (glance tooltip: open on icon enter, close on icon leave).
  let view = null, shown = false, popupEl, stageEl, popupStyle = "", closeTimer = 0, activeHit = null, token = 0, figUrl = "";

  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const my = ++token;
    const v = await fetchCourseView(API_BASE, course).catch(() => null);
    if (!v || my !== token) return;                 // fetch failed, or a newer hover superseded us
    view = v;
    if (figUrl.startsWith("blob:")) URL.revokeObjectURL(figUrl);  // free the previous object URL
    figUrl = freshGifUrl(v.onFire ? v.fireGifUrl : v.gifUrl);     // fresh object URL -> GIF replays from frame 1
    await tick();                                   // CoursePopup renders -> measurable
    place(hitEl);
    requestAnimationFrame(() => (shown = true));    // class drives the fade/scale-in
  }
  function scheduleClose() { clearTimeout(closeTimer); closeTimer = setTimeout(() => (shown = false), 90); }

  function place(hitEl) {
    if (!stageEl || !popupEl) return;
    const fr = stageEl.getBoundingClientRect(), hr = hitEl.getBoundingClientRect();
    const cx = hr.left - fr.left + hr.width / 2, cy = hr.top - fr.top + hr.height / 2;
    const pw = popupEl.offsetWidth, ph = popupEl.offsetHeight, off = Math.max(hr.width, hr.height) * 0.55 + 8;
    // Project from the icon: pick the side the popup actually fits on; if neither fits,
    // the side with more room (then clamp). Avoids slamming centre courses to the edge.
    const fitsRight = cx + off + pw + 6 <= fr.width, fitsLeft = cx - off - pw - 6 >= 0;
    const right = fitsRight || (!fitsLeft && fr.width - cx >= cx);
    const fitsBelow = cy + off + ph + 6 <= fr.height, fitsAbove = cy - off - ph - 6 >= 0;
    const below = fitsBelow || (!fitsAbove && fr.height - cy >= cy);
    let left = right ? cx + off : cx - off - pw, top = below ? cy + off : cy - off - ph;
    left = Math.max(6, Math.min(left, fr.width - pw - 6));
    top = Math.max(6, Math.min(top, fr.height - ph - 6));
    popupStyle = `left:${left}px;top:${top}px;transform-origin:${right ? "left" : "right"} ${below ? "top" : "bottom"}`;
  }

  // Touch: a tap outside the open course's icon dismisses the popup.
  function onDocPointerDown(e) { if (shown && activeHit && !activeHit.contains(e.target)) shown = false; }
  onDestroy(() => {
    clearTimeout(closeTimer);
    if (figUrl.startsWith("blob:")) URL.revokeObjectURL(figUrl);
    if (typeof document !== "undefined") document.removeEventListener("pointerdown", onDocPointerDown);
    tlWorker?.terminate();
    for (const b of tlCache) b?.close?.();
    tlCov?.close?.(); tlBase?.close?.();
  });

  onMount(async () => {
    if (typeof document !== "undefined") document.addEventListener("pointerdown", onDocPointerDown);
    try {
      const r = await fetch(manifestUrl(), { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      manifest = await r.json();
      await tick();            // let Svelte mount the .territory canvas (bind terrCanvas) before drawing
      renderTerritory();       // canonical present (high-res) = default view + fallback
      loadTimeline();          // SP4: fetch history + prepare lazy scrub-frame cache (additive)
    } catch (e) {
      console.error("world map: manifest load failed", e);
      error = true;
    }
    preloadPlayerGifs(API_BASE).catch(() => {});   // warm the GIF cache so hovers don't wait on a load
  });
</script>

<div class="map-view">
  <div class="frame">
    {#if error}
      <div class="msg">Map unavailable.</div>
    {:else if manifest}
      <div class="stage" bind:this={stageEl}>
        <img class="base" src={baseUrl()} alt="Mario Kart World map" />
        <!-- SP2 (territory) draws here, between the base and the icons -->
        <canvas class="territory" bind:this={terrCanvas} aria-hidden="true"></canvas>
        <div class="icons">
          {#each manifest.courses as c (c.slug)}
            <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
            <div class="hit" data-slug={c.slug} style={hitStyle(c.hit)}
                 on:mouseenter={(e) => openCourse(c, e.currentTarget)}
                 on:mouseleave={scheduleClose}
                 on:click={(e) => openCourse(c, e.currentTarget)}>
              <img class="shadow" src={spriteUrl(c.slug)} alt="" aria-hidden="true"
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
              <img class="spr" src={spriteUrl(c.slug)} alt={c.name}
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
            </div>
          {/each}
        </div>
        <div class="popups">
          <div class="popup" class:show={shown} bind:this={popupEl} style={popupStyle} aria-hidden={!shown}>
            <CoursePopup {view} {figUrl} />
          </div>
        </div>
      </div>
    {:else}
      <div class="msg">Loading map…</div>
    {/if}
  </div>
</div>

<style>
  .map-view { padding: 16px; }
  .frame {
    position: relative; max-width: 1100px; margin: 0 auto;
    background: var(--feed-bg); border: 1px solid var(--bd);
    border-radius: var(--r); overflow: hidden;
  }
  .frame::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    border-radius: var(--r); box-shadow: inset 0 0 60px 10px rgba(0,0,0,.45);
  }
  .stage { position: relative; width: 100%; }
  /* Calm at rest: the whole map sits muted so the hovered course (and SP2's territory) leads. */
  .base { display: block; width: 100%; height: auto; filter: saturate(.82) brightness(.82); }
  .territory { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .popups { position: absolute; inset: 0; pointer-events: none; }
  .icons { position: absolute; inset: 0; }
  .hit { position: absolute; cursor: pointer; }
  .spr, .shadow {
    position: absolute; pointer-events: none; will-change: transform;
    transition: transform .18s ease, filter .18s ease, opacity .18s ease;
  }
  /* Icons are muted at rest too; hovering one brings it to full vivid colour (living-icon hover). */
  .spr { transform-origin: 50% 90%; filter: saturate(.78) brightness(.86); }
  /* The shadow is a live black silhouette of the course, sitting just below it. */
  .shadow {
    transform-origin: 50% 100%;
    filter: brightness(0);
    opacity: .42;
    transform: translateY(7%);
  }
  .hit:hover { z-index: 50; }
  /* On hover the course rises and its shadow drops + spreads + fades, so it reads as lifting. */
  .hit:hover .spr {
    transform: translateY(-12%) scale(1.13);
    filter: brightness(1.1) saturate(1.08);
  }
  .hit:hover .shadow {
    transform: translateY(11%) scale(1.06);
    opacity: .30;
  }
  /* The popup: kept in layout (opacity:0) so it can be measured for anchoring, faded/scaled
     in via the .show class only (never inline opacity — inline would override the class). */
  .popup {
    position: absolute; z-index: 80; pointer-events: none;
    opacity: 0; transform: scale(.92);
    transition: opacity .14s ease, transform .14s cubic-bezier(.2,.9,.3,1.2);
  }
  .popup.show { opacity: 1; transform: scale(1); }
  .msg { padding: 4rem; text-align: center; color: var(--tx-dim); }
  @media (max-width: 560px) { .map-view { padding: 8px; } }
</style>
