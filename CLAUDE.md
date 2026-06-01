# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Mario Kart World (MKW) real-time race telemetry tracker.** Captures a 1920×1080 @ 60fps OBS camera feed and uses OpenCV template matching to detect game screens, track player selections, and record race data (laps, coins, timestamps, minimap position). Designed to integrate with a Tauri frontend via stdio sidecar IPC.

## Running the App

```bash
pip install -r requirements.txt

# Run the package (new structured version)
python -m mkw_tracker

# Options
python -m mkw_tracker --purge-tight   # Force-regenerate _tight.png template caches
python -m mkw_tracker --history       # Load "last 100 runs" history replay mode
python -m mkw_tracker --no-ipc        # Disable stdin/stdout IPC (standalone mode)

# DEV TEST: play a recorded video instead of the capture feed (skips setup wizard)
python -m mkw_tracker --video temp/aiden.mp4                       # loops, paced to ~real time, shows overlay window
python -m mkw_tracker --video temp/aiden.mp4 --video-fps 0 --video-once   # rip through once, as fast as possible

# DEV TOOL: bulk-capture full screenshots of every character/costume/kart/course
# for rebuilding match templates. Hover items in-game on the live feed; it reuses
# the existing detection to auto-label and save one full 1920×1080 PNG per item
# into captures/<lang>/<category>/. Auto-grabs on confident+stable detection.
python -m mkw_tracker.tools.capture_sources                       # defaults: saved device + language, min-conf 0.6
python -m mkw_tracker.tools.capture_sources --min-conf 0.85 --hold 4 --no-sound

# DEV TOOL: rebuild char/kart/course/costume match templates from the captures
# above. Cuts each capture at the category's selection ROI (read from settings, same
# as the tracker) into a grayscale crop -> images/<category>/<lang>/*.png; the live
# matcher Canny-edges both at load (detection/selection.py SELECTION_SEARCH_PAD).
# Costumes also get synthetic background variants (<item>__bgdark.png etc.).
python scripts/gen_selection_templates.py                         # all langs + categories
python scripts/gen_selection_templates.py --lang en_uk --dry-run  # report only
python scripts/gen_selection_templates.py --category costumes

# Legacy monolith (reference only — do not edit)
python "!!!FINAL-ab-new-bubbles.py"
```

**Runtime keyboard shortcuts:** `q` quit · `Tab` toggle debug overlay · `m` toggle minimap frame logging · `d` dump debug crops to `debug_laps/`

**Capture-tool keys** (`tools.capture_sources`): `SPACE` force-(re)capture current frame · `s` skip current item · `Tab` toggle HUD · `q` quit

## Package Structure

```
mkw_tracker/
├── main.py          Entry point: cv2 capture loop + asyncio IPC daemon thread
├── config/          Settings dataclass (hot-reloadable from SQLite)
├── database/        SQLite (connection, migrations, config_repo, replay_repo)
├── detection/       ScreenDetector, SelectionTracker, template helpers
├── race/            LapTracker, CoinTracker, TimestampTracker, FinishDetector, MushroomTracker
├── minimap/         MinimapTracker, MinimapRecorder, MinimapPlayer
├── overlay/         Pure OpenCV drawing functions (no logic)
├── lifecycle/       RaceLifecycle: screen-change callback driving all state transitions
├── ipc/             IpcServer (asyncio stdin reader) + protocol message types
├── sync/            SyncClient stub (future server upload/fetch)
└── utils/           Camera setup, image helpers
```

Legacy JSON files (`minimap_seeds.json`, `minimap_rois.json`, `minimap_thresholds.json`, `replays/`) are migrated into SQLite on first run by `database/replay_repo.py:import_json_files()`.

## Architecture

### Screen Detection (`detection/screen.py`)
Two-phase detection:
- **Phase 1** (every frame): re-confirm current screen with one template match
- **Phase 2** (after `CONFIRM_LOSS_FRAMES=1` consecutive misses — i.e. the first lost frame): scan all reachable candidates from `TRANSITIONS` directed graph

