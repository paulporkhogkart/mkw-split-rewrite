import type { ActivityInput } from './types';
import type { ScreenInterval } from '../stats/screen';

export const SCREEN_LABELS: Record<string, string> = {
  CHARACTER_SELECT: 'character select',
  KART_SELECT: 'kart select',
  COURSE_SELECT: 'track select',
  GHOST: 'watching a ghost',
  START_REPLAY: 'watching a ghost',
  REPLAY_MENU: 'watching a ghost',
};

export const labelFor = (screen: string): string => SCREEN_LABELS[screen] ?? 'menus';

export function screenActivityInputs(seasonId: number, playerId: number, intervals: ScreenInterval[]): ActivityInput[] {
  return intervals
    .filter(iv => iv.screen && iv.ended_ms > iv.started_ms)
    .map(iv => ({
      ts: iv.started_ms,
      type: 'screen' as const,
      season_id: seasonId,
      player_id: playerId,
      course_id: null,
      cc: null,
      payload: { screen: labelFor(iv.screen), dwell_ms: iv.ended_ms - iv.started_ms },
    }));
}
