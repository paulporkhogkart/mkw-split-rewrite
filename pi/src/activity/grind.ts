interface Seg { courseId: number; count: number; startedTs: number; lastTs: number }

export interface ClosedSeg { count: number; durationMs: number; courseId: number }

export class GrindTracker {
  private open = new Map<number, Seg>();

  /** Record a run attempt; returns a closed segment if the course changed, otherwise null. */
  note(playerId: number, courseId: number, ts: number): ClosedSeg | null {
    const cur = this.open.get(playerId);
    if (cur && cur.courseId === courseId) {
      cur.count++;
      cur.lastTs = ts;
      return null;
    }
    const closed: ClosedSeg | null = cur
      ? { count: cur.count, durationMs: cur.lastTs - cur.startedTs, courseId: cur.courseId }
      : null;
    this.open.set(playerId, { courseId, count: 1, startedTs: ts, lastTs: ts });
    return closed && closed.count > 0 ? closed : null;
  }

  /** Close + return the open segment (e.g. on a PB), resetting it for this player. */
  close(playerId: number, ts: number): ClosedSeg | null {
    const cur = this.open.get(playerId);
    this.open.delete(playerId);
    if (!cur) return null;
    const durationMs = (ts > cur.lastTs ? ts : cur.lastTs) - cur.startedTs;
    return cur.count > 0 ? { count: cur.count, durationMs, courseId: cur.courseId } : null;
  }
}
