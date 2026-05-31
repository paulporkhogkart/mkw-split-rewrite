# Frontend Redesign — Native Monitor + Edit Mode

**Date:** 2026-06-01
**Status:** Design — pending user review
**Scope:** The Tauri frontend (`src/`). Layout, information architecture, component
structure, and styling — plus the backend/IPC additions the new feed overlay requires.
**Supersedes:** the visual-only `2026-05-31-ui-restyle-design.md`. That restyle deliberately
froze the layout; this redesign changes the layout/IA, which is what the restyle couldn't reach.

## Goal

Turn the frontend into a **native, professional desktop monitor** in the spirit of OBS / DaVinci
Resolve. The app is used primarily as a **live monitor** glanced at on a second screen; the user
watches it, and only drops into configuration when detection goes wrong, then returns to watching.
So the monitor is the product; configuration is an on-demand "repair" mode.

The previous attempt reskinned colors but kept the web-app layout, so it still didn't feel native.
This time the layout, information architecture, and the monolithic `App.svelte` are all in scope.

## Design principles

- **Native restraint, executed precisely.** One UI sans (the platform font, Segoe UI), not a
  themed/display typeface. Hierarchy from weight/size/color, not decoration.
- **No "AI-dashboard" tells.** No decorative monospace in chrome (numbers use the UI sans with
  **tabular figures**). No tracked small-caps micro-labels (sentence-case headers). Flat surfaces,
  hairline dividers, minimal radius. No gradients/glows/pill-chips.
- **Color is functional only.** Green/amber/red = tracking health (the `scoreColor` thresholds:
  ≥0.8 ok, ≥0.5 warn, else err). A single muted blue **accent** marks genuine selection and the
  primary action — nothing else on chrome is colored.
- **The feed is ground truth.** The readout sits beside it and is comparable at a glance; the feed
  is annotated so the user can see what the tracker is doing.

## Application modes

1. **Monitor** (default, primary) — live feed + readout rail + status bar.
2. **Edit mode** (on demand) — entered via an "Edit screens" toggle in the title bar; the window
   becomes a config workspace (screen-graph navigator + ROI canvas + tools). "← Monitor" returns.
3. **Settings / Setup** — a modal (⚙). First-run is the same fields as a short wizard
   (Languages → Video → Done).

