import { C, REPLAY_HUES } from "./palette.js";

/** Map ROI kind to its stroke/label color. */
const KIND_COLOR = {
  tell:    C.accent,
  match:   C.ok,
  context: C.roiCtx,
};

/** Track-state → marker color (mirrors mkw_tracker/overlay/minimap.py semantics). */
const TRACK_STATE_COLOR = {
  tracking:  C.mmRingFace,
  ring_only: C.mmRingOnly,
  reacquire: C.mmReacquire,
};

/** Track-state → short tag shown beside the live marker square. */
const MARKER_LABEL = {
  tracking:  "lock",
  ring_only: "ring",
  reacquire: "reacq",
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

/** Darken (or lighten) a `#rrggbb` hex by `d` per channel → `"rgb(r,g,b)"`. */
function shade(hex, d) {
  const n = parseInt(hex.slice(1), 16);
  const c = (v) => Math.max(0, Math.min(255, v + d));
  return `rgb(${c(n >> 16)},${c((n >> 8) & 255)},${c(n & 255)})`;
}

/**
 * Linear-interpolate a replay's `[x, y]` position at race-elapsed time `tMs`.
 * `points` are `[t_ms, x, y]` sorted by t_ms. Mirrors `MinimapPlayer._interpolate`:
 * clamps to the first/last sample outside the recorded window.
 *
 * @param {[number,number,number][]} points
 * @param {number} tMs
 * @returns {[number,number] | null}
 */
function interpolateXY(points, tMs) {
  if (!points || points.length === 0) return null;
  if (tMs <= points[0][0]) return [points[0][1], points[0][2]];
  const last = points[points.length - 1];
  if (tMs >= last[0]) return [last[1], last[2]];
  let lo = 0, hi = points.length - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] <= tMs) lo = mid; else hi = mid;
  }
  const [t0, x0, y0] = points[lo];
  const [t1, x1, y1] = points[hi];
  if (t1 === t0) return [x0, y0];
  const f = (tMs - t0) / (t1 - t0);
  return [x0 + f * (x1 - x0), y0 + f * (y1 - y0)];
}

/** Draw a bold X (abandoned-run marker) centered at (cx,cy). Mirrors `_draw_x`. */
function drawX(ctx, cx, cy, r, color) {
  const arm = Math.max(3, r);
  const w   = Math.max(2, r / 2);
  ctx.save();
  ctx.lineCap = "round";
  for (const pass of [{ c: shade(color, -60), lw: w + 2 }, { c: color, lw: w }]) {
    ctx.strokeStyle = pass.c;
    ctx.lineWidth   = pass.lw;
    ctx.beginPath();
    ctx.moveTo(cx - arm, cy - arm); ctx.lineTo(cx + arm, cy + arm);
    ctx.moveTo(cx + arm, cy - arm); ctx.lineTo(cx - arm, cy + arm);
    ctx.stroke();
  }
  ctx.restore();
}

/** Small pill tag (dark bg + colored text) anchored with its bottom-left at (x, yBottom). */
function drawTag(ctx, x, yBottom, text, color) {
  const FS = 9, PADX = 4, PADY = 2, R = 2;
  ctx.save();
  ctx.font = `${FS}px sans-serif`;
  const w  = ctx.measureText(text).width + PADX * 2;
  const h  = FS + PADY * 2;
  const tx = Math.round(x);
  const ty = Math.max(0, Math.round(yBottom) - h - 1);
  ctx.fillStyle = "rgba(11,12,14,0.82)";
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(tx, ty, w, h, R); else ctx.rect(tx, ty, w, h);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.textBaseline = "top";
  ctx.fillText(text, tx + PADX, ty + PADY);
  ctx.restore();
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
 * display rectangle.  Pure function of its inputs - no DOM/global state beyond
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
 *   minimap?:  { cx:number, cy:number, radius:number, trackState:string,
 *                roi?:[number,number,number,number] } | null,   // roi = [x,y,w,h] per-map
 *   replays?:  Array<{ id:string|number, points:[number,number,number][],
 *                      is_pb?:boolean, total_time?:string|null }>,
 *   sampleImg?: HTMLImageElement | null,
 *   raceElapsedMs?:  number | null,  // race clock → interpolates the replay dots
 * }} opts
 */
