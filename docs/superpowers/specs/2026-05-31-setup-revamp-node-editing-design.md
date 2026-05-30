# Setup Revamp: Node-Driven Screen Editing + Boolean-Tree Tells

**Date:** 2026-05-31
**Status:** Approved design, pending implementation plan

## Overview

Replace the linear multi-step "Setup" wizard with a node-driven model. The screen
graph becomes the entry point for editing detection: clicking a node opens a focused
editor for that screen, with its detection tell, selection ROIs, HUD ROIs, and
template-capture flows all packaged together. Calibration is removed from the UI.
The underlying tell model is redesigned from three hard-coded slots into a general
boolean tree.

This is primarily a **frontend reorganization** plus a **backend tell-model
refactor**. No new detection *matching* math — the grayscale `TM_CCOEFF_NORMED` +
`search_pad` path and the `dark_loading` statistical detector are preserved exactly;
only the structure that *combines* regions changes.

## Goals

1. Remove calibration from the UI entirely (first-time wizard, rerun wizard, state,
   handlers, markup, IPC sends).
2. First-time setup shrinks to `Language → Camera → Done`.
3. The `⚙` header button opens a **slim Settings panel** (language + camera only).
4. A new **Edit Screens view**: interactive screen graph (left) + tabbed per-node
   editor (right).
5. Clicking a node in the **main-view footer graph** jumps into Edit Screens for
   that node.
6. Redesign the tell model into a **boolean tree** (AND of groups, each group an OR
   of regions), replacing `primary` / `alt` / `required_also` / `dark_loading`.

## Non-Goals

- Backend calibration code (Normalizer, `calib_*` config, `solve_calibration` IPC) is
  **left intact** but unused. Pruning it is a separate, later pass.
- No change to detection matching internals (correlation, binarisation, search_pad,
  dark-loading statistics).
- No nested boolean logic beyond two levels (AND-of-OR / CNF). Deeper trees are YAGNI.

## UI Architecture

Four top-level views (current `view` reactive var, extended):

| View | When | Contents |
|------|------|----------|
| `startup` | before `ready` | unchanged |
| `setup` | first-time (`setup_complete=false`) | `Language → Camera → Done` only |
| `main` | normal | live feed, status sidebar, **footer status graph (now clickable)** |
| `edit` | user opens Edit Screens | split pane: graph + node editor |

Header buttons in `main`:
- `⚙ Settings` → slim Settings panel (language + camera re-selection; the existing
  language/camera step bodies lifted out of the wizard).
- `Edit Screens` → switches to the `edit` view.

The `RERUN_STEPS` array and the `screens` / `selection` / `hud` / `templates` /
`calibration` wizard steps are **deleted**. `FIRST_TIME_STEPS` becomes
`["language", "camera", "done"]`.

### Edit Screens view

Split pane:
- **Left** — interactive screen graph. Reuses `GRAPH_NODES` / `GRAPH_EDGES`, enlarged.
  Nodes are hoverable + clickable; the selected node is highlighted. The live status
  highlighting (active screen, candidate scores) may still render here but is
  secondary to selection.
- **Right** — the node editor for the selected node: a tab bar plus the editor body.

Tabs appear **only for what the node owns**:

| Node | Tabs |
|------|------|
| `CHARACTER_SELECT` | Detection · Selection (char_name, costume) · Templates (characters, costumes) |
| `KART_SELECT` | Detection · Selection (kart_name) · Templates (karts) |
| `COURSE_SELECT` | Detection · Selection (course_name) · Templates (courses) |
| `RACING` | Detection · HUD (lap_current, lap_total, coin_left, coin_right, finish, mushroom) · Templates (mushrooms) |
| all others | Detection only (no tab bar) |

The footer graph in `main` shares the `GRAPH_NODES`/`GRAPH_EDGES` data and stays a
live read-only status display, **except** clicking a node sets the selected node and
switches to the `edit` view.

### Editor tab internals

- **Detection** — the boolean-tree editor (see below). Big live feed with every
  region's ROI drawn; the selected region is drag-editable. Per-region controls:
  threshold slider, Recapture, kind dropdown, delete.
