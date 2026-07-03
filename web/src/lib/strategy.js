// Strategy sorts for the players page. One per-course table (the summary's `pbs`), three sort
// keys, one shared difficulty kernel: the on-fire fireBarPct — the natural spread of time "in
// play" at a given distance off the WR. A fixed ms gap is easy far off WR (fat denominator),
// brutal near it. GOLF = cheapest next place; TURF = softest #1 to steal; TIME = worst PBs.
import { fireBarPct } from "./fireModel.js";

const gapPct = (yourMs, rivalMs, wrMs) => Math.max(0, ((yourMs - rivalMs) / wrMs) * 100);
const offPct = (yourMs, wrMs) => Math.max(0, ((yourMs - wrMs) / wrMs) * 100);

/** Courses you don't lead, ranked by difficulty-adjusted ease of gaining your next single place. */
export function golfList(pbs) {
  return pbs
    .filter((r) => !r.leads && r.next_rank_ms != null && r.wr_ms != null)
    .map((r) => ({ ...r, ease: gapPct(r.your_ms, r.next_rank_ms, r.wr_ms) / fireBarPct(offPct(r.your_ms, r.wr_ms)) }))
    .sort((a, b) => a.ease - b.ease);
}

/** Courses you don't lead, ranked by how snuffable the leader is: ease to the leader, made
 *  easier the further off WR the leader sits (a soft record is more stealable). */
export function turfList(pbs) {
  return pbs
    .filter((r) => !r.leads && r.leader_ms != null && r.wr_ms != null && r.leader_off_wr_pct != null)
    .map((r) => {
      const ease = gapPct(r.your_ms, r.leader_ms, r.wr_ms) / fireBarPct(offPct(r.your_ms, r.wr_ms));
      return { ...r, ease, score: ease / fireBarPct(r.leader_off_wr_pct) };
    })
    .sort((a, b) => a.score - b.score);
}

/** Your PBs, worst % off WR first — where your total time bleeds most. */
export function timeList(pbs) {
  return pbs
    .filter((r) => r.off_wr_pct != null)
    .map((r) => ({ ...r }))
    .sort((a, b) => b.off_wr_pct - a.off_wr_pct);
}
