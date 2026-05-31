# Architecture

## Overview

`mkw_tracker` is a real-time Mario Kart World telemetry tracker. It captures a 1920×1080 @ 60fps OBS virtual camera feed and uses OpenCV template matching to detect game state and record race data.

## Module Map

```
mkw_tracker/
├── main.py            Entry point: cv2 capture loop + asyncio IPC daemon thread
├── config/            Settings dataclass (hot-reloadable from SQLite)
├── database/          SQLite connection, migrations, config_repo, replay_repo
├── detection/         ScreenDetector, SelectionTracker, template helpers
├── race/              LapTracker, CoinTracker, TimestampTracker, FinishDetector, MushroomTracker
├── minimap/           MinimapTracker, MinimapRecorder, MinimapPlayer
├── overlay/           Pure OpenCV drawing functions (no logic)
├── lifecycle/         RaceLifecycle: screen-change callback driving all state transitions
├── ipc/               IpcServer (asyncio stdin reader) + protocol message types
├── sync/              SyncClient stub (future server upload/fetch)
└── utils/             Camera setup, image helpers
```

## Data Flow

```
OBS Camera
    │
    ▼
cap.read() ──► ScreenDetector.update()   ──► screen, PerfStats
                                              │
                                              ▼
                   SelectionTracker.update()  ──► SelectionState
                   LapTracker.update()        ──► LapState, lap_incremented
                   CoinTracker.update()       ──► CoinState
                   TimestampTracker.update()  ──► TimestampState
                   FinishDetector.update()    ──► FinishState
                   MushroomTracker.update()   ──► MushroomState
                   MinimapTracker.update()    ──► MinimapState
                        │
                        ▼
                   MinimapRecorder.update()   (records cx,cy,score,t_ms)
                        │
                   on_screen_change callback
                        │
                        ▼
                   RaceLifecycle              (start/pause/resume/finalize)
                        │
                        ▼
                   SQLite DB                  (replays, points, thresholds)
                        │
                        ▼
                   Overlay drawing ──► cv2.imshow()
                        │
                        ▼
                   IpcServer.emit()           (JSON events → stdout → Tauri)
```

## Threading Model

| Thread | Responsibility |
|--------|---------------|
| Main   | cv2 capture loop, all tracker updates, overlay drawing |
| Daemon | asyncio stdin reader (IpcServer); puts parsed dicts into `inbound_queue` |

Communication: `queue.SimpleQueue` — IPC thread puts inbound commands; main loop drains once per frame before tracker updates.

## Screen Detection

Two-phase detection avoids scanning all templates every frame:
- **Phase 1** (every frame): re-confirm current screen with one template match
- **Phase 2** (after `CONFIRM_LOSS_FRAMES=3` misses): scan all reachable candidates from `TRANSITIONS` graph

## Minimap Tracking

Gatekeeping pipeline per frame:
1. Locked-score false-positive rejection
2. Position-plausibility jump gate (`_MM_MAX_JUMP_PX`)
3. Re-acquire confirmation (`_MM_REACQUIRE_FRAMES`)
4. Hough circle ring detection (disambiguates ghost icons)
5. Two-candidate evaluation (velocity + Hough score tiebreak)
6. EMA smoothing → `cx_smooth`, `cy_smooth`
7. Visual freeze → full freeze on low-confidence streaks

## Race Lifecycle State Machine

```
                 ┌─────────┐
                 │  other  │ ◄──────────────────────────────┐
                 └────┬────┘                                 │
                      │  → RACING (fresh)                    │
                      ▼                                      │
               ┌─────────────┐  → RACE_MENU/HOME  ┌────────────────┐
               │   RACING    │ ─────────────────►  │ PAUSED         │
               │             │ ◄─────────────────  │ (RACE_MENU or  │
               └─────────────┘  ← RACING (resume)  │  HOME)         │
                      │                            └───────┬────────┘
                      │  → POST_TIME_TRIAL                 │  → other
                      │  → RESET                           │
                      ▼                                     ▼
               finalize(completed=True)           finalize(completed=False)
                      │                                     │
                      └──────────────┬──────────────────────┘
                                     ▼
                              _clear_race_state()
```

## Frontend Architecture

### Module layout

