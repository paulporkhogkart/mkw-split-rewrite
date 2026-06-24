// pi/src/activity/backfill.test.ts
import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { backfillActivity } from './backfill';
import { recentActivity } from '../db/activity';

function seed() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name,color) VALUES (1,'Paul','#a78bfa'),(2,'Gub','#38bdf8')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'crown_city','Crown City')");
  return db;
}

describe('backfillActivity', () => {
  it('inserts pb + turf_claim in chronological order, idempotent on second call', () => {
    const db = seed();

    // Paul sets a PB first (he owns the board solo)
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (1,1,1,1,150,'finished','live',108600,'1:48.600','2026-01-01T10:00:00.000Z')`);

    // Gub then beats Paul and takes #1
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (2,1,2,1,150,'finished','live',107980,'1:47.980','2026-01-01T11:00:00.000Z')`);

    const count = backfillActivity(db);

    // Expect at least: pb for Paul, pb for Gub, turf_claim for Gub
    // Paul's run: pb only (first on board → no rank beaten, no claim since board was empty before)
    // Gub's run: pb + rank (beats Paul's #1) + turf_claim (takes #1 from Paul)
    expect(count).toBeGreaterThan(0);

    const events = recentActivity(db, { seasonId: 1, limit: 50 });
    // events are newest-first by id; reverse to get chronological
    const chrono = [...events].reverse();

    // All events should have a ts
    for (const e of chrono) expect(e.ts).toBeGreaterThan(0);

    // Paul's PB should appear first (earlier ended_at)
    const paulPb = chrono.find(e => e.type === 'pb' && e.player?.id === 1);
    const gubPb = chrono.find(e => e.type === 'pb' && e.player?.id === 2);
    const claim = chrono.find(e => e.type === 'turf_claim' && e.player?.id === 2);

    expect(paulPb).toBeDefined();
    expect(gubPb).toBeDefined();
    expect(claim).toBeDefined();

    // pb events carry the expected payloads
    expect((paulPb!.payload as any).time_ms).toBe(108600);
    expect((paulPb!.payload as any).delta_ms).toBeNull(); // first PB, no prior
    expect((gubPb!.payload as any).time_ms).toBe(107980);
    expect((gubPb!.payload as any).delta_ms).toBeNull(); // Gub's first PB too

    // turf_claim: Gub claims from Paul
    expect((claim!.payload as any).rival_id).toBe(1);

    // Events should be in chronological id order: Paul's pb before Gub's pb
    expect(paulPb!.id).toBeLessThan(gubPb!.id);
    expect(gubPb!.id).toBeLessThan(claim!.id);

    // Idempotency: second call inserts nothing and returns 0
    const count2 = backfillActivity(db);
    expect(count2).toBe(0);

    const eventsAfter = recentActivity(db, { seasonId: 1, limit: 50 });
    expect(eventsAfter.length).toBe(events.length);
  });

  it('skips non-PB runs (same player posts a slower run after a faster one)', () => {
    const db = seed();

    // Paul's fast run first
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (1,1,1,1,150,'finished','live',107000,'1:47.000','2026-01-01T10:00:00.000Z')`);
    // Paul's slower run second (NOT a new PB)
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (2,1,1,1,150,'finished','live',108000,'1:48.000','2026-01-01T11:00:00.000Z')`);

    const count = backfillActivity(db);
    const events = recentActivity(db, { seasonId: 1, limit: 50 });

    // Only the first run (new PB) should produce events; the slower second run is skipped
    const pbs = events.filter(e => e.type === 'pb');
    expect(pbs).toHaveLength(1);
    expect((pbs[0].payload as any).time_ms).toBe(107000);
    expect(count).toBe(pbs.length + events.filter(e => e.type !== 'pb').length);
  });

  it('skips carryover and non-finished runs', () => {
    const db = seed();

    // carryover run — should be ignored
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (1,1,1,1,150,'finished','carryover',107000,'1:47.000','2026-01-01T09:00:00.000Z')`);
    // reset run — should be ignored
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (2,1,1,1,150,'reset','live',107000,'1:47.000','2026-01-01T09:30:00.000Z')`);
    // valid finished run
    db.exec(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at)
             VALUES (3,1,2,1,150,'finished','live',107980,'1:47.980','2026-01-01T10:00:00.000Z')`);

    const count = backfillActivity(db);
    const events = recentActivity(db, { seasonId: 1, limit: 50 });
    // Only Gub's finished non-carryover run should generate events
    const pbs = events.filter(e => e.type === 'pb');
    expect(pbs).toHaveLength(1);
    expect(pbs[0].player?.id).toBe(2);
    expect(count).toBe(events.length);
  });
});
