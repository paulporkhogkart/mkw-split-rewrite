import { describe, it, expect } from 'vitest';
import { threshold, zhangSuen } from './skeleton';

function grid(rows: string[]) {                  // '#' = 1
  const h = rows.length, w = rows[0].length, b = new Uint8Array(w * h);
  rows.forEach((r, j) => [...r].forEach((c, i) => { if (c === '#') b[j * w + i] = 1; }));
  return { b, w, h };
}
const count = (a: Uint8Array) => a.reduce((s, v) => s + v, 0);

describe('threshold', () => {
  it('keeps cells above frac*max', () => {
    const r = { wGrid: Float32Array.from([10, 1, 0]) };
    expect([...threshold(r, 0.2)]).toEqual([1, 0, 0]);
  });
});

describe('zhangSuen', () => {
  it('thins a filled block toward a 1px skeleton (fewer pixels, no 2x2 block, still connected)', () => {
    const { b, w, h } = grid(['........', '.######.', '.######.', '.######.', '.######.', '........']);
    const sk = zhangSuen(b, w, h);
    expect(count(sk)).toBeLessThan(count(b));
    expect(count(sk)).toBeGreaterThan(0);
    let block = false;
    for (let j = 0; j < h - 1; j++) for (let i = 0; i < w - 1; i++)
      if (sk[j * w + i] && sk[j * w + i + 1] && sk[(j + 1) * w + i] && sk[(j + 1) * w + i + 1]) block = true;
    expect(block).toBe(false);
  });

  it('leaves a thin line untouched', () => {
    const { b, w, h } = grid(['.....', '#####', '.....']);
    expect([...zhangSuen(b, w, h)]).toEqual([...b]);
  });
});
