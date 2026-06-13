# PB-pace "on fire" portrait effect — design

**Status:** Built + merged into the monitor player cards (2026-06-13).
**Goal:** When a player is mid-run and on PB pace, set their player-card portrait
"on fire" — the Overwatch / Deadlock "on fire" feel — hued to the player's brand
colour. Frontend-only; the trigger data already flows through presence.

## Visual

A **column of flaming aura** the figure stands inside, not a single rounded flame.
Built from metaball ellipses pushed through an SVG goo filter (gaussian blur +
alpha threshold) — fluid and cartoony, smooth edges, no realistic noise. The whole
palette is derived from the player's `--pc` brand colour (deep → base → light →
near-white-hot tip), so blue players burn blue, green burn green, etc. The hot
near-white core keeps a cool hue still reading as fire.

Two layers, both clipped to the portrait column (`x[5..61]`, the 56px `.fig`
strip), clipping hard at the vertical borders:

- **Back** (`z-index:1`, behind the figure): a wide column whose *widest body is
  raised to the head line* (emitter `baseY ≈ 56–104`) so the visible part — the
  top third, above/around the head, where the opaque cutout doesn't hide it — is
  full and wide, with flame **licks** on top. (Earlier versions hid all their width
  down at the base behind the figure and read thin.)
- **Front** (`z-index:3`, `mix-blend:screen`, opacity ~0.62, in front of the
  figure): a **wide flat-bottomed U** — a low central trough with bold **arms
  pinned at both borders**, wrapping the base of the figure. Kept short so it never
  covers the face.

A blurred, screen-blended **backlight** column ties the two together as one aura.
Flicker is globally scaled (`SPEED = 0.70`) — slow, not frantic. The effect fades
in/out (opacity transition) rather than popping.

The shape was tuned live over ~15 rounds in the brainstorm visual companion;
the final constants live in `Fire.svelte` (`BACK` / `FRONT` layer tables).

## Trigger

Fire is lit only while the player is **racing** (`isRacing`) AND on PB pace, where
"on PB pace" depends on the card's existing delta-mode setting
(`src/lib/cardSettings.js`, `"pace"` | `"laps"`):

- **pace (fluid delta):** the live (delayed) pace delta `delayed.pb_delta_ms` is
  ahead of PB (`< 0`) **continuously for `FIRE_ON_MS_PACE = 2000ms`** — the user's
  "consistently under PB". Sustained, so a momentary dip under zero doesn't flash.
- **laps (LiveSplit delta):** the last completed lap split is under PB
  (`entry.lap_delta.delta_ms < 0`). A settled per-lap signal, so it lights at once
  (on-window 0).

Both modes drop after falling behind for `FIRE_OFF_MS = 400ms` (anti-flicker
hysteresis: slow to light, brief grace before dropping). Leaving RACING (finish,
pause, reset) clears the latch immediately.

## Components

- **`src/lib/fireState.js`** — pure, per-player latch. `updateFire(playerId,
  {ahead, racing, now, mode}) -> boolean` applies the on/off windows; `clearFire`.
  The sample buffer only holds ~1.2s (< the 2s on-window), so the latch owns its
  own timing off the injected `now`. Unit-tested (`fireState.test.js`, 9 cases).
- **`src/components/Fire.svelte`** — the visual. Props `color` + `active`. Builds
  the back/front ellipse fields once, runs **one** rAF loop, and only while
  `active` (CPU idle when not on fire). Each instance has its own goo filter ids.
- **`src/components/PlayerCard.svelte`** — computes `aheadNow` (mode-aware) +
  `onFire = updateFire(...)` each render tick, and renders
  `{#if isRacing}<Fire color={vm.color} active={onFire}/>{/if}`. `.fig` and
  `.data` get `z-index:2`, spine `z-index:4`, so the figure sits between the back
  (1) and front (3) fire layers.

## On-pace portrait + frame picker

The fire effect also swaps the **portrait** to a per-player "on-pace" pose while lit
(falls back to the online figure when a player has none set). Frames are hand-picked
with a new dev tool instead of the old auto-heuristic:

- **`scripts/pick_player_figures.py`** — `python scripts/pick_player_figures.py`
  pre-extracts every `temp/360/*.gif` frame to a cache, serves a browser picker
  (gifs grouped by player; scrub slider / step / play; "Set Offline/Online/On-pace"
  per player), seeded with everyone's **current online/offline picks already
  selected**. Save writes `assets/player_figures.json`, copies any newly-chosen gif
  into the committed `assets/player_gifs/`, and regenerates the PNGs.
- **`scripts/gen_player_figures.py`** — now manifest-driven: an explicit `(gif,
  frame)` per state from `player_figures.json` wins; the legacy heuristic (online
  ~88%, offline first-opaque, per the `MAP` gifs) is the fallback. Emits
  `<name>__on.png` / `__off.png` / `__onpace.png` (onpace only when set).
- **`src/lib/playerFigures.js`** — `onpaceFigure(name)`; the glob now also matches
  `__onpace.png`.

## Verification

Fire: `vitest` 114 passed · `svelte-check` 0/0 · `vite build` green.
Picker: `--no-serve` builds the cache + seeds defaults (every player online@~88% /
offline@0, onpace unset); manifest-driven extraction emits on/off/onpace into a temp
dir (verified, no committed-asset churn). Live in-app smoke (a real PB-pace run, both
delta modes; running the picker + saving a pick) still pending the user.
