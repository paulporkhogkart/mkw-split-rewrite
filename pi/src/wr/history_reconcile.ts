import type { DatabaseSync } from 'node:sqlite';
import { splitCharacter, type ScrapedHistoryRow } from './history_parse';
import { resolveItem } from './roster';
import { upsertFlag } from './flags';

export type HistoryReport = {
  course: string; inserted: number; enriched: number; unchanged: number; removed: number; flagged: number;
};

type ExistingRow = {
  id: number; nation: string | null; character_slug: string | null; kart_slug: string | null;
  costume_slug: string | null; lap_splits_ms: string | null; coins: string | null;
  mushrooms: string | null; video_url: string | null; character: string | null; vehicle: string | null;
  date_precision: string | null; source_raw: string | null; removed_at: string | null;
};

const J = (v: unknown): string | null => (v == null ? null : JSON.stringify(v));

export function reconcileHistory(
  db: DatabaseSync, courseId: number, courseName: string, cc: number, rows: ScrapedHistoryRow[],
): HistoryReport {
  const report: HistoryReport = { course: courseName, inserted: 0, enriched: 0, unchanged: 0, removed: 0, flagged: 0 };
  if (rows.length === 0) return report;
  const now = new Date().toISOString();
  const seen: number[] = [];

  const findExisting = db.prepare(
    `SELECT id, nation, character_slug, kart_slug, costume_slug, lap_splits_ms, coins, mushrooms,
            video_url, character, vehicle, date_precision, source_raw, removed_at
     FROM world_records WHERE course_id=? AND cc=? AND record_ms=? AND holder_name IS ?`
  );

  db.exec('BEGIN');
  try {
    for (const r of rows) {
      const { character, costume } = splitCharacter(r.characterRaw ?? '');
      const ch = character ? resolveItem('character', character) : null;
      const co = costume ? resolveItem('costume', costume) : null;
      const ka = r.kartRaw ? resolveItem('kart', r.kartRaw) : null;
      const sourceRaw = JSON.stringify(r);

      const existing = findExisting.get(courseId, cc, r.recordMs, r.holderName) as ExistingRow | undefined;
      let wrId: number;
      if (existing) {
        wrId = existing.id;
        const sets: string[] = [], vals: (string | number | null)[] = [];
        const set = (col: string, val: string | number | null, cur: unknown) => {
          if (val != null && val !== cur) { sets.push(`${col}=?`); vals.push(val); }
        };
        set('nation', r.nation, existing.nation);
        set('character_slug', ch?.slug ?? null, existing.character_slug);
        set('kart_slug', ka?.slug ?? null, existing.kart_slug);
        set('costume_slug', co?.slug ?? null, existing.costume_slug);
        set('lap_splits_ms', J(r.lapSplitsMs), existing.lap_splits_ms);
        set('coins', J(r.coins), existing.coins);
        set('mushrooms', J(r.mushrooms), existing.mushrooms);
        set('video_url', r.videoUrl, existing.video_url);
        set('character', r.characterRaw, existing.character);
        set('vehicle', r.kartRaw, existing.vehicle);
        set('date_precision', r.datePrecision, existing.date_precision);
        set('source_raw', sourceRaw, existing.source_raw);
        if (existing.removed_at != null) { sets.push('removed_at=?'); vals.push(null); }  // reappeared
        if (sets.length) {
          db.prepare(`UPDATE world_records SET ${sets.join(', ')} WHERE id=?`).run(...vals, wrId);
          report.enriched++;
        } else { report.unchanged++; }
      } else {
        const res = db.prepare(
          `INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, achieved_at,
             video_url, character, vehicle, nation, character_slug, kart_slug, costume_slug,
             lap_splits_ms, coins, mushrooms, date_precision, source_raw, provenance, is_current)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'scraped_history', 0)`
        ).run(courseId, cc, r.holderName, r.recordMs, r.recordStr, r.dateIso, r.videoUrl,
              r.characterRaw, r.kartRaw, r.nation, ch?.slug ?? null, ka?.slug ?? null, co?.slug ?? null,
              J(r.lapSplitsMs), J(r.coins), J(r.mushrooms), r.datePrecision, sourceRaw);
        wrId = Number(res.lastInsertRowid);
        report.inserted++;
      }
      seen.push(wrId);

      // Flag unresolved names (present-but-unresolved only; a bare base costume is not flagged).
      if (character && ch && !ch.slug) { upsertFlag(db, { category: 'character', rawValue: character, slugGuess: ch.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
      if (costume && co && !co.slug) { upsertFlag(db, { category: 'costume', rawValue: costume, slugGuess: co.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
      if (r.kartRaw && ka && !ka.slug) { upsertFlag(db, { category: 'kart', rawValue: r.kartRaw, slugGuess: ka.slugGuess, exampleCourseId: courseId, exampleWrId: wrId }); report.flagged++; }
    }

    // is_current: the newest row (last in page order) is the current WR.
    const cur = rows[rows.length - 1];
    const curRow = findExisting.get(courseId, cc, cur.recordMs, cur.holderName) as { id: number } | undefined;
    db.prepare('UPDATE world_records SET is_current=0 WHERE course_id=? AND cc=?').run(courseId, cc);
    if (curRow) db.prepare('UPDATE world_records SET is_current=1 WHERE id=?').run(curRow.id);

    // Soft-remove any row for this course not present in this scrape (mkwrs is authoritative).
    const placeholders = seen.map(() => '?').join(',');
    report.removed = Number((db.prepare(
      `SELECT COUNT(*) c FROM world_records
       WHERE course_id=? AND cc=? AND removed_at IS NULL AND id NOT IN (${placeholders})`
    ).get(courseId, cc, ...seen) as { c: number }).c);
    db.prepare(
      `UPDATE world_records SET removed_at=?
       WHERE course_id=? AND cc=? AND removed_at IS NULL AND id NOT IN (${placeholders})`
    ).run(now, courseId, cc, ...seen);

    db.exec('COMMIT');
  } catch (e) { db.exec('ROLLBACK'); throw e; }
  return report;
}