`engineFrame` (the backend's 10 Hz captured frame) is shown **only** in Settings/Setup, never in
the Monitor. The Monitor's feed is the smooth browser `<video>` (`mainVideoEl`).

## Monitor view

### Title bar
`MKW Tracker` + version (muted sans) · spacer · **Edit screens** (flat hairline button) · **⚙** ·
window controls. Flat `--panel` band, hairline bottom border.

### Feed (annotated, recreated natively over the `<video>`)
The browser `<video>` plays full-rate with audio (feed volume/mute controls retained). The UI draws
overlays on top, in 1080p coordinates scaled to the displayed video (ROIs are fixed rectangles, so
they align regardless of the two capture paths):

- **Active ROI boxes** for the current screen — the detection "tell" region(s) (accent/blue) and
  the relevant Selection or HUD ROIs (green = matching). Small flat tags (e.g. `tell · 0.92`,
  `lap`, `coins`, `time`).
- **Minimap recreation** (bottom-right region, from `MINIMAP_ROI`):
  - boundary box;
  - **icon sample** inset — the locked HSV template crop;
  - **live icon tracking** — crosshair/dot + Hough ring, colored by `track_state`
    (`tracking` = ring+face, `ring_only`, `reacquire`, etc.), mirroring `overlay/minimap.py`;
  - **replay trails** — previous runs on the current course, drawn as muted distinct-hued paths.

### Rail (right, ~236px) — grouping **G1** (tinted header bands, edge-to-edge)
Sections run edge-to-edge (no nested boxes); each has a subtle full-width `--panel` header band and
a hairline above it. Three sections:

**Selection** — one unified readout (detection + selection combined). Rows have **no field labels**;
the value itself names the field, screen rendered human-readable ("Racing", not "RACING"). Each row:
- resting: `value` (left, sans, truncates with ellipsis — never wraps) · `score` (right,
  color-coded by `scoreColor`). **No bar at rest.**
- empty/no selection: filler `no costume` (dim italic) · dim `0.00`.
- **click to expand** → a recessed well unfolds the runners-up *beneath* the row (no separate
  header, no duplicate). In the expanded state, the open row **and** its candidates show a
  color-coded partial-fill **bar** + score — bars appear only here, where comparing magnitudes is
  the point. Rows: screen / character / kart / course / costume.

**Race** — per-lap **splits** (`Lap 1 … Lap N`, dynamic count; the live lap shown as in-progress),
a **Total**, **Coins**, **Mushrooms**. Numbers are tabular sans.

**Event log** — collapsible (debug); a quiet disclosure caret (the one place a caret remains).

The old standalone "Candidates" panel is gone (dissolved into per-row expansions). The input/audio
device selectors that used to live in the Detection panel move to Settings.

### Status bar (footer)
Sans, hairline top border, segments separated by thin `|`: connection (dot + label) · current
screen · live score · fps · resolution. Tabular figures. (Reuses existing `statusDot`/health logic.)

## Edit mode

Entered from the title bar. Layout:
- **Top strip** — the existing interactive pan/zoom **screen graph** as a navigator (current node
  highlighted; click a node to load that screen). The graph is preserved as-is, only relocated.
- **Center** — the **ROI canvas**: the zoomable feed with editable ROI boxes (active region accent,
  sibling-in-group neutral grey, other-group warn — the existing overlay semantics).
- **Right — tools**, **two tabs**:
  - **Detection** — the boolean-tree editor (groups ANDed, regions ORed): group blocks (hairline),
    region rows, add/remove region & group.
  - **Readout** — Selection **or** HUD ROI editing (they're mutually exclusive per screen, so one
    merged tab). **Disabled** on screens with neither (Title, loading, Reset).
  - **Selected-region detail** (either tab): the stored **Template** beside the current **Live**
    crop, the **Match** score + bar, and **Capture** / **Test**. Costume regions render the live
    crop as Canny edges (matching the detector).

`← Monitor` returns. Tabs use an inset underline for the active tab; only **Test** carries the
accent (primary action).

## Setup / Settings

A modal (⚙); first-run presents the same fields stepped (Languages → Video → Done).

- **Two-feed source check** (the purpose of `engineFrame`): *App feed* (browser `<video>`) beside
  *Python engine input* (`engineFrame`), each with its own connection status
  (Connected / Opening / Blocked / Released / Error). They must show the same source so overlays
  align and the tracker scans what you watch. **Preserve all existing camera states/handlers.**
- **Video** device selector (renamed from "Camera"). **Audio** device selector (moved here).
- **Application language** (the UI's language) and **Switch system language** (the console's
  language, which selects the localized text the tracker matches) — two separate selectors.

## Visual system / tokens

Reuse and refine `src/theme.css`. Tokens (CSS vars; JS mirror `C` for canvas/SVG which can't read
CSS vars):

- Surfaces: `--bg #1b1c1e` · `--panel #202023` (headers/bars) · `--raised #26272b` (hover/active) ·
  `--feed #0b0c0e` · `--track #303135`.
- Borders: `--bd #34353a` (hairline) · `--bd2 #27282b` (internal divider).
- Text: `--tx #d9dadd` · `--tx2 #9a9ca1` · `--tx3 #6b6d73`.
- Accent: `--acc #3d7cc2` (selection / primary only).
- Status: `--ok #5aa86a` · `--warn #c89a3e` · `--err #cf5b4e`.
- Type: `--ui` Segoe UI / system-ui; **tabular-nums** globally on data. **No monospace anywhere in
  chrome — including the event log** (it lists events, not code), which uses the UI sans with
  tabular timestamps.
- Geometry: small radius (`--r 4px`); flat; status dots stay round.
- Minimap `track_state` colors mirror `overlay/minimap.py` (ring+face / ring-only / reacquire).

## Data flow / IPC

Existing (reused): `screen_change`, `selection_update`, `lap_update`, `coin_update`, `finish`,
`state` (Python→Tauri); `tells_list` / `rois_list` for ROI geometry; the `engineFrame` 10 Hz poll
(Settings only). ROI coords are full 1080p, scaled to the displayed feed.

**New (required for the annotated feed + rail):**
1. **Stream current-run minimap state** while `RACING`: `{cx, cy, radius, track_state}` mapped into
   `MINIMAP_ROI` — a new `minimap_update` event (throttled ~10–30 Hz) or folded into `state` at a
   higher cadence. (Replays are **not** streamed.)
2. **Fetch replay paths** for the current course from the DB (point sequences + identity/color) to
   draw the trail overlay — a query command (e.g. `get_replay_paths {course}`).
3. **Provide the icon-sample image** (the locked HSV template crop) as a data URL for the inset —
   on seed/course load.
4. **Per-field candidate lists** for the rail expansions: top-N `{name, score}` for screen and for
   character/kart/course/costume. Screen candidates already exist (old Candidates panel); the
   **selection tracker must expose ranked candidates** for the selection fields. *(Open item —
   confirm feasibility/cost; if a field can't produce a ranked list cheaply, its row simply doesn't
   expand.)*

No change to backend detection logic otherwise. Calibration stays disabled/unused.

## Frontend architecture (de-monolith)

`src/App.svelte` (3,268 lines) splits into focused components + modules. **Reuse the proven logic**
(camera/getUserMedia flow, IPC, canvas drawing, graph pan/zoom) by extracting it — not a rewrite
from scratch — so no functionality is lost. Proposed structure:

```
src/
  main.js
  theme.css                  tokens + globals
  lib/
    ipc.js                   send() + subscribe to tracker events; inbound/outbound
    stores.js                Svelte stores for backend state (screen, selection, race, minimap, candidates…)
    camera.js                device enumeration, getUserMedia, dual-feed/source-check logic
    graph.js                 screen-graph layout + pan/zoom
    palette.js               JS color mirror of the tokens (canvas/SVG)
    overlay.js               draw ROI boxes + minimap (sample/tracking/replays) onto the feed
  components/
    TitleBar.svelte
    MonitorView.svelte       feed + rail + statusbar
      FeedOverlay.svelte     <video> + canvas/SVG overlay
      Rail.svelte
        RailSection.svelte   G1 header band + content
        ReadoutRow.svelte    value · score · expand → CandidateList
        CandidateList.svelte candidates w/ bars
        RaceSection.svelte   splits · total · coins · mush
        EventLog.svelte
      StatusBar.svelte
    EditMode.svelte
      ScreenGraph.svelte     top-strip navigator
      RoiCanvas.svelte       zoomable feed + editable ROIs
      ToolsPanel.svelte      Detection / Readout tabs
        DetectionTree.svelte boolean-tree editor
        RegionInspector.svelte  live-vs-template + capture/test
        ReadoutRoiEditor.svelte selection/HUD ROI editing
    SettingsModal.svelte
      SourceCheck.svelte     dual feed (app + engineFrame) + statuses
      DeviceSelectors.svelte video + audio
      LanguageSelectors.svelte application + switch-system
```

This is a substantial refactor; execute it incrementally with the app runnable at each step.

## Scope / non-goals

- **Preserve all existing functionality** and every IPC command/handler (camera states, tell/ROI
  editing, capture/test, replay/PB export, language). "DO NOT LOSE FUNCTIONALITY."
- Calibration remains disabled/unused (no UI), as today.
- No backend detection-logic changes beyond the four IPC additions above.

## Risks / open items

- **Minimap dot vs `<video>` timing:** the dot is streamed from the backend's separate capture, so
  it may trail the smooth video by a frame or two. Accepted for a diagnostic monitor (ROIs are
  static and align exactly).
- **Per-field candidate lists** (#4 above) depend on the selection tracker exposing ranked
  candidates — confirm during planning; degrade gracefully (non-expandable row) if a field can't.
- **Capture device opened by both** browser and backend — existing behavior, preserved.
- Event-log font (mono vs sans) — default sans; revisit if it reads worse.

## Acceptance

- Monitor reads as a native desktop tool: one UI sans, tabular figures, **no chrome monospace**,
  sentence-case headers, flat hairline surfaces, accent only on selection/primary.
- Rail: G1 edge-to-edge sections; label-less Selection rows with color-coded scores; bars only in
  expansions; empty rows show filler + dim `0.00`; Race shows dynamic splits + Total.
- Feed shows live active ROIs + the minimap recreation (sample, tracking w/ state color, replays),
  drawn natively over the `<video>` — never `engineFrame`.
- Edit mode: top-strip graph navigator + ROI canvas + two-tab tools with live-vs-template; reachable
  via the title-bar toggle and reversible.
- Settings: dual-feed source check restored + Video/Audio selectors + Application & Switch-system
  languages; `engineFrame` appears only here.
- `App.svelte` is decomposed into the components/modules above; all prior functionality intact.

## Phasing (suggested)

1. Extract scaffolding: `theme.css` tokens, `lib/` modules (ipc/stores/camera/graph/palette), empty
   component shells; app still runs.
2. Monitor: TitleBar + Rail (G1, readout/race/log) + StatusBar against existing state.
3. FeedOverlay: ROI boxes (from `tells_list`/`rois_list`) over the `<video>`.
4. Minimap recreation + the new IPC (minimap stream, replay fetch, sample) + candidate lists.
5. Edit mode: relocate graph to top strip; canvas + two-tab tools + live-vs-template.
6. Settings/Setup: dual-feed source check + Video/Audio + dual languages; engineFrame confined here.
7. Sweep: remove dead code/CSS; verify no functionality lost.
```
