// Which courses are "on fire" for a given frame, and the render list for the flame layer.
// Pure: reuses the shared per-course derivation (heat.courseRowAt) so the map's flames and the
// heat page agree by construction. fireListAt keeps its { slug, hit, color, t1, t2, wr } shape
// (consumed by WorldMap -> MapFireLayer).
import { isOnFire } from "./fireModel.js";
import { courseRowAt } from "./heat.js";

/** Subset of `entries` ({ slug, t1, t2, wr, ...passthrough }) that is on fire; entries returned
 *  unchanged so render fields (hit, color) ride along. */
export function onFireCourses(entries) {
  return entries.filter((e) => isOnFire({ t1: e.t1, t2: e.t2, wr: e.wr }));
}

/** On-fire render list for the shown frame: each lit course's standing AS OF `t` (shared
 *  derivation), the leader's colour, and the course's hit box. */
export function fireListAt({ courses, events, wrs, colors, t }) {
  const out = [];
  for (const c of courses) {
    const row = courseRowAt({ course: c, events, wrs, colors, t });
    if (row && row.fire) out.push({ slug: row.slug, hit: c.hit, color: row.color, t1: row.t1, t2: row.t2, wr: row.wr });
  }
  return out;
}
