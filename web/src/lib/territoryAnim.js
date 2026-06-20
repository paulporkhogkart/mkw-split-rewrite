// Pure transition math for the territory "invasion" animation. No DOM.
// On a capture, the new owner's colour sweeps across the captured cell as a FRONT advancing from
// where that owner already holds ground (a geodesic reveal); a first claim with no adjacent
// territory grows from its course centre instead. Heavy work (unified owners, nearest-course field,
// the per-cell reveal field) is precomputed once per transition; interpolatePatch is the per-frame
// call: it thresholds the reveal at the eased progress, gooey-smooths the front, lens-paints it, and
// adds a hot leading edge that fades out at the endpoints.
import { boxBlur, gooeyPartition, borderDistance, paintLens, hexRgb, LENS } from "./territory.js";

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const isHex = (c) => c && /^#[0-9a-f]{6}$/i.test(c);

// Unified owner palette across both snapshots; every course is a seed. Unclaimed -> one
// non-paintable index. Returns per-course owner index for A and B in the shared space.
function unifiedOwners(manifestCourses, rowsA, rowsB) {
  const colA = Object.fromEntries(rowsA.map((r) => [r.slug, r.color]));
  const colB = Object.fromEntries(rowsB.map((r) => [r.slug, r.color]));
  const idxOf = {}, ownerRgb = [], paintable = [];
  let unclaimed = -1;
  const ensure = (color) => {
    if (!isHex(color)) { if (unclaimed < 0) { unclaimed = ownerRgb.length; ownerRgb.push([0, 0, 0]); paintable.push(false); } return unclaimed; }
    if (!(color in idxOf)) { idxOf[color] = ownerRgb.length; ownerRgb.push(hexRgb(color)); paintable.push(true); }
    return idxOf[color];
  };
  const centers = [], ownerOfA = [], ownerOfB = [];
  for (const c of manifestCourses) {
    centers.push([c.hit.x + c.hit.w / 2, c.hit.y + c.hit.h / 2]);
    ownerOfA.push(ensure(colA[c.slug]));
    ownerOfB.push(ensure(colB[c.slug]));
  }
  return { centers, ownerOfA, ownerOfB, ownerRgb, paintable };
}

// Index of the nearest course centre for every pixel (Voronoi seed id).
function nearestCourse(W, H, centersPx) {
  const nc = new Int16Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let bi = 0, bd = 1e18;
    for (let i = 0; i < centersPx.length; i++) { const dx = centersPx[i][0] - x, dy = centersPx[i][1] - y, dd = dx * dx + dy * dy; if (dd < bd) { bd = dd; bi = i; } }
    nc[y * W + x] = bi;
  }
  return nc;
}

// Two-pass chamfer distance from a seed mask (1 = seed). Unreached pixels keep ~1e9.
function chamferDist(seed, W, H) {
  const d = new Float32Array(W * H).fill(1e9);
  for (let p = 0; p < W * H; p++) if (seed[p]) d[p] = 0;
  const O = 1, D = 1.41421;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const p = y * W + x; let m = d[p];
    if (x > 0) m = Math.min(m, d[p - 1] + O); if (y > 0) m = Math.min(m, d[p - W] + O);
    if (x > 0 && y > 0) m = Math.min(m, d[p - W - 1] + D); if (x < W - 1 && y > 0) m = Math.min(m, d[p - W + 1] + D); d[p] = m; }
  for (let y = H - 1; y >= 0; y--) for (let x = W - 1; x >= 0; x--) { const p = y * W + x; let m = d[p];
    if (x < W - 1) m = Math.min(m, d[p + 1] + O); if (y < H - 1) m = Math.min(m, d[p + W] + O);
    if (x < W - 1 && y < H - 1) m = Math.min(m, d[p + W + 1] + D); if (x > 0 && y < H - 1) m = Math.min(m, d[p + W - 1] + D); d[p] = m; }
  return d;
}

// The nearest-course Voronoi field + per-course bounding boxes. CONSTANT for a given size (courses
// never move), so compute once and reuse across every transition (see prepareTransition's `field`).
export function buildCourseField(manifestCourses, W, H) {
  const centersPx = manifestCourses.map((c) => [(c.hit.x + c.hit.w / 2) * W, (c.hit.y + c.hit.h / 2) * H]);
  const nc = nearestCourse(W, H, centersPx);
  const courseBox = Array.from({ length: manifestCourses.length }, () => ({ minx: W, miny: H, maxx: -1, maxy: -1 }));
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const b = courseBox[nc[y * W + x]];
    if (x < b.minx) b.minx = x; if (x > b.maxx) b.maxx = x; if (y < b.miny) b.miny = y; if (y > b.maxy) b.maxy = y;
  }
  return { nc, courseBox };
}

