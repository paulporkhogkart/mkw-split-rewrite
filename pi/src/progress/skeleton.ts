// pi/src/progress/skeleton.ts
// Binary thresholding + Zhang-Suen thinning (hand-rolled, no opencv) to reduce a filled trail
// raster to a 1px-wide skeleton whose topology (endpoints, junctions) the graph extractor reads.
import type { Raster } from './raster';

/** Binarise a raster: 1 where score exceeds frac * max. */
export function threshold(r: Pick<Raster, 'wGrid'>, frac: number): Uint8Array {
  let max = 0;
  for (const v of r.wGrid) if (v > max) max = v;
  const thr = frac * max;
  const out = new Uint8Array(r.wGrid.length);
  for (let i = 0; i < r.wGrid.length; i++) out[i] = r.wGrid[i] > thr ? 1 : 0;
  return out;
}

/** Zhang-Suen thinning. Returns a new 1px skeleton; input is not mutated. */
export function zhangSuen(src: Uint8Array, w: number, h: number): Uint8Array {
  const img = Uint8Array.from(src);
  const at = (i: number, j: number) => (i < 0 || j < 0 || i >= w || j >= h ? 0 : img[j * w + i]);
  const dead: number[] = [];

  // One sub-iteration (step 0 or 1); returns whether anything was deleted.
  const pass = (step: 0 | 1): boolean => {
    dead.length = 0;
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      if (img[j * w + i] !== 1) continue;
      // clockwise from North: p2..p9
      const p2 = at(i, j - 1), p3 = at(i + 1, j - 1), p4 = at(i + 1, j), p5 = at(i + 1, j + 1),
            p6 = at(i, j + 1), p7 = at(i - 1, j + 1), p8 = at(i - 1, j), p9 = at(i - 1, j - 1);
      const B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9;
      if (B < 2 || B > 6) continue;
      const seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2];
      let A = 0;
      for (let k = 0; k < 8; k++) if (seq[k] === 0 && seq[k + 1] === 1) A++;
      if (A !== 1) continue;
      if (step === 0) {
        if (p2 * p4 * p6 !== 0 || p4 * p6 * p8 !== 0) continue;
      } else {
        if (p2 * p4 * p8 !== 0 || p2 * p6 * p8 !== 0) continue;
      }
      dead.push(j * w + i);
    }
    for (const idx of dead) img[idx] = 0;
    return dead.length > 0;
  };

  let changed = true;
  while (changed) {
    const a = pass(0);
    const b = pass(1);
    changed = a || b;
  }
  return img;
}