- **Selection / HUD** — ROI drag editing writing to the existing config keys
  (`SELECTION_ROI_CONFIG_KEYS` / `HUD_ROI_CONFIG_KEYS`) via `update_config`. Unchanged
  semantics, re-parented under the node editor.
- **Templates** — the existing template-capture flow (category + item list + capture),
  scoped to that node's categories only.

## Tell Model Redesign: Boolean Tree

### Data model

```
Region:
    kind: "template" | "dark_loading"
    roi: (x1, y1, x2, y2)
    # template kind:
    image_path: str | None
    thresh: int               # binarisation level (binary path); ignored when grayscale
    grayscale: bool = True
    search_pad: int = 6
    template: ndarray          # runtime, loaded from image_path
    # dark_loading kind:
    icon_roi: (x1, y1, x2, y2) | None

Tell:
    screen: Screen
    match_threshold: float = 0.9     # correlation cutoff, per-tell
    groups: list[list[Region]]       # AND of (OR of regions)
```

A tell matches when **every group** matches; a group matches when **any region** in
it matches. This is conjunctive normal form (AND of ORs).

Mapping of current tells:
- Single-region screen → 1 group, 1 region.
- `primary OR alt` (HOME, MAIN_MENU, COURSE_SELECT, RACE_MENU, POST_TIME_TRIAL) →
  1 group, 2 regions.
- `primary AND required_also` (RACING/GHOST/UNKNOWN_RACE_ACTIVE: coin + flag) →
  2 groups, 1 region each.
- `dark_loading` (RESET/GHOST_RESET/UNKNOWN_RESET) → 1 group, 1 region of
  `kind="dark_loading"` carrying both `roi` and `icon_roi`.

### Detection

```
def score_region(frame, region, match_threshold) -> float:
    if region.kind == "dark_loading":
        return 1.0 if _detect_dark_loading(frame, region.roi, region.icon_roi)[0] else 0.0
    return _match_tell(frame, region.roi, region.template,
                       region.thresh, region.grayscale, region.search_pad)

def detect_tell(frame, tell) -> (bool, float):
    if not tell.groups:
        return False, 0.0
    group_scores = [max(score_region(frame, r, tell.match_threshold) for r in group)
                    for group in tell.groups]   # OR within group
    overall = min(group_scores)                  # AND across groups
    return overall >= tell.match_threshold, overall
```

`min(max(...))` generalises the legacy `min`-based AND and `max`-based OR. The
returned score (used for `candidate_scores` display) is the AND-limiting group's best
region score — the same quantity the old code surfaced.

`_match_tell` and `_detect_dark_loading` are unchanged.

### Default tells

The module-level `TELLS` registry in `detection/screen.py` is rewritten directly into
tree form. No migration needed for defaults — only for persisted user overrides.

### Alias propagation

`TELL_ALIAS_GROUPS` is preserved (`RACING → [GHOST, UNKNOWN_RACE_ACTIVE]`,
`RESET → [GHOST_RESET, UNKNOWN_RESET]`). Any structural edit propagates the **whole
tree** (deep copy of `groups`) to alias screens.

## IPC Changes

The `roi_key` scheme (`"primary"` / `"alt"` / `"and_N"`) and the slot-specific ops are
replaced by `(group, region)`-indexed ops. Inbound commands:

| Command | Payload | Effect |
|---------|---------|--------|
| `list_tells` | — | returns trees (`groups` array per screen) |
| `update_region` | screen, group, region, roi?, thresh?, grayscale?, kind?, icon_roi? | mutate one region |
| `add_region` | screen, group, roi? | add an OR alternative to a group |
| `remove_region` | screen, group, region | remove a region (removing the last region removes the group) |
| `add_group` | screen, roi? | add an AND group seeded with one region |
| `remove_group` | screen, group | remove a group |
| `capture_region_template` | screen, group, region | crop+save+reload+rescore (was `capture_template`) |
| `test_region` | screen, group, region | score + template_img + live_crop (was `test_template`) |
| `get_region_images` | screen, group, region | stored template + live crop (was `get_template_images`) |

Selection / HUD / template-asset IPC (`update_config` for ROIs, `capture_asset_template`,
`get_asset_template`, `get_roi_preview`) is unchanged.

