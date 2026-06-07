import type { DatabaseSync } from 'node:sqlite';
import { activeSeasonId, courseIdBySlug } from '../../db/seasons';
import { slugify } from '../../db/slug';
import { mkwrsNameToSlug } from '../../wr/courses';
import { courseLeaderboard, currentWr } from '../../db/reads';
import { overallStandings, wrAggregate, nemesisRows } from '../../db/leaderboards';
import { courseLeaderReign, overallReign } from '../../db/reign';
import {
  msToDisplay, formatTrackLeaderboard, formatTotalLeaderboard, formatNemesisTracks, formatTimeDifference,
} from '../format';
import type { BoardRow, TotalRow, NemesisRow } from '../format';
import { nameForId } from '../players.config';

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

const courseName = (db: DatabaseSync, courseId: number): string =>
  (db.prepare('SELECT display_name FROM courses WHERE id=?').get(courseId) as { display_name: string } | undefined)
    ?.display_name ?? '';

const playerId = (db: DatabaseSync, name: string): number | null =>
  (db.prepare('SELECT id FROM players WHERE display_name=? COLLATE NOCASE').get(name) as { id: number } | undefined)
    ?.id ?? null;

// ---------------------------------------------------------------------------
// buildTrackBoard
// ---------------------------------------------------------------------------

export type TrackBoard =
  | { title: string; body: string; leader: string | null; reign_ms: number | null }
  | { error: string };

/**
 * Pure data assembly for `/leaderboard <track>`.
 * Returns the formatted leaderboard body (using formatTrackLeaderboard) plus
 * the current leader's name and reign duration for the embed footer.
 */
export function buildTrackBoard(db: DatabaseSync, courseInput: string, cc = 150): TrackBoard {
  const season = activeSeasonId(db);
  const courseId = courseIdBySlug(db, slugify(courseInput));
  if (courseId == null) return { error: `Track '${courseInput}' not found.` };

  const lb = courseLeaderboard(db, season, courseId, cc);
  const wrRow = currentWr(db, courseId, cc);

  if (lb.length === 0 && !wrRow) return { error: `No times recorded for ${courseName(db, courseId)}.` };

  const rows: BoardRow[] = lb.map((r, i) => ({
    position: i + 1,
    name: r.display_name,
    time: r.total_time_str ?? msToDisplay(r.total_time_ms),
    time_ms: r.total_time_ms,
  }));

  const body = formatTrackLeaderboard(
    rows,
    wrRow ? { record: wrRow.record_str, record_ms: wrRow.record_ms } : null,
  );

  const reign = courseLeaderReign(db, season, courseId, cc);
  return {
    title: `${courseName(db, courseId)} Leaderboard`,
    body,
    leader: reign?.previous_holder ?? null,
    reign_ms: reign?.reign_ms ?? null,
  };
}

// ---------------------------------------------------------------------------
// buildOverallBoard
// ---------------------------------------------------------------------------

export type OverallBoard = { title: string; body: string; leader: string | null; reign_ms: number | null };

/**
 * Pure data assembly for `/leaderboard` (overall, no track argument).
 * Aggregates all players' summed PB times, computes golf points, and
 * inserts the WR aggregate as the reference row.
 */
export function buildOverallBoard(db: DatabaseSync, cc = 150): OverallBoard {
  const season = activeSeasonId(db);
  const standings = overallStandings(db, season, cc);
  const agg = wrAggregate(db, cc);

  const rows: TotalRow[] = standings.map((s, i) => ({
    position: i + 1,
    name: s.display_name,
    total_display: msToDisplay(s.total_ms),
    total_ms: s.total_ms,
    points: s.points,
  }));

  const body = formatTotalLeaderboard(
    rows,
    agg.count ? msToDisplay(agg.total_ms) : 'N/A',
    agg.total_ms,
  );

  const reign = overallReign(db, season, cc);
  return { title: 'Overall Leaderboard', body, leader: reign.leader, reign_ms: reign.reign_ms };
}

// ---------------------------------------------------------------------------
// buildWrInfo
// ---------------------------------------------------------------------------

export type WrInfo =
  | { title: string; time: string; char: string; kart: string; reign_ms: number | null; video: { url: string; note: string | null } | null }
  | { error: string };

/**
 * Pure data assembly for `/wr <track>`.
 * Resolves by mkwrs alias first, then plain slug.
 * Strips parenthetical variants from the character name (e.g. "Mario (Classic)" → "Mario").
 */
