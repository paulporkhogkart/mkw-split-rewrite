// Presence-driven activity sessions. Fed by the presence hub (onFrame/onOffline) and the runs
// route (noteRun/notePb), it keeps one open session per player, classifies the live screen like
// the player card, debounces flicker, and emits open/final/drop. Finalised sessions are
// persisted + broadcast by the caller's emitFinal (see server.ts wiring).
import { classify, type ScreenClass } from './screenClass';

// A new screen-class must persist this long before it commits as a transition. Shorter blips are
// absorbed into the session that was already open (so a pause flash mid-grind, or a momentary
// menu between two real states, never fragments the feed).
export const STABLE_MS = 1200;

/** The slice of a presence frame the tracker needs (course already resolved to an id). */
export interface FrameInput {
  screen: string | null;
  courseId: number | null;
  character: string | null;
  costume: string | null;
}

export interface SessionView {
  session_id: number;
  state: 'open' | 'final';
  player_id: number;
  cls: ScreenClass;
  course_id: number | null;
  character: string | null;   // racing only
  costume: string | null;     // racing only
  started_ts: number;
  ended_ts: number | null;
  attempts: number | null;    // racing only
  pbs: number | null;         // racing only
}

export interface TrackerDeps {
  now: () => number;
  emitOpen: (s: SessionView) => void;
  emitFinal: (s: SessionView) => void;   // caller persists + broadcasts
  emitDrop: (sessionId: number) => void;
}

interface Session {
  sessionId: number;
  playerId: number;
  cls: ScreenClass;
  courseId: number | null;
  character: string | null;
  costume: string | null;
  startedTs: number;
  attempts: number;
  pbs: number;
}

interface PlayerState {
  session: Session | null;
  pendingKey: string | null;
  pendingSince: number;
}

const keyOf = (cls: ScreenClass, courseId: number | null): string =>
  cls === 'racing' ? `racing:${courseId}` : cls;

export class SessionTracker {
  private players = new Map<number, PlayerState>();
  private nextId = 1;

  constructor(private deps: TrackerDeps) {}

  private st(playerId: number): PlayerState {
    let s = this.players.get(playerId);
    if (!s) { s = { session: null, pendingKey: null, pendingSince: 0 }; this.players.set(playerId, s); }
    return s;
  }

  private view(s: Session, state: 'open' | 'final', endedTs: number | null): SessionView {
    const racing = s.cls === 'racing';
    return {
      session_id: s.sessionId, state, player_id: s.playerId, cls: s.cls,
      course_id: s.courseId,
      character: racing ? s.character : null,
      costume: racing ? s.costume : null,
      started_ts: s.startedTs, ended_ts: endedTs,
      attempts: racing ? s.attempts : null,
      pbs: racing ? s.pbs : null,
    };
  }

  private openSession(st: PlayerState, playerId: number, cls: ScreenClass, courseId: number | null,
                      character: string | null, costume: string | null, startedTs: number,
                      attempts = 0): void {
    const s: Session = {
      sessionId: this.nextId++, playerId, cls, courseId,
      character: cls === 'racing' ? character : null,
      costume: cls === 'racing' ? costume : null,
      startedTs, attempts, pbs: 0,
    };
    st.session = s;
    st.pendingKey = null;
    this.deps.emitOpen(this.view(s, 'open', null));
  }

  private finalize(playerId: number, endedTs: number): void {
    const st = this.st(playerId);
    const s = st.session;
    if (!s) return;
    st.session = null;
    // An aborted race entry (entered RACING but no attempt ever completed) has nothing to
    // report - retract its open row instead of finalising "raced 0 times".
    if (s.cls === 'racing' && s.attempts === 0) { this.deps.emitDrop(s.sessionId); return; }
    this.deps.emitFinal(this.view(s, 'final', endedTs));
  }

