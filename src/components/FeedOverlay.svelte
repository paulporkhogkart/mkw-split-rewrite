<script>
  import { onMount, onDestroy } from "svelte";
  import { screen as screenStore, tells as tellsStore, rois as roisStore,
           minimap as minimapStore, replays as replaysStore, sample as sampleStore } from "../lib/stores.js";
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
  let currentScreen = "—";
  let currentTells  = [];
  let currentRois   = {};
  let currentMinimap = null;
  let currentReplays = [];
  let sampleImg      = null;   // decoded HTMLImageElement | null

  const unsubScreen  = screenStore.subscribe(v  => { currentScreen  = v;      });
  const unsubTells   = tellsStore.subscribe(v   => { currentTells   = v ?? []; });
  const unsubRois    = roisStore.subscribe(v    => { currentRois    = v ?? {}; });
  const unsubMinimap = minimapStore.subscribe(v => { currentMinimap = v ?? null; });
  const unsubReplays = replaysStore.subscribe(v => { currentReplays = v ?? []; });
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
    if (!scr || scr === "—") return [];
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

  // ── Reactive: wire stream / mute / volume to the video element ────────────────
  $: if (videoEl) videoEl.srcObject = stream ?? null;
  $: if (videoEl) videoEl.muted     = muted;
  $: if (videoEl) videoEl.volume    = volume;

  // ── Reactive: redraw when active ROIs, minimap, replays, sampleImg, or canvas size change ───
  $: activeRois = buildActiveRois(currentScreen, currentTells, currentRois);

  $: if (canvasEl && canvasW > 0 && canvasH > 0) {
    // Depend on all dynamic overlays so Svelte re-runs this block on any change.
    void currentMinimap; void currentReplays; void sampleImg;
    canvasEl.width  = canvasW;
    canvasEl.height = canvasH;
    const ctx = canvasEl.getContext("2d");
    if (ctx) drawOverlay(ctx, {
      canvasW, canvasH,
      rois:      activeRois,
      minimap:   currentMinimap,
      replays:   currentReplays,
      sampleImg,
    });
  }

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
    unsubReplays();
    unsubSample();
    _ro?.disconnect();
  });
</script>

<!--
  Positioned container: video fills it, canvas sits exactly on top (pointer-events:none).
  The parent (.feed-area) is position:relative and flex:1 — this div fills it.
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
