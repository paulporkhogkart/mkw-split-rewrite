# UI Theme — Neutral Graphite (OBS/Resolve-style), v2

Living token reference for the Tauri frontend. The UI is a **native professional monitor**
for diagnosing ROI-tracking health: neutral, flat, conventional widgets; color is **functional
only**; numbers use the UI sans with **tabular figures** (no decorative monospace in chrome).
Defined in `src/theme.css`; mirrored for canvas/SVG in `src/lib/palette.js`. Full rationale:
`docs/superpowers/specs/2026-06-01-frontend-redesign-monitor-design.md`.

> If this table and `theme.css` disagree, **`theme.css` wins** — update this file to match.

## Tokens (`:root`)

### Surfaces & borders
| Token | Value | Use |
|---|---|---|
| `--bg` | `#1b1c1e` | app background |
| `--panel` | `#202023` | section-header bands · title bar · status bar |
| `--panel-2` | `#26272b` | nested surfaces · graph nodes · active/open row |
| `--raised` | `#2e2f33` | hover / pressed control background |
| `--well` | `#161718` | recessed candidate-expansion well |
| `--bd` | `#34353a` | hairline border |
| `--bd-soft` | `#27282b` | subtle internal divider |
| `--feed-bg` | `#0b0c0e` | camera-preview / canvas (near-black) |
| `--track` | `#303135` | thin meter / progress-bar track |

### Text
| Token | Value | Use |
|---|---|---|
| `--tx` | `#d9dadd` | primary |
| `--tx-mut` | `#9a9ca1` | secondary / labels |
| `--tx-dim` | `#6b6d73` | tertiary / disabled / hints |

### Accent — the only decorative color (active / selected / primary action)
| Token | Value | Use |
|---|---|---|
| `--accent` | `#3d7cc2` | active · selected · primary button |
| `--accent-soft` | `#2d5e94` | pressed / darker |
| `--accent-bg` | `#26303c` | accent-tinted fill |

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
| `--ui` | `'Segoe UI', system-ui, -apple-system, sans-serif` | **all chrome, incl. numbers** |
| `--mono` | `'Cascadia Code', Consolas, ui-monospace, monospace` | rare; avoid in chrome |

**Rule:** one UI sans everywhere. Numbers align via `font-variant-numeric: tabular-nums`
(set globally on `body`) — **no monospace in chrome** (scores, race data, status bar, event log).

### Geometry
| Token | Value | Use |
|---|---|---|
| `--r` | `4px` | panels · cards · buttons · inputs |
| `--r-sm` | `2px` | chips · meter bars · small controls |
| — | `50%` | status dots (round LED — keep) |

## Functional color rules
- `scoreColor(v)` (`lib/format.js`): `≥0.8 → --ok`, `≥0.5 → --warn`, else `--err`. Applied to the
  **score number** in readout rows (no per-row bar at rest); confidence bars appear only in an
  expanded candidate list.
- `statusDot`: disconnected `--idle`, alive `--ok`, stale/warming `--warn`.
- **Minimap** (`lib/palette.js`, drawn on canvas — mirrors `overlay/minimap.py`): ring+face
  (tracking) yellow, ring-only orange, reacquire yellow-ish; replay trails use muted distinct hues.
- **ROI editor overlay** (canvas): active region `--accent` · sibling-in-group neutral grey
  (`#82858b`) · other-group `--warn` · handles `--accent`.
- **Active ROI tags on the live feed**: `--ok` (matching) / `--accent` (the detection tell).
