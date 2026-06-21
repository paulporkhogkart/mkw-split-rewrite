// Which courses are "on fire" for a given frame, and the render list for the flame layer.
// Pure: reuses the single on-fire formula (fireModel.isOnFire) and the timeline board reducer
// (leaderboardAt). The map renderer (MapFireLayer) consumes fireListAt's output.
import { isOnFire } from "./fireModel.js";
import { leaderboardAt } from "./timeline.js";

const NEUTRAL = "#888";

/** Subset of `entries` ({ slug, t1, t2, wr, ...passthrough }) that is on fire; entries returned
 *  unchanged so render fields (hit, color) ride along. */
export function onFireCourses(entries) {
  return entries.filter((e) => isOnFire({ t1: e.t1, t2: e.t2, wr: e.wr }));
}

/** Build the on-fire render list for the shown frame: for each course, the top-two times AS OF
 *  `t` (from the event stream), the current WR, the leader's colour, and the course's hit box;
 *  then filter to the on-fire subset. */
export function fireListAt({ courses, events, wrs, colors, t }) {
  const entries = [];
  for (const c of courses) {
    const board = leaderboardAt(events, c.slug, t);
    if (board.length < 2) continue;            // need a real #2 to be on fire
    entries.push({
      slug: c.slug,
      hit: c.hit,
      color: colors[board[0].player] || NEUTRAL,
      t1: board[0].ms,
      t2: board[1].ms,
      wr: wrs[c.slug] ?? null,
    });
  }
  return onFireCourses(entries);
}
