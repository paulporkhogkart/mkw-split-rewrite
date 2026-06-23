<script>
  import { onMount, onDestroy } from "svelte";
  import { screen as screenStore, tells as tellsStore, rois as roisStore,
           minimap as minimapStore, trailRuns as trailRunsStore, trailLegend as trailLegendStore, sample as sampleStore,
           race as raceStore } from "../lib/stores.js";
  import { drawOverlay } from "../lib/overlay.js";

  // ── Props ─────────────────────────────────────────────────────────────────────
  /** @type {MediaStream|null} */
  export let stream = null;
  /** @type {boolean} */
  export let muted = false;
  /** @type {number} 0..1 */
  export let volume = 0.5;
  /** @type {boolean} */
  export let hidden = false;

  // ── DOM refs ──────────────────────────────────────────────────────────────────
  let videoEl = null;
  let canvasEl = null;
  let containerEl = null;

  // ── Canvas size (tracked by ResizeObserver) ───────────────────────────────────
  let canvasW = 0;
  let canvasH = 0;

  // ── Store values ──────────────────────────────────────────────────────────────
  let currentScreen = "-";
  let currentTells  = [];
  let currentRois   = {};
  let currentMinimap = null;
  let currentTrails  = [];
  let currentLegend  = [];
  let sampleImg      = null;   // decoded HTMLImageElement | null
  let raceFinishTime = null;   // non-null once the final time is detected
  let anchorMs   = null;       // engine race clock at the anchor | null = no clock yet this race
  let anchorWall = 0;          // performance.now() when anchored
  let staleMs    = null;       // a PREVIOUS race's final clock value, ignored until it changes

  const unsubScreen  = screenStore.subscribe(v  => { currentScreen  = v;      });
  const unsubTells   = tellsStore.subscribe(v   => { currentTells   = v ?? []; });
  const unsubRois    = roisStore.subscribe(v    => { currentRois    = v ?? {}; });
  const unsubMinimap = minimapStore.subscribe(v => { currentMinimap = v ?? null; });
  const unsubTrails  = trailRunsStore.subscribe(v  => { currentTrails  = v ?? []; });
  const unsubLegend  = trailLegendStore.subscribe(v => { currentLegend  = v ?? []; });
  const unsubRace    = raceStore.subscribe(v    => {
    raceFinishTime = v?.finishTime ?? null;
    onEngineClock(v?.elapsedMs ?? null);
  });
  const unsubSample  = sampleStore.subscribe(b64 => {
    if (!b64) { sampleImg = null; return; }
    const img = new Image();
    img.onload = () => { sampleImg = img; };
    img.src = "data:image/png;base64," + b64;
  });

  // ── Node → ROI-key maps (must mirror App.svelte constants exactly) ────────────
  const NODE_SELECTION = {
    CHARACTER_SELECT: ["char_name", "costume"],
    KART_SELECT:      ["kart_name"],
    COURSE_SELECT:    ["course_name"],
  };
  const NODE_HUD = {
    RACING: ["lap_current", "lap_total", "coin_left", "coin_right", "mushroom"],
  };

  // ── Derive active-ROI list for $screen ────────────────────────────────────────
  /**
   * Build the list of ROI descriptors to pass to drawOverlay.
   * - tell regions (kind='tell'):  every region in every group of the screen's tell tree.
   * - match ROIs  (kind='match'):  selection/HUD config ROIs for this screen.
   */
  function buildActiveRois(scr, tells, rois) {
    if (!scr || scr === "-") return [];
    const out = [];

    // ── tell regions ──────────────────────────────────────────────────────────
    const tell = tells.find(t => t.screen === scr);
    if (tell && Array.isArray(tell.groups)) {
      for (const group of tell.groups) {
        if (!Array.isArray(group)) continue;
        for (const region of group) {
          if (!region || !region.roi || region.roi.length < 4) continue;
          out.push({
            box:   region.roi,
            kind:  "tell",
            label: region.kind === "dark_loading" ? "dark-load" : undefined,
          });
        }
      }
    }

    // ── match ROIs (selection + HUD) ──────────────────────────────────────────
    const selKeys = NODE_SELECTION[scr] ?? [];
    const hudKeys = NODE_HUD[scr] ?? [];
    for (const key of [...selKeys, ...hudKeys]) {
      const box = rois[key];
      if (!box || box.length < 4) continue;
      out.push({ box, kind: "match", label: key });
    }

    return out;
  }

  // ── Reactive: wire stream to the video element ────────────────────────────────
  $: if (videoEl) videoEl.srcObject = stream ?? null;
  // Audio is played exclusively through App.svelte's Web Audio gain node (volume +
  // mute live there). The <video> element MUST stay muted or the stream's audio
  // track plays a second time - the "two audio streams" bug. `muted`/`volume`
  // props are retained for API compatibility but no longer drive playback.
  $: if (videoEl) { void muted; void volume; videoEl.muted = true; }

  // ── Reactive: active ROI list for the current screen ──────────────────────────
  $: activeRois = buildActiveRois(currentScreen, currentTells, currentRois);

  // The minimap reconstruction (ROI outline, tracking dot, trails, icon sample)
  // is only meaningful during a live race: RACING screen AND the final time not
  // yet detected. Outside that window none of it is drawn.
  $: mmActive = currentScreen === "RACING" && raceFinishTime == null;

  // ── Race clock: drives replay-dot interpolation ───────────────────────────────
  // Trails are recorded on the ENGINE's race clock (t=0 == GO, frozen through
  // pauses), so playback rides the same clock: the engine's race_time stream
  // (~10Hz via the race store), extrapolated with the wall clock between events
  // for smooth dot motion. A local "since RACING appeared" clock would run the
  // whole countdown ahead of every trail.
  // Until this race's first clock event (countdown + the timer's first digit
  // read) the clock is 0: ghosts wait on their start line.
  // PAUSE_SCREENS mirrors the engine's _PAUSE_SCREENS: returning from one keeps
  // the anchor (the engine clock froze meanwhile); any other fresh entry marks
  // the previous race's final clock value stale until a new value arrives.
  const PAUSE_SCREENS = new Set(["RACE_MENU", "HOME", "PHOTO_MODE", "EXIT_PHOTO_MODE"]);
  let _wasActive    = false;
  let _prevScreen   = null;
  let _raf          = 0;

  function onEngineClock(v) {
    if (v == null) return;
    if (staleMs != null && v === staleMs) return;   // previous race's frozen value
    staleMs = null;
    anchorMs = v;
    anchorWall = performance.now();
  }

  $: onActiveChange(mmActive, currentScreen);
  function onActiveChange(active, screen) {
    if (active && !_wasActive) {
      if (!PAUSE_SCREENS.has(_prevScreen)) {        // fresh race, not a pause return
        staleMs = anchorMs;
        anchorMs = null;
      }
      startLoop();
    } else if (!active && _wasActive) {
      stopLoop();
      redraw();
    }
    _wasActive  = active;
    _prevScreen = screen;
  }

  function startLoop() {
    cancelAnimationFrame(_raf);
    const tick = () => { redraw(); _raf = requestAnimationFrame(tick); };
    _raf = requestAnimationFrame(tick);
  }
  function stopLoop() { cancelAnimationFrame(_raf); _raf = 0; }

  /** Single draw of the whole overlay against current state. */
  function redraw() {
    if (!canvasEl || !(canvasW > 0) || !(canvasH > 0)) return;
    const ctx = canvasEl.getContext("2d");
    if (!ctx) return;
    if (canvasEl.width !== canvasW)  canvasEl.width  = canvasW;
    if (canvasEl.height !== canvasH) canvasEl.height = canvasH;
    const elapsed = mmActive
      ? (anchorMs != null ? anchorMs + (performance.now() - anchorWall) : 0)
      : null;
    drawOverlay(ctx, {
      canvasW, canvasH,
      rois:          activeRois,
      minimap:       mmActive ? currentMinimap : null,
      trails:        mmActive ? currentTrails : [],
      legend:        [],   // minimap legend dropped - the player panel identifies players
      sampleImg:     mmActive ? sampleImg : null,
      raceElapsedMs: elapsed,
    });
  }

  // Static redraws (ROI boxes, sample, canvas resize) happen on any input change.
  // While mmActive the rAF loop also redraws every frame so the dots move.
  $: { void activeRois; void currentMinimap; void currentTrails; void currentLegend; void sampleImg;
       void mmActive; void canvasW; void canvasH; redraw(); }

  // ── ResizeObserver: keep canvas dimensions in sync with container ─────────────
  let _ro = null;

  onMount(() => {
    if (!containerEl) return;
    _ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        canvasW = Math.round(width);
        canvasH = Math.round(height);
      }
    });
    _ro.observe(containerEl);
  });

  onDestroy(() => {
    unsubScreen();
    unsubTells();
    unsubRois();
    unsubMinimap();
    unsubTrails();
    unsubLegend();
    unsubRace();
    unsubSample();
    stopLoop();
    _ro?.disconnect();
  });
</script>

<!--
  Positioned container: video fills it, canvas sits exactly on top (pointer-events:none).
  The parent (.feed-area) is position:relative and flex:1 - this div fills it.
-->
<div class="feed-overlay-wrap" bind:this={containerEl}>
  <!-- svelte-ignore a11y-media-has-caption -->
  <video
    bind:this={videoEl}
    autoplay
    playsinline
    class="feed-video"
    class:feed-hidden={hidden || !stream}
  ></video>

  <canvas
    bind:this={canvasEl}
    class="overlay-canvas"
    aria-hidden="true"
  ></canvas>
</div>

<style>
  .feed-overlay-wrap {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }

  /* video rules already defined globally in App.svelte, but scoped here so the
     component is self-contained (Svelte scoping means these don't clash) */
  .feed-video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }

  .feed-hidden {
    display: none;
  }

  .overlay-canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }
</style>
