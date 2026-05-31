<script>
  /**
   * RoiCanvas — zoomable/pannable canvas that draws an engine-frame background,
   * coloured ROI rectangles, and drag/resize handles on the active ROI.
   *
   * Props:
   *   frame      {string|null}   data-URL of the background frame image (e.g. engineFrame)
   *   rois       {Array}         list of { box:[x1,y1,x2,y2], role:'active'|'sibling'|'other' }
   *   activeBox  {Array|null}    [x1,y1,x2,y2] of the currently-editable ROI (gets handles)
   *   frameW     {number}        logical frame width  (default 1920)
   *   frameH     {number}        logical frame height (default 1080)
   *
   * Events:
   *   change(newBox)   fired when a completed drag/resize produces a new [x1,y1,x2,y2]
   *
   * Methods (via bind:this):
   *   resetView()      snap zoom back to 1× and pan to 0,0
   */
  import { createEventDispatcher, onMount, onDestroy, afterUpdate } from "svelte";
  import { C } from "../lib/palette.js";

  export let frame     = null;        // data-URL string or null
  export let rois      = [];          // { box:[x1,y1,x2,y2], role:'active'|'sibling'|'other' }
  export let activeBox = null;        // [x1,y1,x2,y2] or null
  export let frameW    = 1920;
  export let frameH    = 1080;

  const dispatch = createEventDispatcher();

  // ── Zoom / pan state ──────────────────────────────────────────────────────────
  let fZoom = 1, fPanX = 0, fPanY = 0;
  let _fPanning = false, _fStart = null;

  // ── Drag state ────────────────────────────────────────────────────────────────
  let dragging       = false;
  let dragHandle     = null;
  let dragStartMouse = null;
  let dragStartRoi   = null;
  let hoveredHandle  = null;
  // Live box tracked during drag (drawn instead of activeBox prop so the rectangle
  // moves smoothly without waiting for a parent reactive update).
  let _liveBox       = null;

  const HANDLE_HIT_RADIUS = 9;

  // ROI colour map (same as App.svelte's editRois / editTabRois colour logic)
  // role: 'active' → accent, 'sibling' → neutral grey, 'other' → warn
  const ROLE_COLOR = {
    active:  C.accent,
    sibling: C.roiCtx,
    other:   C.warn,
  };

  // ── Canvas element ────────────────────────────────────────────────────────────
  let canvasEl = null;

  // Cached Image object for the frame so we can drawImage without re-decoding.
  let _frameImg = null;
  let _frameSrc = null;   // track which URL is currently loaded

  $: if (frame !== _frameSrc) {
    _frameSrc = frame;
    if (frame) {
      const img = new Image();
      img.onload  = () => { _frameImg = img; scheduleRedraw(); };
      img.onerror = () => { _frameImg = null; scheduleRedraw(); };
      img.src = frame;
    } else {
      _frameImg = null;
      scheduleRedraw();
    }
  }

  // ── Coordinate helpers (ported 1-for-1 from App.svelte) ──────────────────────

  function getTransform() {
    if (!canvasEl) return null;
    const rect = canvasEl.getBoundingClientRect();
    const bw = canvasEl.clientWidth, bh = canvasEl.clientHeight;
    if (!bw || !bh) return null;
    const z = fZoom, px = fPanX, py = fPanY;
    const pyw = frameW || 1920, pyh = frameH || 1080;
    const eAR = bw / bh, vAR = pyw / pyh;
    let rendW, rendH, ox, oy;
    if (vAR > eAR) { rendW = bw;   rendH = bw / vAR;  ox = 0;             oy = (bh - rendH) / 2; }
    else            { rendH = bh;   rendW = bh * vAR;  ox = (bw - rendW) / 2; oy = 0; }
    return { ox, oy, sx: rendW / pyw, sy: rendH / pyh, rect, z, px, py };
  }

  function frameToCanvas(fx, fy, t) {
    return { cx: t.px + (t.ox + fx * t.sx) * t.z,
             cy: t.py + (t.oy + fy * t.sy) * t.z };
  }

  function canvasToFrame(clientX, clientY, t) {
    const mx = clientX - t.rect.left, my = clientY - t.rect.top;
    return { fx: ((mx - t.px) / t.z - t.ox) / t.sx,
             fy: ((my - t.py) / t.z - t.oy) / t.sy };
  }

  function _clampPan() {
    if (!canvasEl) return;
    const W = canvasEl.clientWidth, H = canvasEl.clientHeight, OVER = 100;
    fPanX = Math.min(OVER, Math.max(W * (1 - fZoom) - OVER, fPanX));
    fPanY = Math.min(OVER, Math.max(H * (1 - fZoom) - OVER, fPanY));
  }

  function getHandlePositions(roi) {
    if (!roi || roi.length < 4) return [];
    const [x1, y1, x2, y2] = roi, mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return [
      { id:"tl", fx:x1, fy:y1, cursor:"nw-resize" }, { id:"tr", fx:x2, fy:y1, cursor:"ne-resize" },
      { id:"bl", fx:x1, fy:y2, cursor:"sw-resize" }, { id:"br", fx:x2, fy:y2, cursor:"se-resize" },
      { id:"t",  fx:mx, fy:y1, cursor:"n-resize"  }, { id:"b",  fx:mx, fy:y2, cursor:"s-resize"  },
      { id:"l",  fx:x1, fy:my, cursor:"w-resize"  }, { id:"r",  fx:x2, fy:my, cursor:"e-resize"  },
    ];
  }

  function hitTest(clientX, clientY, roi) {
    const t = getTransform();
    if (!t || !roi || roi.length < 4) return null;
    const mx = clientX - t.rect.left, my = clientY - t.rect.top;
    for (const h of getHandlePositions(roi)) {
      const c = frameToCanvas(h.fx, h.fy, t);
      if (Math.hypot(mx - c.cx, my - c.cy) <= HANDLE_HIT_RADIUS)
        return { handle: h.id, cursor: h.cursor };
    }
    const a = frameToCanvas(roi[0], roi[1], t), b = frameToCanvas(roi[2], roi[3], t);
    if (mx >= a.cx && mx <= b.cx && my >= a.cy && my <= b.cy)
      return { handle:"move", cursor:"move" };
    return null;
  }

  function applyDrag(roi, handle, dx, dy) {
    let [x1, y1, x2, y2] = roi;
    const MIN = 4, W = frameW || 1920, H = frameH || 1080;
    if      (handle === "tl")   { x1 += dx; y1 += dy; }
    else if (handle === "tr")   { x2 += dx; y1 += dy; }
    else if (handle === "bl")   { x1 += dx; y2 += dy; }
    else if (handle === "br")   { x2 += dx; y2 += dy; }
    else if (handle === "t")    { y1 += dy; }
    else if (handle === "b")    { y2 += dy; }
    else if (handle === "l")    { x1 += dx; }
    else if (handle === "r")    { x2 += dx; }
    else if (handle === "move") { x1 += dx; x2 += dx; y1 += dy; y2 += dy; }
    x1 = Math.max(0, Math.min(x1, W - MIN)); x2 = Math.max(x1 + MIN, Math.min(x2, W));
    y1 = Math.max(0, Math.min(y1, H - MIN)); y2 = Math.max(y1 + MIN, Math.min(y2, H));
    return [Math.round(x1), Math.round(y1), Math.round(x2), Math.round(y2)];
  }

  // ── Drawing ───────────────────────────────────────────────────────────────────

  function _drawOneRoi(ctx, t, roi, color, showHandles) {
    if (!roi || roi.length < 4) return;
    const a = frameToCanvas(roi[0], roi[1], t), b = frameToCanvas(roi[2], roi[3], t);
    const cx1 = a.cx, cy1 = a.cy, cw = b.cx - a.cx, ch = b.cy - a.cy;
    ctx.strokeStyle = "rgba(0,0,0,0.7)"; ctx.lineWidth = 4; ctx.setLineDash([]);
    ctx.strokeRect(cx1, cy1, cw, ch);
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash([7, 4]);
    ctx.strokeRect(cx1, cy1, cw, ch); ctx.setLineDash([]);
    if (showHandles) {
      for (const h of getHandlePositions(roi)) {
        const hc = frameToCanvas(h.fx, h.fy, t), hcx = hc.cx, hcy = hc.cy, r = 5;
        const active = hoveredHandle === h.id || (dragging && dragHandle === h.id);
        ctx.fillStyle   = active ? C.accent : color;
        ctx.strokeStyle = "rgba(0,0,0,0.85)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.rect(hcx - r, hcy - r, r * 2, r * 2); ctx.fill(); ctx.stroke();
      }
    }
  }

  function redraw() {
    if (!canvasEl) return;
    const t = getTransform(); if (!t) return;

    // Match canvas backing-store to display size (avoids blurry pixels)
    canvasEl.width  = canvasEl.clientWidth;
    canvasEl.height = canvasEl.clientHeight;

    const ctx = canvasEl.getContext("2d");
    ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);

    // ── Background frame ──────────────────────────────────────────────────────
    if (_frameImg) {
      // The frame fills the same letterboxed/pillarboxed area the transform math
      // assumes, and the zoom/pan is applied on top.
      const { ox, oy, sx, sy, z, px, py } = t;
      const dw = (frameW || 1920) * sx, dh = (frameH || 1080) * sy;
      ctx.drawImage(_frameImg, px + ox * z, py + oy * z, dw * z, dh * z);
    }

    // ── ROI boxes ─────────────────────────────────────────────────────────────
    // Draw inactive ROIs first, then the active one on top (with handles).
    for (const re of rois) {
      if (re.role === "active") continue;
      _drawOneRoi(ctx, t, re.box, ROLE_COLOR[re.role] ?? C.warn, false);
    }
    // Active box: during a drag use the internally-tracked live position so the
    // rectangle tracks the mouse smoothly without waiting for a parent update.
    const drawnActiveBox = _liveBox ?? activeBox;
    if (drawnActiveBox) {
      _drawOneRoi(ctx, t, drawnActiveBox, ROLE_COLOR["active"], true);
    }
  }

  let _rafId = 0;
  function scheduleRedraw() {
    if (_rafId) return;
    _rafId = requestAnimationFrame(() => { _rafId = 0; redraw(); });
  }

  afterUpdate(scheduleRedraw);

  // ── Public API ────────────────────────────────────────────────────────────────

  /** Snap zoom back to 1× and reset pan to origin. */
  export function resetView() {
    fZoom = 1; fPanX = 0; fPanY = 0;
    scheduleRedraw();
  }

  // ── Event handlers ────────────────────────────────────────────────────────────

  function onMouseDown(e) {
    // 1. Hit-test the active ROI's handles / body first.
    // Use _liveBox if it exists (mid-drag) otherwise the prop value.
    const currentBox = _liveBox ?? activeBox;
    if (currentBox) {
      const hit = hitTest(e.clientX, e.clientY, currentBox);
      if (hit) {
        const t = getTransform();
        const fr = canvasToFrame(e.clientX, e.clientY, t);
        dragging = true; dragHandle = hit.handle;
        dragStartRoi = [...currentBox]; dragStartMouse = { x: fr.fx, y: fr.fy };
        _liveBox = [...currentBox];
        e.preventDefault(); return;
      }
    }
    // 2. Hit-test inactive ROIs — if a click lands on one, emit 'select' so
    //    the parent can switch the active region (App.svelte task 5.4 will use this).
    for (const re of rois) {
      if (re.role === "active" || !re.box) continue;
      if (hitTest(e.clientX, e.clientY, re.box)) {
        dispatch("select", re);
        hoveredHandle = null;
        e.preventDefault(); return;
      }
    }
    // 3. Nothing hit → start panning
    _fPanning = true;
    _fStart = { x: e.clientX, y: e.clientY, px: fPanX, py: fPanY };
    e.preventDefault();
  }

  function onMouseMove(e) {
    if (_fPanning) {
      fPanX = _fStart.px + (e.clientX - _fStart.x);
      fPanY = _fStart.py + (e.clientY - _fStart.y);
      _clampPan();
      scheduleRedraw();
      return;
    }
    if (!dragging) {
      // Update hover highlight on the active ROI
      const currentBox = _liveBox ?? activeBox;
      const hit = currentBox ? hitTest(e.clientX, e.clientY, currentBox) : null;
      const nh = hit?.handle ?? null;
      if (nh !== hoveredHandle) { hoveredHandle = nh; scheduleRedraw(); }
      if (canvasEl) canvasEl.style.cursor = hit?.cursor ?? (fZoom > 1 ? "grab" : "default");
      return;
    }
    const t = getTransform(); if (!t) return;
    const fr = canvasToFrame(e.clientX, e.clientY, t);
    const dx = fr.fx - dragStartMouse.x, dy = fr.fy - dragStartMouse.y;
    _liveBox = applyDrag(dragStartRoi, dragHandle, dx, dy);
    scheduleRedraw();
  }

  function onMouseUp() {
    if (_fPanning) { _fPanning = false; return; }
    if (!dragging) return;
    dragging = false;
    // Dispatch the committed final box so the parent can send the IPC update.
    if (_liveBox) dispatch("change", _liveBox);
    _liveBox = null;
    dragHandle = null; dragStartRoi = null; dragStartMouse = null;
    hoveredHandle = null;
    scheduleRedraw();
  }

  function onWheel(e) {
    if (!canvasEl) return;
    e.preventDefault();
    const r = canvasEl.getBoundingClientRect();
    const u = e.clientX - r.left, v = e.clientY - r.top;
    const nz = Math.min(8, Math.max(1, fZoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    fPanX += u * (1 - nz / fZoom);
    fPanY += v * (1 - nz / fZoom);
    fZoom = nz;
    if (nz === 1) { fPanX = 0; fPanY = 0; }  // fully zoomed out → snap to fit
    else _clampPan();
    scheduleRedraw();
  }

  // Window-level mouseup so drag releases outside the canvas are not lost.
  // Wheel needs {passive:false} so preventDefault() can stop page scroll;
  // Svelte's on:wheel modifier can't set passive:false, so we use addEventListener.
  onMount(() => {
    window.addEventListener("mouseup", onMouseUp);
    if (canvasEl) canvasEl.addEventListener("wheel", onWheel, { passive: false });
  });
  onDestroy(() => {
    window.removeEventListener("mouseup", onMouseUp);
    if (canvasEl) canvasEl.removeEventListener("wheel", onWheel);
    if (_rafId) cancelAnimationFrame(_rafId);
  });

  $: fZoom, fPanX, fPanY, scheduleRedraw();
  // When the parent updates activeBox (e.g. a new region was selected), clear any
  // stale liveBox so the fresh prop value is drawn immediately.
  $: if (activeBox && !dragging) { _liveBox = null; scheduleRedraw(); }
  $: rois, scheduleRedraw();
</script>

<!-- svelte-ignore a11y-no-static-element-interactions -->
<canvas
  bind:this={canvasEl}
  class="roi-canvas"
  on:mousedown={onMouseDown}
  on:mousemove={onMouseMove}
></canvas>

{#if fZoom > 1}
  <button class="zoom-reset" on:click={resetView}>reset {fZoom.toFixed(1)}×</button>
{/if}

<style>
  .roi-canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: default;
  }
  .zoom-reset {
    position: absolute;
    right: 6px;
    top: 6px;
    z-index: 2;
    background: var(--panel);
    border: 1px solid var(--bd);
    color: var(--accent);
    border-radius: var(--r);
    font-family: var(--mono);
    font-size: .6rem;
    padding: 2px 6px;
    cursor: pointer;
  }
  .zoom-reset:hover { background: var(--accent-bg); }
</style>
