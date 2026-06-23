import { describe, it, expect } from 'vitest';
import { DatabaseSync } from 'node:sqlite';
import { reportService, readService } from './serviceStatus';

describe('serviceStatus', () => {
  it('reads null before any write (even with no table), then upserts + reads back', () => {
    const db = new DatabaseSync(':memory:');
    expect(readService(db, 'bot')).toBeNull();                  // table absent -> null, no throw
    reportService(db, 'bot', '2.1.0', 1750000000000);
    expect(readService(db, 'bot')).toEqual({ version: '2.1.0', booted_at: 1750000000000 });
    reportService(db, 'bot', '2.2.0', 1750000001000);          // upsert (PK conflict)
    expect(readService(db, 'bot')).toEqual({ version: '2.2.0', booted_at: 1750000001000 });
    expect(readService(db, 'server')).toBeNull();               // unknown service
  });
});
