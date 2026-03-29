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
