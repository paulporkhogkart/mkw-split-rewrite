import { describe, it, expect } from 'vitest';
import { loadConfig } from './config';

describe('loadConfig', () => {
  it('reads required + defaulted values', () => {
    const c = loadConfig({ DISCORD_BOT_TOKEN: 't', DISCORD_CHANNEL_ID: 'c' } as any);
    expect(c).toEqual({ token: 't', channelId: 'c', guildId: null, dbPath: 'mkw.db', wsUrl: 'ws://127.0.0.1:8787/v1/events' });
  });
  it('honours overrides', () => {
    const c = loadConfig({ DISCORD_BOT_TOKEN: 't', DISCORD_CHANNEL_ID: 'c', DISCORD_GUILD_ID: 'g', MKW_DB: '/x.db', PORT: '9000' } as any);
    expect(c.guildId).toBe('g');
    expect(c.dbPath).toBe('/x.db');
    expect(c.wsUrl).toBe('ws://127.0.0.1:9000/v1/events');
  });
  it('honours the BOT_WS_URL override', () => {
    const c = loadConfig({ DISCORD_BOT_TOKEN: 't', DISCORD_CHANNEL_ID: 'c', BOT_WS_URL: 'ws://prod.example.com/v1/events' } as any);
    expect(c.wsUrl).toBe('ws://prod.example.com/v1/events');
  });
  it('throws when a required var is missing', () => {
    expect(() => loadConfig({ DISCORD_CHANNEL_ID: 'c' } as any)).toThrow(/DISCORD_BOT_TOKEN/);
    expect(() => loadConfig({ DISCORD_BOT_TOKEN: 't' } as any)).toThrow(/DISCORD_CHANNEL_ID/);
  });
});
