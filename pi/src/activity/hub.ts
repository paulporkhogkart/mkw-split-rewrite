import type { ActivityEvent } from './types';

type Sink = (e: ActivityEvent) => void;

export class ActivityHub {
  private sinks = new Set<Sink>();

  subscribe(sink: Sink): () => void {
    this.sinks.add(sink);
    return () => this.sinks.delete(sink);
  }

  publish(e: ActivityEvent): void {
    for (const s of [...this.sinks]) {
      try { s(e); } catch {}
    }
  }

  get size(): number { return this.sinks.size; }
}
