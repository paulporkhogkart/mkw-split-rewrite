import type { DatabaseSync } from 'node:sqlite';
import { activeSeasonId, courseIdBySlug } from '../db/seasons';
import { slugify } from '../db/slug';
import { pbRunFor } from '../db/pb';
import type { LiveCompletion } from './completion';

// The engine's _TrackState values (mkw_tracker/minimap/tracker.py) that mean a confident live
// fix; anything else it emits (e.g. 'reacquire') is treated as stale so completion holds.
const FRESH_TRACK = new Set(['tracking', 'ring_only']);

/** What an app sends each frame (its identity comes from the token, not the frame). */
export interface PresenceFrame {
  screen?: string | null; course?: string | null;
  character?: string | null; kart?: string | null; costume?: string | null;
  cur_lap?: number | null; tot_lap?: number | null;
  coins?: number | null; mushrooms?: number | null;
  resets?: number | null;
  pos?: [number, number] | null; final_time?: string | null;
  track_state?: string | null;
  elapsed_ms?: number | null;
  splits_ms?: number[] | null;   // completed laps' digit-read durations, contiguous from lap 1
}

/** What the server broadcasts per roster player. */
export interface PresenceEntry {
  player_id: number; name: string; color: string | null; online: boolean;
  screen: string | null; course: string | null; character: string | null; kart: string | null; costume: string | null;
  cur_lap: number | null; tot_lap: number | null; coins: number | null; mushrooms: number | null;
  resets: number | null; pb_ms: number | null;
  completion: number | null; dividers: number[]; final_time: string | null; updated_at: number;
  elapsed_ms: number | null;
  has_model: boolean;   // false -> course has no model yet (bar shows "calibrating")
  pb_delta_ms: number | null;   // live ahead(-)/behind(+) vs own PB at the same completion
  lap_delta: LapD | null;       // latest completed lap (the card badge readout)
  lap_deltas: LapD[] | null;    // one row per completed lap (the race rail)
  pb_laps_ms: number[] | null;  // the PB run's lap durations (rail reference column)
  off_stats: { firsts: number; runs_7d: number; pbs_30d: number } | null;   // offline-card stats
}

interface LapD { lap: number; delta_ms: number; seg_delta_ms: number; gained: boolean; gold: boolean; }

/** Live PB pace delta (see presence/pace.ts); the hub only needs the call shape. */
type PaceFn = (playerId: number, course: string | null | undefined,
               completion: number | null | undefined, elapsedMs: number | null | undefined) => number | null;

/** Per-lap LiveSplit-style delta (see presence/lapDelta.ts); call shape only. */
type LapFn = (playerId: number, course: string | null | undefined,
              splitsMs: number[] | null | undefined) => { pb_laps_ms: number[]; deltas: LapD[] } | null;

type Sink = (msg: unknown) => void;

function offlineEntry(player_id: number, name: string, color: string | null, now: number,
                      off_stats: PresenceEntry['off_stats'] = null): PresenceEntry {
  return { player_id, name, color, online: false, screen: null, course: null, character: null, kart: null,
           costume: null, cur_lap: null, tot_lap: null, coins: null, mushrooms: null, resets: null,
           pb_ms: null, completion: null, dividers: [], final_time: null, updated_at: now, elapsed_ms: null,
           has_model: false, pb_delta_ms: null, lap_delta: null, lap_deltas: null, pb_laps_ms: null, off_stats };
}

/** In-memory live presence, keyed by the active-season roster (seeded offline so every card
 *  renders before anyone connects). Sockets are sinks: each gets a snapshot on connect, then
 *  deltas. The roster doubles as the allowlist - frames from non-roster players are ignored. */
export class PresenceHub {
  private map = new Map<number, PresenceEntry>();
  private sinks = new Set<Sink>();
  // The PB (ms + run id) pinned per player for the duration of a race
  // (RACING/POST_TIME_TRIAL): the finish upload updates the db PB within ~a second,
  // but the card delta and the rail's lap comparison must keep reading against the
  // PRE-RACE PB until the next race starts.
  private pbLatch = new Map<number, { course: string; pb: number | null; runId: number | null }>();

