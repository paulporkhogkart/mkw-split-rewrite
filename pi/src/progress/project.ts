// pi/src/progress/project.ts
import type { CourseGraph, GraphEdge, ProjState, Obs } from './types';

const EPS_BACK = 0.004;       // backward tolerance in within-lap progress
const REACH_K = 2.5;          // forward window = K * pixelsMoved / lapLengthPx
const EPS_FWD_MIN = 0.02;     // minimum forward reach in progress
const MATCH_DIST = 60;        // px; nearest edge beyond this -> hold/bootstrap
// staleness is decided by the caller (hub) and passed in as obs.stale.

export interface PreparedEdge { edge: GraphEdge; cumFrac: number[]; }     // arc fraction at each poly vertex
export interface Prepared { edges: PreparedEdge[]; }

export function prepareEdges(g: CourseGraph): Prepared {
  return { edges: g.edges.map((edge) => {
    const cum = [0];
    for (let i = 1; i < edge.poly.length; i++)
      cum.push(cum[i - 1] + Math.hypot(edge.poly[i][0] - edge.poly[i - 1][0], edge.poly[i][1] - edge.poly[i - 1][1]));
    const total = cum[cum.length - 1] || 1;
    return { edge, cumFrac: cum.map((c) => c / total) };
  }) };
}

/** Nearest point on one edge's poly, restricted to progress in [loP, hiP]. */
function nearestOnEdge(pe: PreparedEdge, loP: number, hiP: number, px: number, py: number) {
  const { edge, cumFrac } = pe;
  const span = edge.pHi - edge.pLo || 1;
  let best = Infinity, bestProg = edge.pLo;
  for (let i = 1; i < edge.poly.length; i++) {
    const a = edge.poly[i - 1], b = edge.poly[i];
    const segLoP = edge.pLo + cumFrac[i - 1] * span, segHiP = edge.pLo + cumFrac[i] * span;
    if (Math.max(segLoP, segHiP) < loP || Math.min(segLoP, segHiP) > hiP) continue;
    const dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    let t = L2 > 0 ? ((px - a[0]) * dx + (py - a[1]) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    let prog = segLoP + t * (segHiP - segLoP);
    const pc = Math.max(loP, Math.min(hiP, prog));
    if (pc !== prog) { t = segHiP !== segLoP ? Math.max(0, Math.min(1, (pc - segLoP) / (segHiP - segLoP))) : 0; prog = pc; }
    const x = a[0] + t * dx, y = a[1] + t * dy, d = Math.hypot(px - x, py - y);
    if (d < best) { best = d; bestProg = prog; }
  }
  return { dist: best, progress: bestProg };
}

export function projectStep(state: ProjState, g: CourseGraph, pe: Prepared, obs: Obs):
    { state: ProjState; completion: number | null } {
  const laps = obs.totLap > 0 ? obs.totLap : 3;
  const done = (progress: number) => (obs.lap - 1 + progress) / laps;
  if (obs.stale) return { state, completion: state ? done(state.progress) : null };
  if (pe.edges.length === 0) return { state, completion: state ? done(state.progress) : null };

  const tracking = state != null;
  const loP = tracking ? Math.max(0, state!.progress - EPS_BACK) : 0;
  const moved = tracking ? Math.hypot(obs.x - state!.x, obs.y - state!.y) : 0;
  const reach = Math.max(EPS_FWD_MIN, REACH_K * moved / (g.lapLengthPx || 1));
  const hiP = tracking ? Math.min(1, state!.progress + reach) : 1;

  let best = Infinity, bestProg = tracking ? state!.progress : 0;
  for (const e of pe.edges) {
    const r = nearestOnEdge(e, loP, hiP, obs.x, obs.y);
    if (r.dist < best) { best = r.dist; bestProg = r.progress; }
  }
  if (best > MATCH_DIST && tracking) return { state, completion: done(state!.progress) };   // implausible -> hold

  const progress = Math.max(tracking ? state!.progress - EPS_BACK : 0, Math.min(1, bestProg));
  return { state: { edge: 0, progress, x: obs.x, y: obs.y, t: obs.t }, completion: done(progress) };
}
