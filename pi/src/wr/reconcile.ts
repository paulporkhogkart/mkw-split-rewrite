import type { DatabaseSync } from 'node:sqlite';
import type { EventHub } from '../api/events';
import type { ActivityHub } from '../activity/hub';
import type { ActivityInput } from '../activity/types';
import { resolveCourseId } from './courses';
import type { ScrapedWr } from './parse';
import { resolveLoadout } from './loadout';
import type { Loadout } from './loadout';
import { upsertFlag } from './flags';
import { courseLeaderboard } from '../db/reads';
import { activeSeasonId } from '../db/seasons';
import { turfTransitions } from '../turf/transitions';
import { commitActivity } from '../activity/publish';

export type WrReport = {
  inserted: number;
  reflagged: number;
  backfilled: number;
  unchanged: number;
  unmapped: string[];
};

type Row = {
  id: number; holder_name: string | null; record_ms: number; record_str: string;
  video_url: string | null; character: string | null; vehicle: string | null;
};

const isoDate = (date: string | null): string =>
  date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T00:00:00.000Z` : new Date().toISOString();

/** Update video/character/vehicle (and holder if currently null) on `row` from the
 *  scrape, only where the scraped value is non-empty and differs. A changed raw
 *  character/vehicle also re-resolves its slugs, so an mkwrs correction propagates.
 *  Returns true if it wrote. */
function backfill(db: DatabaseSync, row: Row, s: ScrapedWr): boolean {
  const sets: string[] = [];
  const vals: (string | null)[] = [];
  if (row.holder_name == null && s.holder) { sets.push('holder_name=?'); vals.push(s.holder); }
  if (s.videoUrl && s.videoUrl !== row.video_url) { sets.push('video_url=?'); vals.push(s.videoUrl); }
  if (s.character && s.character !== row.character) {
    const lo = resolveLoadout(s.character, null);
    sets.push('character=?', 'character_slug=?', 'costume_slug=?');
    vals.push(s.character, lo.characterSlug, lo.costumeSlug);
  }
  if (s.vehicle && s.vehicle !== row.vehicle) {
    const lo = resolveLoadout(null, s.vehicle);
    sets.push('vehicle=?', 'kart_slug=?');
    vals.push(s.vehicle, lo.kartSlug);
  }
  if (sets.length === 0) return false;
  db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, row.id);
  return true;
}

export function reconcile(db: DatabaseSync, hub: EventHub, scraped: ScrapedWr[], cc = 150, activity?: ActivityHub): WrReport {
  const report: WrReport = { inserted: 0, reflagged: 0, backfilled: 0, unchanged: 0, unmapped: [] };
  for (const s of scraped) {
    const courseId = resolveCourseId(db, s.courseName);
    if (courseId === null) { report.unmapped.push(s.courseName); continue; }
    try { reconcileOne(db, hub, s, courseId, cc, report, activity); }
    catch (e) { console.error(`[wr] reconcile failed for ${s.courseName}:`, e); }
  }
  return report;
}

/** Record + announce any name in `lo` that failed to resolve. Announce only on first sighting;
 *  an unresolved name blocks WR processing for that record, so it needs a human. */
function flagUnresolved(db: DatabaseSync, hub: EventHub, lo: Loadout,
                        courseName: string, courseId: number, wrId: number | null): void {
  for (const u of lo.unresolved) {
    const { isNew } = upsertFlag(db, {
      category: u.category, rawValue: u.raw, slugGuess: u.slugGuess,
      exampleCourseId: courseId, exampleWrId: wrId ?? undefined,
    });
    if (isNew) hub.publish({ type: 'wr_name_flag', category: u.category,
      raw_value: u.raw, slug_guess: u.slugGuess, course: courseName });
  }
}

function reconcileOne(db: DatabaseSync, hub: EventHub, s: ScrapedWr, courseId: number, cc: number, report: WrReport, activity?: ActivityHub): void {
  const cur = db.prepare(
    `SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
     FROM world_records WHERE course_id=? AND cc=? AND is_current=1`
  ).get(courseId, cc) as Row | undefined;

  // Case 1: same record as current -> backfill metadata in place, no current move.
  if (cur && cur.record_ms === s.recordMs && cur.holder_name === s.holder) {
    if (backfill(db, cur, s)) report.backfilled++; else report.unchanged++;
    flagUnresolved(db, hub, resolveLoadout(s.character, s.vehicle), s.courseName, courseId, cur.id);
    return;
  }

  // Case 2: the current WR changed -> mirror the page (one transaction).
  let insertedWrId: number | null = null;
  let reflaggedWrId: number | null = null;
  db.exec('BEGIN');
  try {
    if (cur) db.prepare('UPDATE world_records SET is_current=0 WHERE id=?').run(cur.id);
    const existing = db.prepare(
      `SELECT id, holder_name, record_ms, record_str, video_url, character, vehicle
       FROM world_records WHERE course_id=? AND cc=? AND record_ms=? AND holder_name IS ?
       ORDER BY id DESC LIMIT 1`
    ).get(courseId, cc, s.recordMs, s.holder) as Row | undefined;
    if (existing) {
      db.prepare('UPDATE world_records SET is_current=1 WHERE id=?').run(existing.id);
      backfill(db, existing, s);
      reflaggedWrId = existing.id;
      report.reflagged++;
    } else {
      const lo = resolveLoadout(s.character, s.vehicle);
      const res = db.prepare(
        `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str,
           achieved_at, video_url, character, vehicle,
           character_slug, costume_slug, kart_slug, provenance, is_current)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'scraped', 1)`
      ).run(courseId, cc, s.holder, s.recordMs, s.recordStr,
            isoDate(s.date), s.videoUrl, s.character, s.vehicle,
            lo.characterSlug, lo.costumeSlug, lo.kartSlug);
      insertedWrId = Number(res.lastInsertRowid);
      report.inserted++;
    }
    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }

  flagUnresolved(db, hub, resolveLoadout(s.character, s.vehicle), s.courseName, courseId,
                 insertedWrId ?? reflaggedWrId);

  // Emit only when a prior current existed (silent first-scrape establishment).
  if (cur) {
    hub.publish({
      type: 'wr_update', course: s.courseName, cc,
      holder: s.holder, total_time: s.recordStr,
      prev_holder: cur.holder_name, prev_time: cur.record_str,
      improvement_ms: cur.record_ms - s.recordMs,
      character: s.character, vehicle: s.vehicle, video_url: s.videoUrl,
    });
    if (activity) {
      const seasonId = activeSeasonId(db);
      const board = courseLeaderboard(db, seasonId, courseId, cc);
      const ts = Date.now();
      const inputs: ActivityInput[] = [{ ts, type: 'wr', season_id: seasonId, player_id: null, course_id: courseId, cc,
        payload: { time_ms: s.recordMs, time_str: s.recordStr, holder: s.holder, delta_ms: s.recordMs - cur.record_ms } }];
      for (const t of turfTransitions({ board, wr: cur.record_ms }, { board, wr: s.recordMs })) {
        if (t.kind === 'fire') inputs.push({ ts, type: 'turf_fire', season_id: seasonId, player_id: t.leaderId, course_id: courseId, cc, payload: {} });
        else if (t.kind === 'waver') inputs.push({ ts, type: 'turf_waver', season_id: seasonId, player_id: t.leaderId, course_id: courseId, cc, payload: {} });
      }
      commitActivity(db, activity, inputs);
    }
  }
}
