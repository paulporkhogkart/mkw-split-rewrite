import { describe, it, expect } from 'vitest';
import { classify, HELD_SCREENS, GHOST_SCREENS } from './screenClass';

describe('classify', () => {
  it('RACING is racing', () => {
    expect(classify('RACING', false)).toBe('racing');
    expect(classify('RACING', true)).toBe('racing');
  });

  it('the select screens map to their own classes', () => {
    expect(classify('CHARACTER_SELECT', false)).toBe('character_select');
    expect(classify('KART_SELECT', false)).toBe('kart_select');
    expect(classify('COURSE_SELECT', false)).toBe('track_select');
  });

  it('ghost screens map to ghost', () => {
    expect(classify('GHOST', false)).toBe('ghost');
    expect(classify('START_REPLAY', false)).toBe('ghost');
    expect(classify('REPLAY_MENU', false)).toBe('ghost');
  });

  it('held screens continue racing only when a racing session is open', () => {
    for (const s of HELD_SCREENS) {
      expect(classify(s, true)).toBe('racing');   // mid-grind pause/reset/results
      expect(classify(s, false)).toBe('menus');   // reached from a cold menu
    }
  });

  it('everything else (incl. START_TIME_TRIAL and unknowns) is menus', () => {
    expect(classify('MAIN_MENU', false)).toBe('menus');
    expect(classify('START_TIME_TRIAL', false)).toBe('menus');
    expect(classify('START_TIME_TRIAL', true)).toBe('menus');   // not a held screen
    expect(classify('UNKNOWN_SCREEN', false)).toBe('menus');
    expect(classify('', false)).toBe('menus');
    expect(classify(null, false)).toBe('menus');
    expect(classify(undefined, true)).toBe('menus');
  });
});

describe('parity with src/lib/playerCard.js (keep in sync)', () => {
  // Copied verbatim from src/lib/playerCard.js. If the card changes these, this test fails
  // and both sides must be reconciled.
  const CARD_HOLD_SCREENS = ['RACE_MENU', 'HOME', 'RESET', 'GHOST_RESET', 'UNKNOWN_RESET',
    'UNKNOWN_RACE_ACTIVE', 'PHOTO_MODE', 'EXIT_PHOTO_MODE'];
  const CARD_SETUP_KEYS = ['CHARACTER_SELECT', 'KART_SELECT', 'COURSE_SELECT',
    'START_TIME_TRIAL', 'GHOST', 'START_REPLAY', 'REPLAY_MENU'];

  it('every card HOLD screen continues racing on the server', () => {
    for (const s of CARD_HOLD_SCREENS) {
      expect(HELD_SCREENS.has(s)).toBe(true);
      expect(classify(s, true)).toBe('racing');
    }
  });

  it('adds POST_TIME_TRIAL (the card inRaceCtx results state) to the held set', () => {
    expect(HELD_SCREENS.has('POST_TIME_TRIAL')).toBe(true);
  });

  it('the card GHOST setup screens are the server ghost set', () => {
    expect([...GHOST_SCREENS].sort()).toEqual(['GHOST', 'REPLAY_MENU', 'START_REPLAY']);
  });

  it('every card SETUP key classifies to a non-menus class, except the documented START_TIME_TRIAL', () => {
    for (const s of CARD_SETUP_KEYS) {
      const c = classify(s, false);
      if (s === 'START_TIME_TRIAL') expect(c).toBe('menus');
      else expect(c).not.toBe('menus');
    }
  });
});
