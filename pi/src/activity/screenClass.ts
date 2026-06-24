// Server-side screen classification for the activity feed. Mirrors the player-card screen
// model so the feed and the cards agree on what "racing" vs "menus" means.
//
// keep in sync with src/lib/playerCard.js (SETUP / HOLD_SCREENS / inRaceCtx).
// The JS/TS package boundary rules out a direct import; screenClass.test.ts pins these sets
// against a copy of the card's literals as the drift guard.

export type ScreenClass = 'racing' | 'menus' | 'character_select' | 'kart_select' | 'track_select' | 'ghost';

// Screens that CONTINUE an open racing session (pause menus, reset loaders, mid-race detection
// blips, and the post-race results screen) instead of reading as menus. This is the card's
// HOLD_SCREENS set plus POST_TIME_TRIAL (the card's inRaceCtx results state).
export const HELD_SCREENS = new Set<string>([
  'RACE_MENU', 'HOME', 'RESET', 'GHOST_RESET', 'UNKNOWN_RESET',
  'UNKNOWN_RACE_ACTIVE', 'PHOTO_MODE', 'EXIT_PHOTO_MODE', 'POST_TIME_TRIAL',
]);

// The card's "Watching a ghost" SETUP screens (START_TIME_TRIAL is intentionally NOT here -
// it's a transient race lead-in that reads as menus and is dropped by the min-session floor).
export const GHOST_SCREENS = new Set<string>(['GHOST', 'START_REPLAY', 'REPLAY_MENU']);

/** Classify a frame's screen into an activity class.
 *
 *  `racingOpen` = the player currently has an open racing session. It is what lets a held
 *  screen (a pause/reset mid-grind) continue racing instead of reading as menus - exactly the
 *  card's `holds`-gated HOLD_SCREENS behaviour. A held screen reached from a cold menu (no open
 *  racing session) is just menus. */
export function classify(screen: string | null | undefined, racingOpen: boolean): ScreenClass {
  if (screen === 'RACING') return 'racing';
  if (screen && HELD_SCREENS.has(screen)) return racingOpen ? 'racing' : 'menus';
  if (screen === 'CHARACTER_SELECT') return 'character_select';
  if (screen === 'KART_SELECT') return 'kart_select';
  if (screen === 'COURSE_SELECT') return 'track_select';
  if (screen && GHOST_SCREENS.has(screen)) return 'ghost';
  return 'menus';
}
