// pi/src/progress/graphExtract.ts
// Skeleton -> topological graph: nodes at 8-neighbour degree != 2 (endpoints + junction clusters),
// edges = the degree-2 pixel chains traced between them. A pure loop (all degree 2) gets one seed
// node + one cyclic edge. Output is in raster-cell coordinates; lapGraphCV maps it to course px.

export interface RawNode { id: number; ci: number; cj: number; }
export interface RawEdge { id: number; a: number; b: number; cells: [number, number][]; }

const NB: [number, number][] = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];

export function extractGraph(skel: Uint8Array, w: number, h: number): { nodes: RawNode[]; edges: RawEdge[] } {
  const idx = (i: number, j: number) => j * w + i;
  const on = (i: number, j: number) => i >= 0 && j >= 0 && i < w && j < h && skel[idx(i, j)] === 1;
  // Crossing number: distinct runs of foreground in the clockwise 8-ring. 1=endpoint, 2=path,
  // >=3=junction. Robust to staircase diagonals (whose extra neighbour is contiguous -> still 2),
  // unlike a raw 8-neighbour count which falsely flags every diagonal step as a junction.
  const ring = (i: number, j: number) =>
    [on(i, j - 1), on(i + 1, j - 1), on(i + 1, j), on(i + 1, j + 1), on(i, j + 1), on(i - 1, j + 1), on(i - 1, j), on(i - 1, j - 1)];
  const crossing = (i: number, j: number) => {
    const p = ring(i, j); let a = 0;
    for (let k = 0; k < 8; k++) if (!p[k] && p[(k + 1) % 8]) a++;
    return a;
  };
  const anyNb = (i: number, j: number) => ring(i, j).some((v) => v);

  const nodeOf = new Int32Array(w * h).fill(-1);
  const nodes: RawNode[] = [];

  // (a) junction clusters: connected components of crossing>=3 pixels collapse to one node (centroid).
  const seen = new Uint8Array(w * h);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    if (!on(i, j) || crossing(i, j) < 3 || seen[idx(i, j)]) continue;
    const id = nodes.length; let sci = 0, scj = 0, n = 0;
    const stack: [number, number][] = [[i, j]]; seen[idx(i, j)] = 1;
    while (stack.length) {
      const [ci, cj] = stack.pop()!;
      nodeOf[idx(ci, cj)] = id; sci += ci; scj += cj; n++;
      for (const [di, dj] of NB) {
        const ni = ci + di, nj = cj + dj;
        if (on(ni, nj) && crossing(ni, nj) >= 3 && !seen[idx(ni, nj)]) { seen[idx(ni, nj)] = 1; stack.push([ni, nj]); }
      }
    }
    nodes.push({ id, ci: Math.round(sci / n), cj: Math.round(scj / n) });
  }
  // (b) endpoints (crossing 1) -> singleton nodes.
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++)
    if (on(i, j) && crossing(i, j) === 1 && nodeOf[idx(i, j)] === -1) {
      nodeOf[idx(i, j)] = nodes.length; nodes.push({ id: nodes.length, ci: i, cj: j });
    }
  // (c) pure loop (no node pixels): seed one node at the first skeleton pixel.
  if (nodes.length === 0) {
    let si = -1, sj = -1;
    for (let j = 0; j < h && si < 0; j++) for (let i = 0; i < w; i++) if (on(i, j)) { si = i; sj = j; break; }
    if (si < 0) return { nodes, edges: [] };
    nodeOf[idx(si, sj)] = 0; nodes.push({ id: 0, ci: si, cj: sj });
  }

  // Trace edges between node pixels through degree-2 chains; mark both directions to dedup.
  const edges: RawEdge[] = [];
  const traced = new Set<string>();
  const key = (a: number, b: number) => a + '>' + b;
  const nodePix: [number, number][] = [];
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) if (nodeOf[idx(i, j)] >= 0) nodePix.push([i, j]);

  for (const [pi, pj] of nodePix) {
    const pnode = nodeOf[idx(pi, pj)];
    for (const [di, dj] of NB) {
      const qi = pi + di, qj = pj + dj;
      if (!on(qi, qj) || nodeOf[idx(qi, qj)] === pnode) continue;     // off, or internal to this node
      if (traced.has(key(idx(pi, pj), idx(qi, qj)))) continue;
      const cells: [number, number][] = [[pi, pj], [qi, qj]];
      let prevI = pi, prevJ = pj, curI = qi, curJ = qj;
      while (nodeOf[idx(curI, curJ)] < 0) {                            // walk path pixels to the next node
        // next = a set neighbour that isn't prev; prefer one NOT 8-adjacent to prev (the true forward
        // step) so a path pixel with a staircase sibling in prev's run doesn't divert the trace.
        let ni = -1, nj = -1, fbI = -1, fbJ = -1;
        for (const [ddi, ddj] of NB) {
          const xi = curI + ddi, xj = curJ + ddj;
          if (!on(xi, xj) || (xi === prevI && xj === prevJ)) continue;
          if (Math.abs(xi - prevI) > 1 || Math.abs(xj - prevJ) > 1) { ni = xi; nj = xj; break; }
          if (fbI < 0) { fbI = xi; fbJ = xj; }
        }
        if (ni < 0) { ni = fbI; nj = fbJ; }
        if (ni < 0) break;
        prevI = curI; prevJ = curJ; curI = ni; curJ = nj;
        cells.push([curI, curJ]);
      }
      if (nodeOf[idx(curI, curJ)] < 0) continue;                       // never reached a node
      traced.add(key(idx(pi, pj), idx(qi, qj)));
      const sl = cells[cells.length - 2];
      traced.add(key(idx(curI, curJ), idx(sl[0], sl[1])));             // reverse first step
      edges.push({ id: edges.length, a: pnode, b: nodeOf[idx(curI, curJ)], cells });
    }
  }
  return { nodes, edges };
}
