# Template-Source Full-Screenshot Capture Tool — Design

- **Date:** 2026-06-02
- **Status:** Approved (pending spec review)
- **Branch:** `capture-template-sources`

## 1. Purpose

A standalone dev/test tool that captures **full 1920×1080 screenshots** of every
character, costume, kart, and course by hovering over them in-game on the live
capture-card feed. Each screenshot is auto-labeled using the **existing**
detection system and saved under a filename matching the existing template
convention, so the shots can be processed offline later into new match templates.

This tool only *captures and labels* full frames. It does not crop, threshold, or
otherwise process them — that happens later, by hand/other tooling.

## 2. Goals

- Reuse the existing `ScreenDetector` + `SelectionTracker` for labeling — no new
  matching logic.
- Save **one** screenshot per **character**, per **distinct costume** (global, not
  per-character), per **kart**, and per **course**.
- Auto-capture when detection is confident and stable, with a manual override.
- Show live progress and a remaining-item checklist so the user knows what is left
  to hover.
- Resume across sessions — never re-grab an item already saved on disk.

## 3. Non-goals

- No changes to the existing `images/` templates (read-only here).
- No Tauri / IPC integration; no race / minimap / timestamp / lap / coin tracking.
- No image processing or cropping of the captured frames (done offline later).

## 4. Locked decisions

| Decision | Value |
|----------|-------|
| Capture trigger | Auto-capture + manual override key |
| Filename convention | Match existing templates (lowercase + underscores) |
| Auto-capture confidence threshold (`--min-conf`) | **0.6** (was 0.8) |
| Output layout | **Split by language**: `captures/<lang>/<category>/` |
| Sound on auto-capture | **Simple beep** (`winsound.Beep`) |

## 5. Module + CLI

New files under a new `tools/` subpackage:

- `mkw_tracker/tools/__init__.py`
- `mkw_tracker/tools/capture_sources.py`

Run with:

```
python -m mkw_tracker.tools.capture_sources [flags]
```

Flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--device NAME` | `camera_device` setting, else auto-probe | Capture device (passed to `build_camera_source`) |
| `--lang LANG`   | `switch2_language` setting (e.g. `en_uk`) | Template language + output subfolder |
| `--out DIR`     | `<data_dir>/captures` | Output root |
| `--min-conf F`  | `0.8` | Min confidence for auto-capture |
| `--hold N`      | `3` | Consecutive stable scans required before auto-capture |
| `--no-sound`    | off | Disable the beep |

## 6. Initialization (mirrors `main.py`)

1. `apply_migrations()` → `get_settings()`.
2. Resolve `lang` (`--lang` or `switch2_language`) and `device` (`--device` or
   `camera_device`).
3. Build `ScreenDetector(switch2_language=lang)`; apply persisted
   `tell_tree_<SCREEN>` overrides and call `_tell.load(lang)` for each tell —
   exactly as `main.py` does (current `main.py:786-792`), so screen detection
   behaves identically to the real app.
4. Build `SelectionTracker(switch2_language=lang)`.
5. Build `build_camera_source(device)`.

## 7. Per-frame loop (~30 fps cap)

```
read frame
resize to 1920×1080         # plain resize; calibration LUT stays disabled, as in main._norm
screen, perf = detector.update(frame)
tracker.update(frame, screen, perf.current_score)
fires = gate.observe(screen, tracker.state)
for each fire: save full frame + beep + HUD flash
draw HUD
handle keys
sleep to cap rate
```

No race/minimap trackers are constructed or updated.

## 8. `CaptureGate` — the core, unit-tested unit

Constructed with a `NameResolver` (one-time dir listing, §9); `observe()` itself
does no cv2 / camera / per-call file IO, so it is unit-tested with synthetic
`SelectionState`s. Holds all dedup + stability state.

- **Screen → fields:**
  - `CHARACTER_SELECT` → `character` **and** `costume`
  - `KART_SELECT` → `kart`
  - `COURSE_SELECT` → `course`
  - anything else → none
- **Per-category transient state:** `last_name`, `streak`.
- **`captured: dict[category → set[base_filename]]`**, **`skipped: dict[category → set[base_filename]]`**.

### `observe(screen, state) -> list[(category, base_filename)]`

Maps `screen` to its field(s), reads each field's `(display_name, conf)` from the
`SelectionState`, and resolves each display name to its base filename via the
injected `NameResolver`. Then, per field `(cat, name, conf)` — where `name` is the
resolved base filename, or `None` when nothing is detected for that field:

```
if name is None or conf < min_conf:
    last_name[cat] = None; streak[cat] = 0; continue
if name == last_name[cat]: streak[cat] += 1
else:                      last_name[cat] = name; streak[cat] = 1
if streak[cat] >= hold and name not in captured[cat] and name not in skipped[cat]:
    captured[cat].add(name)          # mark immediately so it does not re-fire
    emit fire (cat, name)
