import { C, REPLAY_HUES } from "./palette.js";

/** Map ROI kind to its stroke/label color. */
const KIND_COLOR = {
  tell:    C.accent,
  match:   C.ok,
  context: C.roiCtx,
};

// Minimap ROI in full-frame 1080p pixels: [x1, y1, x2, y2]
const MINIMAP_BOX = [1442, 251, 1908, 1047];

/** Track-state → ring color */
const TRACK_STATE_COLOR = {
  tracking:  C.mmRingFace,
  ring_only: C.mmRingOnly,
  reacquire: C.mmReacquire,
};

/**
 * Map a single 1080p point to canvas pixel coords using the display rectangle.
 *
 * @param {number} x  Source x in 1080p pixels.
 * @param {number} y  Source y in 1080p pixels.
 * @param {{ x:number, y:number, w:number, h:number }} displayRect
 * @param {number} [srcW=1920]
 * @param {number} [srcH=1080]
 * @returns {{ x:number, y:number }}
 */
export function pointToScreen(x, y, displayRect, srcW = 1920, srcH = 1080) {
  const scaleX = displayRect.w / srcW;
  const scaleY = displayRect.h / srcH;
  return {
    x: displayRect.x + x * scaleX,
    y: displayRect.y + y * scaleY,
  };
}

/**
 * Compute the object-fit:contain display rectangle for a 1920×1080 source
 * inside a canvas of `canvasW × canvasH` (letterbox: equal scale, centered).
 *
 * @param {number} canvasW  Canvas pixel width.
 * @param {number} canvasH  Canvas pixel height.
 * @param {number} [srcW=1920]
 * @param {number} [srcH=1080]
 * @returns {{ x:number, y:number, w:number, h:number }}
 *   The sub-rectangle of the canvas that contains the scaled source.
 *   Returns a zero-rect `{x:0,y:0,w:0,h:0}` on invalid (zero / NaN) inputs.
 */
export function computeDisplayRect(canvasW, canvasH, srcW = 1920, srcH = 1080) {
  if (!canvasW || !canvasH || !srcW || !srcH ||
      !isFinite(canvasW) || !isFinite(canvasH) ||
      !isFinite(srcW)    || !isFinite(srcH)) {
    return { x: 0, y: 0, w: 0, h: 0 };
  }

  // Scale that fits the source entirely within the canvas (contain).
  const scale = Math.min(canvasW / srcW, canvasH / srcH);
  const w = srcW * scale;
  const h = srcH * scale;
  const x = (canvasW - w) / 2;
  const y = (canvasH - h) / 2;
  return { x, y, w, h };
}

/**
 * Map a 1080p `[x1, y1, x2, y2]` box to canvas-pixel `{ x, y, w, h }` using
 * the display rectangle produced by `computeDisplayRect`.
 *
 * @param {[number,number,number,number]} box  `[x1, y1, x2, y2]` in 1080p pixels.
 * @param {{ x:number, y:number, w:number, h:number }} displayRect
 * @param {number} [srcW=1920]
 * @param {number} [srcH=1080]
 * @returns {{ x:number, y:number, w:number, h:number }}
 */
export function roiToScreen(box, displayRect, srcW = 1920, srcH = 1080) {
  const [x1, y1, x2, y2] = box;
  const scaleX = displayRect.w / srcW;
  const scaleY = displayRect.h / srcH;
  return {
    x: displayRect.x + x1 * scaleX,
    y: displayRect.y + y1 * scaleY,
    w: (x2 - x1) * scaleX,
    h: (y2 - y1) * scaleY,
  };
}

/**
 * Draw ROI overlays onto `ctx`, mapping 1080p coordinates to the letterboxed
 * display rectangle.  Pure function of its inputs — no DOM/global state beyond
 * the passed canvas 2D context.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {{
 *   canvasW:   number,
 *   canvasH:   number,
 *   rois:      Array<{
 *     box:   [number,number,number,number],
 *     kind:  'tell'|'match'|'context',
 *     label?: string,
 *     score?: number,
 *   }>,
 *   minimap?:  { cx:number, cy:number, radius:number, trackState:string } | null,
 *   replays?:  Array<{ id:string|number, points:[number,number][] }>,
 *   sampleImg?: HTMLImageElement | null,
 * }} opts
 */
