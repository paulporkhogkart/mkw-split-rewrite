import { describe, it, expect } from 'vitest';
import { gifFor, nameForId } from './players.config';

describe('players.config', () => {
  it('gifFor returns null for an unknown player (no crash)', () => {
    expect(gifFor('NobodySpecial')).toBeNull();
  });
  it('gifFor returns a configured url for a known player', () => {
    expect(gifFor('paul pork')).toMatch(/^https:\/\/i\.imgur\.com\//);
  });
  it('nameForId maps a known discord id', () => {
    expect(nameForId('1213316126948335636')).toBe('paul pork');
    expect(nameForId('0')).toBeNull();
  });
});
