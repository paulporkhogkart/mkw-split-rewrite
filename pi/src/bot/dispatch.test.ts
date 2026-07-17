import { describe, it, expect } from 'vitest';
import type { EmbedBuilder } from 'discord.js';
import { openDb, applySchema } from '../db/connect';
import { dispatch } from './dispatch';
import type { ServerEvent } from '../db/types';

function db1() {
  const db = openDb(':memory:'); applySchema(db);
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Paul')");
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rr','Rainbow Road')");
  db.exec("INSERT INTO runs(id,season_id,player_id,course_id,cc,status,provenance,total_time_ms,total_time_str,ended_at,is_pb) VALUES (99,1,1,1,150,'finished','live',106000,'1:46.000','2026-03-01T00:00:00.000Z',1)");
  return db;
}

describe('dispatch', () => {
  it('emits a PB embed for pb_achieved', () => {
    const sent: EmbedBuilder[] = [];
    const ev: ServerEvent = { type: 'pb_achieved', player: 'Paul', course: 'rr', cc: 150, total_time: '1:46.000', delta_vs_prev_ms: -4000, rank: 1 };
    dispatch(db1(), ev, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].toJSON().title).toBe('PAUL PERSONAL BEST');   // rank 1 but no prior runs => no measurable reign
  });
  it('emits a WR embed for wr_update', () => {
    const sent: EmbedBuilder[] = [];
    const ev: ServerEvent = { type: 'wr_update', course: 'Rainbow Road', cc: 150, holder: 'Paul', total_time: '1:39.000', prev_holder: 'Luke', prev_time: '1:40.000', improvement_ms: 1000, character: null, vehicle: null, video_url: null };
    dispatch(db1(), ev, (e) => sent.push(e));
    expect(sent[0].toJSON().title).toBe('WORLD RECORD BY PAUL');
  });
  it('ignores unrelated events', () => {
    const sent: EmbedBuilder[] = [];
    dispatch(db1(), { type: 'run_started', player: 'Paul', course: 'rr', cc: 150 }, (e) => sent.push(e));
    expect(sent).toHaveLength(0);
  });

  it('announces an unmapped mkwrs name', () => {
    const db = openDb(':memory:'); applySchema(db);
    const sent: any[] = [];
    dispatch(db, { type: 'wr_name_flag', category: 'kart', raw_value: 'Tiny Titan',
                   slug_guess: 'tiny_titan', course: 'Rainbow Road' }, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].data.title).toBe('UNMAPPED mkwrs NAME');
  });

  it('announces a dead WR trail job', () => {
    const db = openDb(':memory:'); applySchema(db);
    const sent: any[] = [];
    dispatch(db, { type: 'wr_job_dead', wr_id: 10, course: 'Mario Circuit', holder: 'JaK',
                   record_str: '1:02.934', reason: 'time_mismatch detected=1 expected=2',
                   attempts: 1 }, (e) => sent.push(e));
    expect(sent).toHaveLength(1);
    expect(sent[0].data.title).toBe('WR TRAIL JOB DEAD');
    expect(sent[0].data.footer.text).toContain('mkwrs corrects the link');
  });
});
