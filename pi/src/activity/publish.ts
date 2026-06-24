import type { DatabaseSync } from 'node:sqlite';
import type { ActivityInput, ActivityEvent } from './types';
import { insertActivityEvents, resolveActivity } from '../db/activity';
import type { ActivityHub } from './hub';

export function commitActivity(db: DatabaseSync, hub: ActivityHub, inputs: ActivityInput[]): ActivityEvent[] {
  if (!inputs.length) return [];
  const ids = insertActivityEvents(db, inputs);
  const events = ids.map(id => resolveActivity(db, db.prepare('SELECT * FROM activity_events WHERE id=?').get(id) as any));
  for (const e of events) hub.publish({ kind: 'event', event: e }); // ascending id; client prepends so newest ends on top
  return events;
}
