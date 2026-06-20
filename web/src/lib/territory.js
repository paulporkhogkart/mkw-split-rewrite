// Pure territory partition + lens paint for the World Map (SP2). No DOM.
// Algorithm ported from the locked mockup lens-focus-v4.html.

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

export function boxBlur(src, r, W, H) {
  if (r < 1) return src.slice();
  const d = 2 * r + 1, tmp = new Float32Array(W * H), out = new Float32Array(W * H);
  for (let y = 0; y < H; y++) {
    const row = y * W; let acc = 0;
    for (let x = -r; x <= r; x++) acc += src[row + clamp(x, 0, W - 1)];
    for (let x = 0; x < W; x++) { tmp[row + x] = acc / d; acc += src[row + clamp(x + r + 1, 0, W - 1)] - src[row + clamp(x - r, 0, W - 1)]; }
  }
  for (let x = 0; x < W; x++) {
    let acc = 0;
    for (let y = -r; y <= r; y++) acc += tmp[clamp(y, 0, H - 1) * W + x];
    for (let y = 0; y < H; y++) { out[y * W + x] = acc / d; acc += tmp[clamp(y + r + 1, 0, H - 1) * W + x] - tmp[clamp(y - r, 0, H - 1) * W + x]; }
  }
  return out;
}

export function nearestOwner(W, H, centersPx, ownerOf) {
  const near = new Int16Array(W * H).fill(-1);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let bi = -1, bd = 1e18;
    for (let i = 0; i < centersPx.length; i++) {
      const dx = centersPx[i][0] - x, dy = centersPx[i][1] - y, dd = dx * dx + dy * dy;
      if (dd < bd) { bd = dd; bi = i; }
    }
    near[y * W + x] = bi < 0 ? -1 : ownerOf[bi];
  }
  return near;
}

export function gooeyPartition(near, land, W, H, nOwners, radius) {
  const ownerSm = new Int16Array(W * H).fill(-1);
  const best = new Float32Array(W * H), mask = new Float32Array(W * H);
  // Caller contract: nOwners must equal max(ownerOf)+1, else a land pixel whose
  // owner index >= nOwners would stay -1 (a gap). buildTerritory guarantees this.
  for (let o = 0; o < nOwners; o++) {
    mask.fill(0);
    for (let p = 0; p < mask.length; p++) if (land[p] && near[p] === o) mask[p] = 1;
    const b = boxBlur(mask, radius, W, H);
    for (let p = 0; p < b.length; p++) { if (!land[p]) continue; if (b[p] > best[p]) { best[p] = b[p]; ownerSm[p] = o; } }
  }
  return ownerSm;
}

export function borderDistance(ownerSm, W, H) {
  const dB = new Float32Array(W * H).fill(1e9);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const p = y * W + x, o = ownerSm[p]; if (o < 0) continue;
    let edge = (x === 0 || x === W - 1 || y === 0 || y === H - 1);
    if (!edge) edge = ownerSm[p - 1] !== o || ownerSm[p + 1] !== o || ownerSm[p - W] !== o || ownerSm[p + W] !== o;
    if (edge) dB[p] = 0;
  }
  const O = 1, D = 1.41421;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const p = y * W + x; if (ownerSm[p] < 0) continue; let m = dB[p];
    if (x > 0) m = Math.min(m, dB[p - 1] + O); if (y > 0) m = Math.min(m, dB[p - W] + O);
    if (x > 0 && y > 0) m = Math.min(m, dB[p - W - 1] + D); if (x < W - 1 && y > 0) m = Math.min(m, dB[p - W + 1] + D); dB[p] = m;
  }
  for (let y = H - 1; y >= 0; y--) for (let x = W - 1; x >= 0; x--) {
    const p = y * W + x; if (ownerSm[p] < 0) continue; let m = dB[p];
    if (x < W - 1) m = Math.min(m, dB[p + 1] + O); if (y < H - 1) m = Math.min(m, dB[p + W] + O);
    if (x < W - 1 && y < H - 1) m = Math.min(m, dB[p + W + 1] + D); if (x > 0 && y < H - 1) m = Math.min(m, dB[p + W - 1] + D); dB[p] = m;
  }
  return dB;
}

const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
const mix = (a, b, t) => [a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t];

