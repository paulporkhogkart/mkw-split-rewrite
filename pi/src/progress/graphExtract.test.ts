import { describe, it, expect } from 'vitest';
import { extractGraph } from './graphExtract';

function grid(rows: string[]) {                  // '#' = 1
  const h = rows.length, w = rows[0].length, b = new Uint8Array(w * h);
  rows.forEach((r, j) => [...r].forEach((c, i) => { if (c === '#') b[j * w + i] = 1; }));
  return { b, w, h };
}
const incident = (edges: { a: number; b: number }[]) => {
  const d = new Map<number, number>();
  for (const e of edges) { d.set(e.a, (d.get(e.a) ?? 0) + 1); d.set(e.b, (d.get(e.b) ?? 0) + 1); }
  return d;
};

describe('extractGraph', () => {
  it('a straight line -> one edge, two endpoints', () => {
    const { b, w, h } = grid(['.....', '#####', '.....']);
    const g = extractGraph(b, w, h);
    expect(g.nodes.length).toBe(2);
    expect(g.edges.length).toBe(1);
    expect(g.edges[0].cells.length).toBe(5);
  });

  it('a Y -> a degree-3 junction and three edges', () => {
    const { b, w, h } = grid(['#...#', '.#.#.', '..#..', '..#..']);
    const g = extractGraph(b, w, h);
    expect(g.edges.length).toBe(3);
    expect(Math.max(...incident(g.edges).values())).toBe(3);   // the junction node has 3 incident edges
  });

  it('a plain ring -> one seed node and one cyclic edge', () => {
    const { b, w, h } = grid(['.###.', '#...#', '#...#', '#...#', '.###.']);
    const g = extractGraph(b, w, h);
    expect(g.nodes.length).toBe(1);
    expect(g.edges.length).toBe(1);
    expect(g.edges[0].a).toBe(g.edges[0].b);                    // cyclic
  });

  it('terminates on dense non-thinned clumps (chain walks must be bounded)', { timeout: 5000 }, () => {
    // Zhang-Suen residue at multi-lap crossings can leave thick clumps whose
    // pixel cycles trap a prev-only walk forever (real-data OOM: koops trail).
    // Any input must terminate with every chain bounded by the pixel count.
    const { b, w, h } = grid([
      '#.........',
      '.####.....',
      '.####.....',
      '.####.....',
      '..........',
    ]);
    const g = extractGraph(b, w, h);
    const onCount = b.reduce((s, v) => s + v, 0);
    for (const e of g.edges) expect(e.cells.length).toBeLessThanOrEqual(onCount + 1);
  });
});
