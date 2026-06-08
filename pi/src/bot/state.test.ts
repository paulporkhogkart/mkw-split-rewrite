import { describe, it, expect } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openDb, applySchema } from '../db/connect';
import { loadState, saveState } from './state';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','BC')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,ended_at,was_pb) VALUES (42,1,1,1,150,'finished','live',150000,'2026-06-01T00:00:00.000Z',1)");
  return db;
}

describe('bot state watermark', () => {
  it('seeds to the current max run id when no file exists (no history flood)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'botstate-'));
    const path = join(dir, 's.json');
    expect(loadState(path, db1()).lastPbRunId).toBe(42);
    expect(loadState(path, db1()).lastPbRunId).toBe(42);   // persisted
    rmSync(dir, { recursive: true, force: true });
  });

  it('round-trips a saved watermark', () => {
    const dir = mkdtempSync(join(tmpdir(), 'botstate-'));
    const path = join(dir, 's.json');
    saveState(path, { lastPbRunId: 396 });
    expect(loadState(path, db1()).lastPbRunId).toBe(396);
    rmSync(dir, { recursive: true, force: true });
  });
});
