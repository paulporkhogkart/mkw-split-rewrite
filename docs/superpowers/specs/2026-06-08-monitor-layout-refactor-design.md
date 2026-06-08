# Monitor Layout Refactor (Sub-project #1) — Design

**Date:** 2026-06-08
**Status:** Approved (design), ready to implement.
**Part of:** a 3-part monitor redesign — **(1) this layout refactor**, (2) a live presence pipeline, (3) the per-player panel. #2 and #3 get their own specs (not yet brainstormed).

## Goal

Kill the feed's letterbox black bars by anchoring the 1080p video to the **top** of the feed pane, keep the volume/preview footer snug under it, and carve a reserved **band** below it for the upcoming player panel. Also drop the minimap's on-canvas **legend** (the panel will identify players).

## Current

`.main-feed` (flex column) → `.feed-area` (`flex:1`, so it centres the `object-fit:contain` video → top+bottom bars) → `.feed-controls` (🔊 volume + Hide/Show). The minimap **legend** is drawn on the overlay canvas (`drawOverlay({legend})` in `FeedOverlay`).

## Proposed

`.main-feed` (flex column):
- `.feed-area` — **full width, 16:9 (aspect-ratio), anchored top** → no top/bottom bars. **The band gets a guaranteed minimum height** (decision confirmed): the area is capped so that when the window is short the video shrinks to fit (anchored top, so any unavoidable bars fall left/right, never wasting the top) and the band stays usable. In the common landscape case the video is simply full-width 16:9 with no bars and the band takes the remainder.
- `.feed-controls` — unchanged, sits directly under.
- **NEW `.player-band`** — `flex:1` with a `min-height` sized for ~5 cards; **empty placeholder now**, #3 fills it.
- `FeedOverlay` stops drawing the trail **legend** (pass `legend: []`; the legend-draw code in `overlay.js` stays, just unfed).

## Components touched

- `src/App.svelte` — `.feed-area` styles (top-anchored 16:9 + cap), a new `.player-band` element + styles. `.main-feed` is already a column.
- `src/components/FeedOverlay.svelte` — drop `legend` from the `drawOverlay` call.

## Out of scope

The panel's contents (#3) and the live data (#2). The band is a reserved empty placeholder this round.

## Testing

Pure layout — no unit tests. Verify: `svelte-check` clean, the frontend builds, and a visual check (run the app) confirms: no top/bottom bars, footer snug under the video, the band reserves space below, and the minimap legend is gone.
