# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Mario Kart World (MKW) real-time race telemetry tracker.** Captures a 1920×1080 @ 60fps OBS camera feed and uses OpenCV template matching to detect game screens, track player selections, and record race data (laps, coins, timestamps, minimap position). Designed to integrate with a Tauri frontend via stdio sidecar IPC.

## Repo Surfaces

This repo is **four surfaces**, not one. Most of this file documents surface 1 (the desktop
engine); the others have their own `CLAUDE.md` where noted.

| Surface | Path | What it is |
|---------|------|-----------|
| **Desktop app "pbenguin"** | `mkw_tracker/` (Python engine) + `src/`, `src-tauri/` (Tauri v2 + Svelte shell) | The capture/detection client. **Pure detector**: the engine emits `run_finalized` over stdio; Rust does all networking. Product name is `pbenguin` (`src-tauri/tauri.conf.json`); the npm package id is still `mkw-tracker`. Opt-in background WR service (tray-only autostart, settings > Background) replays mkwrs WR videos through a throwaway engine and uploads trails — see docs/superpowers/specs/2026-07-17-wr-service-tray-background-design.md. |
| **Pi server** | `pi/` (Node/TS, Hono + `node:sqlite`, run via `tsx`) — see `pi/CLAUDE.md` | **Canonical source of truth**, runs on a Raspberry Pi. Hosts the token-gated API, Discord bot, WR scraper, stats/`/explorer`, presence/activity. |
| **Website thekartoff.com** | `web/` (Vite + Svelte SPA) — see `web/CLAUDE.md` | Public site, **served by the Pi**. Live cards, Turf (territory) map, activity feed. Imports shared components from the desktop `src/`. |
| **Schema + importer** | `server/` (Python) | Owns the canonical DB DDL `server/schema.sql` (which the Pi loads at boot) and the legacy-data importer (`python -m server.importer`). NOT an HTTP server. |
| **Pork Phone hotline** | `hotline/server/` (Python asyncio, own venv + SQLite + systemd unit on the Pi host) | Viewer call-in show: browser mic (20 ms 8 kHz PCM over WSS via the tunnel) ⇄ Asterisk (ARI + AudioSocket) ⇄ ATA ⇄ Paul's Telecom 802 rotary. **Zero imports from `pi/`**; own subdomain `phone.thekartoff.com`. Spec `docs/superpowers/specs/2026-07-12-pork-phone-hotline-design.md`; Paul's real-world setup steps in `hotline/RUNBOOK.md`. Tests: `cd hotline/server && python -m pytest`. Dev echo mode: `HOTLINE_ECHO=1 python -m hotline` → `http://127.0.0.1:9100/test`. |

**Data flow:** desktop engine → Rust upload → `POST /v1/runs` on the Pi → Pi's SQLite (`runs`,
`run_laps`, `run_trails`, …) → served to the website + Discord bot. The desktop app's own
`mkw_tracker.db` holds only config + minimap detection tuning (race data moved to the Pi).
Deploy is pull-based off git **tags** (`deploy/update.sh`); see `docs/pi-deploy.md`.

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

`Screen` is an Enum. Each `Tell` is a **boolean tree**: `tell.groups` is a list of groups (ANDed) where each group is a list of `Region`s (ORed) — a screen matches when every group matches and a group matches when any region matches (`detect_tell` = `min` over groups of `max` over regions ≥ `match_threshold`). A `Region` is `kind="template"` (grayscale `TM_CCOEFF_NORMED` over a ±`search_pad` window) or `kind="dark_loading"` (crush-invariant dark-ROI statistical match; an optional `icon_roi` must additionally hold a bright **colourful** item — the loading mascot is a varying animated item (mushroom, fire flower, ...) so it can't be template-matched, but it's always saturated colour while dark Switch system screens are grayscale in that corner). The RESET family ANDs its icon group with two icon-less dark groups (top-left, top-right) so the whole frame must be dark — together these reject the Switch 2 boot logo, the user-select screen, and dark race sections with minimap lines in the icon box. Template images live in `images/screens/`. User edits persist as one `tell_tree_<SCREEN>` JSON blob per screen (config table); schema v3 (`database/tell_repo.py`) migrates the legacy `tell_roi_/tell_alt_/tell_req_also_*` keys. A `NO_SIGNAL` screen detects the capture card's "no signal" graphic (grayscale text-region template; Elgato/UGREEN presets auto-selected from the `camera_device` name, editable in Edit Screens with "revert to auto"). It is a universal detection candidate (added in `_candidate_screens()`, scanned only on a confirm-miss); entering it tears down like an app restart - silently discards the active run (no `run_finalized`) and clears all selections.

