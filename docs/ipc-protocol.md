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
