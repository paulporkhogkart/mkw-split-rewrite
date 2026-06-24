import { describe, it, expect } from 'vitest';
import { classify, HELD_SCREENS, GHOST_SCREENS } from './screenClass';

describe('classify', () => {
  it('RACING is racing', () => {
    expect(classify('RACING', null)).toBe('racing');
    expect(classify('RACING', 'racing')).toBe('racing');
  });

  it('the select screens map to their own classes', () => {
    expect(classify('CHARACTER_SELECT', null)).toBe('character_select');
    expect(classify('KART_SELECT', null)).toBe('kart_select');
    expect(classify('COURSE_SELECT', null)).toBe('track_select');
  });

  it('ghost screens map to ghost', () => {
    expect(classify('GHOST', null)).toBe('ghost');
    expect(classify('START_REPLAY', null)).toBe('ghost');
    expect(classify('REPLAY_MENU', null)).toBe('ghost');
  });

  it('held screens continue the open activity (racing or ghost), else menus', () => {
    for (const s of HELD_SCREENS) {
      expect(classify(s, 'racing')).toBe('racing');   // pause/reset/overlay mid-race
      expect(classify(s, 'ghost')).toBe('ghost');     // overlay mid-ghost-watch
      expect(classify(s, null)).toBe('menus');        // reached from a cold menu
      expect(classify(s, 'menus')).toBe('menus');     // not a continuable activity
    }
  });

  it('the Gallery/album overlays are held (they must not interrupt racing or a ghost)', () => {
    expect(classify('GALLERY', 'racing')).toBe('racing');
    expect(classify('GALLERY', 'ghost')).toBe('ghost');
    expect(classify('GALLERY_VIEW', 'ghost')).toBe('ghost');
  });

  it('everything else (incl. START_TIME_TRIAL and unknowns) is menus', () => {
    expect(classify('MAIN_MENU', null)).toBe('menus');
    expect(classify('START_TIME_TRIAL', null)).toBe('menus');
    expect(classify('START_TIME_TRIAL', 'racing')).toBe('menus');   // not a held screen
    expect(classify('UNKNOWN_SCREEN', null)).toBe('menus');
    expect(classify('', null)).toBe('menus');
    expect(classify(null, null)).toBe('menus');
    expect(classify(undefined, 'racing')).toBe('menus');
  });
});

describe('parity with src/lib/playerCard.js (keep in sync)', () => {
  // Copied verbatim from src/lib/playerCard.js. If the card changes these, this test fails and
  // both sides must be reconciled.
  const CARD_HOLD_SCREENS = ['RACE_MENU', 'HOME', 'RESET', 'GHOST_RESET', 'UNKNOWN_RESET',
    'UNKNOWN_RACE_ACTIVE', 'PHOTO_MODE', 'EXIT_PHOTO_MODE', 'GALLERY', 'GALLERY_VIEW'];
  const CARD_GHOST_SCREENS = ['GHOST', 'START_REPLAY', 'REPLAY_MENU'];

  it('every card HOLD screen continues both racing and a ghost on the server', () => {
    for (const s of CARD_HOLD_SCREENS) {
      expect(HELD_SCREENS.has(s)).toBe(true);
      expect(classify(s, 'racing')).toBe('racing');
      expect(classify(s, 'ghost')).toBe('ghost');
    }
  });

  it('adds POST_TIME_TRIAL (the card inRaceCtx results state) to the held set', () => {
    expect(HELD_SCREENS.has('POST_TIME_TRIAL')).toBe(true);
  });

  it('the card GHOST screens are the server ghost set', () => {
    expect([...GHOST_SCREENS].sort()).toEqual([...CARD_GHOST_SCREENS].sort());
  });

  it('the card SETUP select screens classify to their classes; START_TIME_TRIAL is menus', () => {
    expect(classify('CHARACTER_SELECT', null)).toBe('character_select');
    expect(classify('KART_SELECT', null)).toBe('kart_select');
    expect(classify('COURSE_SELECT', null)).toBe('track_select');
    expect(classify('START_TIME_TRIAL', null)).toBe('menus');   // documented: transient lead-in
  });
});