Removed (calibration): `get_calibration`, `capture_calib_frame`, `solve_calibration`,
`clear_calib_frames`, `reset_calibration`, and the `calibration_result` inbound handler
in the frontend. (Python handlers may remain; they simply stop being called.)

## Persistence + Migration

Replace the six per-screen keys (`tell_roi_*`, `tell_thresh_*`, `tell_req_also_*`,
`tell_alt_*`, `tell_and_thresh_*`, `tell_alt_thresh_*`) with a **single** serialized
blob per screen:

```
tell_tree_<SCREEN> = [ [ {kind, roi, image_path, thresh, grayscale, search_pad, icon_roi}, ... ], ... ]
```

`_persist_tell_structure` writes the whole tree (canonical + aliases). Startup loads
`tell_tree_<SCREEN>` and rebuilds `Tell.groups`, then calls `tell.load(language)`.

**One-time data migration** (added alongside existing data migrations, e.g. the
`import_json_files` pattern): for each screen, if `tell_tree_<S>` is absent but any
legacy `tell_*` key is present, build the tree from the legacy keys using the mapping
above, write `tell_tree_<S>`, and delete the six legacy keys. Installs with no
overrides need no migration (defaults already ship as trees).

`reset_to_defaults` and the `reset_tell_overrides` path delete `tell_tree_%` (and any
stale legacy keys) and rebuild from the hardcoded `TELLS` trees.

## Calibration Removal (frontend)

Delete from `App.svelte`: the calib state block (`calibStatus`, `calibValues`,
`CALIB_SLOTS`, `calibCapturedSlots`, `calibFitQuality`, `calibError`,
`calibResetTellOverrides`), the `calibration` entries in step arrays + `STEP_LABELS`,
all calibration markup, handlers (`doSolveCalibration`, `doResetCalibration`, calib
frame capture, `_setCalibValue`), the `calibration_result` inbound case, and
`goStep("calibration")` nav links (lines ~1171, ~1983).

## Frontend State Model

Unify the wizard-step cursors (`screenIdx`, `selectionIdx`, `hudIdx`, asset-category
index, `activeRoiKey`) into a single editor model:

```
selectedNode : Screen name | null     // which graph node is open
activeTab    : "detection" | "selection" | "hud" | "templates"
activeRegion : { group, region } | null   // Detection tab: selected region
activeRoi    : config-key | null           // Selection/HUD tab: selected ROI
activeAsset  : { category, item } | null   // Templates tab: selected item
```

The ROI-canvas drag code and template-thumbnail rendering are preserved verbatim and
re-parented; only the source of "what ROI am I editing" changes from step-index to the
model above.

## Testing

- Unit-test `detect_tell` combine logic on synthetic region scores: single / OR / AND /
  AND-of-OR produce the expected `min(max(...))` and boolean result.
- Regression: re-express each default `TELLS` entry as a tree and assert
  `detect_tell` returns identical `(matched, score)` to the legacy implementation on a
  set of representative frames (or, if no frame corpus is available, on constructed
  crops that exercise each branch).
- Migration test: seed legacy `tell_*` keys for a screen, run the migration, assert the
  resulting `tell_tree_<S>` rebuilds to an equivalent `Tell` and legacy keys are gone.
- Frontend smoke: clicking each node opens the correct tabs; footer-node click enters
  the edit view on that node; Settings panel changes language/camera.

## Suggested Phasing

1. **Backend tell tree** — dataclass, `detect_tell`, serialization, mutation methods,
   persistence + migration, tests. Ships behind the existing UI temporarily (old IPC
   ops can be thin shims during transition, or the frontend lands in the same PR).
2. **Frontend Edit Screens** — new `edit` view, interactive graph, tabbed node editor,
   Detection boolean-tree UI, footer-node click-through.
3. **Calibration removal + slim Settings + first-time wizard shrink.**

## Risks

- **App.svelte size** (3363 lines). Keep edits surgical; preserve ROI-canvas/drag code
  verbatim. Consider extracting the node editor into its own component if it grows.
- **Detection regression.** Mitigated by the regression test asserting tree-form
  reproduces legacy detection.
- **Migration correctness** for existing user DBs with tell overrides. Mitigated by the
  migration test.
