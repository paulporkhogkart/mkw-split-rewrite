import type { DatabaseSync } from 'node:sqlite';
import type { ServerEvent } from '../db/types';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { timeToMs } from '../db/ingest';
import { courseLeaderboard, overallLeaderboard, currentWr } from '../db/reads';
import { mkwrsNameToSlug } from '../wr/courses';
import { wrReign, trackReign } from '../db/reign';
import { formatTimeDifference } from './format';
import type { PbEmbedData, WrEmbedData, OvertakenEntry, StillAhead } from './types';

type PbEvent = Extract<ServerEvent, { type: 'pb_achieved' }>;
type WrEvent = Extract<ServerEvent, { type: 'wr_update' }>;

function courseDisplayName(db: DatabaseSync, courseId: number): string {
  const row = db.prepare('SELECT display_name FROM courses WHERE id=?').get(courseId) as { display_name: string } | undefined;
  return row?.display_name ?? '';
}

export function buildWrData(db: DatabaseSync, ev: WrEvent): WrEmbedData {
  const courseId = courseIdBySlug(db, mkwrsNameToSlug(ev.course));
  const track = courseId ? courseDisplayName(db, courseId) : ev.course;
  // improvement_ms = prev - new (positive = faster); show as new - prev to match the PB delta.
  const improvement_str = ev.improvement_ms != null ? formatTimeDifference(-ev.improvement_ms) : null;
  const reign = courseId ? wrReign(db, courseId, ev.cc, ev.prev_holder, ev.holder) : null;
  return { holder: ev.holder ?? 'Unknown', track, record: ev.total_time, improvement_str, reign };
}
