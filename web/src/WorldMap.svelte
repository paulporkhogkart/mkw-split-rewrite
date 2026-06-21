<script>
  import { onMount, onDestroy, tick } from "svelte";
  import { baseUrl, manifestUrl, spriteUrl, hitStyle, spriteStyle } from "./lib/map.js";
  import CoursePopup from "./CoursePopup.svelte";
  import { buildCourseView, preloadPlayerGifs, freshGifUrl } from "./lib/courseData.js";
  import { API_BASE, territoryUrl, territoryTimelineUrl } from "./lib/api.js";
  import { buildSnapshots, flippedCourses, leaderboardAt } from "./lib/timeline.js";
  import { prepareTransition, interpolatePatch, buildCourseField } from "./lib/territoryAnim.js";
  import TimelineScrubber from "./TimelineScrubber.svelte";

  let manifest = null;
  let error = false;

  // One territory canvas sized to the display's device pixels (hi-res -> downscale, never up).
  // LIVE shows the canonical high-res present; historical indices show cached scrub frames.
  let terr;                        // the single territory canvas
  let backW = 1100, backH = 888;   // backing-store pixels (device px), set by sizeCanvas
  const ASSET_W = 2200;            // cap: the island/base asset native width
  let presentBitmap = null;   // canonical present (native res), shown at LIVE + as fallback
  let playing = false;
  // showSnapshot coalescing: the knob (tlIndex) tracks instantly while only the newest request paints.
  let pendingIndex = null, rendering = false;
  // Capture animation: per-frame source buffers (backing res) + the constant course field + the rAF runner.
  let bkCoverage = null, bkTerr = null, bkField = null, slug2idx = null;
  let animRaf = 0, animResolve = null;
  const FRONT_SPEED = 0.30;   // px/ms — the front advances at this CONSTANT speed (so nothing "slows down"); tunable
  const MIN_MS = 320, MAX_MS = 5000, MAX_RUN = 12;   // duration clamp + max cells coalesced into one continuous sweep
  const easeFlow = (t) => t;   // linear: a steady advance, so a coalesced run reads as one unbroken motion

  // Fit-to-viewport layout: the console (title + transport) sits on top; the map fills the
  // remaining height so the whole view fits without scrolling. Box sizes are computed in JS.
  let headerH = 46, mapViewEl, consoleEl, mapW = 0, mapH = 0, ro = null;
  function fitMap() {
    if (!mapViewEl) return;
    const padV = 24, padH = 24;               // .map-view padding (12*2)
    const consoleH = consoleEl ? consoleEl.offsetHeight + 10 : 0;   // console + its 10px gap to the frame
    const availH = mapViewEl.clientHeight - consoleH - padV;
    const availW = mapViewEl.clientWidth - padH;
    const ar = 2200 / 1775;
    let h = Math.max(140, availH), w = h * ar;
    if (w > availW) { w = availW; h = availW / ar; }
    mapW = Math.round(w); mapH = Math.round(h);
  }

  // Date of the currently-shown snapshot, stamped on the map pane (top-right). Inter + tabular
  // figures rather than a monospace readout, so it reads as a broadcast caption, not code.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // Date + time of the shown snapshot from its raw timestamp, so the time is in the viewer's
  // local timezone (the snapshot's `date` field is UTC and used only for identity/dedup).
  function fmtStamp(t) {
    if (t == null) return null;
    const dt = new Date(t);
    return {
      date: `${dt.getDate()} ${MONTHS[dt.getMonth()]} ${dt.getFullYear()}`,
      time: dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    };
  }
  $: stamp = (timelineReady && snapshots[tlIndex]) ? fmtStamp(snapshots[tlIndex].t) : null;
  // The shown frame's cutoff time. buildSnapshots only emits on ownership change, so the last
  // snapshot's t can predate recent runs - at LIVE use Infinity to include every event.
  $: atLive = !timelineReady || tlIndex >= snapshots.length - 1;
  $: frameTime = atLive ? Infinity : (snapshots[tlIndex]?.t ?? Infinity);

  // Backing store = display CSS width x devicePixelRatio (capped at the 2200 asset), so the
  // territory is rendered hi-res then downscaled into device pixels (crisp on any DPI).
  function sizeCanvas() {
    if (!terr) return;
    const cssW = mapW || (stageEl && stageEl.clientWidth) || 1100;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    backW = Math.min(ASSET_W, Math.round(cssW * dpr));
    backH = Math.round(backW * (1775 / 2200));
    terr.width = backW; terr.height = backH;
  }
  function paintBitmap(bitmap) {     // draw a full-frame territory bitmap, AA-downscaled into the backing store
    if (!terr || !bitmap) return;
    const ctx = terr.getContext("2d");
    ctx.clearRect(0, 0, backW, backH);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bitmap, 0, 0, bitmap.width, bitmap.height, 0, 0, backW, backH);
  }

  // --- Timeline (SP4): historical ownership snapshots, lazily rendered + capped cache ---
  // N can be ~200; full-res layers (2200x1775, ~15 MB each) x N would OOM, so historical frames
  // render at a reduced internal width and only a bounded window is kept (re-rendered on demand).
  // The present view stays the canonical high-res renderTerritory (= /v1/territory).
  const TL_CACHE_CAP = 24;    // max cached scrub bitmaps; rendered at the backing size (<= asset 2200)
  let snapshots = [];
  let tlEvents = [], tlColors = {}, tlWrs = {};   // retained run stream + colour + current-WR maps
  let tlIndex = 0;
  let timelineReady = false;
  let tlWorker = null, tlCov = null, tlBase = null, tlW = 0, tlH = 0;
  const tlQueue = [];         // FIFO of {resolve,reject} awaiting worker replies, matched in post order
  const tlInFlight = new Map(); // index -> in-flight render promise, so concurrent callers SHARE one render
  const tlCache = [];         // index -> ImageBitmap (sparse)
  const tlOrder = [];         // FIFO of cached indices, for eviction

  // Canonical present territory (high-res /v1/territory): render once and paint it to the canvas.
  // Kept as the LIVE view and as the fallback when the timeline endpoint is unavailable.
  async function renderTerritory() {
    if (!terr || !manifest) return;
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
          presentBitmap = e.data.bitmap;        // native 2200 canonical present
          if (!snapshots.length || tlIndex >= snapshots.length - 1) paintBitmap(presentBitmap);  // only when LIVE is showing
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
      tlQueue.push({ resolve, reject });     // worker replies in post order -> FIFO match, never a clobbered resolver
      tlWorker.postMessage({
        coverageBitmap: tlCov, baseBitmap: tlBase, W: tlW, H: tlH,
        targetW: backW, targetH: backH,            // render scrub frames at the backing resolution
        manifestCourses: manifest.courses, territoryRows,
      });
    });
  }

  // Lazily render + cache snapshot i's bitmap, evicting the oldest beyond the cap (never the
  // current index or the one just produced). Evicted frames re-render on demand.
  async function ensureBitmap(i) {
    if (tlCache[i]) return tlCache[i];
    if (tlInFlight.has(i)) return tlInFlight.get(i);   // already rendering i -> share it (no duplicate worker job)
    const rows = Object.entries(snapshots[i].owners).map(([slug, o]) => ({ slug, color: o.color }));
    const pr = (async () => {
      const bmp = await tlRenderViaWorker(rows);
      tlCache[i] = bmp;
      tlOrder.push(i);
      while (tlOrder.length > TL_CACHE_CAP) {
        const old = tlOrder.shift();
        if (old !== tlIndex && old !== i && tlCache[old]) { tlCache[old].close?.(); tlCache[old] = undefined; }
      }
      return bmp;
    })();
    tlInFlight.set(i, pr);
    try { return await pr; } finally { tlInFlight.delete(i); }
  }

  // Fetch the merged run history, build ownership snapshots, and prepare the reduced-res worker
  // + source bitmaps. Additive: the canonical present render stays as-is; on any failure (e.g.
  // endpoint unavailable) we simply keep the live one-shot territory and no timeline appears.
  async function loadTimeline() {
    try {
      const res = await fetch(territoryTimelineUrl(150));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { events, colors, wrs } = await res.json();
      const snaps = buildSnapshots(events, colors);
      if (!snaps.length) return false;
      tlEvents = events; tlColors = colors; tlWrs = wrs || {};   // kept for the unified board + fire
      const [cov, base] = await Promise.all([
        createImageBitmap(await (await fetch(`/map/island.png`)).blob()),   // full-res source; the worker downscales per frame
        createImageBitmap(await (await fetch(`/map/base.jpg`)).blob()),
      ]);
      tlCov = cov; tlBase = base; tlW = cov.width; tlH = cov.height;
      tlWorker = new Worker(new URL("./lib/territoryWorker.js", import.meta.url), { type: "module" });
      tlWorker.onmessage = (e) => { tlQueue.shift()?.resolve(e.data.bitmap); };
      tlWorker.onerror = (ev) => { console.error("timeline worker error", ev.message || ev); tlQueue.shift()?.reject(new Error("worker error")); };
      snapshots = snaps;
      tlIndex = snaps.length - 1;
      timelineReady = true;
      await tick();               // the transport row mounts -> the console grew, so re-fit the map
      refit();                    // re-fit + size canvas + build the animation source buffers + repaint
      return true;
    } catch (e) {
      console.error("timeline load failed (keeping live territory):", e);
      return false;
    }
  }

  // Hard-cut to snapshot i (scrub + the default). Rendering is coalesced so rapid scrubbing only
  // paints the newest frame. Play animates the step instead (see Task 5's animateTransition).
  async function showSnapshot(i) {
    i = Math.max(0, Math.min(i, snapshots.length - 1));
    tlIndex = i;
    pendingIndex = i;
    if (rendering) return;            // an active loop will pick up pendingIndex
    rendering = true;
    try {
      while (pendingIndex !== null) {
        const target = pendingIndex; pendingIndex = null;
        let bmp;
        try { bmp = await ensureBitmap(target); }
        catch { continue; }
        if (pendingIndex !== null) continue;   // superseded mid-render -> skip the stale paint
        paintBitmap(bmp);
      }
    } finally { rendering = false; }
  }

  // Paint one snapshot's canonical full frame (LIVE = present bitmap, else the cached/rendered
  // scrub frame). Awaitable; used to settle each animated step on a crisp AA frame.
  async function drawBaseFrame(i) {
    i = Math.max(0, Math.min(i, snapshots.length - 1));
    let bmp;
    try { bmp = await ensureBitmap(i); }
    catch { return; }
    paintBitmap(bmp);
  }

  // Backing-resolution coverage + terrain buffers (read once from the full-res source bitmaps),
  // so the per-frame transition patch composites in device pixels that line up with the canvas.
  function buildBackingBuffers() {
    if (!tlCov || !tlBase || !backW) return;
    const c = document.createElement("canvas"); c.width = backW; c.height = backH;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(tlCov, 0, 0, backW, backH);
    const cd = x.getImageData(0, 0, backW, backH).data;
    bkCoverage = new Uint8Array(backW * backH);
    for (let p = 0; p < bkCoverage.length; p++) bkCoverage[p] = cd[p * 4];
    x.clearRect(0, 0, backW, backH);
    x.drawImage(tlBase, 0, 0, backW, backH);
    bkTerr = new Uint8ClampedArray(x.getImageData(0, 0, backW, backH).data);
    bkField = manifest ? buildCourseField(manifest.courses, backW, backH) : null;   // constant nearest-course field + adjacency (reused every step)
    if (manifest && !slug2idx) slug2idx = Object.fromEntries(manifest.courses.map((cc, i) => [cc.slug, i]));
  }

  const rowsOf = (i) => Object.entries(snapshots[i].owners).map(([slug, o]) => ({ slug, color: o.color }));

  function cancelAnim() {                 // stop the running transition and unblock its awaiter
    cancelAnimationFrame(animRaf);
    if (animResolve) { const r = animResolve; animResolve = null; r(); }
  }

  // Animate the capture from `from` to `to`. The canvas must already show `from`; only the changed
  // cells are re-rendered per frame (the invasion front), so the rest stays still (no flash). The
  // tau=1 patch is LEFT on the canvas (no per-step full settle) so chained steps flow continuously;
  // the canonical AA frame is settled on pause / at the end.
  function animateTransition(from, to) {
    return new Promise((resolve) => {
      cancelAnim();
      animResolve = resolve;
      const done = () => { if (animResolve === resolve) { animResolve = null; resolve(); } };
      const hardSet = () => drawBaseFrame(to).then(done);     // can't animate -> just show `to`
      if (!flippedCourses(snapshots[from], snapshots[to]).length) { done(); return; }   // no ownership change -> take zero time
      if (!bkCoverage || !bkTerr) { hardSet(); return; }
      let prep;
      try {
        prep = prepareTransition({ coverage: bkCoverage, terr: bkTerr, W: backW, H: backH,
          manifestCourses: manifest.courses, rowsA: rowsOf(from), rowsB: rowsOf(to), field: bkField });
      } catch (e) { console.error("transition prep failed", e); hardSet(); return; }
      if (!prep) { hardSet(); return; }
      const dur = Math.max(MIN_MS, Math.min(MAX_MS, prep.extent / FRONT_SPEED));   // constant speed -> duration scales with the front's travel
      const ctx = terr.getContext("2d");
      const t0 = performance.now();
      const tick = (now) => {
        if (animResolve !== resolve) return;            // cancelled by scrub/pause
        const tau = easeFlow(Math.min(1, (now - t0) / dur));
        const patch = interpolatePatch(prep, tau);
        ctx.putImageData(new ImageData(patch.rgba, patch.w, patch.h), patch.x, patch.y);
        if (tau < 1) animRaf = requestAnimationFrame(tick);
        else done();        // leave the tau=1 patch; settle on pause/end keeps chained play fluid
      };
      animRaf = requestAnimationFrame(tick);
    });
  }

  // Coalesce a run of consecutive captures by the SAME owner into ADJOINING cells -> the front sweeps
  // across them as one continuous motion (no per-cell break). Returns the run's end snapshot index.
  function singleNewOwner(snapIdx, flips) {
    let ob = null;
    for (const s of flips) { const p = snapshots[snapIdx].owners[s]?.player ?? null; if (p == null) return null; if (ob == null) ob = p; else if (ob !== p) return null; }
    return ob;
  }
  function runEnd(from) {
    const last = snapshots.length - 1;
    if (from >= last || !bkField || !slug2idx) return Math.min(from + 1, last);
    const ob = singleNewOwner(from + 1, flippedCourses(snapshots[from], snapshots[from + 1]));
    if (ob == null) return from + 1;                    // multi-owner step -> don't coalesce
    const acc = new Set();                              // the RUN's captured cells ONLY -> stays spatially contiguous
    const box = { x0: backW, y0: backH, x1: -1, y1: -1 };
    const add = (ci) => { acc.add(ci); const b = bkField.courseBox[ci]; if (b.minx < box.x0) box.x0 = b.minx; if (b.miny < box.y0) box.y0 = b.miny; if (b.maxx > box.x1) box.x1 = b.maxx; if (b.maxy > box.y1) box.y1 = b.maxy; };
    for (const s of flippedCourses(snapshots[from], snapshots[from + 1])) if (s in slug2idx) add(slug2idx[s]);
    const WIN_CAP = 150000;                            // cap the run's WINDOW so it stays on the fast LIVE render path (no per-run render hitch)
    let to = from + 1;
    while (to < last && to - from < MAX_RUN) {
      const flips = flippedCourses(snapshots[to], snapshots[to + 1]);
      if (singleNewOwner(to + 1, flips) !== ob) break;  // a different owner captures next -> stop the run
      let ok = flips.length > 0, nx0 = box.x0, ny0 = box.y0, nx1 = box.x1, ny1 = box.y1;
      for (const s of flips) {
        const ci = slug2idx[s]; let touches = false;
        if (ci != null) { for (const a of bkField.adj[ci]) if (acc.has(a)) { touches = true; break; }
          const b = bkField.courseBox[ci]; nx0 = Math.min(nx0, b.minx); ny0 = Math.min(ny0, b.miny); nx1 = Math.max(nx1, b.maxx); ny1 = Math.max(ny1, b.maxy); }
        if (!touches) { ok = false; break; }            // not adjoining the run SO FAR -> stop (contiguous only)
      }
      if (!ok || (nx1 - nx0 + 176) * (ny1 - ny0 + 176) > WIN_CAP) break;   // padded window too big -> would need the slow path
      for (const s of flips) add(slug2idx[s]);
      to++;
    }
    return to;
  }

  async function step() {
    if (!playing) return;
    const last = snapshots.length - 1;
    const from = tlIndex;
    const to = runEnd(from);               // coalesce an adjoining same-owner run into one continuous sweep
    tlIndex = to;
    await animateTransition(from, to);
    if (!playing) { drawBaseFrame(tlIndex); return; }          // paused -> settle the crisp canonical frame
    if (to >= last) { playing = false; drawBaseFrame(last); return; }   // reached LIVE -> canonical present
    step();                                                    // chain immediately, no dwell -> continuous flow
  }
  async function togglePlay() {
    if (playing) { playing = false; cancelAnim(); return; }    // pause: stop the sweep; step() settles the frame
    if (tlIndex >= snapshots.length - 1) { await drawBaseFrame(0); tlIndex = 0; }   // restart: show frame 0 first
    playing = true;
    step();
  }
  function onScrub(index) {                 // dragging the knob pauses play and hard-cuts to the frame
    playing = false; cancelAnim();
    showSnapshot(index);
  }

  // Hover popup (glance tooltip: open on icon enter, close on icon leave).
  let view = null, shown = false, popupEl, stageEl, popupStyle = "", closeTimer = 0, activeHit = null, token = 0, figUrl = "";

  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const my = ++token;
    // One path for every frame: the board AS OF the shown moment, reconstructed from the
    // in-memory event stream (matches the territory colours). WR is the current WR (tlWrs).
    // A course with no runs by then has no board -> no popup opens.
    const standings = leaderboardAt(tlEvents, course.slug, frameTime);
    const v = standings.length
      ? buildCourseView({ standings, colorByName: tlColors, courseName: course.name, wr: tlWrs[course.slug] ?? null })
      : null;
    if (!v || my !== token) return;                 // empty board, or a newer hover superseded us
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
    ro?.disconnect();
    cancelAnimationFrame(animRaf);
    tlWorker?.terminate();
    for (const b of tlCache) b?.close?.();
    tlCov?.close?.(); tlBase?.close?.(); presentBitmap?.close?.();
  });

  function refit() {                            // re-fit the map box + rebuild backing buffers on any size change
    fitMap();
    sizeCanvas();
    buildBackingBuffers();
    for (const b of tlCache) b?.close?.();      // cached scrub frames are sized to the old backing -> drop them
    tlCache.length = 0; tlOrder.length = 0;
    if (snapshots.length) showSnapshot(tlIndex);
    else if (presentBitmap) paintBitmap(presentBitmap);
  }

  onMount(async () => {
    if (typeof document !== "undefined") {
      document.addEventListener("pointerdown", onDocPointerDown);
      headerH = document.querySelector(".top")?.offsetHeight || headerH;
    }
    try {
      const r = await fetch(manifestUrl(), { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      manifest = await r.json();
      await tick();            // mount the .console + .territory canvas before measuring/drawing
      fitMap(); await tick(); fitMap();   // two-pass: the console width settles to the map width
      sizeCanvas();            // size the backing store to the fitted map before the first paint
      const ok = await loadTimeline();   // primary: history drives every frame incl. LIVE (last snapshot)
      if (!ok) renderTerritory();        // fallback only: static present, no scrubber/popups/fire
    } catch (e) {
      console.error("world map: manifest load failed", e);
      error = true;
    }
    if (mapViewEl && typeof ResizeObserver !== "undefined") { ro = new ResizeObserver(refit); ro.observe(mapViewEl); }
    preloadPlayerGifs(API_BASE).catch(() => {});   // warm the GIF cache so hovers don't wait on a load
  });
</script>

<div class="map-view" bind:this={mapViewEl} style="height:calc(100dvh - {headerH}px)">
  {#if timelineReady && snapshots.length}
    <div class="console" bind:this={consoleEl} style="width:{mapW ? mapW + 'px' : '100%'}">
      <TimelineScrubber {snapshots} index={tlIndex} {playing}
        on:scrub={(e) => onScrub(e.detail.index)}
        on:toggle={togglePlay} />
    </div>
  {/if}

  <div class="frame" style={mapW ? `width:${mapW}px;height:${mapH}px` : ""}>
    {#if error}
      <div class="msg">Map unavailable.</div>
    {:else if manifest}
      <div class="stage" bind:this={stageEl}>
        <img class="base" src={baseUrl()} alt="Mario Kart World map" />
        <!-- SP2 (territory) draws here, between the base and the icons -->
        <canvas class="territory" bind:this={terr} aria-hidden="true"></canvas>
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
        {#if stamp}<div class="datestamp"><span class="d">{stamp.date}</span><span class="t">{stamp.time}</span></div>{/if}
      </div>
    {:else}
      <div class="msg">Loading map…</div>
    {/if}
  </div>
</div>

<style>
  .map-view {
    display: flex; flex-direction: column; align-items: center; gap: 10px;
    padding: 12px; box-sizing: border-box; overflow: hidden;
  }

  /* Console: a slim graphite panel holding just the timeline scrubber, aligned to the map width. */
  .console { max-width: 100%; padding: 7px 12px; background: var(--panel); border: 1px solid var(--bd); border-radius: var(--r); }

  /* The map: a feed-style frame sized in JS to fill the remaining viewport height. */
  .frame {
    position: relative; flex: none; min-height: 140px;
    background: var(--feed-bg); border: 1px solid var(--bd);
    border-radius: var(--r); overflow: hidden;
  }
  .frame::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    border-radius: var(--r); box-shadow: inset 0 0 60px 10px rgba(0,0,0,.45);
  }
  .stage { position: relative; width: 100%; height: 100%; }
  /* Calm at rest: the whole map sits muted so the hovered course (and SP2's territory) leads. */
  .base { display: block; width: 100%; height: 100%; object-fit: cover; filter: saturate(.82) brightness(.82); }
  .territory { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
  .popups { position: absolute; inset: 0; pointer-events: none; }
  /* Date of the shown frame, stamped top-right on the map. Inter + tabular figures (not a mono
     readout) so it reads as a broadcast caption rather than a code stamp. */
  .datestamp {
    position: absolute; top: 10px; right: 12px; z-index: 60; pointer-events: none;
    display: flex; flex-direction: column; align-items: flex-end; gap: 1px;
    font-family: 'Inter', var(--ui); font-variant-numeric: tabular-nums;
    text-shadow: 0 1px 8px rgba(0,0,0,.85), 0 0 2px rgba(0,0,0,.9);
  }
  .datestamp .d { font-weight: 700; font-size: 12px; letter-spacing: .16em; text-transform: uppercase; color: #f3f4f6; }
  .datestamp .t { font-weight: 600; font-size: 11px; letter-spacing: .14em; color: #b9bbc0; }
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
  @media (max-width: 640px) { .map-view { height: auto !important; padding: 8px; } }
</style>
