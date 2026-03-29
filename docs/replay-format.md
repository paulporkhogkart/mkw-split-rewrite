# Replay Format

`.mkwreplay` is the wire format for server upload and manual file sharing.

## JSON Schema (version 1)

```json
{
  "version": 1,
  "course": "Mario Circuit",
  "player": "PlayerName",
  "character": "Mario",
  "costume": "Pro Racer",
  "kart": "Standard Kart",
  "total_time": "2:34.567",
  "recorded_at": "2026-03-28T14:55:00Z",
  "points": [
    [0,    1542.3, 678.1, 0.997],
    [16,   1543.1, 677.8, 0.994],
    [32,   1544.0, 677.5, 0.991]
  ]
}
```

## Fields

| Field | Type | Description |
|---|---|---|
| `version` | int | Schema version (currently 1) |
| `course` | string | Course name |
| `player` | string | Player name (`"me"` for local runs) |
| `character` | string \| null | Selected character |
| `costume` | string \| null | Selected costume |
| `kart` | string \| null | Selected kart |
| `total_time` | string \| null | Formatted total time `"M:SS.mmm"`, or null if aborted |
| `recorded_at` | string | ISO 8601 UTC timestamp |
| `points` | array | Time-series minimap positions |

## Point Format

Each point is a 4-element array: `[t_ms, cx, cy, score]`

| Index | Type | Description |
|---|---|---|
| 0 | int | Elapsed race time in milliseconds |
| 1 | float | X position in full 1080p pixels |
| 2 | float | Y position in full 1080p pixels |
| 3 | float | NCC match score (0.0–1.0) |

## Export

Use the IPC command `{"type":"export_pb","course":"<course name>"}` to receive the payload as a `pb_export` event. The `mkwreplay` field contains the full dict above.
