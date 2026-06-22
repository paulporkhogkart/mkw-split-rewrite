import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { makeLapDelta } from './lapDelta';

const COURSE = 'Bowsers Castle';
const CID = 1;

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Paul'),(2,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(${CID},'bowsers_castle','Bowsers Castle');`);
  return d;
}

function insertRun(d: DatabaseSync, id: number, player: number, lapMs: number[],
                   opts: { isPb?: number; status?: string; provenance?: string } = {}) {
  const total = lapMs.reduce((a, b) => a + b, 0);
  d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
             VALUES(?,1,?,?,150,?,?,?,?)`)
    .run(id, player, CID, opts.status ?? 'finished', total, opts.isPb ?? 0, opts.provenance ?? 'live');
  const stmt = d.prepare('INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (?,?,?)');
  lapMs.forEach((ms, i) => stmt.run(id, i + 1, ms));
}

// PB laps 51s/52s/53s; an older (slower-total) run holds every gold BELOW the PB
// laps (50/51/50), so gaining on the PB and golding are distinct conditions.
function seeded() {
  const d = db();
  insertRun(d, 1, 1, [50000, 51000, 50000]);              // golds per lap
  insertRun(d, 2, 1, [51000, 52000, 53000], { isPb: 1 });
  return d;
}
const last = (r: ReturnType<ReturnType<typeof makeLapDelta>>) => r!.deltas[r!.deltas.length - 1];

describe('makeLapDelta', () => {
  it('returns one delta row per completed lap plus the PB laps', () => {
    const r = makeLapDelta(seeded())(1, COURSE, [52000, 51500]);
    expect(r!.pb_laps_ms).toEqual([51000, 52000, 53000]);
    expect(r!.deltas).toEqual([
      { lap: 1, delta_ms: 1000, seg_delta_ms: 1000, gained: false, gold: false },
      { lap: 2, delta_ms: 500, seg_delta_ms: -500, gained: true, gold: false },
    ]);
  });

  it('signs the cumulative delta and flags the segment gain per LiveSplit rules', () => {
    const lap = makeLapDelta(seeded());
    expect(last(lap(1, COURSE, [52000]))).toEqual({ lap: 1, delta_ms: 1000, seg_delta_ms: 1000, gained: false, gold: false });
    expect(last(lap(1, COURSE, [49500, 53000]))).toEqual({ lap: 2, delta_ms: -500, seg_delta_ms: 1000, gained: false, gold: false });
    expect(last(lap(1, COURSE, [51000, 52000, 52500]))).toEqual({ lap: 3, delta_ms: -500, seg_delta_ms: -500, gained: true, gold: false });
  });

  it('flags a gold when the lap beats the best-ever finished segment', () => {
    const lap = makeLapDelta(seeded());
    expect(last(lap(1, COURSE, [49000]))).toEqual({ lap: 1, delta_ms: -2000, seg_delta_ms: -2000, gained: true, gold: true });
    expect(last(lap(1, COURSE, [50500])).gold).toBe(false);   // beats the PB lap, not the 50.0 gold
    const d = seeded();
    insertRun(d, 3, 1, [40000], { status: 'reset' });
    expect(last(makeLapDelta(d)(1, COURSE, [49000])).gold).toBe(true);   // reset laps never hold golds
  });

  it('with no completed laps yet, still serves the PB laps (the rail shows them from the start)', () => {
    const lap = makeLapDelta(seeded());
    expect(lap(1, COURSE, [])).toEqual({ pb_laps_ms: [51000, 52000, 53000], deltas: [] });
    expect(lap(1, COURSE, null)).toEqual({ pb_laps_ms: [51000, 52000, 53000], deltas: [] });
  });

  it('gates: no PB / PB without laps / unknown course', () => {
    const d = db();
    const lap = makeLapDelta(d);
    expect(lap(1, COURSE, [50000])).toBeNull();                     // no runs at all
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
               VALUES(9,1,2,${CID},150,'finished',150000,1,'carryover')`).run();
    expect(lap(2, COURSE, [50000])).toBeNull();                     // carryover PB has no run_laps
    expect(makeLapDelta(seeded())(1, 'Nope', [50000])).toBeNull();
  });

  it('clamps to the PB lap count and re-keys when the PB changes', () => {
    const d = seeded();
    const lap = makeLapDelta(d);
    expect(lap(1, COURSE, [51000, 52000, 53000, 99000])!.deltas).toHaveLength(3);
    insertRun(d, 4, 1, [50000, 51000, 52000]);
    d.exec('UPDATE runs SET is_pb=0 WHERE id=2; UPDATE runs SET is_pb=1 WHERE id=4;');
    expect(last(lap(1, COURSE, [51000]))).toEqual({ lap: 1, delta_ms: 1000, seg_delta_ms: 1000, gained: false, gold: false });
    expect(lap(1, COURSE, [])!.pb_laps_ms).toEqual([50000, 51000, 52000]);
  });

  it('honours a pinned PB run id over the current is_pb (post-finish upload flip)', () => {
    const d = seeded();
    const lap = makeLapDelta(d);
    insertRun(d, 4, 1, [50000, 51000, 52000]);   // the run just finished, now the db PB
    d.exec('UPDATE runs SET is_pb=0 WHERE id=2; UPDATE runs SET is_pb=1 WHERE id=4;');
    const pinned = lap(1, COURSE, [51000], 2);   // hub pins the pre-race PB (run 2)
    expect(pinned!.pb_laps_ms).toEqual([51000, 52000, 53000]);
    expect(last(pinned)).toEqual({ lap: 1, delta_ms: 0, seg_delta_ms: 0, gained: false, gold: false });
    expect(lap(1, COURSE, [51000])!.pb_laps_ms).toEqual([50000, 51000, 52000]);  // unpinned: live PB
  });

  it('invalidateCourse drops the cached comparison', () => {
    const d = seeded();
    const lap = makeLapDelta(d);
    expect(last(lap(1, COURSE, [52000])).delta_ms).toBe(1000);
    d.exec('DELETE FROM run_laps WHERE run_id=2');        // PB loses its laps
    expect(last(lap(1, COURSE, [52000])).delta_ms).toBe(1000); // still cached
    lap.invalidateCourse(CID);
    expect(lap(1, COURSE, [52000])).toBeNull();           // fresh load -> no comparison
  });

  it('bestSegments snapshots the current best-ever finished segment per lap', () => {
    const lap = makeLapDelta(seeded());
    expect(lap.bestSegments(1, COURSE)).toEqual(new Map([[1, 50000], [2, 51000], [3, 50000]]));
    expect(lap.bestSegments(1, 'Nope')).toBeNull();       // unknown course
  });

  it('honours a pinned gold baseline so a fresh gold survives the finish upload', () => {
    const d = seeded();                                   // pre-race best lap 1 = 50000 (run 1)
    const lap = makeLapDelta(d);
    const golds = new Map<number, number | null>([[1, 50000], [2, 51000], [3, 50000]]);
    // During the race the live lap 1 (49000) beats the pre-race best -> gold.
    expect(last(lap(1, COURSE, [49000], 2, golds)).gold).toBe(true);
    // The run finishes + uploads; its lap 1 (49000) is now the db best and the
    // model-rebuild hook drops the cache.
    insertRun(d, 5, 1, [49000, 52000, 53000], { isPb: 1 });
    d.exec('UPDATE runs SET is_pb=0 WHERE id=2');
    lap.invalidateCourse(CID);
    // Still pinned to the pre-race baseline: the gold must NOT demote to green.
    expect(last(lap(1, COURSE, [49000], 2, golds)).gold).toBe(true);
  });
});
