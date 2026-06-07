import type { DatabaseSync } from 'node:sqlite';

export type ReignInfo = {
  previous_holder: string | null;
  reign_ms: number | null;
  is_same_person: boolean;
} | null;

/** Reign of the holder being dethroned (prevHolder), from world_records history.
 *  Walks newest->oldest; the reign starts at the oldest contiguous prevHolder row.
 *  Graceful: null duration when timestamps are missing. */
export function wrReign(
  db: DatabaseSync, courseId: number, cc: number,
  prevHolder: string | null, newHolder: string | null,
): ReignInfo {
  if (!prevHolder) return null;
  const rows = db.prepare(
    `SELECT holder_name, achieved_at FROM world_records
     WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC`
  ).all(courseId, cc) as { holder_name: string | null; achieved_at: string | null }[];

  let reignStart: string | null = null;
  for (const r of rows) {
    if (r.holder_name === prevHolder) reignStart = r.achieved_at ?? reignStart;
    else if (reignStart !== null) break;   // passed the contiguous prevHolder block
  }
  const is_same_person = newHolder != null && newHolder === prevHolder;
  if (!reignStart) return { previous_holder: prevHolder, reign_ms: null, is_same_person };
  const ms = Date.now() - Date.parse(reignStart);
  return { previous_holder: prevHolder, reign_ms: Number.isFinite(ms) && ms >= 0 ? ms : null, is_same_person };
}