export function prepareTransition({ coverage, terr, W, H, manifestCourses, rowsA, rowsB, field }) {
  const { centers, ownerOfA, ownerOfB, ownerRgb, paintable } = unifiedOwners(manifestCourses, rowsA, rowsB);
  const flipped = new Set();
  for (let i = 0; i < manifestCourses.length; i++) if (ownerOfA[i] !== ownerOfB[i]) flipped.add(i);
  if (flipped.size === 0) return null;

  // The nearest-course field + per-course bboxes are CONSTANT (courses never move), so the caller
  // passes a cached `field` to skip the O(W*H*courses) recompute every step (that recompute was the
  // ~34ms-per-step playback hitch). Fallback builds it on demand.
  const { nc, courseBox } = field || buildCourseField(manifestCourses, W, H);

  // Padding (the square-glow fix): paintLens drives the interior tint off the border-distance field
  // over `borderLean` and the rim over `halo`; a cropped window's edge is a false border whose
  // effects bleed `reach` inward. Pad the COMPOSITE (kept) region by `reach` and DISCARD a further
  // `reach` ring so kept pixels keep their true (saturated-interior) distance = identical to the base.
  const gooeyR = Math.round(LENS.gooeyF * W);
  const reach = Math.ceil((LENS.borderLeanF + LENS.haloF) * W) + 2;
  const pad = Math.max(reach, gooeyR);
  let minx = W, miny = H, maxx = -1, maxy = -1;
  for (const c of flipped) { const b = courseBox[c]; if (b.maxx < 0) continue;
    if (b.minx < minx) minx = b.minx; if (b.miny < miny) miny = b.miny; if (b.maxx > maxx) maxx = b.maxx; if (b.maxy > maxy) maxy = b.maxy; }
  const cx0 = clamp(minx - pad, 0, W - 1), cy0 = clamp(miny - pad, 0, H - 1);          // composite (output) region
  const cx1 = clamp(maxx + pad, 0, W - 1), cy1 = clamp(maxy + pad, 0, H - 1);
  const gx0 = clamp(cx0 - reach, 0, W - 1), gy0 = clamp(cy0 - reach, 0, H - 1);         // compute region (discard ring = reach)
  const gx1 = clamp(cx1 + reach, 0, W - 1), gy1 = clamp(cy1 + reach, 0, H - 1);
  const gw = gx1 - gx0 + 1, gh = gy1 - gy0 + 1;

  // Window slices + per-pixel owner (A and B states) and the course id.
  const nOwners = ownerRgb.length;
  const land = new Uint8Array(gw * gh), covSlice = new Float32Array(gw * gh), terrSlice = new Uint8ClampedArray(gw * gh * 4);
  const oaField = new Int16Array(gw * gh), obField = new Int16Array(gw * gh), courseLocal = new Int16Array(gw * gh);
  for (let yy = 0; yy < gh; yy++) for (let xx = 0; xx < gw; xx++) {
    const gp = (gy0 + yy) * W + (gx0 + xx), lp = yy * gw + xx;
    land[lp] = coverage[gp] > 127 ? 1 : 0; covSlice[lp] = coverage[gp] / 255;
    terrSlice[lp * 4] = terr[gp * 4]; terrSlice[lp * 4 + 1] = terr[gp * 4 + 1]; terrSlice[lp * 4 + 2] = terr[gp * 4 + 2]; terrSlice[lp * 4 + 3] = terr[gp * 4 + 3];
    const c = nc[gp]; courseLocal[lp] = c; oaField[lp] = ownerOfA[c]; obField[lp] = ownerOfB[c];
  }

  // Reveal field over the flipped cells: geodesic distance from the NEW owner's pre-existing (A-state)
  // territory -> the front advances from the attacker's side. No such territory (first claim) -> radial
  // from the course centre. Normalised to 0..1 within each cell.
  const reveal = new Float32Array(gw * gh);
  const chamferByOb = {};                               // ob -> chamfer from its A-state territory (lazy)
  const chamferFor = (ob) => {
    if (!(ob in chamferByOb)) {
      const seed = new Uint8Array(gw * gh); let any = false;
      for (let lp = 0; lp < gw * gh; lp++) if (oaField[lp] === ob) { seed[lp] = 1; any = true; }
      chamferByOb[ob] = any ? chamferDist(seed, gw, gh) : null;
    }
    return chamferByOb[ob];
  };
  for (const c of flipped) {
    const ob = ownerOfB[c];
    // The front only sweeps when the new owner's territory actually SHARES A BORDER with this cell.
    // Near-but-not-adjacent territory (caught by the patch padding) must NOT pull a front from a
    // disconnected blob -> a capture with no adjoining owner land erupts radially from the course.
    const cell = []; let adjacent = false;
    for (let lp = 0; lp < gw * gh; lp++) {
      if (courseLocal[lp] !== c) continue;
      cell.push(lp);
      if (!adjacent) {
        const x = lp % gw, y = (lp / gw) | 0;
        if ((x + 1 < gw && courseLocal[lp + 1] !== c && oaField[lp + 1] === ob) ||
            (x - 1 >= 0 && courseLocal[lp - 1] !== c && oaField[lp - 1] === ob) ||
            (y + 1 < gh && courseLocal[lp + gw] !== c && oaField[lp + gw] === ob) ||
            (y - 1 >= 0 && courseLocal[lp - gw] !== c && oaField[lp - gw] === ob)) adjacent = true;
      }
    }
    const cham = adjacent ? chamferFor(ob) : null;      // front iff the new owner borders this cell
    const cxp = centers[c][0] * W - gx0, cyp = centers[c][1] * H - gy0;
    let mx = 1e-6;
    for (const lp of cell) {
      const x = lp % gw, y = (lp / gw) | 0;
      const r = (cham && cham[lp] < 1e8) ? cham[lp] : Math.hypot(x - cxp, y - cyp);   // front, else radial
      reveal[lp] = r; if (r > mx) mx = r;
    }
    for (const lp of cell) reveal[lp] /= mx;
  }

  return {
    gw, gh, nOwners, ownerRgb, paintable, gooeyR, land, covSlice, terrSlice, oaField, obField, reveal,
    out: { x: cx0, y: cy0, w: cx1 - cx0 + 1, h: cy1 - cy0 + 1, ox: cx0 - gx0, oy: cy0 - gy0 },
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W },
  };
}

