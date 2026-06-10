import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { makeLiveCompletion } from './completion';
import { saveCourseModel } from '../db/courseModels';
import type { CourseGraph } from '../progress/types';
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
const MODEL: CourseGraph = { version: 1, startNode: 0, lapLengthPx: 40, status: 'centerline',
  nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
  edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 40, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] };

function seedModel(d: DatabaseSync) {
  const id = courseIdBySlug(d, slugify('Bowsers Castle'))!;
  saveCourseModel(d, id, 150, MODEL, 1);
}

describe('makeLiveCompletion', () => {
  it('returns null with no position or an unknown course', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 2, null)).toBeNull();
    expect(live('Nope', 1, [0, 0])).toBeNull();
  });

  it('returns null when the course has no stored model', () => {
    const d = db();   // no seedModel
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 3)).toBeNull();
  });

  it('projects live position onto the course model, monotonic per lap', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 3)).toBeCloseTo(0.25 / 3, 2);   // quarter -> 0.25/3
    expect(live('Bowsers Castle', 1, [10, 10], 1, 1100, false, 3)).toBeCloseTo(0.5 / 3, 2);    // half  -> 0.5/3
  });

  it('holds completion while stale', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 0], 1, 1000, false, 3);
    expect(live('Bowsers Castle', 1, [0, 0], 1, 1100, true, 3)).toBeCloseTo(0.25 / 3, 2);       // held
  });

  it('wraps within-lap progress at the seam (advances into lap 2, no stick)', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 10], 1, 1000, false, 3);                                      // lap 1, progress 0.5
    const c2 = live('Bowsers Castle', 2, [10, 0], 1, 2000, false, 3);                            // lap 2, quarter
    expect(c2).toBeCloseTo((1 + 0.25) / 3, 2);   // (2-1+0.25)/3 ≈ 0.417 — reset wrapped; NOT 0.5 (stuck) or 0.667
  });

  it('resets a player on a course change', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 1, [10, 10], 1, 1000, false, 3);
    expect(live('Bowsers Castle', 1, null, 1, 1100, false, 3)).toBeNull();   // pos clears -> state dropped
    expect(live('Bowsers Castle', 1, [10, 0], 1, 1200, false, 3)).toBeCloseTo(0.25 / 3, 2);   // fresh quarter
  });

  it('holds at 100% past the final lap (post-finish frames report lap > totLap)', () => {
    const d = db(); seedModel(d);
    const live = makeLiveCompletion(d);
    live('Bowsers Castle', 3, [10, 10], 1, 1000, false, 3);                  // final lap, mid
    live('Bowsers Castle', 3, [0, 0], 1, 1100, false, 3);                    // reach the line, progress ~1
    expect(live('Bowsers Castle', 4, [10, 0], 1, 1200, false, 3)).toBe(1);   // lap 4 of 3 -> held at 100%, not wrapped
  });
});
