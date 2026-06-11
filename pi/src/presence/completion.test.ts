import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { makeLiveCompletion } from './completion';
import { saveCourseModel } from '../db/courseModels';
import type { CourseModel } from '../progress/types';
import { courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name) VALUES(1,'Luke');
          INSERT INTO courses(id,slug,display_name) VALUES(1,'bowsers_castle','Bowsers Castle');`);
  return d;
}

// A unit square centerline: (0,0)->(10,0)->(10,10)->(0,10)->(0,0), perimeter 40px, progress 0..1.
const SQUARE: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const lapRoute = (index: number, startOffsetPx: number) => ({ index, lengthPx: 40, startOffsetPx,
  graph: { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline' as const,
    nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
    edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main' as const, passThrough: null }] } });
// Two laps, total perimeter 80px; completion is cumulative distance.
const MODEL: CourseModel = { version: 2, totalLengthPx: 80, status: 'centerline', laps: [lapRoute(1, 0), lapRoute(2, 40)] };

function seedModel(d: DatabaseSync) {
  const id = courseIdBySlug(d, slugify('Bowsers Castle'))!;
  saveCourseModel(d, id, 150, MODEL, 1);
}

describe('makeLiveCompletion', () => {
  it('returns null completion with no position or an unknown course', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 2, null).completion).toBeNull();
    expect(live('Nope', 1, [0, 0]).completion).toBeNull();
  });

  it('returns null completion when the course has no stored model', () => {
    const d = db();   // no seedModel
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).completion).toBeNull();
  });

  it('exposes the model interior lap boundaries as dividers, from the first frame', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    // total 80, laps start at 0 and 40 -> one interior boundary at 40/80 = 0.5
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).dividers).toEqual([0.5]);
    expect(live('Bowsers Castle', 1, null, 1, 1000, false, 2).dividers).toEqual([0.5]);   // known even with no position
    expect(live('Nope', 1, [0, 0]).dividers).toEqual([]);                                 // unknown course -> none
  });

  it('projects live position onto the course model, monotonic per lap (cumulative distance)', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).completion).toBeCloseTo(10 / 80, 2);    // quarter of lap 1 -> 10/80
    expect(live('Bowsers Castle', 1, [10, 10], 1, 1100, false, 2).completion).toBeCloseTo(20 / 80, 2);   // half of lap 1  -> 20/80
  });

  it('reports has-model truthfully (the calibrating signal)', () => {
    const noModel = makeLiveCompletion(db());        // course exists, no stored model
    expect(noModel('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).model).toBe(false);
    expect(noModel('Nope', 1, [10, 0]).model).toBe(false);   // unknown course
    const d = db(); seedModel(d);
    const withModel = makeLiveCompletion(d);
    expect(withModel('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).model).toBe(true);
    expect(withModel('Bowsers Castle', 1, null, 1, 1000, false, 2).model).toBe(true);  // no pos, model known
  });

  it('invalidate() makes the next call reload the stored model', () => {
    const d = db(); seedModel(d);
    const id = courseIdBySlug(d, slugify('Bowsers Castle'))!;
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2).completion).toBeCloseTo(10 / 80, 2);
    // a rebuilt model with twice the total length lands in the db...
    saveCourseModel(d, id, 150, { ...MODEL, totalLengthPx: 160 }, 2);
    // ...still cached: same value
    expect(live('Bowsers Castle', 1, [10, 0], 2, 1000, false, 2).completion).toBeCloseTo(10 / 80, 2);
    live.invalidate(id);
    // fresh player projects on the new model: 10/160
    expect(live('Bowsers Castle', 1, [10, 0], 3, 1000, false, 2).completion).toBeCloseTo(10 / 160, 2);
  });

  it('holds completion while stale', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 2);
    expect(live('Bowsers Castle', 1, [0, 0], 1, 1100, true, 2).completion).toBeCloseTo(10 / 80, 2);       // held
  });

  it('starts a new lap at its boundary, not snapped to the lap end on the same line', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 10], 1, 1000, false, 2);                       // lap 1
    const a = live('Bowsers Castle', 2, [1, 0], 1, 2000, false, 2).completion!;   // just across the line into lap 2
    const b = live('Bowsers Castle', 2, [10, 0], 1, 2100, false, 2).completion!;  // advancing through lap 2
    expect(a).toBeCloseTo(0.5, 1);     // the lap-2 boundary (40/80), NOT ~0.97 (its end sits on the same line)
    expect(b).toBeGreaterThan(a);      // sweeps forward instead of freezing near the boundary
  });

  it('resets a player on a course change / pos clear', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 10], 1, 1000, false, 2);
    expect(live('Bowsers Castle', 1, null, 1, 1100, false, 2).completion).toBeNull();   // pos clears -> state dropped
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1200, false, 2).completion).toBeCloseTo(10 / 80, 2);   // fresh quarter
  });

  it('holds at 100% past the final lap (post-finish frames report lap > totLap)', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 2, [10, 10], 1, 1000, false, 2);                  // final lap, mid
    live('Bowsers Castle', 2, [0, 0], 1, 1100, false, 2);                    // reach the line, progress ~1
    expect(live('Bowsers Castle', 3, [10, 0], 1, 1200, false, 2).completion).toBe(1);   // lap 3 of 2 -> held at 100%, not wrapped
  });
});