export function interpolatePatch(prep, tau) {
  const { gw, gh, nOwners, ownerRgb, paintable, gooeyR, land, covSlice, terrSlice, oaField, obField, reveal, out, px } = prep;
  const t = clamp(tau, 0, 1);
  // Owner per pixel at this progress: flipped cells switch where the front has passed (t >= reveal);
  // clamped so t=0 is exactly the A-state and t=1 the B-state (the patch then equals the base frame).
  const ownerField = new Int16Array(gw * gh);
  for (let p = 0; p < gw * gh; p++) {
    const oa = oaField[p], ob = obField[p];
    ownerField[p] = oa === ob ? oa : t <= 0 ? oa : t >= 1 ? ob : (t >= reveal[p] ? ob : oa);
  }
  const ownerSm = gooeyPartition(ownerField, land, gw, gh, nOwners, gooeyR);          // gooey-smooth the hard front
  const dB = borderDistance(ownerSm, gw, gh);
  const rgba = paintLens({ W: gw, H: gh, terr: terrSlice, ownerRgb, paintable, ownerSm, dB, near: ownerField, coastCov: covSlice, px });

  // Hot leading edge along the advancing front, fading out at the endpoints (so t=0/1 match the base).
  const bump = 4 * t * (1 - t);
  if (bump > 0.01) {
    const GW = 0.12, S = 0.9 * bump;
    for (let p = 0; p < gw * gh; p++) {
      if (oaField[p] === obField[p] || !land[p]) continue;
      const d = Math.abs(reveal[p] - t); if (d >= GW) continue;
      const k = (1 - d / GW) * S, q = p * 4;
      rgba[q] += (255 - rgba[q]) * k * 0.85; rgba[q + 1] += (250 - rgba[q + 1]) * k * 0.8; rgba[q + 2] += (255 - rgba[q + 2]) * k;
    }
  }

  // Crop the composite sub-rect (drop the discarded reach ring).
  const outRgba = new Uint8ClampedArray(out.w * out.h * 4);
  for (let yy = 0; yy < out.h; yy++) for (let xx = 0; xx < out.w; xx++) {
    const s = ((out.oy + yy) * gw + (out.ox + xx)) * 4, d = (yy * out.w + xx) * 4;
    outRgba[d] = rgba[s]; outRgba[d + 1] = rgba[s + 1]; outRgba[d + 2] = rgba[s + 2]; outRgba[d + 3] = rgba[s + 3];
  }
  return { x: out.x, y: out.y, w: out.w, h: out.h, rgba: outRgba };
}