export const LENS = { DIM:0.40, tint:0.40, rimBright:0.74, rimWidthF:0.0020, haloF:0.0093, borderLeanF:0.0293, gooeyF:0.014, lightF:0.55 };

export const hexRgb = (h) => { h = h.replace("#",""); return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)]; };
export const ownerLightOf = (rgb) => mix(rgb, [255,255,255], LENS.lightF);

export function paintLens(o) {
  const { W, H, terr, ownerSm, dB, coastCov, near, ownerRgb, px, paintable } = o;
  const out = new Uint8ClampedArray(W * H * 4);
  const light = ownerRgb.map(ownerLightOf);
  for (let p = 0; p < W * H; p++) {
    const cov = coastCov[p]; if (cov <= 0.004) continue;
    let oi = ownerSm[p], dist;
    if (oi < 0) { oi = near[p]; if (oi < 0) continue; dist = 0; } else dist = dB[p];   // ocean feather borrows nearest owner at the rim
    if (paintable && !paintable[oi]) continue;   // unclaimed owner -> leave plain terrain showing through
    const O = ownerRgb[oi], Ol = light[oi], q = p * 4;
    const Dd = [terr[q] * LENS.DIM, terr[q + 1] * LENS.DIM, terr[q + 2] * LENS.DIM];   // dimmed terrain (texture survives)
    const inward = smooth(0, px.borderLean, dist);
    const tint = clamp(LENS.tint * (0.55 + 0.9 * (1 - inward)), 0, 0.9);               // subtle inside, leans into the border
    let col = mix(Dd, O, tint);
    const core = smooth(px.rimW, 0, dist), halo = smooth(px.halo, px.rimW, dist);
    col = mix(col, Ol, clamp(core * LENS.rimBright + halo * 0.22, 0, 1));              // bright owner rim + soft halo
    out[q] = col[0]; out[q + 1] = col[1]; out[q + 2] = col[2]; out[q + 3] = cov * 255;
  }
  return out;
}

export function prepareOwners(manifestCourses, territoryRows) {
  const colorBySlug = Object.fromEntries(territoryRows.map((r) => [r.slug, r.color]));
  const idxOf = {}, ownerRgb = [], paintable = [], centers = [], ownerOf = [];
  let unclaimedIdx = -1;
  // Seed EVERY course so a claim only owns its own Voronoi cell. Unclaimed courses
  // collapse to one non-paintable owner that still occupies its cell (blocks bleed).
  for (const c of manifestCourses) {
    const color = colorBySlug[c.slug];
    const claimed = color && /^#[0-9a-f]{6}$/i.test(color);
    let oi;
    if (claimed) {
      if (!(color in idxOf)) { idxOf[color] = ownerRgb.length; ownerRgb.push(hexRgb(color)); paintable.push(true); }
      oi = idxOf[color];
    } else {
      if (unclaimedIdx < 0) { unclaimedIdx = ownerRgb.length; ownerRgb.push([0, 0, 0]); paintable.push(false); }
      oi = unclaimedIdx;
    }
    centers.push([c.hit.x + c.hit.w / 2, c.hit.y + c.hit.h / 2]);
    ownerOf.push(oi);
  }
  return { centers, ownerOf, ownerRgb, paintable };
}

export function buildTerritory({ coverage, W, H, terr, manifestCourses, territoryRows }) {
  const { centers, ownerOf, ownerRgb, paintable } = prepareOwners(manifestCourses, territoryRows);
  if (!paintable.some(Boolean)) return new Uint8ClampedArray(W * H * 4);   // nobody claims anything
  const land = new Uint8Array(W * H), coastCov = new Float32Array(W * H);
  for (let p = 0; p < W * H; p++) { land[p] = coverage[p] > 127 ? 1 : 0; coastCov[p] = coverage[p] / 255; }
  const centersPx = centers.map((c) => [c[0] * W, c[1] * H]);
  const near = nearestOwner(W, H, centersPx, ownerOf);
  const ownerSm = gooeyPartition(near, land, W, H, ownerRgb.length, Math.round(LENS.gooeyF * W));
  const dB = borderDistance(ownerSm, W, H);
  return paintLens({ ownerSm, dB, coastCov, near, W, H, ownerRgb, terr, paintable,
    px: { rimW: LENS.rimWidthF * W, halo: LENS.haloF * W, borderLean: LENS.borderLeanF * W } });
}
