import { describe, it, expect } from 'vitest';
import type { EmbedBuilder } from 'discord.js';
import { openDb, applySchema } from '../db/connect';
import { announceMissedPbs } from './catchup';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'bc','Bowsers Castle')");
  const ins = (id: number, ms: number, str: string) => db.prepare(
    "INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,was_pb,is_pb) VALUES (?,1,1,1,150,'finished','live',?,?,?,1,0)"
  ).run(id, ms, str, `2026-06-${String(id).padStart(2, "0")}T00:00:00.000Z`);
  ins(10, 153101, '2:33.101');     // PB-setting run
  ins(11, 152837, '2:32.837');     // a faster PB
  db.prepare('UPDATE runs SET is_pb=1 WHERE id=11').run();
  return db;
}

describe('announceMissedPbs', () => {
  it('announces was_pb runs newer than the watermark and advances/persists it', () => {
    const db = db1();
    const sent: EmbedBuilder[] = [];
    const saved: number[] = [];
    const state = { lastPbRunId: 9 };
    const n = announceMissedPbs(db, { send: (e) => sent.push(e), state, persist: (s) => saved.push(s.lastPbRunId) });
    expect(n).toBe(2);
    expect(sent).toHaveLength(2);
    expect(state.lastPbRunId).toBe(11);
    expect(saved).toEqual([10, 11]);             // persisted after each, monotonic

    // A second pass (e.g. a redundant live nudge / reconnect) announces nothing.
    const again: EmbedBuilder[] = [];
    expect(announceMissedPbs(db, { send: (e) => again.push(e), state, persist: () => {} })).toBe(0);
    expect(again).toHaveLength(0);
  });

  it('only announces strictly past the watermark', () => {
    const db = db1();
    const sent: EmbedBuilder[] = [];
    const state = { lastPbRunId: 10 };           // run 10 already announced
    announceMissedPbs(db, { send: (e) => sent.push(e), state, persist: () => {} });
    expect(sent).toHaveLength(1);                // only run 11
    expect(state.lastPbRunId).toBe(11);
  });

  it('self-heals a watermark from before a db wipe (watermark > max run id)', () => {
    const db = db1();                            // max run id = 11
    const sent: EmbedBuilder[] = [];
    const saved: number[] = [];
    const state = { lastPbRunId: 397 };          // pre-wipe watermark; ids restarted at 1
    const n = announceMissedPbs(db, { send: (e) => sent.push(e), state, persist: (s) => saved.push(s.lastPbRunId) });
    expect(n).toBe(2);                           // both post-wipe PBs announced
    expect(state.lastPbRunId).toBe(11);
    expect(saved[0]).toBe(0);                    // reset persisted before announcing
  });

  it('does not reset the watermark when it merely equals the max run id', () => {
    const db = db1();
    const sent: EmbedBuilder[] = [];
    announceMissedPbs(db, { send: (e) => sent.push(e), state: { lastPbRunId: 11 }, persist: () => {} });
    expect(sent).toHaveLength(0);
  });

  it('never announces imported/carryover rows, but uses them as the delta reference', () => {
    const db = db1();
    // A restored carryover PB: inserted AFTER the live runs (higher id) but achieved
    // long before them (older ended_at). It must not be announced as a new PB, yet
    // the first live improvement's delta must be measured against it.
    db.prepare(
      `INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,was_pb,is_pb)
       VALUES (99,1,1,1,150,'finished','carryover',155000,'2:35.000','2026-06-01T00:00:00.000Z',1,0)`
    ).run();
    const sent: EmbedBuilder[] = [];
    const state = { lastPbRunId: 0 };
    const n = announceMissedPbs(db, { send: (e) => sent.push(e), state, persist: () => {} });
    expect(n).toBe(2);                                 // only the live PBs
    expect(state.lastPbRunId).toBe(11);
    const delta = (e: EmbedBuilder) => e.toJSON().fields!.find((f) => f.name === 'DELTA')!.value;
    expect(delta(sent[0])).toBe('`-1.899s`');          // run 10 vs the 2:35.000 carryover
    expect(delta(sent[1])).toBe('`-0.264s`');          // run 11 vs run 10
  });
});
