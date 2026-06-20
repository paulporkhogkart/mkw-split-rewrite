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
  const n = manifestCourses.length;
  const courseBox = Array.from({ length: n }, () => ({ minx: W, miny: H, maxx: -1, maxy: -1 }));
  const adj = Array.from({ length: n }, () => new Set());   // Voronoi neighbours (which cells border which)
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const c = nc[y * W + x], b = courseBox[c];
    if (x < b.minx) b.minx = x; if (x > b.maxx) b.maxx = x; if (y < b.miny) b.miny = y; if (y > b.maxy) b.maxy = y;
    if (x + 1 < W) { const d = nc[y * W + x + 1]; if (d !== c) { adj[c].add(d); adj[d].add(c); } }
    if (y + 1 < H) { const d = nc[(y + 1) * W + x]; if (d !== c) { adj[c].add(d); adj[d].add(c); } }
  }
  return { nc, courseBox, adj };
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

  // Reveal field. Cells are grouped by NEW OWNER and each group reveals as ONE continuous front: a
  // geodesic (chamfer) sweep from the owner's pre-existing (A-state) land IF the group adjoins it (so
  // the front advances from the attacker's side AND chains across a whole run of adjoining captures);
  // a group that doesn't adjoin the owner's land erupts radially from its centroid. Normalised 0..1
  // across the group, so one progress sweeps the entire run. `extent` (max px) drives the duration.
  // Reveal time per pixel (0..1), set for the WHOLE window from each group's chamfer so the gooey band
  // just outside the flipped cells transitions WITH the front, not early. 2 = "never" (until the clamp).
  const reveal = new Float32Array(gw * gh).fill(2);
  let extent = 1;
  const groups = new Map();                             // new-owner index -> Set(flipped course index)
  for (const c of flipped) { const ob = ownerOfB[c]; if (!groups.has(ob)) groups.set(ob, new Set()); groups.get(ob).add(c); }
  for (const [ob, cellSet] of groups) {
    const gpix = []; let sx = 0, sy = 0, adjacent = false;
    for (let lp = 0; lp < gw * gh; lp++) {
      if (!cellSet.has(courseLocal[lp])) continue;
      const x = lp % gw, y = (lp / gw) | 0; gpix.push(lp); sx += x; sy += y;
      if (!adjacent &&                                  // does the group share a border with ob's A-state land?
        ((x + 1 < gw && !cellSet.has(courseLocal[lp + 1]) && oaField[lp + 1] === ob) ||
         (x - 1 >= 0 && !cellSet.has(courseLocal[lp - 1]) && oaField[lp - 1] === ob) ||
         (y + 1 < gh && !cellSet.has(courseLocal[lp + gw]) && oaField[lp + gw] === ob) ||
         (y - 1 >= 0 && !cellSet.has(courseLocal[lp - gw]) && oaField[lp - gw] === ob))) adjacent = true;
    }
    const seed = new Uint8Array(gw * gh);
    if (adjacent) { for (let lp = 0; lp < gw * gh; lp++) if (oaField[lp] === ob && !cellSet.has(courseLocal[lp])) seed[lp] = 1; }   // front from ob's border
    else if (gpix.length) seed[Math.round(sy / gpix.length) * gw + Math.round(sx / gpix.length)] = 1;   // radial from the group centroid
    const cham = chamferDist(seed, gw, gh);
    let mx = 1e-6;
    for (const lp of gpix) if (cham[lp] < 1e8 && cham[lp] > mx) mx = cham[lp];
    for (let lp = 0; lp < gw * gh; lp++) { const rv = cham[lp] < 1e8 ? cham[lp] / mx : 2; if (rv < reveal[lp]) reveal[lp] = rv; }
    if (mx > extent) extent = mx;
  }

  // Small windows (a single cell) re-partition LIVE each frame: cheap, and prepareTransition stays
  // ~3ms so there's no per-step hitch, plus the moving border keeps its rim. Big windows (a coalesced
  // run) render the two endpoint states ONCE here; interpolatePatch then just blends between them
  // along the front -> O(window)/frame, and the one-time render cost is amortised over the long sweep.
  const px = { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W };
  const live = gw * gh <= 165000;
  let startRgba = null, endRgba = null;
  if (!live) {
    const renderState = (field) => {
      const sm = gooeyPartition(field, land, gw, gh, nOwners, gooeyR);
      const dB = borderDistance(sm, gw, gh);
      return paintLens({ W: gw, H: gh, terr: terrSlice, ownerRgb, paintable, ownerSm: sm, dB, near: field, coastCov: covSlice, px });
    };
    startRgba = renderState(oaField); endRgba = renderState(obField);
  }
  return {
    live, gw, gh, land, reveal, extent, out: { x: cx0, y: cy0, w: cx1 - cx0 + 1, h: cy1 - cy0 + 1, ox: cx0 - gx0, oy: cy0 - gy0 },
    nOwners, ownerRgb, paintable, gooeyR, covSlice, terrSlice, oaField, obField, px,   // live mode
    startRgba, endRgba,                                                                // blend mode
  };
}