export function drawOverlay(ctx, opts) {
  const { canvasW, canvasH, rois, minimap = null, replays = [], sampleImg = null,
          raceElapsedMs = null } = opts;

  ctx.clearRect(0, 0, canvasW, canvasH);

  const displayRect = computeDisplayRect(canvasW, canvasH);
  if (displayRect.w === 0 || displayRect.h === 0) return;

  const scale = displayRect.w / 1920;

  ctx.save();

  // Crisp 1px-aligned pixel grid.
  ctx.translate(0.5, 0.5);

  // ── Minimap ROI outline - the actual per-map ROI sent by the backend ─────────
  // (minimap.roi = [x, y, w, h] in 1080p). Drawn from the lock payload so the box
  // always matches the per-course ROI the detector is using, never a stale
  // hardcoded rectangle.
  if (minimap && Array.isArray(minimap.roi) && minimap.roi.length === 4) {
    const [rx, ry, rw, rh] = minimap.roi;
    const mmR = roiToScreen([rx, ry, rx + rw, ry + rh], displayRect);
    ctx.save();
    ctx.strokeStyle = C.roiCtx;
    ctx.lineWidth   = 1;
    ctx.globalAlpha = 0.6;
    ctx.strokeRect(Math.round(mmR.x), Math.round(mmR.y), Math.round(mmR.w), Math.round(mmR.h));
    ctx.restore();
  }

  // ── Replay dots (time-interpolated moving dots; mirrors MinimapPlayer) ───────
  // Each replay shows a single dot at its recorded position for the current
  // race-elapsed time - not a static path. PB dots are larger; an abandoned run
  // (no total_time) becomes an X once its clock runs out.
  if (replays && replays.length > 0 && raceElapsedMs != null) {
    for (let i = 0; i < replays.length; i++) {
      const trail = replays[i];
      if (!trail || !Array.isArray(trail.points) || trail.points.length === 0) continue;
      const pos = interpolateXY(trail.points, raceElapsedMs);
      if (!pos) continue;
      const color = REPLAY_HUES[i % REPLAY_HUES.length];
      const p     = pointToScreen(pos[0], pos[1], displayRect);
      const dotR  = Math.max(3, 5 * scale) + (trail.is_pb ? 2 : 0);
      const lastT = trail.points[trail.points.length - 1][0];
      const abandoned = trail.total_time == null && !trail.is_pb;

      ctx.save();
      if (raceElapsedMs >= lastT && abandoned) {
        drawX(ctx, p.x, p.y, dotR, color);
      } else {
        // Dark halo for legibility, then the colored dot (mirrors cv2 shadow+fill).
        ctx.beginPath(); ctx.arc(p.x, p.y, dotR + 1, 0, Math.PI * 2);
        ctx.fillStyle = shade(color, -55); ctx.fill();
        ctx.beginPath(); ctx.arc(p.x, p.y, dotR, 0, Math.PI * 2);
        ctx.fillStyle = color; ctx.fill();
      }
      ctx.restore();
    }
  }

  // ── Live tracked marker - square + state tag (echoes the backend overlay,
  //    which reads cleaner than the old ring + dot). ────────────────────────────
  if (minimap) {
    const color = TRACK_STATE_COLOR[minimap.trackState] ?? C.mmRingFace;
    const ctr   = pointToScreen(minimap.cx, minimap.cy, displayRect);
    // Fixed marker size - the player icon is a fixed-size sprite, so the square
    // must stay constant. minimap.radius is the tracker's noisy per-frame Hough
    // estimate (used for detection, not display); sizing the square from it made
    // it visibly pulse. The backend overlay likewise draws a fixed crosshair.
    const half  = Math.max(8, 16 * scale);

    // Square with a black halo for legibility on any background.
    ctx.save();
    ctx.strokeStyle = "rgba(0,0,0,0.75)";
    ctx.lineWidth   = 3;
    ctx.strokeRect(ctr.x - half, ctr.y - half, half * 2, half * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1.5;
    ctx.strokeRect(ctr.x - half, ctr.y - half, half * 2, half * 2);
    ctx.restore();

    // State tag above the square.
    drawTag(ctx, ctr.x - half, ctr.y - half, MARKER_LABEL[minimap.trackState] ?? "lock", color);
  }

  // ── Icon sample inset - anchored to the per-map ROI's top-left corner ───────
  if (sampleImg && minimap && Array.isArray(minimap.roi) && minimap.roi.length === 4) {
    const [rx, ry, rw, rh] = minimap.roi;
    const mmR     = roiToScreen([rx, ry, rx + rw, ry + rh], displayRect);
    const INSET   = 4 * scale;
    const SIZE    = 28 * scale;
    const ix      = mmR.x + INSET;
    const iy      = mmR.y + INSET;

    ctx.save();
    ctx.drawImage(sampleImg, ix, iy, SIZE, SIZE);
    ctx.strokeStyle = C.txDim;
    ctx.lineWidth   = 1;
    ctx.strokeRect(ix, iy, SIZE, SIZE);
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
