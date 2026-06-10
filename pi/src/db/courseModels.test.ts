// pi/src/db/courseModels.test.ts
import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from './connect';
import { saveCourseModel, loadCourseModel, savePlayerAlignment, loadPlayerAlignment } from './courseModels';
import type { CourseModel } from '../progress/types';

function db() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'s',1)");
  d.exec("INSERT INTO players(id,display_name) VALUES (1,'P')");
  d.exec("INSERT INTO courses(id,slug,display_name) VALUES (5,'bc','Bowsers Castle')");
  return d;
}
const SQUARE: [number, number][] = [[0, 0], [10, 0], [0, 0]];
const G: CourseModel = { version: 2, totalLengthPx: 100, status: 'centerline',
  laps: [{ index: 1, lengthPx: 100, startOffsetPx: 0,
    graph: { version: 1, startNode: 0, lapLengthPx: 100, status: 'centerline',
      nodes: [{ id: 0, x: 0, y: 0, progress: 0 }],
      edges: [{ id: 0, a: 0, b: 0, poly: SQUARE, arcLen: 100, pLo: 0, pHi: 1, kind: 'main', passThrough: null }] }}] };

describe('courseModels repo', () => {
  it('round-trips a model and upserts on rebuild', () => {
    const d = db();
    saveCourseModel(d, 5, 150, G, 3);
    expect(loadCourseModel(d, 5, 150)!.totalLengthPx).toBe(100);
    saveCourseModel(d, 5, 150, { ...G, totalLengthPx: 222 }, 4);   // replace
    expect(loadCourseModel(d, 5, 150)!.totalLengthPx).toBe(222);
    expect(loadCourseModel(d, 99, 150)).toBeNull();
  });

  it('round-trips alignment; missing -> identity', () => {
    const d = db();
    expect(loadPlayerAlignment(d, 1)).toEqual({ dx: 0, dy: 0, scale: 1 });   // identity default
    savePlayerAlignment(d, 1, { dx: -6, dy: 2, scale: 1 }, 3);
    expect(loadPlayerAlignment(d, 1)).toEqual({ dx: -6, dy: 2, scale: 1 });
  });
});