export function buildWrInfo(db: DatabaseSync, courseInput: string, cc = 150): WrInfo {
  const courseId =
    courseIdBySlug(db, mkwrsNameToSlug(courseInput)) ??
    courseIdBySlug(db, slugify(courseInput));
  if (courseId == null) return { error: `Track '${courseInput}' not found.` };

  const wr = currentWr(db, courseId, cc);
  if (!wr) return { error: `No world record found for ${courseName(db, courseId)}.` };

  // Strip parenthetical variant from character name: "Mario (Classic)" → "Mario"
  let character: string = wr.character || 'Unknown';
  if (character.includes('(')) character = character.split('(')[0].trim();

  const video = wrVideo(db, courseId, cc, wr.video_url ?? null);
  const reign_ms = currentWrReignMs(db, courseId, cc, wr.holder_name ?? null);

  return {
    title: `${wr.holder_name}'s ${courseName(db, courseId)}`,
    time: wr.record_str,
    char: character,
    kart: wr.vehicle || 'Unknown',
    reign_ms,
    video,
  };
}

/**
 * Duration in ms that the current WR holder has held the record (contiguous block from history).
 * Ports legacy get_current_reign_duration.
 */
function currentWrReignMs(db: DatabaseSync, courseId: number, cc: number, holder: string | null): number | null {
  if (!holder) return null;
  const rows = db
    .prepare(
      'SELECT holder_name, achieved_at FROM world_records WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC',
    )
    .all(courseId, cc) as { holder_name: string | null; achieved_at: string | null }[];
  let start: string | null = null;
  for (const r of rows) {
    if (r.holder_name === holder) start = r.achieved_at ?? start;
    else break; // end of contiguous block
  }
  if (!start) return null;
  const ms = Date.now() - Date.parse(start);
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

/**
 * Returns the current WR's video URL, or falls back to the most recent prior WR row with a video.
 * Ports legacy _find_wr_video.
 */
function wrVideo(
  db: DatabaseSync,
  courseId: number,
  cc: number,
  currentUrl: string | null,
): { url: string; note: string | null } | null {
  if (currentUrl) return { url: currentUrl, note: null };
  const rows = db
    .prepare(
      'SELECT video_url, is_current FROM world_records WHERE course_id=? AND cc=? ORDER BY achieved_at DESC, id DESC LIMIT 10',
    )
    .all(courseId, cc) as { video_url: string | null; is_current: number }[];
  // Skip the current (index 0) — it has no video (otherwise we'd have returned above).
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].video_url && rows[i].is_current !== 1) {
      const pos = i + 1; // 1-based position in the full list (i=1 → 2nd most recent)
      const ord = pos === 2 ? '2nd' : pos === 3 ? '3rd' : `${pos}th`;
      return { url: rows[i].video_url!, note: `Current WR has no video, showing ${ord} most recent:` };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// buildNemesis
// ---------------------------------------------------------------------------

export type NemesisView =
  | { title: string; rows: NemesisRow[]; targeted: boolean }
  | { error: string };

/**
 * Pure data assembly for `/nemesis [player]`.
 * Resolves the requester's Discord id → display name via players.config, then
 * fetches courses where they are behind (vs a target or vs the course leader).
 */
export function buildNemesis(
  db: DatabaseSync,
  requesterDiscordId: string,
  targetName: string | null,
  cc = 150,
): NemesisView {
  const requester = nameForId(requesterDiscordId);
  if (!requester) return { error: 'You are not registered as a player.' };

  const season = activeSeasonId(db);
  const meId = playerId(db, requester);
  if (meId == null) return { error: 'You are not registered as a player.' };

  const targetId = targetName ? playerId(db, targetName) : null;
  if (targetName && targetId == null) return { error: `No data found for comparison with ${targetName}` };

  const data = nemesisRows(db, season, cc, meId, targetId);
  if (data.length === 0) {
    return { error: `No data found for comparison${targetName ? ' with ' + targetName : ''}` };
  }

  const title = `${requester}'s Nemesis Tracks${targetName ? ' vs ' + targetName : ''}`;
  const rows: NemesisRow[] = data.map((d) => ({
    track_name: d.track_name,
    time_difference_str: formatTimeDifference(d.diff_ms),
    ahead_player: d.ahead_player,
  }));

  return { title, rows, targeted: targetName != null };
}
