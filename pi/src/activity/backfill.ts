// pi/src/activity/backfill.ts
// One-time idempotent history replay: replays all finished non-carryover runs
// in deterministic ended_at order, maintaining a per-(season,course,cc) leaderboard
// and inserting pb/rank/turf_claim events for each new PB. Invoked once from
// applySchema (guarded by empty-table check).
import type { DatabaseSync } from 'node:sqlite';
import type { LeaderRow } from '../db/reads';
import { buildRunCascade } from './cascade';
import { insertActivityEvents } from '../db/activity';

type RunRow = {
  id: number;
  season_id: number;
  player_id: number;
  course_id: number;
  cc: number;
  total_time_ms: number;
  total_time_str: string | null;
  ended_at: string;
  character: string | null;
  kart: string | null;
  costume: string | null;
};

/** Rebuild 1-based ranks on a sorted (ascending) leaderboard array in-place. */
function rerank(board: LeaderRow[]): void {
  for (let i = 0; i < board.length; i++) board[i].rank = i + 1;
}

export function backfillActivity(db: DatabaseSync): number {
  // Idempotency guard: if any events already exist, skip entirely.
  if (((db.prepare('SELECT COUNT(*) c FROM activity_events').get() as any).c as number) > 0) return 0;

  // player_id → display_name lookup
  const nameMap: Record<number, string> = {};
  for (const p of db.prepare('SELECT id, display_name FROM players').all() as { id: number; display_name: string }[])
    nameMap[p.id] = p.display_name;

  // All finished, non-carryover runs with a time, in deterministic order
  const runs = db.prepare(
    `SELECT id, season_id, player_id, course_id, cc, total_time_ms, total_time_str, ended_at,
            character, kart, costume
     FROM runs
     WHERE status='finished' AND provenance != 'carryover'
       AND total_time_ms IS NOT NULL AND ended_at IS NOT NULL
     ORDER BY ended_at ASC, id ASC`
  ).all() as RunRow[];

  // Per (season_id, course_id, cc) leaderboard: array sorted by total_time_ms ASC, 1-based rank.
  // Keyed as `${season_id}:${course_id}:${cc}`.
  const boards = new Map<string, LeaderRow[]>();

  let totalInserted = 0;

  for (const run of runs) {
    const key = `${run.season_id}:${run.course_id}:${run.cc}`;
    if (!boards.has(key)) boards.set(key, []);
    const board = boards.get(key)!;

    // Find the player's current best on this board
    const existingIdx = board.findIndex(r => r.player_id === run.player_id);
    const existing = existingIdx >= 0 ? board[existingIdx] : null;

    // Skip if this run is NOT a new PB for this player
    if (existing != null && run.total_time_ms >= existing.total_time_ms) continue;

    // Snapshot the board BEFORE the update (clone for cascade's `before`)
    const before: LeaderRow[] = board.map(r => ({ ...r }));

    // Update the board: replace or insert the player's entry
    const newEntry: LeaderRow = {
      player_id: run.player_id,
      display_name: nameMap[run.player_id] ?? String(run.player_id),
      total_time_ms: run.total_time_ms,
      total_time_str: run.total_time_str,
      rank: 0, // will be set by rerank()
    };

    if (existingIdx >= 0) {
      board.splice(existingIdx, 1);
    }
    // Insert in sorted position (ascending total_time_ms)
    const insertAt = board.findIndex(r => r.total_time_ms > run.total_time_ms);
    if (insertAt === -1) board.push(newEntry);
    else board.splice(insertAt, 0, newEntry);
    rerank(board);

    const after: LeaderRow[] = board.map(r => ({ ...r }));

    const prevPbMs = existing?.total_time_ms ?? null;
    const ts = Date.parse(run.ended_at);
    if (!Number.isFinite(ts)) continue; // unparseable timestamp — skip

    const inputs = buildRunCascade({
      ts,
      seasonId: run.season_id,
      cc: run.cc,
      courseId: run.course_id,
      moverId: run.player_id,
      moverName: nameMap[run.player_id] ?? String(run.player_id),
      before,
      after,
      beforeWr: null,
      afterWr: null,
      prevPbMs,
      character: run.character, kart: run.kart, costume: run.costume,
    });

    if (inputs.length > 0) {
      const ids = insertActivityEvents(db, inputs);
      totalInserted += ids.length;
    }
  }

  return totalInserted;
}
