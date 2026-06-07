import { describe, it, expect } from 'vitest';
import { parseEvent } from './ws';

describe('parseEvent', () => {
  it('parses a valid ServerEvent', () => {
    expect(parseEvent('{"type":"pb_achieved","player":"Paul"}')?.type).toBe('pb_achieved');
  });
  it('returns null for non-JSON or shapeless payloads', () => {
    expect(parseEvent('not json')).toBeNull();
    expect(parseEvent('{"no":"type"}')).toBeNull();
  });
});
