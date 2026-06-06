# Phase 2d — Engine race-store teardown Implementation Plan

> Executed inline. Spec: `docs/superpowers/specs/2026-06-06-server-phase2-read-migration-design.md` §2d. MinimapPlayer removed entirely (user decision).

**Goal:** Remove the engine's race-data tier now that the monitor reads from the server (2a-2c). Engine keeps detection config (minimap seeds/ROIs/thresholds), the live recorder (points for overlay + `run_finalized` upload), and calibration.

**Removals:**
- `minimap/player.py` — **deleted** (MinimapPlayer; engine no longer plays ghosts — frontend draws server trails).
- `tests/test_pb_splits.py`, `tests/test_replay_paths.py` — **deleted** (covered removed functions).
- `database/replay_repo.py` — remove `save_run`, `_maybe_update_pb`, `_prune_history`, `get_pb`, `get_pb_splits`, `get_history`, `get_friends_pbs`, `_load_replay`, `export_mkwreplay`, `save_friend_pb`, `replay_paths` + the now-unused `import json` / `from .config_repo import get_config`. Keep `_to_ms` + the minimap seed/ROI/threshold get/set.
- `minimap/recorder.py` — remove the `save` method + `from ..database.replay_repo import save_run`. Keep start/update/points/pause/resume/stop/_elapsed_ms/retroactive_filter.
- `lifecycle/race.py` — remove the `MinimapPlayer` import, the `mm_player` + `history_mode` ctor params, `self._mm_player`/`self._history_mode`, every `self._mm_player.*` use (pause/resume/start/stop/load), and the `self._mm_rec.save(...)` call in `_finalize_recording`. Keep `mm_rec` (recorder), calibration, the `run_finalized` emit.
- `main.py` — remove the `export_mkwreplay` import, `emit_pb_export`/`emit_replay_paths` from the protocol import, the `MinimapPlayer` import, the `mm_player` construction, the `mm_player=`/`history_mode=` lifecycle args, the `export_pb`/`get_replay_paths`/`get_pb_splits` IPC handler branches, and the `--history` argparse. Keep `get_minimap_sample` + `emit_minimap_sample`.
- `ipc/protocol.py` — remove `ExportPbCmd`, `emit_pb_export`, `emit_pb_splits`, `emit_replay_paths`. Keep minimap emits.
- `src/App.svelte` — remove the now-dead `pb_splits` / `replay_paths` tracker-event handler cases (engine no longer emits them).
- `database/migrations.py` — drop the `replays`/`replay_points` CREATE+indexes from `_SCHEMA_V1` and make `_SCHEMA_V4` a no-op (keep the v4 version bump). Fresh DBs are clean; existing DBs keep the now-unused tables (no destructive DROP migration).

**Test updates:**
- `tests/test_race_lifecycle_finish.py` — drop `mm_player=` from the lifecycle build + the `_mm_rec.save`/`_mm_player.stop` assertions; assert `lc._finalized is True` instead (finalize ran without crash).
- `tests/test_run_finalized.py` — unchanged (uses `MinimapRecorder()`/`.points`, both kept).

**Verify:** full engine suite `python -m pytest tests/ -q` green; `npm run check` 0/0 + `npm run build` (App.svelte handler removal). ff-merge to main.
