# UI Theme — Neutral Graphite (OBS-style)

Living token reference for the Tauri frontend (`src/App.svelte`). The UI is a **professional
desktop monitor** for diagnosing ROI-tracking health: neutral, flat, conventional widgets;
color is **functional only**. See `docs/superpowers/specs/2026-05-31-ui-restyle-design.md`
for the full rationale, migration map, and scope.

> If this table and the spec ever disagree, **this file wins** — it's the maintained reference.

## Tokens (`:root`)

### Surfaces & borders
| Token | Value | Use |
|---|---|---|
| `--bg` | `#1b1c1e` | app background |
| `--panel` | `#232427` | panels · section headers · title bar |
| `--panel-2` | `#2a2b2f` | nested surfaces · graph nodes |
| `--raised` | `#303135` | hover / active control background |
| `--bd` | `#3a3b40` | hairline borders |
| `--bd-soft` | `#2e2f33` | subtle internal dividers |
| `--feed-bg` | `#0c0d0f` | camera-preview area (near-black) |
| `--track` | `#0e0f11` | meter / progress track |

### Text
| Token | Value | Use |
|---|---|---|
| `--tx` | `#d8d9dc` | primary |
| `--tx-mut` | `#9a9ca1` | secondary / labels |
| `--tx-dim` | `#6b6d73` | tertiary / disabled / hints |

### Accent — the only decorative color (active / selected / primary action)
| Token | Value | Use |
|---|---|---|
| `--accent` | `#3d7cc2` | active · selected · primary button |
| `--accent-soft` | `#2d5e94` | pressed / darker |
| `--accent-bg` | `#26303c` | accent-tinted fill (selected node / active row) |

### Status — functional (tracking health)
| Token | Value | Use |
|---|---|---|
| `--ok` | `#5aa86a` | healthy · high confidence · connected |
| `--warn` | `#c89a3e` | marginal · low confidence |
| `--err` | `#cf5b4e` | fail · error |
| `--idle` | `#56585e` | inactive · no signal · disconnected |
| `--close` | `#c4382a` | window-close hover only |

### Typography
| Token | Value | Use |
|---|---|---|
| `--ui` | `'Segoe UI', system-ui, -apple-system, sans-serif` | chrome · labels · buttons |
| `--mono` | `'Cascadia Code', Consolas, ui-monospace, monospace` | numbers · scores · log · IDs · ROI tags |

**Rule:** sans for UI chrome; **mono only for data**.

### Geometry
| Token | Value | Use |
|---|---|---|
| `--r` | `3px` | panels · cards · buttons · inputs |
| `--r-sm` | `2px` | chips · meter bars · small controls |
| — | `50%` | status dots (round LED — keep) |

## Functional color rules
- `scoreColor(v)`: `≥0.8 → --ok`, `≥0.5 → --warn`, else `--err`.
- `statusDot`: disconnected `--idle`, alive `--ok`, stale/warming `--warn`.
- **ROI editor overlay** (canvas, drawn from a JS palette mirror — canvas can't read CSS vars):
  active `--accent` · sibling-in-group neutral grey `#8a8d93` · other-group `--warn` · handles `--accent`.
- **ROI status tags on the live feed**: `--ok` / `--warn` / `--accent`.
