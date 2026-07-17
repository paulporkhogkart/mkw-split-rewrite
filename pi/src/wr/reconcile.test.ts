import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import type { ServerEvent } from '../db/types';
import { reconcile } from './reconcile';
import type { ScrapedWr } from './parse';
import { upsertFlag } from './flags';
import { claimJob, failJob } from '../db/wrJobs';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  const hub = new EventHub();
  const events: ServerEvent[] = [];
  hub.subscribe((e) => events.push(e));
  return { db, hub, events };
}

function wr(over: Partial<ScrapedWr> = {}): ScrapedWr {
  return {
    courseName: 'Rainbow Road', recordMs: 100000, recordStr: '1:40.000',
    holder: 'Alice', date: '2026-06-01', character: 'Mario', vehicle: 'B Dasher',
    videoUrl: 'https://youtu.be/aaa', ...over,
  };
}

function curRow(db: any) {
  return db.prepare('SELECT holder_name, record_ms, is_current, provenance, video_url FROM world_records WHERE course_id=1 AND is_current=1').get();
}

describe('reconcile', () => {
  it('establishes the first current with no event (cur was null)', () => {
    const { db, hub, events } = setup();
    const rep = reconcile(db, hub, [wr()], 150);
    expect(rep.inserted).toBe(1);
    expect(events).toEqual([]);                       // silent first establishment
    expect(curRow(db)).toMatchObject({ record_ms: 100000, is_current: 1, provenance: 'scraped' });
  });

  it('inserts a strictly faster WR, moves current, emits a positive wr_update', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr()], 150);                  // baseline
    events.length = 0;
    const rep = reconcile(db, hub, [wr({ recordMs: 99000, recordStr: '1:39.000', holder: 'Bob' })], 150);
    expect(rep.inserted).toBe(1);
    expect(curRow(db)).toMatchObject({ record_ms: 99000, holder_name: 'Bob', is_current: 1 });
    expect(db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1 AND is_current=1').get()).toEqual({ c: 1 });
    expect(events).toEqual([{
      type: 'wr_update', course: 'Rainbow Road', cc: 150, holder: 'Bob', total_time: '1:39.000',
      prev_holder: 'Alice', prev_time: '1:40.000', improvement_ms: 1000,
      character: 'Mario', vehicle: 'B Dasher', video_url: 'https://youtu.be/aaa',
    }]);
  });

  it('reverts to an existing history row (DQ) without inserting a duplicate', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ recordMs: 100000, holder: 'Alice', recordStr: '1:40.000' })], 150);
    reconcile(db, hub, [wr({ recordMs: 99000, holder: 'Bob', recordStr: '1:39.000' })], 150);
    const before = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    events.length = 0;
    // Bob's run is removed; the page reverts to Alice 1:40.000.
    const rep = reconcile(db, hub, [wr({ recordMs: 100000, holder: 'Alice', recordStr: '1:40.000' })], 150);
    expect(rep.reflagged).toBe(1);
    expect(rep.inserted).toBe(0);
    const after = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    expect(after.c).toBe(before.c);                   // no new row
    expect(curRow(db)).toMatchObject({ record_ms: 100000, holder_name: 'Alice', is_current: 1 });
    expect(events[0]).toMatchObject({ type: 'wr_update', improvement_ms: -1000 });   // reverted, slower
  });

  it('backfills a later-added video on the unchanged current, with no event and no new row', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ videoUrl: null })], 150);
    const before = db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number };
    events.length = 0;
    const rep = reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/new' })], 150);
    expect(rep.backfilled).toBe(1);
    expect(rep.inserted).toBe(0);
    expect((db.prepare('SELECT COUNT(*) c FROM world_records WHERE course_id=1').get() as { c: number }).c).toBe(before.c);
    expect(curRow(db)).toMatchObject({ video_url: 'https://youtu.be/new' });
    expect(events).toEqual([]);
  });

  it('does not overwrite a non-null holder during backfill', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ holder: 'Alice' })], 150);
    reconcile(db, hub, [wr({ holder: 'Alice', character: 'Peach' })], 150);   // same record, new character
    expect(curRow(db)).toMatchObject({ holder_name: 'Alice' });
  });

  it('is idempotent: re-running the same scrape writes nothing', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()], 150);
    const rep = reconcile(db, hub, [wr()], 150);
    expect(rep).toMatchObject({ inserted: 0, reflagged: 0, backfilled: 0, unchanged: 1 });
  });

  it('keeps a course current when it is absent from the batch', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()], 150);
    reconcile(db, hub, [], 150);                      // empty scrape
    expect(curRow(db)).toMatchObject({ record_ms: 100000, is_current: 1 });
  });

  it('records unmapped course names without throwing', () => {
    const { db, hub } = setup();
    const rep = reconcile(db, hub, [wr({ courseName: 'Mystery Track' })], 150);
    expect(rep.unmapped).toEqual(['Mystery Track']);
    expect(rep.inserted).toBe(0);
  });

  it('writes loadout slugs on a fresh insert', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)', vehicle: 'Mach Rocket' })]);
    const row = db.prepare(
      'SELECT character, character_slug, costume_slug, kart_slug FROM world_records WHERE is_current=1'
    ).get() as any;
    expect(row.character).toBe('Toadette (Conductor)');   // raw is still stored
    expect(row.character_slug).toBe('toadette');
    expect(row.costume_slug).toBe('conductor');
    expect(row.kart_slug).toBe('mach_rocket');
  });

  it('re-resolves slugs when the raw value changes on the current row', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)', vehicle: 'Mach Rocket' })]);
    // same record + holder -> Case 1 backfill path, with a corrected loadout
    reconcile(db, hub, [wr({ character: 'Bowser (Biker)', vehicle: 'Reel Racer' })]);
    const row = db.prepare(
      'SELECT character, character_slug, costume_slug, kart_slug FROM world_records WHERE is_current=1'
    ).get() as any;
    expect(row.character).toBe('Bowser (Biker)');
    expect(row.character_slug).toBe('bowser');
    expect(row.costume_slug).toBe('biker');
    expect(row.kart_slug).toBe('reel_racer');
  });

  it('clears the costume slug when the raw drops back to a base costume', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ character: 'Toadette (Conductor)' })]);
    reconcile(db, hub, [wr({ character: 'Toadette' })]);
    const row = db.prepare('SELECT costume_slug FROM world_records WHERE is_current=1').get() as any;
    expect(row.costume_slug).toBeNull();
  });

  it('flags + announces an unresolvable kart once, on first sighting only', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    const flags = () => events.filter((e) => e.type === 'wr_name_flag');
    expect(flags()).toHaveLength(1);
    expect(flags()[0]).toMatchObject({
      type: 'wr_name_flag', category: 'kart', raw_value: 'Fake Kart',
      slug_guess: 'fake_kart', course: 'Rainbow Road',
    });
    // second scrape of the same broken name -> counted, not re-announced
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    expect(flags()).toHaveLength(1);
    const row = db.prepare('SELECT occurrences FROM wr_name_flags WHERE raw_value=?').get('Fake Kart') as any;
    expect(row.occurrences).toBe(2);
  });

  it('still alerts when the history scraper flagged the name first (race regression)', () => {
    // history_reconcile.ts calls upsertFlag directly with no EventHub, so it can never alert.
    // If the history scraper wins the race and flags 'Fake Kart' before the current-WR scraper
    // ever sees it, the current scraper must still alert the first time it gets there.
    const { db, hub, events } = setup();
    upsertFlag(db, { category: 'kart', rawValue: 'Fake Kart', slugGuess: 'fake_kart',
      exampleCourseId: 1 });   // simulates history_reconcile's un-alerting upsertFlag call
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    const flags = events.filter((e) => e.type === 'wr_name_flag');
    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({ category: 'kart', raw_value: 'Fake Kart' });
    // and it still only alerts once on repeat.
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Fake Kart' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(1);
  });

  it('does not flag a base costume', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ character: 'Bowser', vehicle: 'Reel Racer' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(0);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_name_flags').get()).toMatchObject({ n: 0 });
  });

  it('flags + announces an unmapped course, once', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ courseName: 'Brand New Track' })]);
    const flags = events.filter((e) => e.type === 'wr_name_flag');
    expect(flags).toHaveLength(1);
    expect(flags[0]).toMatchObject({ category: 'course', raw_value: 'Brand New Track',
                                     slug_guess: 'brand_new_track' });
    reconcile(db, hub, [wr({ courseName: 'Brand New Track' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(1);
  });

  it('does not flag a glitch category (legitimately skipped, not broken)', () => {
    const { db, hub, events } = setup();
    reconcile(db, hub, [wr({ courseName: 'Rainbow Road (glitch)' })]);
    expect(events.filter((e) => e.type === 'wr_name_flag')).toHaveLength(0);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_name_flags').get()).toMatchObject({ n: 0 });
  });

  it('enqueues a wr_job for a newly inserted current WR', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr()]);
    const id = (db.prepare('SELECT id FROM world_records WHERE is_current=1').get() as any).id;
    expect(db.prepare('SELECT wr_id FROM wr_jobs').all()).toEqual([{ wr_id: id }]);
  });

  it('does not enqueue a WR with no video', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ videoUrl: null })]);
    expect(db.prepare('SELECT COUNT(*) n FROM wr_jobs').get()).toMatchObject({ n: 0 });
  });

  it('a changed video link revives a time_mismatch-dead job with fresh attempts', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/wrong' })]);
    const id = (db.prepare('SELECT id FROM world_records WHERE is_current=1').get() as any).id;
    expect(claimJob(db, 'w1')).toMatchObject({ wr_id: id });
    failJob(db, id, 'w1', 'time_mismatch detected=1 expected=2');
    expect(claimJob(db, 'w1')).toBeNull();                       // dead
    // Same record + holder -> Case 1 backfill; only the link changed.
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/corrected' })]);
    expect(db.prepare('SELECT attempts, last_error FROM wr_jobs WHERE wr_id=?').get(id))
      .toMatchObject({ attempts: 0, last_error: null });
    expect(claimJob(db, 'w1')).toMatchObject({ wr_id: id,
      video_url: 'https://youtu.be/corrected', attempt: 1 });    // alive again, fresh
  });

  it('revival mid-lease also evicts the lease so a stale verdict cannot re-kill the job', () => {
    const { db, hub } = setup();
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/wrong' })]);
    const id = (db.prepare('SELECT id FROM world_records WHERE is_current=1').get() as any).id;
    expect(claimJob(db, 'w1')).toMatchObject({ wr_id: id });          // w1 is mid-processing…
    reconcile(db, hub, [wr({ videoUrl: 'https://youtu.be/corrected' })]);  // …when the link is fixed
    // The stale worker's eventual verdict must be an ownership no-op, not a re-kill —
    // otherwise the job dies terminal with the corrected link already stored and NOTHING
    // ever revives it (the link no longer changes on future scrapes).
    expect(failJob(db, id, 'w1', 'time_mismatch detected=1 expected=2')).toBe(false);
    // And the corrected link is immediately claimable with a fresh budget:
    expect(claimJob(db, 'w2')).toMatchObject({
      wr_id: id, video_url: 'https://youtu.be/corrected', attempt: 1 });
  });
});
