import { describe, it, expect } from 'vitest';
import { SCREEN_LABELS, labelFor, screenActivityInputs } from './screens';
import type { ScreenInterval } from '../stats/screen';

describe('labelFor', () => {
  it('maps known screen names', () => {
    expect(labelFor('CHARACTER_SELECT')).toBe('character select');
    expect(labelFor('KART_SELECT')).toBe('kart select');
    expect(labelFor('COURSE_SELECT')).toBe('track select');
    expect(labelFor('GHOST')).toBe('watching a ghost');
    expect(labelFor('START_REPLAY')).toBe('watching a ghost');
    expect(labelFor('REPLAY_MENU')).toBe('watching a ghost');
  });

  it('defaults to menus for unknown screens', () => {
    expect(labelFor('MAIN_MENU')).toBe('menus');
    expect(labelFor('RACING')).toBe('menus');
    expect(labelFor('UNKNOWN_SCREEN')).toBe('menus');
    expect(labelFor('')).toBe('menus');
  });
});

describe('screenActivityInputs', () => {
  const seasonId = 1;
  const playerId = 42;

  it('emits one input per positive-length interval', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'CHARACTER_SELECT', started_ms: 1000, ended_ms: 5000 },
      { screen: 'KART_SELECT', started_ms: 6000, ended_ms: 8000 },
    ];
    const results = screenActivityInputs(seasonId, playerId, intervals);
    expect(results).toHaveLength(2);
  });

  it('computes dwell_ms correctly', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'COURSE_SELECT', started_ms: 1000, ended_ms: 3500 },
    ];
    const [result] = screenActivityInputs(seasonId, playerId, intervals);
    expect(result.payload.dwell_ms).toBe(2500);
  });

  it('uses started_ms as ts', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'GHOST', started_ms: 9000, ended_ms: 15000 },
    ];
    const [result] = screenActivityInputs(seasonId, playerId, intervals);
    expect(result.ts).toBe(9000);
  });

  it('sets type=screen, course_id=null, cc=null', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'CHARACTER_SELECT', started_ms: 0, ended_ms: 1000 },
    ];
    const [result] = screenActivityInputs(seasonId, playerId, intervals);
    expect(result.type).toBe('screen');
    expect(result.course_id).toBeNull();
    expect(result.cc).toBeNull();
  });

  it('sets season_id and player_id from args', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'KART_SELECT', started_ms: 0, ended_ms: 500 },
    ];
    const [result] = screenActivityInputs(5, 99, intervals);
    expect(result.season_id).toBe(5);
    expect(result.player_id).toBe(99);
  });

  it('has NO duration floor — a 200 ms blip still emits', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'MAIN_MENU', started_ms: 0, ended_ms: 200 },
    ];
    const results = screenActivityInputs(seasonId, playerId, intervals);
    expect(results).toHaveLength(1);
    expect(results[0].payload.dwell_ms).toBe(200);
  });

  it('applies label mapping including menus default', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'CHARACTER_SELECT', started_ms: 0, ended_ms: 1000 },
      { screen: 'GHOST', started_ms: 2000, ended_ms: 3000 },
      { screen: 'MAIN_MENU', started_ms: 4000, ended_ms: 5000 },
    ];
    const results = screenActivityInputs(seasonId, playerId, intervals);
    expect(results[0].payload.screen).toBe('character select');
    expect(results[1].payload.screen).toBe('watching a ghost');
    expect(results[2].payload.screen).toBe('menus');
  });

  it('skips zero-length and negative-length intervals', () => {
    const intervals: ScreenInterval[] = [
      { screen: 'MAIN_MENU', started_ms: 5000, ended_ms: 5000 },   // zero
      { screen: 'MAIN_MENU', started_ms: 6000, ended_ms: 5000 },   // negative
      { screen: 'MAIN_MENU', started_ms: 7000, ended_ms: 8000 },   // valid
    ];
    const results = screenActivityInputs(seasonId, playerId, intervals);
    expect(results).toHaveLength(1);
  });

  it('skips intervals with empty screen name', () => {
    const intervals: ScreenInterval[] = [
      { screen: '', started_ms: 0, ended_ms: 1000 },
    ];
    const results = screenActivityInputs(seasonId, playerId, intervals);
    expect(results).toHaveLength(0);
  });

  it('returns empty array for empty input', () => {
    expect(screenActivityInputs(seasonId, playerId, [])).toHaveLength(0);
  });
});
