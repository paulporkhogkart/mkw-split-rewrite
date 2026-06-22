import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { reconcileHistory } from './history_reconcile';
import type { ScrapedHistoryRow } from './history_parse';

function freshDb() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec(`INSERT INTO courses(id, slug, display_name) VALUES (7,'mario_bros_circuit','Mario Bros. Circuit')`);
  return db;
}

function row(over: Partial<ScrapedHistoryRow>): ScrapedHistoryRow {
  return {
    recordMs: 100000, recordStr: '1:40.000', dateIso: '2025-07-01T00:00:00.000Z',
    datePrecision: 'day', holderName: 'Alice', holderKey: 'Alice', nation: 'US',
    lapSplitsMs: [33000, 33000, 34000], coins: [8, 0, 0], mushrooms: [1, 1, 1],
    characterRaw: 'Toadette (Conductor)', kartRaw: 'Mach Rocket', videoUrl: 'https://y/1', ...over,
  };
}

describe('reconcileHistory', () => {
  it('inserts new rows, resolves names, and marks only the newest as current', () => {
    const db = freshDb();
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [
      row({ recordMs: 110000, holderName: 'Old' }),
      row({ recordMs: 100000, holderName: 'Alice' }),
    ]);
    expect(rep.inserted).toBe(2);
    const all = db.prepare(`SELECT holder_name, is_current, character_slug, kart_slug, costume_slug,
      lap_splits_ms, coins FROM world_records WHERE course_id=7 ORDER BY record_ms DESC`).all() as any[];
    expect(all.find((r) => r.holder_name === 'Alice').is_current).toBe(1);
    expect(all.find((r) => r.holder_name === 'Old').is_current).toBe(0);
    const a = all.find((r) => r.holder_name === 'Alice');
    expect([a.character_slug, a.costume_slug, a.kart_slug]).toEqual(['toadette', 'conductor', 'mach_rocket']);
    expect(JSON.parse(a.coins)).toEqual([8, 0, 0]);
  });

  it('enriches an existing legacy row in place (matched by natural key, not duplicated)', () => {
    const db = freshDb();
    db.exec(`INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, provenance)
             VALUES (7,150,'Alice',100000,'1:40.000','legacy_import')`);
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [row({})]);
    expect(rep.inserted).toBe(0);
    expect(rep.enriched).toBe(1);
    const cnt = db.prepare(`SELECT COUNT(*) c FROM world_records WHERE course_id=7`).get() as any;
    expect(cnt.c).toBe(1);                                  // enriched, not re-inserted
    const r = db.prepare(`SELECT nation, kart_slug FROM world_records WHERE course_id=7`).get() as any;
    expect([r.nation, r.kart_slug]).toEqual(['US', 'mach_rocket']);
  });

  it('soft-removes a row no longer present and flags an unresolved kart', () => {
    const db = freshDb();
    db.exec(`INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, provenance)
             VALUES (7,150,'Ghost',999999,'9:99.999','scraped_history')`);
    const rep = reconcileHistory(db, 7, 'Mario Bros. Circuit', 150, [row({ kartRaw: 'Totally Fake Kart' })]);
    expect(rep.removed).toBe(1);
    expect(rep.flagged).toBe(1);
    const ghost = db.prepare(`SELECT removed_at FROM world_records WHERE holder_name='Ghost'`).get() as any;
    expect(ghost.removed_at).not.toBeNull();
    const flag = db.prepare(`SELECT raw_value FROM wr_name_flags WHERE category='kart'`).get() as any;
    expect(flag.raw_value).toBe('Totally Fake Kart');
  });
});