export function drawOverlay(ctx, opts) {
  const { canvasW, canvasH, rois, minimap = null, replays = [], sampleImg = null } = opts;

  ctx.clearRect(0, 0, canvasW, canvasH);

  const displayRect = computeDisplayRect(canvasW, canvasH);
  if (displayRect.w === 0 || displayRect.h === 0) return;

  const scale = displayRect.w / 1920;

  ctx.save();

  // Crisp 1px-aligned pixel grid.
  ctx.translate(0.5, 0.5);

  // ── Minimap ROI outline ────────────────────────────────────────────────────
  {
    const mmR = roiToScreen(MINIMAP_BOX, displayRect);
    ctx.save();
    ctx.strokeStyle = C.roiCtx;
    ctx.lineWidth   = 1;
    ctx.globalAlpha = 0.5;
    ctx.strokeRect(Math.round(mmR.x), Math.round(mmR.y), Math.round(mmR.w), Math.round(mmR.h));
    ctx.restore();
  }

  // ── Replay trails ──────────────────────────────────────────────────────────
  if (replays && replays.length > 0) {
    for (let i = 0; i < replays.length; i++) {
      const trail = replays[i];
      if (!trail || !Array.isArray(trail.points) || trail.points.length < 2) continue;
      const color = REPLAY_HUES[i % REPLAY_HUES.length];
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth   = 1.4;
      ctx.globalAlpha = 0.5;
      ctx.lineJoin    = "round";
      ctx.lineCap     = "round";
      ctx.beginPath();
      const p0 = pointToScreen(trail.points[0][0], trail.points[0][1], displayRect);
      ctx.moveTo(p0.x, p0.y);
      for (let j = 1; j < trail.points.length; j++) {
        const p = pointToScreen(trail.points[j][0], trail.points[j][1], displayRect);
        ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
      ctx.restore();
    }
  }

  // ── Live tracked icon ──────────────────────────────────────────────────────
  if (minimap) {
    const color = TRACK_STATE_COLOR[minimap.trackState] ?? C.mmRingFace;
    const ctr   = pointToScreen(minimap.cx, minimap.cy, displayRect);
    const r     = minimap.radius * scale;

    // Black halo under the ring for legibility.
    ctx.save();
    ctx.beginPath();
    ctx.arc(ctr.x, ctr.y, r + 2, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.lineWidth   = 4;
    ctx.stroke();
    ctx.restore();

    // Colored ring.
    ctx.save();
    ctx.beginPath();
    ctx.arc(ctr.x, ctr.y, r, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth   = 2;
    ctx.stroke();
    ctx.restore();

    // Small filled dot at centre.
    ctx.save();
    ctx.beginPath();
    ctx.arc(ctr.x, ctr.y, Math.max(2, r * 0.25), 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();
  }

  // ── Icon sample inset ──────────────────────────────────────────────────────
  if (sampleImg) {
    const mmR     = roiToScreen(MINIMAP_BOX, displayRect);
    const INSET   = 4 * scale;
    const SIZE    = 28 * scale;
    const ix      = mmR.x + INSET;
    const iy      = mmR.y + INSET;

    ctx.save();
    // Thin border.
    ctx.strokeStyle = C.txDim;
    ctx.lineWidth   = 1;
    ctx.strokeRect(ix, iy, SIZE, SIZE);
    ctx.drawImage(sampleImg, ix, iy, SIZE, SIZE);
    ctx.restore();
  }

  // ── ROI boxes (existing behavior) ─────────────────────────────────────────
  if (rois && rois.length > 0) {
    for (const roi of rois) {
      const color = KIND_COLOR[roi.kind] ?? C.roiCtx;
      const r = roiToScreen(roi.box, displayRect);

      // --- Stroke the ROI rectangle ---
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth   = 1.5;
      ctx.lineJoin    = "round";
      ctx.strokeRect(Math.round(r.x), Math.round(r.y), Math.round(r.w), Math.round(r.h));
      ctx.restore();

      // --- Optional label tag ---
      if (roi.label) {
        ctx.save();

        const FONT_SIZE  = 9;
        const PAD_X      = 4;
        const PAD_Y      = 2;
        const TAG_RADIUS = 2;

        ctx.font = `${FONT_SIZE}px sans-serif`;

        let text = roi.label;
        if (typeof roi.score === "number" && isFinite(roi.score)) {
          text += ` · ${roi.score.toFixed(2)}`;
        }

        const textW  = ctx.measureText(text).width;
        const tagW   = textW + PAD_X * 2;
        const tagH   = FONT_SIZE + PAD_Y * 2;

        // Position just above the box; clamp so it never overflows the top edge.
        const tagX = Math.round(r.x);
        const tagY = Math.max(0, Math.round(r.y) - tagH - 1);

        // Translucent dark pill background.
        ctx.fillStyle = "rgba(11,12,14,0.8)";
        ctx.beginPath();
        if (ctx.roundRect) {
          ctx.roundRect(tagX, tagY, tagW, tagH, TAG_RADIUS);
        } else {
          // Fallback for environments without roundRect (e.g. Node test stubs).
          ctx.rect(tagX, tagY, tagW, tagH);
        }
        ctx.fill();

        // Label text in the kind's color.
        ctx.fillStyle    = color;
        ctx.textBaseline = "top";
        ctx.fillText(text, tagX + PAD_X, tagY + PAD_Y);

        ctx.restore();
      }
    }
  }

  ctx.restore();
}
