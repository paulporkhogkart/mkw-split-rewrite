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
| `list_tells` | — | Emit `tells_list` (boolean-tree config for every screen) |
| `list_rois` | — | Emit `rois_list` (selection + HUD config ROIs) |
| `update_region` | `screen`, `group`, `region`, `roi?`/`thresh?`/`grayscale?`/`kind?`/`icon_roi?` | Mutate one detection region; persists `tell_tree_<SCREEN>`; emits `tells_list` |
| `add_region` / `remove_region` | `screen`, `group`(, `region`) | Add/remove an OR region in a group; emits `tells_list` |
| `add_group` / `remove_group` | `screen`(, `group`) | Add/remove an AND group; emits `tells_list` |
| `capture_region_template` | `screen`, `group`, `region` | Crop+save+reload the region's template; emits `template_saved` |
| `test_region` / `get_region_images` | `screen`, `group`, `region` | Score / fetch stored template + live crop; emits `template_score` / `template_images` |
| `reset_tell` | `screen` | Restore one screen's tell (and aliases) to defaults; drops `tell_tree_*`; emits `tells_list` |
| `reset_roi` | `key` (e.g. `char_name_roi`) | Restore one selection/HUD ROI to its packaged default; emits `rois_list` |
| `capture_asset_template` / `get_asset_template` | `category`, `item_name` | Capture / preview a per-item reference image (characters/costumes/karts/courses/mushrooms) |

> **Calibration (below) is disabled.** The backend handlers remain but the UI no longer sends them; image normalization is a no-op. The legacy `update_tell`/`add_alt`/`add_required_also`/`capture_template`/`test_template`/`get_template_images` commands were replaced by the region ops above.

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
| `minimap_update` | `cx`, `cy` (full-frame 1080p px), `radius` (px), `track_state` (`"tracking"` \| `"ring_only"` \| `"reacquire"` \| `"lost"`) — emitted at ≤15 Hz during `RACING` only, only when lock is active (`tracking=True` and `cx`/`cy` are not None), and only when the value changes |
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
