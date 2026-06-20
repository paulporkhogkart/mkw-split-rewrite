// Pure transition math for the territory border-push animation. No DOM.
// Heavy work (unified owners, nearest-course field, per-owner blurred masks for the
// before/after snapshots) is precomputed once per transition; interpolatePatch is the
// cheap per-frame call. Lerping the BLURRED owner masks then argmaxing makes the gooey
// border slide continuously from the A-partition to the B-partition (a real border push;
// an isolated first claim with no adjacent same-owner mass instead grows from its course).
import { boxBlur, borderDistance, paintLens, hexRgb, LENS } from "./territory.js";

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

export function prepareTransition({ coverage, terr, W, H, manifestCourses, rowsA, rowsB }) {
  const { centers, ownerOfA, ownerOfB, ownerRgb, paintable } = unifiedOwners(manifestCourses, rowsA, rowsB);
  const flipped = new Set();
  for (let i = 0; i < manifestCourses.length; i++) if (ownerOfA[i] !== ownerOfB[i]) flipped.add(i);
  if (flipped.size === 0) return null;

  const centersPx = centers.map((c) => [c[0] * W, c[1] * H]);
  const nc = nearestCourse(W, H, centersPx);

  // Tight bbox of the flipped cells, then pad: haloPx (rim bleed into neighbours) is kept in
  // the composite output; blurR (gooey reach) extends the COMPUTE region and is discarded so
  // borderDistance's artificial frame-edge rim never reaches a painted pixel.
  const blurR = Math.round(LENS.gooeyF * W);
  const haloPx = Math.ceil(LENS.haloF * W) + 2;
  let minx = W, miny = H, maxx = -1, maxy = -1;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (flipped.has(nc[y * W + x])) {
    if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y;
  }
  const cx0 = clamp(minx - haloPx, 0, W - 1), cy0 = clamp(miny - haloPx, 0, H - 1);   // composite (output) region
  const cx1 = clamp(maxx + haloPx, 0, W - 1), cy1 = clamp(maxy + haloPx, 0, H - 1);
  const gx0 = clamp(cx0 - blurR, 0, W - 1), gy0 = clamp(cy0 - blurR, 0, H - 1);        // compute region (+blurR ring)
  const gx1 = clamp(cx1 + blurR, 0, W - 1), gy1 = clamp(cy1 + blurR, 0, H - 1);
  const gw = gx1 - gx0 + 1, gh = gy1 - gy0 + 1;

  const nOwners = ownerRgb.length;
  const land = new Uint8Array(gw * gh), covSlice = new Float32Array(gw * gh), terrSlice = new Uint8ClampedArray(gw * gh * 4);
  const maskA = Array.from({ length: nOwners }, () => new Float32Array(gw * gh));
  const maskB = Array.from({ length: nOwners }, () => new Float32Array(gw * gh));
  const nearA = new Int16Array(gw * gh), nearB = new Int16Array(gw * gh);
  for (let yy = 0; yy < gh; yy++) for (let xx = 0; xx < gw; xx++) {
    const gp = (gy0 + yy) * W + (gx0 + xx), lp = yy * gw + xx;
    const isLand = coverage[gp] > 127 ? 1 : 0;
    land[lp] = isLand; covSlice[lp] = coverage[gp] / 255;
    terrSlice[lp * 4] = terr[gp * 4]; terrSlice[lp * 4 + 1] = terr[gp * 4 + 1]; terrSlice[lp * 4 + 2] = terr[gp * 4 + 2]; terrSlice[lp * 4 + 3] = terr[gp * 4 + 3];
    const course = nc[gp], oa = ownerOfA[course], ob = ownerOfB[course];
    nearA[lp] = oa; nearB[lp] = ob;
    if (isLand) { maskA[oa][lp] = 1; maskB[ob][lp] = 1; }
  }
  const blurA = maskA.map((m) => boxBlur(m, blurR, gw, gh));
  const blurB = maskB.map((m) => boxBlur(m, blurR, gw, gh));

  return {
    gw, gh, nOwners, ownerRgb, paintable, land, covSlice, terrSlice, blurA, blurB, nearA, nearB,
    out: { x: cx0, y: cy0, w: cx1 - cx0 + 1, h: cy1 - cy0 + 1, ox: cx0 - gx0, oy: cy0 - gy0 },
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W },
  };
}

export function interpolatePatch(prep, tau) {
  const { gw, gh, nOwners, ownerRgb, paintable, land, covSlice, terrSlice, blurA, blurB, nearA, nearB, out, px } = prep;
  const t = clamp(tau, 0, 1);
  const ownerSm = new Int16Array(gw * gh).fill(-1);
  for (let p = 0; p < gw * gh; p++) {
    if (!land[p]) continue;
    let best = -Infinity, bi = -1;
    for (let o = 0; o < nOwners; o++) { const v = (1 - t) * blurA[o][p] + t * blurB[o][p]; if (v > best) { best = v; bi = o; } }
    ownerSm[p] = bi;
  }
  const near = t < 0.5 ? nearA : nearB;                       // coast feather owner (exact at the endpoints)
  const dB = borderDistance(ownerSm, gw, gh);
  const full = paintLens({ W: gw, H: gh, terr: terrSlice, ownerRgb, paintable, ownerSm, dB, near, coastCov: covSlice, px });

  const rgba = new Uint8ClampedArray(out.w * out.h * 4);     // crop the composite sub-rect (drop the blurR ring)
  for (let yy = 0; yy < out.h; yy++) for (let xx = 0; xx < out.w; xx++) {
    const s = ((out.oy + yy) * gw + (out.ox + xx)) * 4, d = (yy * out.w + xx) * 4;
    rgba[d] = full[s]; rgba[d + 1] = full[s + 1]; rgba[d + 2] = full[s + 2]; rgba[d + 3] = full[s + 3];
  }
  return { x: out.x, y: out.y, w: out.w, h: out.h, rgba };
}
