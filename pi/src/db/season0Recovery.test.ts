import { describe, it, expect } from 'vitest';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openDb, applySchema } from './connect';
import { applyRecoveredSeason0, migrateSeason0Recovered, type RecoveredEvent } from './season0Recovery';

function dbWithSeasons() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,started_at,ended_at,is_active) VALUES (1,'Season 0','2025-06-01T00:00:00Z','2026-06-05T00:00:00+00:00',0)");
  db.exec("INSERT INTO seasons(id,name,started_at,is_active) VALUES (2,'Season 1','2026-06-05T00:00:00+00:00',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Aliias'),(2,'Gub')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'mario_circuit','Mario Circuit')");
  return db;
}

const ev = (o: Partial<RecoveredEvent>): RecoveredEvent => ({
  attempt_id: 'dc-x', player: 'Aliias', course_slug: 'mario_circuit', cc: 150,
  total_time_ms: 83000, total_time_str: '1:23.000', ended_at: '2025-06-26T00:00:00Z', ...o,
});

describe('applyRecoveredSeason0', () => {
  it('replaces S0 legacy_import with recovered events before the cutover, tagged source=discord', () => {
    const db = dbWithSeasons();
    db.exec("INSERT INTO runs(attempt_id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms) VALUES ('old',1,1,1,150,'finished','legacy_import','2025-07-01T00:00:00Z',90000)");
    applyRecoveredSeason0(db, [
      ev({ attempt_id: 'dc-a', player: 'Aliias', total_time_ms: 83000, ended_at: '2025-06-26T00:00:00Z' }),
      ev({ attempt_id: 'dc-b', player: 'Gub', total_time_ms: 70000, ended_at: '2026-07-01T00:00:00Z' }), // after cutover -> excluded
    ]);
    const s0 = db.prepare("SELECT attempt_id, source, provenance FROM runs WHERE season_id=1 ORDER BY attempt_id").all() as any[];
    expect(s0.map((r) => r.attempt_id)).toEqual(['dc-a']);  // synthetic removed, post-cutover excluded
    expect(s0[0].source).toBe('discord');
    expect(s0[0].provenance).toBe('legacy_import');
  });

  it('recomputes is_pb so the fastest recovered run wins its scope', () => {
    const db = dbWithSeasons();
    applyRecoveredSeason0(db, [
      ev({ attempt_id: 'dc-slow', total_time_ms: 90000, ended_at: '2025-06-24T00:00:00Z' }),
      ev({ attempt_id: 'dc-fast', total_time_ms: 83000, ended_at: '2025-06-26T00:00:00Z' }),
    ]);
    const pb = (db.prepare("SELECT attempt_id FROM runs WHERE season_id=1 AND is_pb=1").all() as any[]).map((r) => r.attempt_id);
    expect(pb).toEqual(['dc-fast']);
  });

  it('is idempotent and leaves Season 1 untouched', () => {
    const db = dbWithSeasons();
    db.exec("INSERT INTO runs(attempt_id,season_id,player_id,course_id,cc,status,provenance,ended_at,total_time_ms,is_pb) VALUES ('cv',2,1,1,150,'finished','carryover','2025-07-01T00:00:00Z',88000,1)");
    const events = [ev({ attempt_id: 'dc-a', ended_at: '2025-06-26T00:00:00Z' })];
    applyRecoveredSeason0(db, events);
    applyRecoveredSeason0(db, events);   // second call: no-op
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE season_id=1").get() as any).c).toBe(1);
    const s1 = db.prepare("SELECT attempt_id, provenance, is_pb FROM runs WHERE season_id=2").all() as any[];
    expect(s1).toEqual([{ attempt_id: 'cv', provenance: 'carryover', is_pb: 1 }]);
  });

  it('migrateSeason0Recovered loads the data file and applies it', () => {
    const db = dbWithSeasons();
    const p = join(tmpdir(), `s0rec-${Date.now()}.json`);
    writeFileSync(p, JSON.stringify([ev({ attempt_id: 'dc-a', ended_at: '2025-06-26T00:00:00Z' })]));
    migrateSeason0Recovered(db, p);
    expect((db.prepare("SELECT COUNT(*) c FROM runs WHERE season_id=1 AND source='discord'").get() as any).c).toBe(1);
  });
});
