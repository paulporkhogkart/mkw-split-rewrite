import type { ActivityEvent } from './types';
import type { ScreenClass } from './screenClass';

/** A finalised-or-live session resolved for the wire (player/course names attached). */
export interface SessionWire {
  session_id: number;
  state: 'open' | 'final';
  player: { id: number; name: string; color: string | null } | null;
  course: { slug: string; name: string } | null;
  cls: ScreenClass;
  character: string | null;
  costume: string | null;
  started_ts: number;
  ended_ts: number | null;
  duration_ms: number | null;
  attempts: number | null;
  pbs: number | null;
}

/** Everything the activity stream carries. Milestones are immutable `event`s; sessions arrive
 *  open -> final (in place) or are retracted by `session_drop`; a fresh client gets a snapshot
 *  of the in-flight open sessions on connect. */
export type ActivityStreamMsg =
  | { kind: 'event'; event: ActivityEvent }
  | { kind: 'session'; session: SessionWire }
  | { kind: 'session_drop'; session_id: number }
  | { kind: 'sessions_snapshot'; sessions: SessionWire[] };

type Sink = (msg: ActivityStreamMsg) => void;

export class ActivityHub {
  private sinks = new Set<Sink>();

  subscribe(sink: Sink): () => void {
    this.sinks.add(sink);
    return () => this.sinks.delete(sink);
  }

  publish(msg: ActivityStreamMsg): void {
    for (const s of [...this.sinks]) {
      try { s(msg); } catch { /* sink gone */ }
    }
  }

  get size(): number { return this.sinks.size; }
}
