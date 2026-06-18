# SP3 — Territory Map Hover Popup (design)

**Date:** 2026-06-18
**Project:** thekartoff territory map (`web/`), sub-project SP3. Follows SP1 (map foundation, merged). Precedes SP2 (territory colour) / SP4 (timeline).
**Status:** design locked via the visual companion + a clickable `file://` prototype (`tools/popup-prototype.html`, verified in headless Edge). Ready for an implementation plan.

## Goal

Hovering a course on the World Map (`web/`, `#/map`) opens a sleek popup, projected from the course icon, showing **that course's current leader** (their posted GIF) and the **track leaderboard**. A leader who is *dominant relative to the world record* burns "on fire."

## Where it lives

- `web/src/WorldMap.svelte` already renders the base + 30 course sprites and reserves a `.popups` layer (SP1). The popup mounts there.
- Pure browser SPA; reuses desktop `src/` components where useful (`Fire.svelte`, player-figure helpers).

## Data (all existing, open reads — no new server work)

From `api.thekartoff.com` (token-less reads; `pi/src/api/reads.ts`):
- `GET /v1/leaderboard?course=<slug-or-name>&cc=150` → ranked PBs `{player_id, display_name, total_time_ms, total_time_str, rank}`.
- `GET /v1/roster` → player colours (server `players.color` wins).
- `GET /v1/world-records?course=&cc=150` → `{holder_name, record_ms, ...}`.

Course identity comes from SP1's `web/public/map/manifest.json` (`slug`, `name`, `hit`, `spr`). Default `cc=150`. Season = active. Fetch lazily per course on first hover; cache for the session.

## The popup card (LOCKED)

A flat dark card (`#121419`, 1px `#2a2d33`, 6px radius, soft shadow), graphite chrome (`src/theme.css` tokens), ~344px wide.

**Left — controller strip** (`~80px`, *thinner than the 96px prototype*): just the leader's **edge-cropped posted GIF** as a vertical cut-out — cropped harder on the left/right edges (player-card figure style), flush to the bottom — plus a 3px **player-colour spine**. No caption, no name/time (the leaderboard's #1 row already carries them).

**Right — track leaderboard:**
- Header: course **title** left; **WR** in the **top-right corner** (`WR  m:ss.sss`, dim); a fade-at-both-ends **hairline** beneath it.
- Rows: a rounded vertical **colour bar at the left edge** of each row (card-spine motif, not inline dots), then `#`, `Player`, `Time` (tabular mono), `Gap` (to #1, tabular mono). The leader's own gap renders as a dimmed `-.---`. The #1 row is a subtle white-text tinted pill.
- Tight row height; horizontal padding lives *inside* the rows so the #1 pill and gap never clip the right edge, with the colour bar flush left.

## Interaction (LOCKED)

- **Glance tooltip, icon-only:** open on course-icon `mouseenter`; close on icon `mouseleave`. The popup does **not** stay open when the cursor is over the popup itself. (Short open/close debounce ~80ms to avoid flicker between adjacent icons.) It is informational — no in-popup interaction needed.
- **Spring-open animation:** scale `0.92 → 1` + fade `0 → 1` over ~140ms, `transform-origin` at the course icon, so it visibly springs out of the course. Quick reverse on close (~100–120ms). The icon keeps SP1's lift-off-shadow hover.
- **GIF plays once** on open (see Assets).
- **Anchoring:** position beside the icon and **flip horizontally/vertically** to stay within the map frame. Refine over the prototype's first pass so a centre course projects with a small offset from the icon rather than clamping hard to the frame edge (compute the flip from the icon's quadrant; clamp only as a last resort).
- **Touch:** tap a course to open; tap elsewhere / another course to close.

## The "on fire" dominance model (LOCKED)

A course is **on fire while the leader's margin clears an exponential bar**:

```
lead   = (t2 − t1) / WR          # #1's margin over #2, as a fraction of the WR
off    = (t1 − WR) / WR           # how far #1's PB sits off the WR
onFire = lead ≥ E0 · e^(off / K)
```

where **`lead` and `off` are expressed as percent of WR** (e.g. `4.04` and `2.95`), with **`E0 = 0.2`, `K = 4`** (equivalently in fractions: `E0 = 0.002`, `K = 0.04`). Course-length-independent. **Stateless** — computed live from the leaderboard + WR, no server state, no hysteresis.

Properties (validated against real Season-1/150cc PBs): resilience is **exponential in closeness to the WR** — at WR pace a rival must nearly tie to snuff it (~0.13s short / ~0.21s mid / ~0.47s long track); far off the WR it snuffs easily. On current data ~4 courses light (Mario Bros. Circuit, Boo Cinema, Peach Stadium, Salty Salty Speedway). Needs ≥2 PBs and a current WR; otherwise not on fire.

**This same `dominance(lead, off)` is SP2's territory "strength"** — implement it as one reusable pure function.

**Re-tuning:** `tools/fire-model-explorer.html` (open in a browser, drag `E0`/`K`); `tools/fire-model-explorer-regen.py` refreshes the PB snapshot from `pi/mkw.db`.

**Fire visual:** a burning course's strip swaps the posted GIF for the **animated GIF behind pbenguin's on-pace figure** — the source GIF the on-pace still-frame is cut from, *not* the frozen frame — looping, under **`Fire.svelte`** brand-hued flames tinted by the leader's colour.

## Assets

- **Posted GIFs:** `assets/player_gifs/<player>Posted.gif` exist for gub/paul/luke/aliias. Bundle them for `web/` (e.g. `web/public/players/<player>.gif`) via a small build step that also writes a **non-looping** copy (loop-count 1, via `gifsicle`/PIL) so the browser plays them **once** natively; re-arm on open by resetting `src`.
- **Fallback (no posted GIF, e.g. Alex):** the static player figure (`playerFigures` `on` still-frame); if none, colour spine + name only.
- **On-pace (fire) GIF + flames:** the animated GIF the on-pace figure is derived from, per player — sourced from the figure pipeline referenced by `src/lib/playerFigures.js` (and `assets/player_gifs/`) and bundled into `web/`, kept **looping** (unlike the play-once posted GIF). Flames via `src/components/Fire.svelte`. *Locate the exact per-player on-pace source GIF during the build.*

## Components & modules (proposed)

- `web/src/lib/fireModel.js` — pure `dominance({t1, t2, wr})` + `isOnFire(...)`; unit-tested. (Reused by SP2.)
- `web/src/lib/courseData.js` — fetch + cache leaderboard / WR / roster; assemble a per-course view model (leader, colour, gif, rows with gap, wr, onFire).
- `web/src/CoursePopup.svelte` — the card (strip + leaderboard + fire), driven by the view model.
- `web/src/WorldMap.svelte` — wire hover open/close + spring + anchoring into the existing `.popups` layer.
- A bundling step for the GIFs/figures (script + `web/public/`), including the non-looping GIF pass.

## Testing & verification

- Vitest: `fireModel` (table of real cases → expected on-fire set), `courseData` assembly (gap math, fallback selection).
- Visual: headless-Edge screenshots of the real `#/map` (per [[map-icon-haze-fix]] — **browser is ground truth, never OpenCV**): a fire course, a calm course, an edge course (flip), and the missing-GIF fallback.
- `svelte-check` clean.

## Out of scope

SP2 territory colour field, SP4 timeline. Keep `fireModel.dominance` reusable for SP2. No new server endpoints.