```
src/
├── App.svelte           Thin shell: IPC handler, store mirrors, view router, editor event
│                        forwarding, camera/wizard logic
├── lib/
│   ├── ipc.js           send() — fire-and-forget invoke("send_to_tracker", …)
│   ├── stores.js        Svelte writable stores fed from tracker-event (connection, screen,
│   │                    liveScore, candidates, selection, race, minimap, replays, sample,
│   │                    devices, tells, rois)
│   ├── palette.js       Design tokens (C object + REPLAY_HUES)
│   ├── format.js        scoreColor(), screenLabel(), fmtScore(), fmtTime()
│   ├── graph.js         Screen-graph node positions, edge list, pan/zoom math
│   └── overlay.js       Canvas drawing functions (ROI outlines, minimap dot/ring/trails)
└── components/
    ├── TitleBar.svelte          Window controls + "Edit screens" / "← Monitor" toggle
    ├── StatusBar.svelte         Connection dot, screen name + score, fps, resolution
    ├── FeedOverlay.svelte       <video> + <canvas> overlay (ROIs + minimap reconstruction)
    ├── Rail.svelte              Right-hand panel (Selection, Race, Event log sections)
    ├── RailSection.svelte       Collapsible section wrapper
    ├── ReadoutRow.svelte        Single labelled value row with expand toggle
    ├── CandidateList.svelte     Ranked candidates dropdown (screen/char/kart/course/costume)
    ├── RaceSection.svelte       Lap splits + finish time
    ├── EventLog.svelte          Scrollable backend event list
    ├── EditMode.svelte          Edit view shell: ScreenGraph + RoiCanvas + ToolsPanel
    ├── ScreenGraph.svelte       Pan/zoom screen-graph navigator (uses graph.js)
    ├── RoiCanvas.svelte         Zoomable canvas with drag-resize handles; engine frame background
    ├── ToolsPanel.svelte        Detection / Readout tab strip
    ├── DetectionTree.svelte     AND-groups / OR-regions boolean-tree editor
    ├── RegionInspector.svelte   Live crop vs stored template side-by-side
    ├── ReadoutRoiEditor.svelte  Selection/HUD ROI editing with per-item template capture
    ├── SettingsModal.svelte     Wizard/settings dialog shell
    ├── SourceCheck.svelte       Dual browser+Python feed source preview
    ├── DeviceSelectors.svelte   Video + audio device dropdowns
    └── LanguageSelectors.svelte Application language + Switch-system language selectors
```

### Data flow (frontend)

```
Tauri "tracker-event"
        │
        ▼
App.svelte (switch on msg.type)
  ├─ updates local vars (engineFrame, trackerConnected, …)
  └─ mirrors into stores (screen, liveScore, candidates, selection, race, minimap, …)
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │              Monitor view                    │
  │  FeedOverlay ← stores (screen, tells, rois, │
  │                minimap, replays, sample)     │
  │  Rail        ← stores (screen, liveScore,   │
  │                candidates, selection, race)  │
  └─────────────────────────────────────────────┘
        │ title-bar toggle
        ▼
  ┌─────────────────────────────────────────────────┐
  │               Edit mode                          │
  │  ScreenGraph  ← backendScreen / selectedNode    │
  │  RoiCanvas    ← engineFrame / roiBoxes          │
  │  ToolsPanel / DetectionTree / RegionInspector   │
  │    ← tells, activeRegion, templateImg, liveImg  │
  │  All interactions → App.svelte event handlers   │
  │    → send() → Tauri → Python sidecar            │
  └─────────────────────────────────────────────────┘
```

### Views

**Monitor** (`$viewStore === "monitor"`): `FeedOverlay` renders the browser `MediaStream` in a `<video>` element and draws a `<canvas>` overlay using `overlay.js` — active tell ROIs colored by match/context role, plus the minimap reconstruction (icon sample circle, live tracking dot with EMA-smoothed position, historical replay trails per previous run). The `Rail` to the right provides expandable `ReadoutRow` entries for screen detection (with `CandidateList` candidates on expand), character/kart/course/costume selection, dynamic lap splits + finish time (`RaceSection`), and a collapsible `EventLog`.

**Edit** (`$viewStore === "edit"`): `EditMode` hosts a top-strip `ScreenGraph` (pan/zoom via `graph.js`, click selects the screen to edit), a `RoiCanvas` (drag-to-resize ROI handles over the engine frame), and a `ToolsPanel`. Detection tab: `DetectionTree` (AND-group / OR-region tree with add/remove group/region controls) + `RegionInspector` (live crop vs stored template). Readout tab: `ReadoutRoiEditor` (selection and HUD ROI boxes with per-item template capture). All edits are forwarded as events to `App.svelte`, which calls `send()` with the region-indexed IPC commands (`update_region`, `add_region`, `remove_region`, etc.).

**Settings modal** (`SettingsModal`): first-run wizard (Language → Camera → Done) or returning-user single-screen re-run. Contains `SourceCheck` (browser + Python feed preview), `DeviceSelectors` (video/audio device dropdowns), `LanguageSelectors` (app + Switch-system language).

### Styling notes

All numeric readouts use `font-variant-numeric: tabular-nums` and a monospace stack for stable-width display. Design tokens live in `src/theme.css` (CSS custom properties) and `src/lib/palette.js` (JS `C` object); colors are functional-only (no theming for aesthetics). Scrollbars use `::-webkit-scrollbar` thin-native style.
