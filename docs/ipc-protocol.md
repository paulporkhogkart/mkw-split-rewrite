# IPC Protocol

Messages are newline-delimited JSON on stdio (Python sidecar ↔ Tauri frontend).

## Tauri → Python (commands)

| `type` | Additional fields | Effect |
|---|---|---|
| `update_config` | `key`, `value` | Write to `config` table; hot-reload affected tracker |
| `get_state` | — | Emit full `state` snapshot on next frame |
| `force_screen` | `screen` (Screen enum name) | Call `detector.force_screen()` |
| `toggle_debug` | `enabled` (bool) | Show/hide debug overlay |
| `export_pb` | `course` | Emit `pb_export` event with `.mkwreplay` payload |
| `set_seed` | `course`, `cx`, `cy`, `radius` | Write to `minimap_seeds` table |
| `set_roi` | `course`, `x`, `y`, `w`, `h` | Write to `minimap_rois` table |
| `get_calibration` | — | Echo current `calib_*` values via `calibration_result` (`is_echo=true`) and current capture-slot states via two `calib_capture` events |
| `capture_calib_frame` | `slot` (1..7) | Snapshot the current camera frame into the backend's calibration slot cache; emits `calib_capture` with `captured=true` |
| `solve_calibration` | `reset_tell_overrides` (bool) | Pair every cached capture slot with its matching shipped reference and run `solve_transform` on the pooled patches; persists `calib_*` keys; emits `calibration_result`; clears the slot cache.  When `reset_tell_overrides=true`, also wipes `tell_thresh_*` / `tell_alt_thresh_*` / `tell_and_thresh_*` keys |
| `clear_calib_frames` | — | Empty the slot cache; emits one `calib_capture` event per slot (1..7) with `captured=false` |
| `calibrate_now` | `reset_tell_overrides` (bool) | Legacy single-shot path: solve using the current frame and whichever single shipped reference is available.  Wizard uses `capture_calib_frame` + `solve_calibration` instead |
| `reset_calibration` | — | Restore all `calib_*` keys to defaults; emits `calibration_result` |

## Python → Tauri (events)

| `type` | Key payload fields |
|---|---|
| `screen_change` | `from`, `to` |
| `selection_update` | `character`, `costume`, `kart`, `course` |
| `lap_update` | `current`, `total`, `split` |
| `coin_update` | `coins` |
| `finish` | `result`, `total_time`, `splits` |
| `pb_achieved` | `course`, `time` |
| `pb_export` | `course`, `mkwreplay` (full payload dict) |
| `state` | Full snapshot of all tracker states |
| `calibration_result` | `ok`, `error`, `is_echo`, `gain_r`/`gain_g`/`gain_b`, `offset_r`/`offset_g`/`offset_b`, `gamma`, `fit_quality` (RMSE 0–255, lower is better; <10 great, 10–20 ok, >20 poor). `is_echo=true` for `get_calibration` replies; `false` for fresh auto-fit results |
| `calib_capture` | `slot` (1 or 2), `captured` (bool), `error` (empty unless capture failed) |
| `error` | `message` |

## Example Exchange

```jsonl
// Tauri → Python
{"type":"update_config","key":"selection_scan_interval","value":0.2}

// Tauri → Python
{"type":"export_pb","course":"Mario Circuit"}

// Python → Tauri
{"type":"pb_export","course":"Mario Circuit","mkwreplay":{...}}

// Python → Tauri (screen change)
{"type":"screen_change","from":"CHARACTER_SELECT","to":"KART_SELECT"}
```
