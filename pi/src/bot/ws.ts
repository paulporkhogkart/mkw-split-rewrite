import type { ServerEvent } from '../db/types';

/** Parse a WS frame into a ServerEvent, or null if it is not one. */
export function parseEvent(data: string): ServerEvent | null {
  try {
    const o = JSON.parse(data);
    return o && typeof o.type === 'string' ? (o as ServerEvent) : null;
  } catch {
    return null;
  }
}

/** Connect to the server's /v1/events stream and call onEvent for each event.
 *  Reconnects with capped exponential backoff. Returns a closer. */
export function startEventStream(
  url: string,
  onEvent: (e: ServerEvent) => void,
  onConnect: () => void = () => {},
  log: (m: string) => void = console.log,
): { close(): void } {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(url);
    // 'open' fires on the initial connect AND every reconnect - the moment to catch up on
    // anything missed while we were down / disconnected.
    ws.addEventListener('open', () => { log(`[bot] ws connected ${url}`); backoff = 1000; onConnect(); });
    ws.addEventListener('message', (ev: MessageEvent) => {
      const e = parseEvent(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (e) onEvent(e);
    });
    ws.addEventListener('close', () => {
      if (closed) return;
      log(`[bot] ws closed; reconnecting in ${backoff}ms`);
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 30000);
    });
    ws.addEventListener('error', (ev: Event) => {
      log(`[bot] ws error: ${(ev as { message?: string }).message ?? 'unknown'}`);
      try { ws?.close(); } catch { /* ignore */ }
    });
  };

  connect();
  return { close() { closed = true; try { ws?.close(); } catch { /* ignore */ } } };
}