`Screen` is an Enum. Each `Tell` is a **boolean tree**: `tell.groups` is a list of groups (ANDed) where each group is a list of `Region`s (ORed) — a screen matches when every group matches and a group matches when any region matches (`detect_tell` = `min` over groups of `max` over regions ≥ `match_threshold`). A `Region` is `kind="template"` (grayscale `TM_CCOEFF_NORMED` over a ±`search_pad` window) or `kind="dark_loading"` (crush-invariant dark-ROI + bright-icon statistical match, for the RESET family). Template images live in `images/screens/`. User edits persist as one `tell_tree_<SCREEN>` JSON blob per screen (config table); schema v3 (`database/tell_repo.py`) migrates the legacy `tell_roi_/tell_alt_/tell_req_also_*` keys.

### Selection Tracking (`detection/selection.py`)
Scans ROIs at 10Hz max. All four categories (characters, karts, courses, costumes) match the same way: a full-ROI grayscale crop is Canny-edged (`prepare_text_edges`, background-agnostic) and slid over a live crop padded by `SELECTION_SEARCH_PAD`, scored with `TM_CCOEFF_NORMED` (`match_variants`). Edges strip the shared name-plate background that made plain-grayscale cross-scores between similar names (Mario/Wario) sit at ~0.89; on edges they fall to ~0.5-0.66, so margins are 0.3-0.5 and similar names no longer nearly tie. Templates are rebuilt from `captures/` via `scripts/gen_selection_templates.py`. **Costumes** carry synthetic background variants (`<item>__bgdark/bgbright/bgsplit.png`) because their name banner's background varies (bright/dark/split); `match_variants` takes the best variant, so a costume scores high whatever the live background does (it never *misreads*, only under-scores - hence its low `SELECTION_COSTUME_FLOOR=0.30`). A confident character match commits immediately (the old multi-frame "pending" confirmation was dropped once edges made the character signal strong); `COSTUME_LOSS_FRAMES=4` (~0.4s) consecutive misses before a costume clears to Base. `SELECTION_RECONFIRM_THRESHOLD=0.80` holds the current selection only while it still scores above the worst cross-score, so a switch is never sticky.

### Race Telemetry (`race/`)
All trackers run during `RACING` screen only, at 10Hz. `TimestampTracker` uses burst capture (3 consecutive scans, 2 identical required) triggered by a lap crossing or the final-finish event. The **final finish** is detected by `FinishStillDetector` (`race/finish.py`): on the final lap (`current_lap == total_laps`) the timer freezes on the total time with no gold/white flash, so a masked frame-diff of the bright digit pixels stays still for `STILL_SECONDS`. (The old `FinishDetector` 1st/2nd/3rd position-ROI scan is kept in code but disabled — re-enable by uncommenting `finish.update(...)` in `main.py` + restoring the HUD `finish` ROI.) The mushroom counter (`race/mushrooms.py`) matches grayscale templates (cropped from `old_assets/*mush.png`) with `search_pad`, not binary.

### Minimap Tracking (`minimap/tracker.py`)
`seed()` locks an HSV-CLAHE-normalized template. Per-frame gatekeeping pipeline: locked-score rejection → jump gate (40px) → re-acquire (5 frames) → Hough ring detection → two-candidate tiebreak (velocity + Hough). EMA smoothing. Visual freeze after 4 low-conf frames, full suspend after 36. Auto-calibration on race completion via inverse-scaled margin formula.

### Race Lifecycle (`lifecycle/race.py`)
`RaceLifecycle.on_screen_change()` drives all start/pause/resume/finalize transitions. Attached to `ScreenDetector.on_screen_change`.

