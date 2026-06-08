import type { PresenceHub, PresenceFrame } from '../presence/hub';

interface WsLike { send(data: string): void; }

/** WebSocket handlers for /v1/presence. `playerId` is the authenticated sender (null =
 *  receive-only). onOpen subscribes the socket to the hub (snapshot + live deltas); onMessage
 *  applies the sender's frame; onClose unsubscribes + marks the sender offline. Kept separate
 *  from the route so it's unit-testable without a real socket upgrade. */
export function presenceHandlers(presence: PresenceHub, playerId: number | null) {
  let unsub = () => {};
  return {
    onOpen(_evt: unknown, ws: WsLike) { unsub = presence.addSink((msg) => ws.send(JSON.stringify(msg))); },
    onMessage(evt: { data: unknown }) {
      if (playerId == null) return;                       // token-less socket: receive-only
      try {
        const frame = JSON.parse(typeof evt.data === 'string' ? evt.data : String(evt.data)) as PresenceFrame;
        if (frame && typeof frame === 'object') presence.update(playerId, frame);
      } catch { /* ignore malformed frame */ }
    },
    onClose() { unsub(); if (playerId != null) presence.setOffline(playerId); },
  };
}
