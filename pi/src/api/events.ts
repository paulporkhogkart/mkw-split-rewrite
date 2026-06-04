import type { ServerEvent } from '../db/types';

type Sink = (e: ServerEvent) => void;

export class EventHub {
  private sinks = new Set<Sink>();
  subscribe(sink: Sink): () => void { this.sinks.add(sink); return () => this.sinks.delete(sink); }
  publish(e: ServerEvent): void { for (const s of [...this.sinks]) { try { s(e); } catch {} } }
  get size(): number { return this.sinks.size; }
}