export function interpolatePatch(prep, tau) {
  const t = clamp(tau, 0, 1);
  return prep.live ? livePatch(prep, t) : blendPatch(prep, t);
}

// Glow envelope: full across the whole sweep, fading only at the very ends (so t=0/1 == the base).
const glowBump = (t) => clamp(Math.min(t, 1 - t) / 0.12, 0, 1);

function crop(full, prep) {
  const { gw, out } = prep;
  const r = new Uint8ClampedArray(out.w * out.h * 4);
  for (let yy = 0; yy < out.h; yy++) for (let xx = 0; xx < out.w; xx++) {
    const s = ((out.oy + yy) * gw + (out.ox + xx)) * 4, d = (yy * out.w + xx) * 4;
    r[d] = full[s]; r[d + 1] = full[s + 1]; r[d + 2] = full[s + 2]; r[d + 3] = full[s + 3];
  }
  return { x: out.x, y: out.y, w: out.w, h: out.h, rgba: r };
}

// Live: re-partition the (small) window each frame -> a true gooey moving border + rim.
function livePatch(prep, t) {
  const { gw, gh, nOwners, ownerRgb, paintable, gooeyR, land, covSlice, terrSlice, oaField, obField, reveal, px } = prep;
  const ownerField = new Int16Array(gw * gh);
  for (let p = 0; p < gw * gh; p++) {
    const oa = oaField[p], ob = obField[p];
    ownerField[p] = oa === ob ? oa : t <= 0 ? oa : t >= 1 ? ob : (t >= reveal[p] ? ob : oa);
  }
  const ownerSm = gooeyPartition(ownerField, land, gw, gh, nOwners, gooeyR);
  const dB = borderDistance(ownerSm, gw, gh);
  const rgba = paintLens({ W: gw, H: gh, terr: terrSlice, ownerRgb, paintable, ownerSm, dB, near: ownerField, coastCov: covSlice, px });
  const bump = glowBump(t);
  if (bump > 0.01) for (let p = 0; p < gw * gh; p++) {
    if (oaField[p] === obField[p] || !land[p]) continue;
    const d = Math.abs(reveal[p] - t); if (d >= 0.12) continue;
    const k = (1 - d / 0.12) * 0.9 * bump, q = p * 4;
    rgba[q] += (255 - rgba[q]) * k * 0.85; rgba[q + 1] += (250 - rgba[q + 1]) * k * 0.8; rgba[q + 2] += (255 - rgba[q + 2]) * k;
  }
  return crop(rgba, prep);
}

// Blend: a big coalesced run -> blend the two precomputed endpoint renders along the front (cheap).
function blendPatch(prep, t) {
  const { gw, gh, land, reveal, startRgba, endRgba } = prep;
  const FEATHER = 0.06, bump = glowBump(t);
  const full = new Uint8ClampedArray(gw * gh * 4);
  for (let p = 0; p < gw * gh; p++) {
    const q = p * 4, r = reveal[p];
    const f = t <= 0 ? 0 : t >= 1 ? 1 : clamp((t - r) / FEATHER + 0.5, 0, 1);   // 1 behind the front, 0 ahead, soft between
    full[q] = startRgba[q] + (endRgba[q] - startRgba[q]) * f;
    full[q + 1] = startRgba[q + 1] + (endRgba[q + 1] - startRgba[q + 1]) * f;
    full[q + 2] = startRgba[q + 2] + (endRgba[q + 2] - startRgba[q + 2]) * f;
    full[q + 3] = startRgba[q + 3] + (endRgba[q + 3] - startRgba[q + 3]) * f;
    if (bump > 0.01 && land[p]) {
      const d = Math.abs(r - t);
      if (d < 0.12) { const k = (1 - d / 0.12) * 0.9 * bump;
        full[q] += (255 - full[q]) * k * 0.85; full[q + 1] += (250 - full[q + 1]) * k * 0.8; full[q + 2] += (255 - full[q + 2]) * k; }
    }
  }
  return crop(full, prep);
}
