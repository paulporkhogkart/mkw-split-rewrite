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

describe('makeLapDelta', () => {
  it('signs the cumulative delta and flags the segment gain per LiveSplit rules', () => {
    const lap = makeLapDelta(seeded());
    // lap1 52.0 vs PB 51.0: lost the segment, behind overall
    expect(lap(1, COURSE, [52000])).toEqual({ lap: 1, delta_ms: 1000, gained: false, gold: false });
    // lap2 51.5 vs PB 52.0: gained the segment, still behind (+0.5 total)
    expect(lap(1, COURSE, [52000, 51500])).toEqual({ lap: 2, delta_ms: 500, gained: true, gold: false });
    // lap2 53.0 vs PB 52.0: lost the segment, still ahead (started -1.5)
    expect(lap(1, COURSE, [49500, 53000])).toEqual({ lap: 2, delta_ms: -500, gained: false, gold: false });
    // lap3 52.5 vs PB 53.0: gained and ahead
    expect(lap(1, COURSE, [51000, 52000, 52500])).toEqual({ lap: 3, delta_ms: -500, gained: true, gold: false });
  });

  it('flags a gold when the lap beats the best-ever finished segment', () => {
    const lap = makeLapDelta(seeded());
    // 49.0 beats the 50.0 gold (and the 51.0 PB lap)
    expect(lap(1, COURSE, [49000])).toEqual({ lap: 1, delta_ms: -2000, gained: true, gold: true });
    // 50.5 beats the PB lap but not the 50.0 gold
    expect(lap(1, COURSE, [50500])!.gold).toBe(false);
    // reset runs never hold golds
    const d = seeded();
    insertRun(d, 3, 1, [40000], { status: 'reset' });
    expect(makeLapDelta(d)(1, COURSE, [49000])!.gold).toBe(true);   // 40s reset lap ignored
  });

  it('gates: no PB / PB without laps / no splits / unknown course', () => {
    const d = db();
    const lap = makeLapDelta(d);
    expect(lap(1, COURSE, [50000])).toBeNull();                     // no runs at all
    // a carryover PB has no run_laps -> nothing to compare against
    d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
               VALUES(9,1,2,${CID},150,'finished',150000,1,'carryover')`).run();
    expect(lap(2, COURSE, [50000])).toBeNull();
    expect(makeLapDelta(seeded())(1, COURSE, [])).toBeNull();
    expect(makeLapDelta(seeded())(1, COURSE, null)).toBeNull();
    expect(makeLapDelta(seeded())(1, 'Nope', [50000])).toBeNull();
  });

  it('clamps to the PB lap count and re-keys when the PB changes', () => {
    const d = seeded();
    const lap = makeLapDelta(d);
    // 4 live laps vs a 3-lap PB: compared over the first 3
    expect(lap(1, COURSE, [51000, 52000, 53000, 99000])!.lap).toBe(3);
    // a new PB run (laps 50/51/52) takes over without invalidation
    insertRun(d, 4, 1, [50000, 51000, 52000]);
    d.exec('UPDATE runs SET is_pb=0 WHERE id=2; UPDATE runs SET is_pb=1 WHERE id=4;');
    expect(lap(1, COURSE, [51000])).toEqual({ lap: 1, delta_ms: 1000, gained: false, gold: false });
  });

  it('invalidateCourse drops the cached comparison', () => {
    const d = seeded();
    const lap = makeLapDelta(d);
    expect(lap(1, COURSE, [52000])!.delta_ms).toBe(1000);
    d.exec('DELETE FROM run_laps WHERE run_id=2');        // PB loses its laps
    expect(lap(1, COURSE, [52000])!.delta_ms).toBe(1000); // still cached
    lap.invalidateCourse(CID);
    expect(lap(1, COURSE, [52000])).toBeNull();           // fresh load -> no comparison
  });
});