### IPC (`ipc/`)
`IpcServer` runs asyncio in a daemon thread. Reads newline-delimited JSON from stdin into `inbound_queue`; main loop drains queue once per frame. Outbound events written to stdout. See `docs/ipc-protocol.md`. Tell editing is region-indexed: `update_region` / `add_region` / `remove_region` / `add_group` / `remove_group` / `capture_region_template` / `test_region` / `get_region_images` (all keyed by `screen`+`group`+`region`), plus `reset_tell` (one screen → defaults) and `reset_roi` (one selection/HUD config ROI → default). Calibration IPC remains in the backend but is unused (no UI).

### Frontend (`src/App.svelte` + `src/lib/` + `src/components/`)
`App.svelte` is the thin shell: `tracker-event` IPC handler, Svelte store mirrors, view router (`"monitor"` | `"edit"`), editor event forwarding, and camera/wizard logic. Shared modules in `src/lib/`: `ipc.js` (fire-and-forget `send()`), `stores.js` (writable stores for all backend state), `palette.js` (design tokens), `format.js` (score formatting / labels), `graph.js` (screen-graph layout + pan/zoom math), `overlay.js` (canvas drawing functions).

Two top-level views, toggled by a title-bar button (`TitleBar`):

- **Monitor view** — `FeedOverlay` draws active ROIs and the minimap reconstruction (icon sample / live tracking dot / historical replay trails) over a `<video>` element showing the browser camera stream. To its right: the **Rail** (`Rail.svelte`) — a `RailSection`-based column with expandable `ReadoutRow` entries (click to reveal `CandidateList` with ranked confidence scores) for screen, character, kart, course, and costume; a `RaceSection` showing dynamic lap splits and total time; and a collapsible `EventLog`.
- **Edit mode** — `EditMode.svelte` hosts a top-strip `ScreenGraph` navigator (pan/zoom, click-to-select), a `RoiCanvas` (zoomable, drag-to-resize ROI handles, engine frame as background), and a `ToolsPanel` with Detection and Readout tabs. The Detection tab contains `DetectionTree` (boolean-tree AND-groups / OR-regions editor) and `RegionInspector` (live crop vs stored template side-by-side). The Readout tab provides `ReadoutRoiEditor` with inline per-item template capture.

**Settings modal** (`SettingsModal.svelte`) — `SourceCheck` (dual browser+Python feed preview), `DeviceSelectors` (video + audio device dropdowns), and `LanguageSelectors` (application language + Switch-system language). First-run wizard follows Language → Camera → Done; returning-user re-run shows all on one screen.

A bottom `StatusBar` shows connection dot, live screen name + confidence score, fps, and capture resolution. All components use tabular-figures monospace for numeric readouts. The engine-frame poll pauses while the monitor view is active (browser stream renders directly); ROI canvas redraws are rAF-coalesced inside `RoiCanvas.svelte`.

### Config (`config/`)
`Defaults` dataclass defines all ~60 constants. `Settings` loads from `config` table (falls back to defaults). `settings.update(key, value)` writes to DB; `settings.reload(keys)` hot-reloads affected trackers.

## Key Data Files

| Path | Purpose |
|------|---------|
| `mkw_tracker.db` | SQLite: config, replays, minimap seeds/ROIs/thresholds |
| `images/screens/*.png` | Screen detection templates |
| `images/characters/*.png` | Character name templates |
| `images/costumes/*.png` | Costume name templates (edge-based) |
| `images/karts/*.png` | Kart name templates |
| `images/courses/*.png` | Course name templates |
| `images/timestamps/cropped/0.png`–`9.png` | Digit templates |
| `images/mushrooms/1mush.png`–`3mush.png` | Mushroom count templates |
| `images/heads/<player>.png` | Head images for replay bubbles (BGRA) |

## Important Constants

All ROI coordinates are **full 1080p pixels**. `DISPLAY_SCALE = 720/1080` applied for 720p label drawing. Minimap tuning knobs prefixed `_MM_`. All constants are also in the `config` table and listed in `docs/config-reference.md`.