  constructor(private db: DatabaseSync, private completion: LiveCompletion,
              private pace: PaceFn = () => null, private laps: LapFn = () => null,
              private now: () => number = Date.now) {
    this.seedRoster();
  }

  seedRoster(): void {
    const rows = this.db.prepare(
      `SELECT p.id, p.display_name, p.color FROM season_rosters sr JOIN players p ON p.id=sr.player_id WHERE sr.season_id=?`
    ).all(activeSeasonId(this.db)) as { id: number; display_name: string; color: string | null }[];
    for (const r of rows)
      if (!this.map.has(r.id)) this.map.set(r.id, offlineEntry(r.id, r.display_name, r.color, 0, this.offStats(r.id)));
  }

  /** Career snapshot for an offline card: current #1 leaderboard spots, attempts in
   *  the last 7 days, PBs set in the last 30. Computed at seed + on going offline. */
  private offStats(playerId: number): PresenceEntry['off_stats'] {
    try {
      const season = activeSeasonId(this.db);
      const firsts = (this.db.prepare(
        `SELECT COUNT(*) AS n FROM (
           SELECT player_id, RANK() OVER (PARTITION BY course_id ORDER BY total_time_ms ASC, ended_at ASC) AS rk
           FROM runs WHERE season_id=? AND cc=150 AND is_pb=1 AND status='finished'
         ) WHERE rk=1 AND player_id=?`).get(season, playerId) as { n: number }).n;
      const runs_7d = (this.db.prepare(
        `SELECT COUNT(*) AS n FROM runs WHERE season_id=? AND player_id=? AND provenance='live'
           AND datetime(ended_at) >= datetime('now','-7 days')`).get(season, playerId) as { n: number }).n;
      const pbs_30d = (this.db.prepare(
        `SELECT COUNT(*) AS n FROM runs WHERE season_id=? AND player_id=? AND provenance='live' AND was_pb=1
           AND datetime(ended_at) >= datetime('now','-30 days')`).get(season, playerId) as { n: number }).n;
      return { firsts, runs_7d, pbs_30d };
    } catch { return null; }
  }

  snapshot(): { type: 'presence_snapshot'; players: PresenceEntry[] } {
    return { type: 'presence_snapshot', players: [...this.map.values()] };
  }

  addSink(sink: Sink): () => void {
    this.sinks.add(sink);
    sink(this.snapshot());
    return () => this.sinks.delete(sink);
  }

  /** Apply a frame from `playerId` (resolved from their token). Non-roster players are ignored. */
  update(playerId: number, frame: PresenceFrame): void {
    const cur = this.map.get(playerId);
    if (!cur) return;
    const now = this.now();
    const stale = frame.track_state != null && !FRESH_TRACK.has(frame.track_state);
    const { completion, dividers, model } = this.completion(frame.course, frame.cur_lap, frame.pos, playerId, now, stale, frame.tot_lap);
    // Live PB pace delta only while actually racing (the finished card shows the exact one).
    const racing = frame.screen === 'RACING' && !frame.final_time;
    const inRaceCtx = frame.screen === 'RACING' || frame.screen === 'POST_TIME_TRIAL';
    const pb_delta_ms = racing ? this.pace(playerId, frame.course, completion, frame.elapsed_ms) : null;
    const pin = this.latchedPb(playerId, cur, frame);
    // Lap comparison stays up through the finished/results state (the rail reads
    // it there), pinned to the pre-race PB run.
    const li = inRaceCtx ? this.laps(playerId, frame.course, frame.splits_ms, pin.runId) : null;

    const entry: PresenceEntry = {
      player_id: playerId, name: cur.name, color: cur.color, online: true,
      screen: frame.screen ?? null, course: frame.course ?? null,
      character: frame.character ?? null, kart: frame.kart ?? null, costume: frame.costume ?? null,
      cur_lap: frame.cur_lap ?? null, tot_lap: frame.tot_lap ?? null,
      coins: frame.coins ?? null, mushrooms: frame.mushrooms ?? null, resets: frame.resets ?? null,
      elapsed_ms: frame.elapsed_ms ?? null,
      completion, pb_ms: pin.pb,
      dividers, final_time: frame.final_time ?? null, updated_at: now,
      has_model: model, pb_delta_ms,
      lap_delta: li && li.deltas.length ? li.deltas[li.deltas.length - 1] : null,
      lap_deltas: li ? li.deltas : null,
      pb_laps_ms: li ? li.pb_laps_ms : null,
      off_stats: null,
    };
    this.map.set(playerId, entry);
    this.broadcast({ type: 'presence_update', player: entry });
  }

