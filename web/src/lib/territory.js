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
