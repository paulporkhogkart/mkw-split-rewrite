import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { makePaceDelta } from './pace';
import { saveCourseModel, savePlayerAlignment } from '../db/courseModels';
import { insertTrail } from '../db/trails';
import type { CourseModel } from '../progress/types';

const COURSE = 'Bowsers Castle';
const CID = 1;

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Paul'),(2,'Luke'),(3,'Aiden');
          INSERT INTO courses(id,slug,display_name) VALUES(${CID},'bowsers_castle','Bowsers Castle');`);
  return d;
}

// Unit-square centerline, 2 laps, 40px each (same fixture as completion.test.ts).
const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const lapRoute = (index: number, startOffsetPx: number) => ({ index, lengthPx: 40, startOffsetPx,
  graph: { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline' as const,
    nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
    edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main' as const, passThrough: null }] } });
const MODEL: CourseModel = { version: 2, totalLengthPx: 80, status: 'centerline', laps: [lapRoute(1, 0), lapRoute(2, 40)] };
const seedModel = (d: DatabaseSync) => saveCourseModel(d, CID, 150, MODEL, 1);

/** Position on the square at cumulative distance D px (perimeter 40/lap). */
function walk(D: number): [number, number] {
  const d = ((D % 40) + 40) % 40;
  if (d < 10) return [d, 0];
  if (d < 20) return [10, d - 10];
  if (d < 30) return [10 - (d - 20), 10];
  return [0, 10 - (d - 30)];
}

/** Trail points every 500ms from a distance-over-time profile. */
function mkPoints(totalMs: number, Dof: (t: number) => number,
                  opts: { shift?: [number, number]; lapNull?: boolean } = {}): [number, number, number, number | null][] {
  const [sx, sy] = opts.shift ?? [0, 0];
  const out: [number, number, number, number | null][] = [];
  for (let t = 0; t <= totalMs; t += 500) {
    const D = Dof(t);
    const [x, y] = walk(D);
    out.push([t, x + sx, y + sy, opts.lapNull ? null : Math.min(D < 40 ? 1 : 2, 2)]);
  }
  return out;
}

function insertRun(d: DatabaseSync, id: number, player: number, totalMs: number, lapMs: number[],
                   pts: [number, number, number, number | null][], isPb = 1) {
  d.prepare(`INSERT INTO runs(id,season_id,player_id,course_id,cc,status,total_time_ms,is_pb,provenance)
             VALUES(?,1,?,?,150,'finished',?,?,'live')`).run(id, player, CID, totalMs, isPb);
  const lapStmt = d.prepare('INSERT INTO run_laps(run_id,lap_index,lap_time_ms) VALUES (?,?,?)');
  lapMs.forEach((ms, i) => lapStmt.run(id, i + 1, ms));
  if (pts.length === 0) return;   // old row-loop was a no-op on []; keep that (Luke's PB w/ no trail)
  insertTrail(d, id, pts.map(([t, x, y, lap]) => ({ t_ms: t, cx: x, cy: y, score: 1, lap })));
}

// Linear PB: 1px/s, 80s total, laps 40s+40s -> pb_time_at(c) = 80000*c.
const linearPb = (d: DatabaseSync, player = 1, id = 1) =>
  insertRun(d, id, player, 80000, [40000, 40000], mkPoints(80000, (t) => t / 1000));

describe('makePaceDelta', () => {
  it('self-replay delta is ~0 across the run', () => {
    const d = db(); seedModel(d); linearPb(d);
    const pace = makePaceDelta(d);
    for (const c of [0.1, 0.25, 0.5, 0.75, 0.9])
      expect(Math.abs(pace(1, COURSE, c, 80000 * c)!)).toBeLessThanOrEqual(50);
  });

  it('reports behind/ahead as +/- the time offset', () => {
    const d = db(); seedModel(d); linearPb(d);
    const pace = makePaceDelta(d);
    expect(pace(1, COURSE, 0.5, 43000)).toBeCloseTo(3000, -2);   // 3s behind PB
    expect(pace(1, COURSE, 0.5, 37000)).toBeCloseTo(-3000, -2);  // 3s ahead
  });

  it('converges to the exact final delta at the line', () => {
    const d = db(); seedModel(d); linearPb(d);
    const pace = makePaceDelta(d);
    expect(Math.abs(pace(1, COURSE, 1, 80000)!)).toBeLessThanOrEqual(50);
    expect(pace(1, COURSE, 1, 81160)).toBeCloseTo(1160, -2);
  });

  it('uses the earliest crossing through a hold (stopped PB)', () => {
    const d = db(); seedModel(d);
    // Stops at D=20 (c=0.25) from t=20s to t=30s, then resumes; 90s total.
    const Dof = (t: number) => (t < 20000 ? t / 1000 : t < 30000 ? 20 : 20 + (t - 30000) / 1000);
    insertRun(d, 1, 1, 90000, [50000, 40000], mkPoints(90000, Dof));
    const pace = makePaceDelta(d);
    expect(Math.abs(pace(1, COURSE, 0.25, 20000)!)).toBeLessThanOrEqual(50);
    expect(pace(1, COURSE, 0.25, 30000)).toBeCloseTo(10000, -2);
  });

  it('falls back to run_laps lap derivation when points carry no lap stamp', () => {
    const d = db(); seedModel(d);
    insertRun(d, 1, 1, 80000, [40000, 40000], mkPoints(80000, (t) => t / 1000, { lapNull: true }));
    const pace = makePaceDelta(d);
    expect(Math.abs(pace(1, COURSE, 0.75, 60000)!)).toBeLessThanOrEqual(50);
  });

  it('applies the player alignment to the PB trail (same frame as the live path)', () => {
    const d = db(); seedModel(d);
    insertRun(d, 3, 3, 80000, [40000, 40000], mkPoints(80000, (t) => t / 1000, { shift: [-5, 0] }));
    savePlayerAlignment(d, 3, { dx: 5, dy: 0, scale: 1 }, 1);
    const pace = makePaceDelta(d);
    expect(Math.abs(pace(3, COURSE, 0.0625, 5000)!)).toBeLessThanOrEqual(50);
  });

  it('gates: no PB / PB without trail / no model / unknown course / null inputs', () => {
    const d = db(); seedModel(d); linearPb(d);
    insertRun(d, 4, 2, 70000, [35000, 35000], []);            // Luke's PB has no points
    const pace = makePaceDelta(d);
    expect(pace(2, COURSE, 0.5, 30000)).toBeNull();           // trail-less PB
    expect(pace(1, 'Nope', 0.5, 30000)).toBeNull();           // unknown course
    expect(pace(1, COURSE, null, 30000)).toBeNull();
    expect(pace(1, COURSE, 0.5, null)).toBeNull();
    const noModel = makePaceDelta(db());
    expect(noModel(1, COURSE, 0.5, 30000)).toBeNull();        // course known, no model
    const noPb = makePaceDelta(db());
    expect(noPb(1, COURSE, 0.5, 30000)).toBeNull();           // no runs at all
  });

  it('picks up a new PB on the next call without external invalidation', () => {
    const d = db(); seedModel(d); linearPb(d);
    const pace = makePaceDelta(d);
    expect(Math.abs(pace(1, COURSE, 0.5, 40000)!)).toBeLessThanOrEqual(50);
    // A faster run becomes the PB: 76s total, same course.
    insertRun(d, 2, 1, 76000, [38000, 38000], mkPoints(76000, (t) => (t * 80) / 76000), 0);
    d.exec('UPDATE runs SET is_pb=0 WHERE id=1; UPDATE runs SET is_pb=1 WHERE id=2;');
    expect(pace(1, COURSE, 0.5, 40000)).toBeCloseTo(2000, -2);  // now 2s behind the new PB
  });

  it('invalidateCourse drops the cached model + curves', () => {
    const d = db(); seedModel(d); linearPb(d);
    const pace = makePaceDelta(d);
    expect(pace(1, COURSE, 0.5, 43000)).toBeCloseTo(3000, -2);
    d.exec('DELETE FROM course_models');
    expect(pace(1, COURSE, 0.5, 43000)).toBeCloseTo(3000, -2);  // still cached
    pace.invalidateCourse(CID);
    expect(pace(1, COURSE, 0.5, 43000)).toBeNull();             // model gone -> no curve
  });
});
