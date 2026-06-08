import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { applySchema } from '../db/connect';
import { PresenceHub } from '../presence/hub';
import { presenceHandlers } from './presence';

function hub() {
  const d = new DatabaseSync(':memory:'); applySchema(d);
  d.exec(`INSERT INTO seasons(id,name,is_active) VALUES(1,'S1',1);
          INSERT INTO players(id,display_name,color) VALUES(1,'Paul',NULL);
          INSERT INTO season_rosters(season_id,player_id) VALUES(1,1);`);
  return new PresenceHub(d, () => null, () => 1000);
}

describe('presenceHandlers', () => {
  it('authed socket: snapshot on open, frame -> online; close -> offline (broadcast to others)', () => {
    const h = hub();
    // An observing socket (stays open) witnesses broadcasts - the closing socket is
    // unsubscribed before setOffline, so it must NOT receive its own offline update.
    const obs: any[] = [];
    const observer = presenceHandlers(h, null);
    observer.onOpen(null, { send: (s: string) => obs.push(JSON.parse(s)) });

    const sent: any[] = [];
    const sender = presenceHandlers(h, 1);
    sender.onOpen(null, { send: (s: string) => sent.push(JSON.parse(s)) });
    expect(sent[0].type).toBe('presence_snapshot');
    sender.onMessage({ data: JSON.stringify({ screen: 'RACING' }) });
    expect(obs.at(-1)).toMatchObject({ type: 'presence_update', player: { player_id: 1, online: true, screen: 'RACING' } });
    sender.onClose();
    expect(obs.at(-1)).toMatchObject({ type: 'presence_update', player: { player_id: 1, online: false } });
  });

  it('token-less socket receives but cannot send presence', () => {
    const sent: any[] = [];
    const ws = { send: (s: string) => sent.push(JSON.parse(s)) };
    const h = presenceHandlers(hub(), null);
    h.onOpen(null, ws);
    const before = sent.length;
    h.onMessage({ data: JSON.stringify({ screen: 'RACING' }) });   // ignored
    expect(sent.length).toBe(before);
  });
});