### Selection Tracking (`detection/selection.py`)
Scans ROIs at 10Hz max. All four categories (characters, karts, courses, costumes) match the same way: a full-ROI grayscale crop is Canny-edged (`prepare_text_edges`, background-agnostic) and slid over a live crop padded by `SELECTION_SEARCH_PAD`, scored with `TM_CCOEFF_NORMED` (`match_variants`). Edges strip the shared name-plate background that made plain-grayscale cross-scores between similar names (Mario/Wario) sit at ~0.89; on edges they fall to ~0.5-0.66, so margins are 0.3-0.5 and similar names no longer nearly tie. Templates are rebuilt from `captures/` via `scripts/gen_selection_templates.py`. **Costumes** carry synthetic background variants (`<item>__bgdark/bgbright/bgsplit.png`) because their name banner's background varies (bright/dark/split); `match_variants` takes the best variant, so a costume scores high whatever the live background does (it never *misreads*, only under-scores - hence its low `SELECTION_COSTUME_FLOOR=0.30`). A confident character match commits immediately (the old multi-frame "pending" confirmation was dropped once edges made the character signal strong); `COSTUME_LOSS_FRAMES=4` (~0.4s) consecutive misses before a costume clears to Base. `SELECTION_RECONFIRM_THRESHOLD=0.80` holds the current selection only while it still scores above the worst cross-score, so a switch is never sticky.

### Race Telemetry (`race/`)
All trackers run during `RACING` screen only, at 10Hz. `TimestampTracker` uses burst capture (3 consecutive scans, 2 identical required) triggered by a lap crossing or the final-finish event. The **final finish** is detected by `FinishStillDetector` (`race/finish.py`): on the final lap (`current_lap == total_laps`) the timer freezes on the total time with no gold/white flash, so a masked frame-diff of the bright digit pixels stays still for `STILL_SECONDS`. (The old `FinishDetector` 1st/2nd/3rd position-ROI scan is kept in code but disabled — re-enable by uncommenting `finish.update(...)` in `main.py` + restoring the HUD `finish` ROI.) The mushroom counter (`race/mushrooms.py`) matches grayscale templates (cropped from `old_assets/*mush.png`) with `search_pad`, not binary.

### Minimap Tracking (`minimap/tracker.py`)
`seed()` snaps the stored per-course seed onto the live ring (annulus NCC) and locks a **badge template** (`minimap/badge.py`): a 44×44 masked Lab crop of the face + white ring. Per frame: HoughCircles finds the ring in the search window → the badge template slides ±8px around it (masked `TM_CCOEFF_NORMED`, gain/offset-invariant — survives HDR washout) → the correlation **argmax** is published, so Hough centre wobble never reaches the UI/recorder. Score gates: accept 0.45 (wrong-target reject) / confident 0.65 (auto-calibrated per course+character+costume by `calibrate_from_race`). Jump gate (40px) → re-acquire (4 frames) → LOST after 36 misses. The TT ghost has no ring and is rejected structurally. Measurement harness: `temp/mm_lab.py` (regression numbers in the 2026-06-11 design spec).

### Race Lifecycle (`lifecycle/race.py`)
`RaceLifecycle.on_screen_change()` drives all start/pause/resume/finalize transitions. Attached to `ScreenDetector.on_screen_change`.

### IPC (`ipc/`)
`IpcServer` runs asyncio in a daemon thread. Reads newline-delimited JSON from stdin into `inbound_queue`; main loop drains queue once per frame. Outbound events written to stdout. See `docs/ipc-protocol.md`. Tell editing is region-indexed: `update_region` / `add_region` / `remove_region` / `add_group` / `remove_group` / `capture_region_template` / `test_region` / `get_region_images` (all keyed by `screen`+`group`+`region`), plus `reset_tell` (one screen → defaults) and `reset_roi` (one selection/HUD config ROI → default). Calibration IPC remains in the backend but is unused (no UI).

### Frontend (`src/App.svelte` + `src/lib/` + `src/components/`)
`App.svelte` is the thin shell: `tracker-event` IPC handler, Svelte store mirrors, view router (`"monitor"` | `"edit"`), editor event forwarding, and camera/wizard logic. Shared modules in `src/lib/`: `ipc.js` (fire-and-forget `send()`), `stores.js` (writable stores for all backend state), `palette.js` (design tokens), `format.js` (score formatting / labels), `graph.js` (screen-graph layout + pan/zoom math), `overlay.js` (canvas drawing functions).

Two top-level views, toggled by a title-bar button (`TitleBar`):

- **Monitor view** — `FeedOverlay` draws active ROIs and the minimap reconstruction (icon sample / live tracking dot / historical replay trails / WR ghost dots — grey, the current WR pulsing) over a `<video>` element showing the browser camera stream. To its right: the **Rail** (`Rail.svelte`) — a `RailSection`-based column with expandable `ReadoutRow` entries (click to reveal `CandidateList` with ranked confidence scores) for screen, character, kart, course, and costume; a `RaceSection` showing dynamic lap splits and total time; and a collapsible `EventLog`.
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

**Chip sprite-sheet pack:** built by `tools/asset_matte/build_site_pack.py`, delivered as GitHub
Release assets pinned by `web/chips.lock`, served at `/chips/anim/` — see
`docs/superpowers/specs/2026-07-18-chip-site-pack-design.md`.

## Important Constants

All ROI coordinates are **full 1080p pixels**. `DISPLAY_SCALE = 720/1080` applied for 720p label drawing. Minimap tuning knobs prefixed `_MM_`. All constants are also in the `config` table and listed in `docs/config-reference.md`.
