// pi/src/progress/project.ts
import type { CourseModel, GraphEdge, ProjState, Obs } from './types';

const EPS_BACK = 0.004;       // backward tolerance in within-lap progress
const REACH_K = 2.5;          // forward window = K * pixelsMoved / lapLengthPx
const EPS_FWD_MIN = 0.02;     // minimum forward reach in progress
const RATE_ALPHA = 0.2;       // pace EMA (within-course completion per ms)
const GLIDE_MAX_MS = 2000;    // hold paths dead-reckon at pace for at most this long
// Mutable so the projector-lab sweep can tune against recorded trails;
// production code treats it as a constant.
export const TUNING = {
  matchDist: 60,              // px; nearest edge beyond this -> hold/bootstrap
};
// staleness is decided by the caller (hub) and passed in as obs.stale.

interface PreparedEdge { edge: GraphEdge; cumFrac: number[]; }
export interface PreparedLap { edges: PreparedEdge[]; }
export interface Prepared { laps: PreparedLap[]; }

export function prepareModel(m: CourseModel): Prepared {
  return { laps: m.laps.map((lap) => ({ edges: lap.graph.edges.map((edge) => {
    const cum = [0];
    for (let i = 1; i < edge.poly.length; i++)
      cum.push(cum[i - 1] + Math.hypot(edge.poly[i][0] - edge.poly[i - 1][0], edge.poly[i][1] - edge.poly[i - 1][1]));
    const total = cum[cum.length - 1] || 1;
    return { edge, cumFrac: cum.map((c) => c / total) };
  }) })) };
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

/** Project onto the current lap's route; completion is cumulative distance over the whole course. */
export function projectStep(state: ProjState, m: CourseModel, pe: Prepared, obs: Obs):
    { state: ProjState; completion: number | null } {
  const N = m.laps.length || 1;
  const k = Math.min(Math.max(obs.lap, 1), N);
  const lapRoute = m.laps[k - 1];
  const plap = pe.laps[k - 1];
  const toPct = (u: number) =>
    Math.max(0, Math.min(1, (lapRoute.startOffsetPx + u * lapRoute.lengthPx) / (m.totalLengthPx || 1)));
  // Hold paths publish the anchor dead-reckoned at the learned pace (bounded),
  // floored by the last published value so re-acquisition can never snap the
  // bar backward; the stored anchor itself is never advanced.
  const hold = (): { state: ProjState; completion: number | null } => {
    if (!state) return { state, completion: null };
    const dt = Math.max(0, obs.t - state.t);
    const glide = state.rate != null ? state.rate * Math.min(dt, GLIDE_MAX_MS) : 0;
    const c = Math.max(toPct(state.progress) + glide, state.pub ?? 0);
    return { state: { ...state, pub: c }, completion: c };
  };
  const finished = obs.lap > N;
  if (finished) return { state, completion: 1 };
  if (obs.stale) return hold();
  if (!plap || plap.edges.length === 0) return hold();

  const tracking = state != null;
  const loP = tracking ? Math.max(0, state!.progress - EPS_BACK) : 0;
  const moved = tracking ? Math.hypot(obs.x - state!.x, obs.y - state!.y) : 0;
  const reach = Math.max(EPS_FWD_MIN, REACH_K * moved / (lapRoute.lengthPx || 1));
  const hiP = tracking ? Math.min(1, state!.progress + reach) : 1;

  let best = Infinity, bestU = tracking ? state!.progress : 0;
  for (const e of plap.edges) {
    const r = nearestOnEdge(e, loP, hiP, obs.x, obs.y);
    if (r.dist < best) { best = r.dist; bestU = r.progress; }
  }
  if (best > TUNING.matchDist && tracking) return hold();

  const u = Math.max(tracking ? state!.progress - EPS_BACK : 0, Math.min(1, bestU));
  const cTrue = toPct(u);
  // Pace EMA on the true (un-floored) completion deltas of confident steps.
  const dt = tracking ? Math.max(0, obs.t - state!.t) : 0;
  const obsRate = tracking && dt > 0 ? Math.max(0, (cTrue - toPct(state!.progress)) / dt) : null;
  const rate = !tracking ? null
    : obsRate == null ? (state!.rate ?? null)
    : state!.rate == null ? obsRate
    : state!.rate + RATE_ALPHA * (obsRate - state!.rate);
  const c = Math.max(cTrue, (tracking ? state!.pub : undefined) ?? 0);
  return { state: { edge: 0, progress: u, x: obs.x, y: obs.y, t: obs.t, rate, pub: c }, completion: c };
}