```

Returns the list of fired `(category, base_filename)` for the caller to save.

### Other methods

- `mark_captured(cat, name)` — used by resume-from-disk and force-capture.
- `skip(cat, name)` — add to `skipped[cat]`.
- `remaining(cat)` → `known[cat] - captured[cat] - skipped[cat]`.

`known[cat]` is the set of base filenames for that category (from `NameResolver`).

## 9. `NameResolver` — display name → base filename

The tracker reports **display names** (`"Mario"`, `"Pro Racer"`, `"All-Terrain"`).
We must map those back to the on-disk **base filename** (`mario`, `pro_racer`,
`all_terrain`) so saved files are drop-in for the template pipeline.

- For each category, list `images/<category>/<lang>/*.png` (exclude `*_tight.png`)
  via `resource_path`.
- Build `map[_norm_name(base)] = base`, reusing `selection._norm_name`
  (lowercase + strip non-alphanumerics).
- `resolve(category, display_name)` → `map.get(_norm_name(display_name))`, falling
  back to a slug of the display name (`lower`, non-alphanumeric runs → `_`).
- `known(category)` → `set(map.values())`.

This round-trips correctly for hyphens, apostrophes, and periods
(`"All-Terrain"` → `all_terrain`, `"Chargin' Chuck"` → `chargin_chuck`) because the
detected display name is itself derived from these same files, and `_norm_name`
collapses every separator/case difference.

## 10. Save + resume

- **Path:** `<out>/<lang>/<category>/<base>.png` (directories created as needed).
- **Write:** `cv2.imwrite` of the full 1920×1080 BGR frame — no processing.
- **Auto-save:** when the gate fires → `imwrite` → beep (unless `--no-sound`) →
  HUD flash.
- **Resume on startup:** scan `<out>/<lang>/<category>/*.png` and call
  `gate.mark_captured(cat, base)` for each existing file, so a prior session's
  captures are not re-grabbed.

## 11. Sound

- An in-memory sine-tone WAV played via `winsound.PlaySound(..., SND_MEMORY)` from a
  short-lived daemon thread, so it routes to the user's **default audio device**.
  (`winsound.Beep` was tried first but targets the PC-speaker / Beep-driver path,
  which can be silent or sent to the wrong endpoint.)
- Errors are printed (not swallowed); no-ops if `--no-sound` is set.
- A startup test beep plays once on launch so audio is verifiable independent of captures.

## 12. HUD (OpenCV — functional / plain, matching the existing dev overlay)

- **Header:** current screen name + screen confidence; active language; output root.
- **Live fields** for the current screen, each line: name, confidence, and a tag
  `NEW` / `CAPTURED` / `SKIPPED`.
- **Flash:** brief `SAVED <category>/<base>` banner for ~0.6 s after each save.
- **Progress counts:** `characters X/50`, `costumes X/39`, `karts X/40`,
  `courses X/30` (denominators come from `NameResolver.known()`).
- **Remaining list** for the current screen's category — the item names still
  needed — so the user knows what to hover next.

### Keybindings

| Key | Action |
|-----|--------|
| `SPACE` | Force-(re)capture the current frame now for the detected item(s) on this screen, **overwriting** any existing file (for when an auto-grab caught a bad frame). Beeps + marks captured. |
| `s` | Skip: ignore the currently-detected item for this session (drops it from "remaining"; for a flaky/misdetecting item). |
| `Tab` | Toggle the HUD overlay. |
| `q` / `Esc` | Quit. |

## 13. Testing (TDD the pure core)

New `tests/test_capture_sources.py` (flat layout, matching existing tests):

- **`CaptureGate`:** feed `(name, conf)` sequences and assert fire timing
  (threshold gate, hold gate), dedup (no re-fire after capture), skip behavior, and
  `remaining()` math.
- **`NameResolver`:** hyphen / apostrophe / period / case round-trips;
  `known()` sets; slug fallback for an unknown name.
- **Resume dedup:** pre-create PNGs in a temp `--out` dir, build the gate from them,
  and assert those items do not re-fire.

The camera + HUD + main loop is the thin, untested I/O shell.

## 14. Files touched

- **New:** `mkw_tracker/tools/__init__.py`, `mkw_tracker/tools/capture_sources.py`,
  `tests/test_capture_sources.py`.
- **Docs (low priority):** add the new command to the "Running the App" section of
  `CLAUDE.md`.
- **Output (generated):** `captures/<lang>/{characters,costumes,karts,courses}/*.png`.

## 15. Notes / open items for implementation

- `captures/` is **tracked in git** (user decision, 2026-06-02) — no `.gitignore`
  entry; the captured PNGs are committed alongside the code.
- Language-split output keeps per-language capture sets isolated and mirrors the
  `images/<category>/<lang>/` layout, so a future template-build step can map a
  capture straight to its destination.
- Item counts as of this design (from `en_uk` template dirs): characters 50,
  costumes 39, karts 40, courses 30 (159 total).
