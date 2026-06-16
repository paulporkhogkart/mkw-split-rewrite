# NO_SIGNAL screen detection — design

- **Date:** 2026-06-16
- **Status:** Design approved (pending written-spec review)
- **Topic:** Detect a capture card's "no signal" screen as a first-class screen state; on entry, tear down like an app restart (clear selections, silently discard any active run). Auto-pick the preset template from the capture-card device name; editable like any other screen.

## Goal

Add a `NO_SIGNAL` screen that fires when the capture card shows its "no signal" graphic (a dark screen with the card's text/logo). Entering it is treated like an app restart: the tracker drops to the unknown state, clears all current selections, and **silently discards** any in-progress run (nothing saved, nothing queued for review). It is checked essentially always (reachable from every screen) but cheaply. The active template is chosen automatically from the selected video-input device name, unless the user has hand-edited the screen, with a one-click "revert to auto".

## Reference images (provided)

- `temp/nosignal.png` — **Elgato**, 1920×1080, near-black background with white "NO SIGNAL" text + elgato logo.
- `temp/nosignal2.png` — **UGREEN**, **2560×1440** (must be downscaled to 1080p), pure-black background with yellow "无信号 / No Signal" text.

Device names (confirmed by user): Elgato enumerates as `Elgato 4K X`, UGREEN as `UGREEN 25773`. Brand substring is reliable, so the device→preset map is a plain case-insensitive `contains`:

```
NO_SIGNAL_DEVICE_HINTS = {"elgato": ["elgato"], "ugreen": ["ugreen"]}
```

## Approaches considered

- **A — Template-match the card's text/logo region (CHOSEN).** A normal `template` Region (grayscale `TM_CCOEFF_NORMED` + `search_pad`) over a tight ROI on the distinctive text. Reuses the entire existing screen-tell stack (Edit-mode editing, in-app capture, persistence, graph node). Robust and specific: bright text on black correlates strongly; a textless dark/loading frame scores ~0, so it never collides with the RESET `dark_loading` family.
- **B — Dark-frame heuristic (rejected).** Fire on any near-black/blank frame. Rejected: the codebase has repeatedly fought dark-screen false positives (the RESET `dark_loading` chroma-gate work); it cannot tell "no signal" from loading/fades/dark race sections; and it ignores the per-card-template framing.
- **C — Hybrid (A + a "rest of frame is dark" group).** Extra robustness, but the text template alone is already highly specific. Not built now; the dark group can be added later if false positives appear.

## Locked decisions

1. New `Screen.NO_SIGNAL`; detection via Approach A (template on the card's text region).
2. Two shipped preset templates (`nosignal_elgato.png` default, `nosignal_ugreen.png`), cut by a new gen script; UGREEN downscaled 1440→1080 first.
3. Always a detection candidate via a `_candidate_screens()` augmentation (not per-screen `TRANSITIONS` edges). From `NO_SIGNAL`, candidates == the `UNKNOWN` reachable set (re-detect from scratch on signal return). Negligible cost.
4. **Auto mode** (no persisted `tell_tree_NO_SIGNAL`): the engine picks the preset from the configured `camera_device` name (case-insensitive substring, first match wins; no match → keep the baked **Elgato** default). The swap is in-memory only, never persisted, so it re-derives each launch. Runs on startup and on camera-device change.
5. **Manual mode** (a `tell_tree_NO_SIGNAL` override exists, written by any Edit-mode edit/capture): auto no longer touches it.
6. **"Revert to auto"** for the `NO_SIGNAL` node: deletes the override and re-runs auto-selection.
7. On entering `NO_SIGNAL`: a branch at the **top** of `RaceLifecycle.on_screen_change()` that returns early — `_clear_race_state()` (no `_finalize_recording`, so **no** `run_finalized` emit → silent discard) + `SelectionTracker.reset()` + disarm any ghost.
8. No Settings UI. Everything lives in **Edit Screens**.

## Design

### 1. Screen, tell, and assets — `detection/screen.py`, `scripts/`, `images/`, `screenshots/`

- Add `NO_SIGNAL = auto()` to the `Screen` enum.
- Add preset and device-hint tables:
  ```python
  NO_SIGNAL_PRESETS = {
      "elgato": {"image_path": "images/screens/nosignal_elgato.png", "roi": (<from gen script>)},
      "ugreen": {"image_path": "images/screens/nosignal_ugreen.png", "roi": (<from gen script>)},
  }
  NO_SIGNAL_DEVICE_HINTS = {"elgato": ["elgato"], "ugreen": ["ugreen"]}
  ```
- Add a `Tell` for `NO_SIGNAL` to `TELLS`, defaulting to the **Elgato** preset's `image_path` + `roi`, `search_pad≈8`, `match_threshold≈0.65` (verified against both reference images during implementation; the margin to a textless frame is large, so the exact value is non-critical but must reject RESET/racing frames).
- Add `Screen.NO_SIGNAL: "nosignal.png"` to `GRAPH_NODE_SHOTS` only (not `SCREENSHOT_FILES`, which is one-screenshot-per-screen and feeds `gen_grayscale_templates.py`; NO_SIGNAL has two preset sources — mirrors how RESET is in `GRAPH_NODE_SHOTS` only).
- `scripts/gen_nosignal_templates.py` (new): reads `temp/nosignal.png` and `temp/nosignal2.png`; downscales UGREEN to 1920×1080; for each, computes the padded bounding box of bright text, crops, grayscales, and writes `images/screens/nosignal_{elgato,ugreen}.png`; also writes `screenshots/en_uk/nosignal.png` (full Elgato frame) for the graph node; prints both ROIs to bake into `NO_SIGNAL_PRESETS`.

### 2. Always-checked wiring + performance — `detection/screen.py`

Augment `ScreenDetector._candidate_screens()`:
- When `current_screen == NO_SIGNAL`: return a copy of `TRANSITIONS[UNKNOWN]` (re-detect everything on signal return).
- Otherwise: existing logic (incl. the HOME special-case), then add `Screen.NO_SIGNAL` to the returned set (it is a universal candidate). Do not add it when it is already the current screen.

Cost: `NO_SIGNAL` is never evaluated while the current screen keeps re-confirming (Phase 1). It is matched only (a) on the first frame the current screen fails to confirm (Phase 2 already runs then) — one extra template match — and (b) as the cheap Phase-1 re-confirm while sitting on `NO_SIGNAL`. No new per-frame full-frame scan. A mid-RACING signal drop is caught on the first lost frame (RACING tell fails → Phase 2 → `NO_SIGNAL` matches). Pure-black frames before the graphic appears keep the current screen (nothing matches) until the graphic shows — acceptable few-frame delay.

New detector methods:
- `set_nosignal_region(preset_name: str) -> Optional[dict]`: look up `NO_SIGNAL_PRESETS[preset_name]`, rewrite the `NO_SIGNAL` tell's single region `image_path` + `roi`, reload its template. In-memory only (caller decides whether to persist).
- module fn `auto_nosignal_preset(device_name: str) -> Optional[str]`: lowercase the name, return the first preset key whose hint substring is contained, else `None`.

### 3. Auto / manual / revert orchestration — `mkw_tracker/main.py`

- Helper `_apply_nosignal_auto(settings, detector, ipc)`:
  - Determine mode: `manual` iff `_get_config_direct("tell_tree_NO_SIGNAL")` is truthy; else `auto`.
  - If auto: `preset = auto_nosignal_preset(settings.get("camera_device", "") or "")`; `detector.set_nosignal_region(preset or "elgato")` (no persist); emit mode status with `brand = preset` (or `None` = "Elgato default").
  - If manual: do nothing to the tell; emit mode status `manual`.
- Call sites:
  - **Startup:** after the existing `tell_tree_*` blob load loop, call `_apply_nosignal_auto(...)`.
  - **Camera-device change:** in the `open_camera` handler (both the main-loop drain and the `cam_paused` drain), after the device opens, call `_apply_nosignal_auto(...)`.
  - **New IPC `reset_nosignal_auto`:** `delete_configs_like("tell_tree_NO_SIGNAL")`, rebuild the NO_SIGNAL tell from the hardcoded default (`detector.reset_tell("NO_SIGNAL")`), then `_apply_nosignal_auto(...)`, then `emit_tells_list(...)`.
  - **After any NO_SIGNAL edit:** the existing `update_region` / `add_region` / `remove_region` / `add_group` / `remove_group` / `capture_region_template` handlers already persist `tell_tree_NO_SIGNAL` via `_persist_tell_tree`; when `screen == "NO_SIGNAL"`, also emit mode status `manual` so the badge flips.

No new stored config key — mode is derived from `tell_tree_NO_SIGNAL` presence and the auto choice is derived from the device name.

### 4. Lifecycle teardown (silent discard) — `lifecycle/race.py`, `detection/selection.py`, `main.py`

- `RaceLifecycle.on_screen_change()`: a branch at the **very top** (after the transition print + `emit_screen_change`), before the `old == RACING` handling:
  ```python
  if new == Screen.NO_SIGNAL:
      self._paused_from_racing = False
      self._resuming_race = False
      if self._ghost.armed or self._ghost.recording:
          self._ghost.disarm()
          self._emit_ghost_state()
      self._clear_race_state()     # resets trackers + emits race_cleared; NO finalize
      self._selection.reset()      # clears SelectionState
      return
  ```
  The early `return` guarantees `_finalize_recording` never runs, so no `run_finalized` is emitted — the run is silently discarded.
- `SelectionTracker.reset()` (new): set `self.state = SelectionState()`, clear `_relevant_costumes`, `_costume_loss_streak`, and the four `_*_scores` maps.
- **Making the cleared selection visible:** the main-loop selection emit currently guards on `any(sel_key)`, so an all-None clear is suppressed and `_prev_sel` goes stale. Widen the guard to:
  ```python
  if sel_key != _prev_sel and (any(sel_key) or any(_prev_sel)):
  ```
  This emits the all-None `selection_update` exactly once and keeps `_prev_sel` in sync. The frontend `selection_update` handler must clear its readouts when all four fields are null (verify/adjust in `App.svelte` + `stores.js`).

### 5. Frontend — `src/App.svelte`, `src/components/ToolsPanel.svelte`, `src/lib/stores.js`

- `App.svelte`:
  - Add `"NO_SIGNAL"` to `SCREEN_NAMES` (so it is an editable graph node); add `NO_SIGNAL: "No Signal (Capture Card)"` to `SCREEN_LABELS` and a one-line `SCREEN_HINTS` entry.
  - `resetDetection()`: when `selectedNode === "NO_SIGNAL"`, send `{ type:"reset_nosignal_auto" }` instead of `{ type:"reset_tell", ... }`.
  - Handle a new `nosignal_mode` tracker-event → a store; ensure `selection_update` with all-null clears the selection stores.
- `ToolsPanel.svelte` (Detection tab): for the NO_SIGNAL node, show an **Auto / Manual** badge (e.g. "Auto · matched Elgato", "Auto · Elgato default (no card match)", or "Manual (custom)") and relabel the reset control to **"Revert to auto"**. Other screens keep "reset to default".
- `screenLabel("NO_SIGNAL")` already renders "No Signal" in the status bar and rail — no change to `format.js`.

### 6. IPC — `ipc/protocol.py`, `docs/ipc-protocol.md`

- Inbound (dispatched in `main.py` by `type`, no `protocol.py` change needed for parsing): `reset_nosignal_auto`.
- Outbound: `emit_nosignal_mode(auto: bool, brand: Optional[str])` builder. Emitted on startup, camera-device change, after a NO_SIGNAL edit, and on `reset_nosignal_auto`.
- Document both in `docs/ipc-protocol.md`.

### 7. Docs

- `docs/ipc-protocol.md` — the new command + event (above).
- `docs/config-reference.md` — note NO_SIGNAL is auto-selected (no new key).
- `CLAUDE.md` (Screen Detection section) — short paragraph: NO_SIGNAL screen, auto-template-by-device-name, silent-discard teardown.

## Files touched (summary)

| File | Change |
|------|--------|
| `mkw_tracker/detection/screen.py` | `NO_SIGNAL` enum; `NO_SIGNAL_PRESETS`/`NO_SIGNAL_DEVICE_HINTS`; NO_SIGNAL `Tell`; `GRAPH_NODE_SHOTS` entry; `_candidate_screens()` augmentation; `set_nosignal_region()`; `auto_nosignal_preset()` |
| `mkw_tracker/detection/selection.py` | `SelectionTracker.reset()` |
| `mkw_tracker/lifecycle/race.py` | top-of-`on_screen_change` NO_SIGNAL discard branch |
| `mkw_tracker/main.py` | `_apply_nosignal_auto()`; startup + camera-change calls; `reset_nosignal_auto` handler; NO_SIGNAL-edit mode emit; widen selection emit guard |
| `mkw_tracker/ipc/protocol.py` | `emit_nosignal_mode()` |
| `scripts/gen_nosignal_templates.py` | new — cut both preset templates + graph screenshot, print ROIs |
| `images/screens/nosignal_elgato.png`, `images/screens/nosignal_ugreen.png` | new template assets |
| `screenshots/en_uk/nosignal.png` | new graph-node reference |
| `src/App.svelte` | SCREEN_NAMES/LABELS/HINTS; `resetDetection` special-case; `nosignal_mode` handling; selection-clear on all-null |
| `src/components/ToolsPanel.svelte` | Auto/Manual badge + "Revert to auto" label for NO_SIGNAL |
| `src/lib/stores.js` | `nosignal_mode` store; selection stores clear on all-null |
| `docs/ipc-protocol.md`, `docs/config-reference.md`, `CLAUDE.md` | docs |

## Testing & validation

- **Detection (pytest):** with the Elgato preset, the NO_SIGNAL tell matches `temp/nosignal.png`; with the UGREEN preset (downscaled), it matches `temp/nosignal2.png`; it does **not** match a RESET/loading fixture or a racing frame.
- **Detector candidates:** `NO_SIGNAL` is present in `_candidate_screens()` from a sample of screens; from `NO_SIGNAL`, candidates equal `TRANSITIONS[UNKNOWN]`.
- **Auto-selection:** `auto_nosignal_preset("Elgato 4K X")=="elgato"`, `auto_nosignal_preset("UGREEN 25773")=="ugreen"`, unknown name → `None`; `_apply_nosignal_auto` swaps the region in auto mode and is a no-op in manual mode (with `tell_tree_NO_SIGNAL` present).
- **Revert to auto:** after a simulated manual edit (override present), `reset_nosignal_auto` removes the override and re-derives the region from the device name.
- **Lifecycle (pytest):** RACING → NO_SIGNAL emits `race_cleared`, resets selections, and emits **no** `run_finalized`; an armed/recording ghost is disarmed.
- **Selection clear:** the widened emit guard emits one all-null `selection_update` on clear.
- **Frontend:** `svelte-check` 0/0 and a production build green.

## Non-goals / out of scope

- Detecting a **true device disconnect** (no frames at all, as opposed to the card's "no signal" graphic) — handled by existing camera-status plumbing.
- The hybrid "rest of frame is dark" group (Approach C) — only if false positives appear.
- Any Settings-modal UI; per-language NO_SIGNAL templates (the graphic is card-, not game-language-specific).

## Risks / watch-items

- **Threshold tuning:** confirm `match_threshold` rejects RESET/loading and racing frames while accepting both reference graphics; tighten or add the §C dark group only if needed.
- **`TM_CCOEFF_NORMED` on mostly-black templates:** the ROI must be centered on the text (non-zero variance); the gen script's bright-text bbox handles this.
- **Selection-clear frontend path:** verify the `selection_update` all-null case actually empties the rail readouts (the engine never emitted all-null before).
- **Device-name source:** auto matches the configured `camera_device` DirectShow friendly name; confirm it is populated before `_apply_nosignal_auto` runs at startup.