  /** A presence frame. Drives the session start/continue/finalise state machine. */
  onFrame(playerId: number, frame: FrameInput): void {
    const now = this.deps.now();
    const st = this.st(playerId);
    const racingOpen = st.session?.cls === 'racing';
    const cls = classify(frame.screen, racingOpen);
    // A RACING frame uses its own course (falling back to the open session's if the read
    // blipped null); a held screen continuing racing inherits the open session's course.
    const targetCourseId = cls === 'racing'
      ? (frame.screen === 'RACING'
          ? (frame.courseId ?? st.session?.courseId ?? null)
          : (st.session?.courseId ?? frame.courseId ?? null))
      : null;

    // Continue the open session? (For racing, an unknown course on either side still continues -
    // only two different known courses are a real change.)
    if (st.session) {
      const cur = st.session;
      const continues = cur.cls === 'racing'
        ? (cls === 'racing' && (cur.courseId == null || targetCourseId == null || cur.courseId === targetCourseId))
        : cur.cls === cls;
      if (continues) {
        st.pendingKey = null;
        if (cur.cls === 'racing') {   // fill course/character/costume read after the race opened
          let changed = false;
          if (cur.courseId == null && targetCourseId != null) { cur.courseId = targetCourseId; changed = true; }
          if (cur.character == null && frame.character) { cur.character = frame.character; changed = true; }
          if (cur.costume == null && frame.costume) { cur.costume = frame.costume; changed = true; }
          if (changed) this.deps.emitOpen(this.view(cur, 'open', null));
        }
        return;
      }
    }

    // Debounced transition: a differing key must hold for STABLE_MS before it commits.
    const key = keyOf(cls, targetCourseId);
    if (st.pendingKey !== key) { st.pendingKey = key; st.pendingSince = now; return; }
    if (now - st.pendingSince < STABLE_MS) return;
    const t = st.pendingSince;   // the prior session ended, and the new one began, when the key first appeared
    this.finalize(playerId, t);
    this.openSession(st, playerId, cls, targetCourseId, frame.character, frame.costume, t);
  }

  /** A completed attempt (run POST) for `courseId`. Increments the open racing session, switches
   *  course within racing, or opens one - but never from a non-racing state unless presence
   *  corroborates, so a stray/late/ghost POST can't manufacture a phantom ~0s racing row. */
  noteRun(playerId: number, courseId: number): void {
    const st = this.st(playerId);
    const cur = st.session;
    // Already racing: the player is demonstrably on a track, so a run is legit even if the course
    // differs (a course change presence is still catching up on). Increment a match; finalise +
    // reopen on a real course change.
    if (cur && cur.cls === 'racing') {
      if (cur.courseId == null || cur.courseId === courseId) {
        if (cur.courseId == null) cur.courseId = courseId;
        cur.attempts++;
        this.deps.emitOpen(this.view(cur, 'open', null));
        return;
      }
      const now = this.deps.now();
      this.finalize(playerId, now);
      this.openSession(st, playerId, 'racing', courseId, null, null, now, 1);
      return;
    }
    // Non-racing or no session: open ONLY when presence doesn't contradict racing this course -
    // either no presence state yet (a presence-less feed), or presence is mid-debounce toward
    // racing this very course. A run landing while presence shows a committed non-racing session
    // (or a pending non-racing key) is ignored - otherwise it manufactures a phantom ~0s racing
    // row that the next menu frame finalises, re-introducing the "1 run · 0:00" defect.
    const noPresenceState = !cur && st.pendingKey == null;
    const corroborated = st.pendingKey === `racing:${courseId}`;
    if (noPresenceState || corroborated) {
      const now = this.deps.now();
      this.finalize(playerId, now);   // no-op when cur is null
      this.openSession(st, playerId, 'racing', courseId, null, null, now, 1);
    }
  }

  /** That attempt was a PB - records the outcome for the racing session's finalised row. */
  notePb(playerId: number, courseId: number): void {
    const cur = this.st(playerId).session;
    if (cur && cur.cls === 'racing' && (cur.courseId == null || cur.courseId === courseId)) cur.pbs++;
  }

  /** The player went offline - finalise their open session. */
  onOffline(playerId: number): void {
    this.finalize(playerId, this.deps.now());
    this.players.delete(playerId);
  }

  /** Every player's current open session (for the activity-stream connect snapshot). */
  openSessions(): SessionView[] {
    const out: SessionView[] = [];
    for (const st of this.players.values()) if (st.session) out.push(this.view(st.session, 'open', null));
    return out;
  }
}
