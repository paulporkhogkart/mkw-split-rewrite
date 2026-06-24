import { describe, it, expect } from 'vitest';
import { openDb, applySchema } from '../db/connect';
import { EventHub } from '../api/events';
import { ActivityHub } from '../activity/hub';
import type { ActivityEvent } from '../activity/types';
import { reconcile } from './reconcile';
import type { ScrapedWr } from './parse';

function setup() {
  const db = openDb(':memory:');
  applySchema(db);

  // Course
  db.exec("INSERT INTO courses(id,slug,display_name) VALUES (1,'rainbow_road','Rainbow Road')");
  // Active season
  db.exec("INSERT INTO seasons(id,name,is_active) VALUES (1,'S1',1)");
  // Two players
  db.exec("INSERT INTO players(id,display_name) VALUES (1,'Alice'),(2,'Bob')");
  // PBs: Alice 100100 (leader), Bob 100400 — same course, cc 150
  db.exec(`
    INSERT INTO runs(season_id, player_id, course_id, cc, total_time_ms, total_time_str, is_pb, status, provenance)
    VALUES
      (1, 1, 1, 150, 100100, '1:40.100', 1, 'finished', 'live'),
      (1, 2, 1, 150, 100400, '1:40.400', 1, 'finished', 'live')
  `);
  // Current WR row: 100050 — Alice is on fire under this WR
  db.exec(`
    INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, provenance, is_current)
    VALUES (1, 150, 'Pro', 100050, '1:40.050', 'scraped', 1)
  `);

  const hub = new EventHub();
  const activity = new ActivityHub();
  const activityEvents: ActivityEvent[] = [];
  activity.subscribe((m) => { if (m.kind === 'event') activityEvents.push(m.event); });

  return { db, hub, activity, activityEvents };
}

function newWrScrape(over: Partial<ScrapedWr> = {}): ScrapedWr {
  return {
    courseName: 'Rainbow Road', recordMs: 98000, recordStr: '1:38.000',
    holder: 'SpeedRun', date: '2026-06-24', character: 'Mario', vehicle: 'B Dasher',
    videoUrl: 'https://youtu.be/new', ...over,
  };
}

describe('reconcile-activity (WR change)', () => {
  it('emits a wr activity event and a turf_waver for the leader when a faster WR snuffs their fire', () => {
    const { db, hub, activity, activityEvents } = setup();

    // Apply a faster WR: 98000 (delta_ms = 98000 - 100050 = -2050)
    // Under old WR (100050): Alice on fire. Under new WR (98000): Alice snuffed -> turf_waver.
    reconcile(db, hub, [newWrScrape()], 150, activity);

    const wrEvent = activityEvents.find((e) => e.type === 'wr');
    expect(wrEvent).toBeDefined();
    expect(wrEvent!.payload).toMatchObject({
      time_ms: 98000,
      time_str: '1:38.000',
      holder: 'SpeedRun',
      delta_ms: -2050,
    });
    expect(wrEvent!.course).toMatchObject({ slug: 'rainbow_road' });
    expect(wrEvent!.player).toBeNull();

    const waverEvent = activityEvents.find((e) => e.type === 'turf_waver');
    expect(waverEvent).toBeDefined();
    expect(waverEvent!.player).toMatchObject({ name: 'Alice' });
    expect(waverEvent!.course).toMatchObject({ slug: 'rainbow_road' });
  });
});
