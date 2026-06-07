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

export function buildPbData(db: DatabaseSync, ev: PbEvent): PbEmbedData {
  const seasonId = activeSeasonId(db);
  const courseId = courseIdBySlug(db, slugify(ev.course));
  const track = courseId ? courseDisplayName(db, courseId) : ev.course;
  const newMs = timeToMs(ev.total_time) ?? 0;
  const prevMs = ev.delta_vs_prev_ms != null ? newMs - ev.delta_vs_prev_ms : null;
  const improvement_str = ev.delta_vs_prev_ms != null ? formatTimeDifference(ev.delta_vs_prev_ms) : '';

  const lb = courseId ? courseLeaderboard(db, seasonId, courseId, ev.cc) : [];
  const wr = courseId ? currentWr(db, courseId, ev.cc) : null;
  const others = lb.filter((r) => r.display_name !== ev.player);

  const newTrackPos = ev.rank;
  const oldTrackPos = prevMs == null ? null : others.filter((r) => r.total_time_ms < prevMs).length + 1;

  const overtaken: OvertakenEntry[] = [];
  if (wr && newMs < wr.record_ms) overtaken.push({ name: 'WR', diff_str: formatTimeDifference(wr.record_ms - newMs) });
  if (prevMs != null) for (const r of others)
    if (r.total_time_ms > newMs && r.total_time_ms < prevMs)
      overtaken.push({ name: r.display_name, diff_str: formatTimeDifference(r.total_time_ms - newMs) });

  let still_ahead: StillAhead = null;
  if (newTrackPos != null && newTrackPos > 1) {
    const ahead = lb[newTrackPos - 2];
    if (ahead && ahead.display_name !== ev.player)
      still_ahead = { name: ahead.display_name, diff_str: formatTimeDifference(ahead.total_time_ms - newMs) };
  } else if (wr && newMs > wr.record_ms) {
    still_ahead = { name: 'WR', diff_str: formatTimeDifference(wr.record_ms - newMs) };
  }

  const overall = overallLeaderboard(db, seasonId, ev.cc) as { display_name: string; total_time_ms: number }[];
  const myIdx = overall.findIndex((o) => o.display_name === ev.player);
  const newTotalPos = myIdx >= 0 ? myIdx + 1 : null;
  let oldTotalPos: number | null = null;
  if (myIdx >= 0 && prevMs != null) {
    const myOld = overall[myIdx].total_time_ms - newMs + prevMs;
    oldTotalPos = overall.filter((o) => o.display_name !== ev.player && o.total_time_ms < myOld).length + 1;
  }

  const is_new_track_record = newTrackPos === 1 && (oldTrackPos == null || oldTrackPos > 1 || others.length === 0);
  let reign = null;
  if (is_new_track_record && courseId) {
    const pbRun = db.prepare(
      `SELECT id FROM runs WHERE season_id=? AND player_id=(SELECT id FROM players WHERE display_name=?)
         AND course_id=? AND cc=? AND is_pb=1`
    ).get(seasonId, ev.player, courseId, ev.cc) as { id: number } | undefined;
    reign = trackReign(db, seasonId, courseId, ev.cc, ev.player, pbRun?.id ?? -1);
  }

  return {
    player: ev.player, track, time: ev.total_time, improvement_str, is_new_track_record, reign,
    positions: { track: { old: oldTrackPos, new: newTrackPos }, total: { old: oldTotalPos, new: newTotalPos } },
    overtaken, still_ahead,
  };
}