  /** The PB for the entry: pinned at race entry, held through RACING/POST_TIME_TRIAL
   *  (so the finished delta + lap comparison read against the pre-race PB), live
   *  everywhere else. Returns { pb, runId }. */
  private latchedPb(playerId: number, cur: PresenceEntry, frame: PresenceFrame): { pb: number | null; runId: number | null } {
    const inRace = frame.screen === 'RACING' || frame.screen === 'POST_TIME_TRIAL';
    if (!inRace) {
      this.pbLatch.delete(playerId);
      return this.pbForCourse(playerId, frame.course);
    }
    const wasInRace = cur.online && (cur.screen === 'RACING' || cur.screen === 'POST_TIME_TRIAL');
    const course = frame.course ?? '';
    let latch = this.pbLatch.get(playerId);
    if (!latch || !wasInRace || latch.course !== course) {
      latch = { course, ...this.pbForCourse(playerId, frame.course) };
      this.pbLatch.set(playerId, latch);
    }
    return latch;
  }

  setOffline(playerId: number): void {
    this.pbLatch.delete(playerId);
    const e = this.map.get(playerId);
    if (!e || !e.online) return;
    const off = offlineEntry(e.player_id, e.name, e.color, this.now(), this.offStats(playerId));
    this.map.set(playerId, off);
    this.broadcast({ type: 'presence_update', player: off });
  }

  /** Recompute + rebroadcast off_stats for offline entries - a finished upload can
   *  change ANOTHER player's standing (steal a #1) while they are offline. Wired to
   *  the run-upload invalidation hook in server.ts. */
  refreshOffStats(): void {
    for (const e of this.map.values()) {
      if (e.online) continue;
      const next = this.offStats(e.player_id);
      if (JSON.stringify(next) !== JSON.stringify(e.off_stats)) {
        const upd = { ...e, off_stats: next };
        this.map.set(e.player_id, upd);
        this.broadcast({ type: 'presence_update', player: upd });
      }
    }
  }

  /** Flip any online entry with no frame in the last `maxAgeMs` to offline (dead-socket guard,
   *  paired with the app's idle heartbeat). */
  sweep(maxAgeMs: number): void {
    const cutoff = this.now() - maxAgeMs;
    for (const e of this.map.values()) if (e.online && e.updated_at < cutoff) this.setOffline(e.player_id);
  }

  private pbForCourse(playerId: number, course: string | null | undefined): { pb: number | null; runId: number | null } {
    if (!course) return { pb: null, runId: null };
    const courseId = courseIdBySlug(this.db, slugify(course));
    if (courseId == null) return { pb: null, runId: null };
    const run = pbRunFor(this.db, activeSeasonId(this.db), playerId, courseId, 150);
    return run ? { pb: run.total_time_ms, runId: run.id } : { pb: null, runId: null };
  }

  private broadcast(msg: unknown): void { for (const s of [...this.sinks]) { try { s(msg); } catch { /* sink gone */ } } }
}
