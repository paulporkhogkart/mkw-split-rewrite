// Server-side screen classification for the activity feed. Mirrors the player-card screen
// model so the feed and the cards agree on what "racing" vs "menus" means.
//
// keep in sync with src/lib/playerCard.js (SETUP / HOLD_SCREENS / inRaceCtx).
// The JS/TS package boundary rules out a direct import; screenClass.test.ts pins these sets
// against a copy of the card's literals as the drift guard.

export type ScreenClass = 'racing' | 'menus' | 'character_select' | 'kart_select' | 'track_select' | 'ghost';

// Screens that CONTINUE the current activity (racing OR watching-a-ghost) rather than ending it:
// the race pause menu + reset loaders, mid-race detection blips, the post-race results screen,
// and the universal system overlays (Home, the Gallery/album, Photo mode). This is the card's
// HOLD_SCREENS plus POST_TIME_TRIAL (its inRaceCtx results state) plus the Gallery screens.
export const HELD_SCREENS = new Set<string>([
  'RACE_MENU', 'HOME', 'RESET', 'GHOST_RESET', 'UNKNOWN_RESET', 'UNKNOWN_RACE_ACTIVE',
  'PHOTO_MODE', 'EXIT_PHOTO_MODE', 'GALLERY', 'GALLERY_VIEW', 'POST_TIME_TRIAL',
]);

// The card's "Watching a ghost" SETUP screens (the ghost equivalent of RACING; REPLAY_MENU is
// the ghost's pause menu). START_TIME_TRIAL is intentionally NOT here - it's a transient race
// lead-in that reads as menus and is dropped by the min-session floor.
export const GHOST_SCREENS = new Set<string>(['GHOST', 'START_REPLAY', 'REPLAY_MENU']);

/** Classify a frame's screen into an activity class.
 *
 *  `openClass` = the class of the player's currently-open session (or null). It is what lets a
 *  held screen - a race pause/reset, or a Home/Gallery/Photo overlay - CONTINUE the current
 *  activity (racing or watching-a-ghost) instead of reading as menus. A held screen reached from
 *  a cold menu (no open session) is just menus. This mirrors the card's `holds`-gated
 *  HOLD_SCREENS, generalised so the overlays don't interrupt a ghost-watch either. */
export function classify(screen: string | null | undefined, openClass: ScreenClass | null): ScreenClass {
  if (screen === 'RACING') return 'racing';
  if (screen && GHOST_SCREENS.has(screen)) return 'ghost';
  if (screen && HELD_SCREENS.has(screen)) return (openClass === 'racing' || openClass === 'ghost') ? openClass : 'menus';
  if (screen === 'CHARACTER_SELECT') return 'character_select';
  if (screen === 'KART_SELECT') return 'kart_select';
  if (screen === 'COURSE_SELECT') return 'track_select';
  return 'menus';
}
