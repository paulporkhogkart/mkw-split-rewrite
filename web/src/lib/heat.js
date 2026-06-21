// Per-course competitive standing + "on fire" metrics, derived live from the timeline event
// stream. Uses the same inputs as the territory map's flames (leaderboardAt + fireModel), so the
// heat page and the map cannot disagree on which courses are lit. Pure: no DOM, no fetch.
import { fireBarPct, isOnFire, snuffLeadMs } from "./fireModel.js";
import { leaderboardAt } from "./timeline.js";

const NEUTRAL = "#888";

/** Standing + fire metrics for one course AS OF `t`, or null when the course lacks a real #2 or
 *  a current WR (both are required to judge "on fire", matching the explorer's regen). */
export function courseRowAt({ course, events, wrs, colors, t }) {
  const board = leaderboardAt(events, course.slug, t);
  const wr = wrs[course.slug] ?? null;
  if (board.length < 2 || !wr) return null;
  const t1 = board[0].ms;
  const t2 = board[1].ms;
  const leader = board[0].player;
  const offPct = ((t1 - wr) / wr) * 100;
  return {
    slug: course.slug,
    name: course.name,
    leader,
    color: colors[leader] || NEUTRAL,
    t1,
    t2,
    wr,
    leadPct: ((t2 - t1) / wr) * 100, // lead over #2, % of WR (x axis)
    offPct,                          // how far the PB sits off the WR, % (y axis)
    barPct: fireBarPct(offPct),      // locked fire bar at this off%
    fire: isOnFire({ t1, t2, wr }),  // locked-model verdict (map parity / no-drift)
    snuffMs: snuffLeadMs({ t1, wr }),// lead in ms a rival must beat to snuff
  };
}

/** One row per qualifying course (real #2 + current WR) as of `t`. */
export function heatRows({ courses, events, wrs, colors, t }) {
  const rows = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrs, colors, t });
    if (row) rows.push(row);
  }
  return rows;
}
