# NO_SIGNAL Screen Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect a capture card's "no signal" graphic as a first-class `NO_SIGNAL` screen; on entry, silently discard any active run and clear selections (like an app restart); auto-pick the preset template from the capture-card device name, editable like any screen in Edit Screens with a "revert to auto" escape hatch.

**Architecture:** A new `Screen.NO_SIGNAL` with a standard grayscale-template tell on the card's centered text/logo. It is a *universal* detection candidate (added in `_candidate_screens()`), so it is matched only on confirmation-miss frames — negligible cost. Two preset templates ship (Elgato default, UGREEN); in "auto" mode (no persisted tell override) the engine swaps the active template based on the `camera_device` name. Entering `NO_SIGNAL` runs a top-of-`on_screen_change` discard branch (no `run_finalized` emit). All UI lives in the existing screen-graph editor.

**Tech Stack:** Python 3 (OpenCV `TM_CCOEFF_NORMED`), pytest; Svelte 4 + Vite, vitest, svelte-check; SQLite config table.

**Spec:** `docs/superpowers/specs/2026-06-16-nosignal-screen-detection-design.md`

**Branch:** `nosignal-screen-detection` (already checked out; spec committed at `ca0dc19`).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `mkw_tracker/detection/screen.py` | `NO_SIGNAL` enum, preset/hint tables, `auto_nosignal_preset()`, the tell, `_candidate_screens()` augmentation, `set_nosignal_region()`, `GRAPH_NODE_SHOTS` entry | Modify |
| `scripts/gen_nosignal_templates.py` | Cut preset templates + graph screenshot + test fixtures from `temp/` references | Create |
| `images/screens/nosignal_{elgato,ugreen}.png` | Shipped preset templates | Create (asset) |
| `screenshots/en_uk/nosignal.png` | Edit-graph node reference | Create (asset) |
| `tests/fixtures/nosignal_{elgato,ugreen}_frame.png` | Full-frame detection fixtures | Create (asset) |
| `mkw_tracker/detection/selection.py` | `SelectionTracker.reset()` | Modify |
| `mkw_tracker/lifecycle/race.py` | Top-of-`on_screen_change` NO_SIGNAL discard branch | Modify |
| `mkw_tracker/ipc/protocol.py` | `emit_nosignal_mode()` | Modify |
| `mkw_tracker/main.py` | `_apply_nosignal_auto()`, startup + camera-change wiring, `reset_nosignal_auto` handler, manual-mode emit, widened selection guard | Modify |
| `src/lib/nosignal.js` (+ `.test.js`) | Badge-label helper | Create |
| `src/lib/stores.js` | `nosignalMode` store | Modify |
| `src/App.svelte` | SCREEN_NAMES/LABELS/HINTS, `nosignal_mode` handler, `resetDetection` branch, thread mode into bundle | Modify |
| `src/components/DetectionTree.svelte` | "Revert to auto" relabel + Auto/Manual badge for NO_SIGNAL | Modify |
| `docs/ipc-protocol.md`, `docs/config-reference.md`, `CLAUDE.md` | Docs | Modify |

---

## Task 1: Screen enum, preset/hint tables, `auto_nosignal_preset()`, graph node

**Files:**
- Modify: `mkw_tracker/detection/screen.py`
- Test: `tests/test_nosignal.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_nosignal.py`:

```python
"""NO_SIGNAL screen detection: presets, device-name auto-selection, tell match,
universal-candidate wiring, and region swapping."""
import os
import cv2
from mkw_tracker.detection.screen import (
    Screen, TELLS, TRANSITIONS, GRAPH_NODE_SHOTS, ScreenDetector, detect_tell,
    NO_SIGNAL_PRESETS, NO_SIGNAL_DEVICE_HINTS, auto_nosignal_preset,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _img(name):
    img = cv2.imread(os.path.join(FIXTURES, name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    assert img.shape[:2] == (1080, 1920), f"{name} not 1080p"
    return img


def test_no_signal_enum_and_graph_node_exist():
    assert Screen.NO_SIGNAL.name == "NO_SIGNAL"
    assert GRAPH_NODE_SHOTS[Screen.NO_SIGNAL] == "nosignal.png"


def test_presets_are_well_formed():
    for key in ("elgato", "ugreen"):
        p = NO_SIGNAL_PRESETS[key]
        assert p["image_path"].endswith(f"nosignal_{key}.png")
        assert len(p["roi"]) == 4 and p["roi"][2] > p["roi"][0] and p["roi"][3] > p["roi"][1]


def test_auto_nosignal_preset_matches_brand_substring():
    assert auto_nosignal_preset("Elgato 4K X") == "elgato"
    assert auto_nosignal_preset("UGREEN 25773") == "ugreen"
    assert auto_nosignal_preset("elgato 4k x") == "elgato"     # case-insensitive
    assert auto_nosignal_preset("Some USB Capture") is None
    assert auto_nosignal_preset("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal.py -v`
Expected: FAIL with `ImportError` / `AttributeError` (`NO_SIGNAL`, `NO_SIGNAL_PRESETS`, etc. not defined).

- [ ] **Step 3: Add the enum member**

In `mkw_tracker/detection/screen.py`, in `class Screen(Enum)`, add after `TIME_TRIALS = auto()`:

```python
    NO_SIGNAL            = auto()
```

- [ ] **Step 4: Add preset/hint tables + `auto_nosignal_preset()`**

In `mkw_tracker/detection/screen.py`, immediately after the `class Screen(Enum)` block (before the `# Transition graph` section), add:

```python
# ---------------------------------------------------------------------------
# NO_SIGNAL presets + device-name auto-selection
# ---------------------------------------------------------------------------
# The capture card's "no signal" graphic is a static, card-specific screen.  We
# template-match its centered text/logo.  Two presets ship; the active one is
# auto-picked from the selected video device's name unless the user hand-edits
# the tell.  ROIs are the single source of truth, shared with
# scripts/gen_nosignal_templates.py (which crops the references at exactly these
# boxes).  If the gen script's bright-pixel gate fails, nudge the ROI here.
NO_SIGNAL_PRESETS = {
    "elgato": {"image_path": "images/screens/nosignal_elgato.png",
               "roi": (640, 470, 1280, 740)},
    "ugreen": {"image_path": "images/screens/nosignal_ugreen.png",
               "roi": (600, 350, 1320, 560)},
}

# Case-insensitive substring -> preset.  Device names confirmed by the user:
# "Elgato 4K X", "UGREEN 25773".  First match wins.
NO_SIGNAL_DEVICE_HINTS = {
    "elgato": ["elgato"],
    "ugreen": ["ugreen"],
}


def auto_nosignal_preset(device_name: str) -> Optional[str]:
    """Return the preset key whose hint substring is in *device_name* (case-
    insensitive), or None if no brand matches."""
    name = (device_name or "").lower()
    for preset, hints in NO_SIGNAL_DEVICE_HINTS.items():
        if any(h in name for h in hints):
            return preset
    return None
```

- [ ] **Step 5: Add the graph-node screenshot mapping**

In `mkw_tracker/detection/screen.py`, in the `GRAPH_NODE_SHOTS` dict, add the NO_SIGNAL entry:

```python
GRAPH_NODE_SHOTS: Dict[Screen, str] = {
    **SCREENSHOT_FILES,
    Screen.RESET:         "reset.png",
    Screen.GHOST_RESET:   "reset.png",
    Screen.UNKNOWN_RESET: "reset.png",
    Screen.NO_SIGNAL:     "nosignal.png",
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal.py -v`
Expected: PASS for `test_no_signal_enum_and_graph_node_exist`, `test_presets_are_well_formed`, `test_auto_nosignal_preset_matches_brand_substring`. (The `_img` helper is unused so far — no failure.)

- [ ] **Step 7: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_nosignal.py
git commit -m "feat(nosignal): Screen.NO_SIGNAL enum, presets + device auto-select"
```

---

## Task 2: Gen script + preset templates + fixtures

**Files:**
- Create: `scripts/gen_nosignal_templates.py`
- Create (assets): `images/screens/nosignal_elgato.png`, `images/screens/nosignal_ugreen.png`, `screenshots/en_uk/nosignal.png`, `tests/fixtures/nosignal_elgato_frame.png`, `tests/fixtures/nosignal_ugreen_frame.png`

- [ ] **Step 1: Write the generator script**

Create `scripts/gen_nosignal_templates.py`:

```python
"""Generate NO_SIGNAL preset templates, the edit-graph screenshot, and test
fixtures from the two reference captures in temp/.

Outputs (all 1080p space):
  images/screens/nosignal_elgato.png        grayscale crop at the Elgato preset ROI
  images/screens/nosignal_ugreen.png        grayscale crop at the UGREEN preset ROI
  screenshots/en_uk/nosignal.png            full Elgato frame (edit-graph node)
  tests/fixtures/nosignal_elgato_frame.png  full Elgato frame
  tests/fixtures/nosignal_ugreen_frame.png  full UGREEN frame (downscaled 1440->1080)

The crop ROIs are read from NO_SIGNAL_PRESETS (single source of truth).  Each
crop is asserted to contain bright text; if the gate fails, adjust the ROI in
detection/screen.py and re-run.
"""
import os
import sys
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mkw_tracker.detection.screen import NO_SIGNAL_PRESETS   # noqa: E402

REFS = {
    "elgato": os.path.join(ROOT, "temp", "nosignal.png"),
    "ugreen": os.path.join(ROOT, "temp", "nosignal2.png"),
}


def _load_1080p(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"missing reference: {path}")
    h, w = img.shape[:2]
    if (w, h) != (1920, 1080):
        img = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)
    return img


def main():
    os.makedirs(os.path.join(ROOT, "images", "screens"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "screenshots", "en_uk"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "tests", "fixtures"), exist_ok=True)

    for preset, ref in REFS.items():
        frame = _load_1080p(ref)
        cv2.imwrite(os.path.join(ROOT, "tests", "fixtures",
                                 f"nosignal_{preset}_frame.png"), frame)
        x1, y1, x2, y2 = NO_SIGNAL_PRESETS[preset]["roi"]
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        bright = int((gray >= 180).sum())
        if bright < 200:
            raise SystemExit(
                f"{preset}: ROI {(x1, y1, x2, y2)} has only {bright} bright px - "
                f"adjust NO_SIGNAL_PRESETS['{preset}']['roi'] in detection/screen.py")
        out = os.path.join(ROOT, NO_SIGNAL_PRESETS[preset]["image_path"])
        cv2.imwrite(out, gray)
        print(f"{preset}: {out}  {gray.shape[1]}x{gray.shape[0]}  ({bright} bright px)")

    cv2.imwrite(os.path.join(ROOT, "screenshots", "en_uk", "nosignal.png"),
                _load_1080p(REFS["elgato"]))
    print("done")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `python scripts/gen_nosignal_templates.py`
Expected: prints two `elgato:`/`ugreen:` lines with a bright-pixel count > 200 each, then `done`. If it raises `... bright px - adjust ...`, the ROI doesn't cover the text: open the just-written `tests/fixtures/nosignal_<preset>_frame.png`, pick a centered box over the text, update `NO_SIGNAL_PRESETS[<preset>]["roi"]` in `detection/screen.py`, and re-run.

- [ ] **Step 3: Verify the outputs visually**

Run: `python -c "import cv2; im=cv2.imread('images/screens/nosignal_elgato.png',0); print('elgato', im.shape); im2=cv2.imread('images/screens/nosignal_ugreen.png',0); print('ugreen', im2.shape)"`
Expected: two shapes printed (heights/widths matching the ROIs, e.g. elgato `(270, 640)`, ugreen `(210, 720)`). Open the PNGs and confirm each shows the card's "no signal" text.

- [ ] **Step 4: Commit**

```bash
git add scripts/gen_nosignal_templates.py images/screens/nosignal_elgato.png images/screens/nosignal_ugreen.png screenshots/en_uk/nosignal.png tests/fixtures/nosignal_elgato_frame.png tests/fixtures/nosignal_ugreen_frame.png
git commit -m "feat(nosignal): generator script + Elgato/UGREEN preset templates and fixtures"
```

---

## Task 3: NO_SIGNAL tell + Elgato detection

**Files:**
- Modify: `mkw_tracker/detection/screen.py:TELLS`
- Test: `tests/test_nosignal.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nosignal.py`:

```python
def _nosignal_tell():
    """A detector instance loads every tell's template; return the NO_SIGNAL tell
    with its (Elgato-default) template populated."""
    d = ScreenDetector()
    return d._tells_by_screen[Screen.NO_SIGNAL]


def test_nosignal_tell_matches_elgato_frame():
    detected, score = detect_tell(_img("nosignal_elgato_frame.png"), _nosignal_tell())
    assert detected, f"Elgato no-signal should detect (score={score})"


def test_nosignal_tell_rejects_reset_and_racing():
    tell = _nosignal_tell()
    for name in ("reset_dev.png", "racing_dark_section.png"):
        detected, score = detect_tell(_img(name), tell)
        assert not detected, f"{name} must NOT detect as NO_SIGNAL (score={score})"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal.py -k "elgato_frame or reset_and_racing" -v`
Expected: FAIL with `KeyError: <Screen.NO_SIGNAL>` (no tell registered yet).

- [ ] **Step 3: Register the tell**

In `mkw_tracker/detection/screen.py`, in the `TELLS` list, add a NO_SIGNAL entry (place it after the `TIME_TRIALS` tell). It uses the Elgato preset by default and a low threshold (bright text on black correlates strongly; a textless frame scores ~0):

```python
    Tell(screen=Screen.NO_SIGNAL, match_threshold=0.6, groups=[[
        _tmpl(NO_SIGNAL_PRESETS["elgato"]["image_path"],
              NO_SIGNAL_PRESETS["elgato"]["roi"])]]),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal.py -k "elgato_frame or reset_and_racing" -v`
Expected: PASS. If `rejects_reset_and_racing` fails (a dark frame scored ≥ 0.6), raise `match_threshold` toward 0.75 and re-run; if `matches_elgato_frame` then fails, the ROI/template is degenerate — revisit Task 2.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_nosignal.py
git commit -m "feat(nosignal): NO_SIGNAL tell (Elgato default) with detection tests"
```

---

## Task 4: `set_nosignal_region()` + UGREEN swap

**Files:**
- Modify: `mkw_tracker/detection/screen.py:ScreenDetector`
- Test: `tests/test_nosignal.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nosignal.py`:

```python
def test_set_nosignal_region_swaps_to_ugreen_and_matches():
    d = ScreenDetector()
    res = d.set_nosignal_region("ugreen")
    assert res is not None
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["ugreen"]["roi"]
    assert region.image_path.endswith("nosignal_ugreen.png")
    detected, score = detect_tell(_img("nosignal_ugreen_frame.png"),
                                  d._tells_by_screen[Screen.NO_SIGNAL])
    assert detected, f"UGREEN no-signal should detect after swap (score={score})"


def test_set_nosignal_region_unknown_preset_returns_none():
    assert ScreenDetector().set_nosignal_region("bogus") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal.py -k "set_nosignal_region" -v`
Expected: FAIL with `AttributeError: 'ScreenDetector' object has no attribute 'set_nosignal_region'`.

- [ ] **Step 3: Implement the method**

In `mkw_tracker/detection/screen.py`, in `class ScreenDetector`, add this method (place it next to `reset_tell`):

```python
    def set_nosignal_region(self, preset_name: str) -> Optional[dict]:
        """Point the NO_SIGNAL tell's single region at a preset's template + ROI
        and reload it.  In-memory only - the caller decides whether to persist."""
        preset = NO_SIGNAL_PRESETS.get(preset_name)
        if preset is None:
            return None
        tell = self._tells_by_screen.get(Screen.NO_SIGNAL)
        if tell is None or not tell.groups or not tell.groups[0]:
            return None
        region = tell.groups[0][0]
        region.image_path = preset["image_path"]
        region.roi = tuple(preset["roi"])
        region.template = None
        tell.load(self._switch2_language)
        return self._tell_to_dict(Screen.NO_SIGNAL, tell)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal.py -k "set_nosignal_region" -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_nosignal.py
git commit -m "feat(nosignal): set_nosignal_region() preset swap + UGREEN detection"
```

---

## Task 5: Universal-candidate wiring

**Files:**
- Modify: `mkw_tracker/detection/screen.py:ScreenDetector._candidate_screens`
- Test: `tests/test_nosignal.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_nosignal.py`:

```python
def test_nosignal_is_universal_candidate_without_mutating_transitions():
    d = ScreenDetector()
    d.current_screen = Screen.RACING
    assert Screen.NO_SIGNAL in d._candidate_screens()
    # The shared TRANSITIONS table must not be polluted by the augmentation.
    assert Screen.NO_SIGNAL not in TRANSITIONS[Screen.RACING]


def test_from_nosignal_rescans_unknown_set():
    d = ScreenDetector()
    d.current_screen = Screen.NO_SIGNAL
    assert d._candidate_screens() == set(TRANSITIONS[Screen.UNKNOWN])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal.py -k "universal_candidate or rescans_unknown" -v`
Expected: FAIL — `NO_SIGNAL` not in the RACING candidate set (and the from-NO_SIGNAL set is empty: `TRANSITIONS.get(NO_SIGNAL, set())` is empty).

- [ ] **Step 3: Augment `_candidate_screens()`**

In `mkw_tracker/detection/screen.py`, replace the body of `_candidate_screens` with:

```python
    def _candidate_screens(self) -> Set[Screen]:
        # Signal restored: from NO_SIGNAL re-detect from scratch, like UNKNOWN.
        if self.current_screen == Screen.NO_SIGNAL:
            return set(self.transitions.get(Screen.UNKNOWN, set()))
        if self.current_screen == Screen.HOME:
            base = self.transitions.get(Screen.HOME, set()).copy()
            if self._pre_home_screen is None:
                base |= self.transitions.get(Screen.UNKNOWN, set())
            else:
                # Add the pre-home screen itself AND all its neighbours - we may
                # have been mid-transition when HOME was pressed, so any screen
                # reachable from the last known state is a valid landing point.
                base.add(self._pre_home_screen)
                base |= self.transitions.get(self._pre_home_screen, set())
        else:
            # set() copies so adding NO_SIGNAL never mutates the shared TRANSITIONS.
            base = set(self.transitions.get(self.current_screen, set()))
        base.add(Screen.NO_SIGNAL)   # always a candidate (only scanned on a confirm-miss)
        return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the existing transition tests (no regression)**

Run: `python -m pytest tests/test_reset_transitions.py -v`
Expected: PASS (HOME/reset transition invariants unchanged).

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_nosignal.py
git commit -m "feat(nosignal): NO_SIGNAL as a universal detection candidate"
```

---

## Task 6: `SelectionTracker.reset()`

**Files:**
- Modify: `mkw_tracker/detection/selection.py:SelectionTracker`
- Test: `tests/test_nosignal_selection.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_nosignal_selection.py`:

```python
"""SelectionTracker.reset() clears all selections (used on NO_SIGNAL teardown)."""
from mkw_tracker.detection.selection import SelectionTracker


def test_selection_reset_clears_all_fields(memdb):
    t = SelectionTracker(switch2_language="en_uk")
    t.state.character = "Mario";  t.state.character_conf = 0.9
    t.state.costume = "Touring";  t.state.costume_conf = 0.8
    t.state.kart = "Tiny Titan";  t.state.kart_conf = 0.7
    t.state.course = "Rainbow Road"; t.state.course_conf = 0.6
    t._costume_loss_streak = 3

    t.reset()

    assert t.state.character is None and t.state.character_conf == 0.0
    assert t.state.costume is None and t.state.costume_conf == 0.0
    assert t.state.kart is None and t.state.kart_conf == 0.0
    assert t.state.course is None and t.state.course_conf == 0.0
    assert t._costume_loss_streak == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal_selection.py -v`
Expected: FAIL with `AttributeError: 'SelectionTracker' object has no attribute 'reset'`.

- [ ] **Step 3: Implement `reset()`**

In `mkw_tracker/detection/selection.py`, in `class SelectionTracker`, add (place it next to `reload_language`):

```python
    def reset(self):
        """Clear all selections to the cold-start state (NO_SIGNAL teardown)."""
        self.state = SelectionState()
        self._relevant_costumes = {}
        self._costume_loss_streak = 0
        self._char_scores = {}
        self._kart_scores = {}
        self._course_scores = {}
        self._costume_scores = {}
        if self.on_selection_change:
            self.on_selection_change(self.state)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal_selection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/detection/selection.py tests/test_nosignal_selection.py
git commit -m "feat(nosignal): SelectionTracker.reset() to clear selections"
```

---

## Task 7: Lifecycle NO_SIGNAL discard branch

**Files:**
- Modify: `mkw_tracker/lifecycle/race.py:RaceLifecycle.on_screen_change`
- Test: `tests/test_nosignal_lifecycle.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_nosignal_lifecycle.py`:

```python
"""Entering NO_SIGNAL is an app-restart-style teardown: discard the active run
WITHOUT finalizing it, clear selections, and disarm any ghost capture."""
import json
import pytest
from mkw_tracker.lifecycle.race import RaceLifecycle
from mkw_tracker.detection.screen import Screen


@pytest.fixture(autouse=True)
def _stub_minimap_db(monkeypatch):
    import mkw_tracker.lifecycle.race as race_mod
    monkeypatch.setattr(race_mod, "get_minimap_roi", lambda *a, **k: None)
    monkeypatch.setattr(race_mod, "get_minimap_seed", lambda *a, **k: None)


class _Stub:
    def __init__(self): self.reset_calls = 0
    def reset(self): self.reset_calls += 1
    def calibrate_from_race(self): return 0.5
    def set_roi(self, *a, **k): pass
    def start(self): pass


class _Sel(_Stub):
    class _S:
        course = "Choco Mountain"; character = "Mario"; kart = "K"; costume = "Base"
    state = _S()


class _Laps(_Stub):
    class _S:
        total_laps = 3; current_lap = 2
    state = _S()


class _Ts(_Stub):
    total_time = None
    splits = {}


class _Mm(_Stub):
    _calibrated = False
    _badge = None
    def seed(self, *a, **k): pass


class _Rec(_Stub):
    points = []


class _Ipc:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def _make(ipc=None):
    return RaceLifecycle(selection=_Sel(), laps=_Laps(), coins=_Stub(), ts=_Ts(),
                         finish=_Stub(), mush=_Stub(), minimap=_Mm(), mm_rec=_Rec(), ipc=ipc)


def test_no_signal_from_racing_discards_without_finalizing():
    ipc = _Ipc(); lc = _make(ipc)
    lc.on_screen_change(Screen.START_TIME_TRIAL, Screen.RACING)   # start a race
    lc.on_screen_change(Screen.RACING, Screen.NO_SIGNAL)          # signal drops mid-race
    types = [json.loads(e).get("type") for e in ipc.events]
    assert "race_cleared" in types
    assert "run_finalized" not in types          # silently discarded, not queued for review
    assert lc._selection.reset_calls >= 1        # selections cleared


def test_no_signal_disarms_active_ghost():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)         # recording
    assert lc.ghost_recording
    lc.on_screen_change(Screen.GHOST, Screen.NO_SIGNAL)
    assert not lc.ghost_armed and not lc.ghost_recording
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal_lifecycle.py -v`
Expected: FAIL — `run_finalized` IS emitted (the `old == RACING` path finalizes), and/or selections not reset.

- [ ] **Step 3: Add the discard branch**

In `mkw_tracker/lifecycle/race.py`, in `on_screen_change`, insert the branch immediately after the `emit_screen_change` block and **before** `# ── From RACING ──` / `if old == Screen.RACING:`:

```python
        # ── Signal lost: app-restart-style teardown ─────────────────────────
        # Discard any active run WITHOUT finalizing (no run_finalized -> not
        # queued for review), clear selections, and drop any ghost capture.
        # Early return so none of the finalize paths below can run.
        if new == Screen.NO_SIGNAL:
            self._paused_from_racing = False
            self._resuming_race      = False
            if self._ghost.armed or self._ghost.recording:
                self._ghost.disarm()
                self._emit_ghost_state()
            self._clear_race_state()
            self._selection.reset()
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal_lifecycle.py -v`
Expected: PASS (both).

- [ ] **Step 5: Run the existing lifecycle/ghost tests (no regression)**

Run: `python -m pytest tests/test_ghost_lifecycle.py tests/test_race_lifecycle_finish.py tests/test_run_finalized.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/lifecycle/race.py tests/test_nosignal_lifecycle.py
git commit -m "feat(nosignal): discard active run + clear selections on NO_SIGNAL"
```

---

## Task 8: `emit_nosignal_mode()` IPC event

**Files:**
- Modify: `mkw_tracker/ipc/protocol.py`
- Test: `tests/test_nosignal_protocol.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_nosignal_protocol.py`:

```python
import json
from mkw_tracker.ipc.protocol import emit_nosignal_mode


def test_emit_nosignal_mode_auto_with_brand():
    assert json.loads(emit_nosignal_mode(True, "ugreen")) == {
        "type": "nosignal_mode", "auto": True, "brand": "ugreen"}


def test_emit_nosignal_mode_manual():
    assert json.loads(emit_nosignal_mode(False)) == {
        "type": "nosignal_mode", "auto": False, "brand": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal_protocol.py -v`
Expected: FAIL with `ImportError: cannot import name 'emit_nosignal_mode'`.

- [ ] **Step 3: Add the emitter**

In `mkw_tracker/ipc/protocol.py`, after `emit_ghost_import_state`, add:

```python
def emit_nosignal_mode(auto: bool, brand: Optional[str] = None) -> str:
    """NO_SIGNAL detection mode for the editor badge.  auto=True -> the template
    is picked from the capture-card device name (brand = matched preset key, or
    None = Elgato default / no match).  auto=False -> user hand-edited (manual)."""
    return _emit("nosignal_mode", auto=auto, brand=brand)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal_protocol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/ipc/protocol.py tests/test_nosignal_protocol.py
git commit -m "feat(nosignal): emit_nosignal_mode IPC event"
```

---

## Task 9: main.py orchestration (auto-select, camera-change, reset, manual emit)

**Files:**
- Modify: `mkw_tracker/main.py`
- Test: `tests/test_nosignal_apply_auto.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_nosignal_apply_auto.py`:

```python
"""_apply_nosignal_auto: in auto mode (no tell_tree_NO_SIGNAL override) swap the
region from the device name; in manual mode leave it and report manual."""
import json
from mkw_tracker.database.config_repo import set_config
from mkw_tracker.detection.screen import ScreenDetector, Screen, NO_SIGNAL_PRESETS
from mkw_tracker.main import _apply_nosignal_auto


class _Settings:
    def __init__(self, device): self._device = device
    def get(self, key, default=None):
        return self._device if key == "camera_device" else default


class _Ipc:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def _modes(ipc):
    return [json.loads(e) for e in ipc.events if json.loads(e).get("type") == "nosignal_mode"]


def test_apply_auto_picks_preset_from_device(memdb):
    d = ScreenDetector(); ipc = _Ipc()
    _apply_nosignal_auto(_Settings("UGREEN 25773"), d, ipc)
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["ugreen"]["roi"]
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": True, "brand": "ugreen"}


def test_apply_auto_unknown_device_keeps_elgato_default(memdb):
    d = ScreenDetector(); ipc = _Ipc()
    _apply_nosignal_auto(_Settings("Random USB Cam"), d, ipc)
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["elgato"]["roi"]
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": True, "brand": None}


def test_apply_auto_manual_when_override_present(memdb):
    set_config("tell_tree_NO_SIGNAL", "{}")            # simulate a hand edit
    d = ScreenDetector(); ipc = _Ipc()
    before = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0].roi
    _apply_nosignal_auto(_Settings("UGREEN 25773"), d, ipc)
    after = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0].roi
    assert after == before                              # manual: not swapped
    assert _modes(ipc)[-1] == {"type": "nosignal_mode", "auto": False, "brand": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nosignal_apply_auto.py -v`
Expected: FAIL with `ImportError: cannot import name '_apply_nosignal_auto'`.

- [ ] **Step 3: Add `emit_nosignal_mode` to the main.py protocol import**

In `mkw_tracker/main.py`, in the `from .ipc.protocol import (...)` block (around line 51-61), add `emit_nosignal_mode` to the imported names (e.g. on the line with `emit_option_lists`):

```python
                            emit_option_lists, emit_nosignal_mode)
```

- [ ] **Step 4: Add the `_apply_nosignal_auto` helper**

In `mkw_tracker/main.py`, add this module-level function next to `_persist_tell_tree`:

```python
def _apply_nosignal_auto(settings, detector, ipc) -> None:
    """Auto-pick the NO_SIGNAL template from the capture-card device name, unless
    the user has hand-edited the tell (a persisted tell_tree_NO_SIGNAL override is
    manual mode).  In-memory only - never persisted, so it re-derives each launch.
    Emits nosignal_mode for the editor badge."""
    from .detection.screen import auto_nosignal_preset
    if _get_config_direct("tell_tree_NO_SIGNAL"):
        ipc.emit(emit_nosignal_mode(auto=False, brand=None))
        return
    preset = auto_nosignal_preset(settings.get("camera_device", "") or "")
    detector.set_nosignal_region(preset or "elgato")
    ipc.emit(emit_nosignal_mode(auto=True, brand=preset))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_nosignal_apply_auto.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Wire the startup call**

In `mkw_tracker/main.py`, in `run()`, right after `ipc.emit(emit_option_lists(**tracker.option_lists()))` (≈ line 894), add:

```python
    _apply_nosignal_auto(settings, detector, ipc)
```

- [ ] **Step 7: Wire the camera-change calls**

In `mkw_tracker/main.py`, in the `cam_paused` drain's `resume_camera`/`open_camera` block, after `_cap_ref[0] = cap` and before `cam_paused[0] = False`, add:

```python
                    _apply_nosignal_auto(settings, detector, ipc)
```

And in the main-loop drain's `open_camera` `elif` branch, after its final `_cap_ref[0] = cap`, add:

```python
                _apply_nosignal_auto(settings, detector, ipc)
```

- [ ] **Step 8: Add the `reset_nosignal_auto` command handler**

In `mkw_tracker/main.py`, in `_handle_ipc_command`, add a new branch (place it after the `reset_tell` branch):

```python
    elif t == "reset_nosignal_auto":
        # "Revert to auto": drop the manual override and re-derive from the device.
        delete_configs_like("tell_tree_NO_SIGNAL")
        detector.reset_tell("NO_SIGNAL")
        _apply_nosignal_auto(settings, detector, ipc)
        ipc.emit(emit_tells_list(detector.get_tells_config()))
```

- [ ] **Step 9: Emit manual mode after a NO_SIGNAL hand-edit**

In `mkw_tracker/main.py` `_handle_ipc_command`, after the `_persist_tell_tree(...)` call in the `update_region` branch, add:

```python
            if sn == "NO_SIGNAL":
                ipc.emit(emit_nosignal_mode(auto=False, brand=None))
```

Do the same in the combined `add_region`/`remove_region`/`add_group`/`remove_group` branch (after its `_persist_tell_tree`), and in the `capture_region_template` branch (after its `_persist_tell_tree`, using `msg.get("screen", "")` as the screen name):

```python
                if msg.get("screen", "") == "NO_SIGNAL":
                    ipc.emit(emit_nosignal_mode(auto=False, brand=None))
```

- [ ] **Step 10: Verify no import/syntax regressions**

Run: `python -c "import mkw_tracker.main"`
Expected: no output, exit 0 (module imports cleanly).

Run: `python -m pytest tests/test_nosignal_apply_auto.py -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add mkw_tracker/main.py tests/test_nosignal_apply_auto.py
git commit -m "feat(nosignal): auto-select by device, reset_nosignal_auto, manual emit"
```

---

## Task 10: Widen the selection-clear emit guard

**Files:**
- Modify: `mkw_tracker/main.py` (main loop, selection on-change emit, ≈ line 1176-1186)

The engine only emitted `selection_update` when at least one field was set (`any(sel_key)`), so the all-null clear from `SelectionTracker.reset()` (Task 6) never reached the UI and `_prev_sel` went stale. The frontend handler already maps each field through `?? null` (App.svelte:824-827), so emitting the all-null update is sufficient to clear the readouts.

- [ ] **Step 1: Edit the guard**

In `mkw_tracker/main.py`, find:

```python
        sel_key = (selection.character, selection.costume,
                   selection.kart, selection.course)
        if sel_key != _prev_sel and any(sel_key):
```

Replace the `if` line with:

```python
        if sel_key != _prev_sel and (any(sel_key) or any(_prev_sel)):
```

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "import mkw_tracker.main"`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "fix(nosignal): emit the all-null selection_update so cleared selections reach the UI"
```

---

## Task 11: Frontend — badge helper, store, App.svelte wiring

**Files:**
- Create: `src/lib/nosignal.js`, `src/lib/nosignal.test.js`
- Modify: `src/lib/stores.js`, `src/App.svelte`

- [ ] **Step 1: Write the failing vitest test**

Create `src/lib/nosignal.test.js`:

```js
import { describe, it, expect } from "vitest";
import { nosignalBadgeLabel } from "./nosignal.js";

describe("nosignalBadgeLabel", () => {
  it("reports the matched brand in auto mode", () => {
    expect(nosignalBadgeLabel({ auto: true, brand: "elgato" })).toBe("Auto · matched Elgato");
    expect(nosignalBadgeLabel({ auto: true, brand: "ugreen" })).toBe("Auto · matched UGREEN");
  });
  it("reports the Elgato default when auto matches nothing", () => {
    expect(nosignalBadgeLabel({ auto: true, brand: null })).toBe("Auto · Elgato default (no card match)");
  });
  it("reports manual when the user hand-edited", () => {
    expect(nosignalBadgeLabel({ auto: false, brand: null })).toBe("Manual (custom)");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:js -- nosignal`
Expected: FAIL — cannot resolve `./nosignal.js`.

- [ ] **Step 3: Implement the helper**

Create `src/lib/nosignal.js`:

```js
// Editor badge label for the NO_SIGNAL node's detection mode.
// mode = { auto: boolean, brand: "elgato" | "ugreen" | null }
const BRAND_LABELS = { elgato: "Elgato", ugreen: "UGREEN" };

export function nosignalBadgeLabel(mode) {
  const m = mode || {};
  if (!m.auto) return "Manual (custom)";
  if (m.brand && BRAND_LABELS[m.brand]) return `Auto · matched ${BRAND_LABELS[m.brand]}`;
  return "Auto · Elgato default (no card match)";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:js -- nosignal`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the `nosignalMode` store**

In `src/lib/stores.js`, add near the other detection stores (e.g. after `tells`):

```js
export const nosignalMode  = writable({ auto: true, brand: null }); // NO_SIGNAL editor badge state
```

- [ ] **Step 6: Register NO_SIGNAL as an editable screen node**

In `src/App.svelte`, add `"NO_SIGNAL"` to the `SCREEN_NAMES` array (≈ line 539), and add entries to `SCREEN_LABELS` and `SCREEN_HINTS`:

```js
  // in SCREEN_NAMES, append:
    "NO_SIGNAL",
```

```js
  // in SCREEN_LABELS:
    NO_SIGNAL:"No Signal (Capture Card)",
```

```js
  // in SCREEN_HINTS:
    NO_SIGNAL:"Your capture card's 'no signal' screen. Auto-selected from your device; entering it clears selections and discards the active run.",
```

- [ ] **Step 7: Handle the `nosignal_mode` event**

In `src/App.svelte`, import the store. Find the existing stores import (the line importing from `./lib/stores.js`) and add `nosignalMode` to it. Then in the `switch (msg.type)` block, add a case (next to `tells_list`):

```js
      case "nosignal_mode":
        nosignalMode.set({ auto: !!msg.auto, brand: msg.brand ?? null });
        break;
```

- [ ] **Step 8: Route `resetDetection` to auto-revert for NO_SIGNAL**

In `src/App.svelte`, replace the body of `resetDetection()` (≈ line 262-266):

```js
  function resetDetection() {
    if (!selectedNode) return;
    if (selectedNode === "NO_SIGNAL") send({ type:"reset_nosignal_auto" });
    else send({ type:"reset_tell", screen:selectedNode });
    activeRegion = { group: 0, region: 0 };
    detResetPending = false;
  }
```

- [ ] **Step 9: Thread the mode into the detection bundle**

In `src/App.svelte`, in the `detectionBundle` reactive block (≈ line 349-356), add `nosignalMode` to the `tree` object so DetectionTree can render the badge/relabel:

```js
    tree: {
      groups:       editTell?.groups ?? [],
      active:       activeRegion,
      resetPending: detResetPending,
      currentScore,
      screenName:   selectedNode ?? "",
      nosignalMode: $nosignalMode,
    },
```

- [ ] **Step 10: Verify check + build + tests**

Run: `npm run test:js -- nosignal`
Expected: PASS.
Run: `npm run check`
Expected: 0 errors, 0 warnings.
Run: `npm run build`
Expected: build completes with no errors.

- [ ] **Step 11: Commit**

```bash
git add src/lib/nosignal.js src/lib/nosignal.test.js src/lib/stores.js src/App.svelte
git commit -m "feat(nosignal): editor node, nosignal_mode store + badge helper, revert-to-auto wiring"
```

---

## Task 12: Frontend — DetectionTree badge + "Revert to auto" label

**Files:**
- Modify: `src/components/DetectionTree.svelte`

`DetectionTree.svelte` owns the reset button (it dispatches `resetDetection`) and now receives `screenName` and `nosignalMode` via the bundle (Task 11, Step 9). Add the `nosignalMode` prop, relabel the reset button for NO_SIGNAL, and render a one-line mode badge.

- [ ] **Step 1: Add the prop + import the helper**

In `src/components/DetectionTree.svelte` `<script>`, add the new prop alongside the existing `screenName` / `resetPending` exports, and import the helper:

```js
  import { nosignalBadgeLabel } from "../lib/nosignal.js";
  export let nosignalMode = { auto: true, brand: null };
```

- [ ] **Step 2: Render the badge (NO_SIGNAL only)**

In `src/components/DetectionTree.svelte` markup, near the top of the tree panel (above the groups), add:

```svelte
  {#if screenName === "NO_SIGNAL"}
    <div class="ns-badge">{nosignalBadgeLabel(nosignalMode)}</div>
  {/if}
```

Add a minimal style in the component `<style>`:

```css
  .ns-badge {
    font-size: .64rem; color: var(--tx-mut);
    padding: .2rem .4rem; margin-bottom: .4rem;
    border: 1px solid var(--bd); border-radius: var(--r);
    background: var(--panel-2); text-align: center;
  }
```

- [ ] **Step 3: Relabel the reset button for NO_SIGNAL**

In `src/components/DetectionTree.svelte`, find the reset button text (the control that, when confirmed, dispatches `resetDetection` — its default label is the "reset to defaults" text). Make the label conditional:

```svelte
  {screenName === "NO_SIGNAL" ? "Revert to auto" : "Reset to defaults"}
```

(If the existing confirm-gate shows a different confirm prompt, keep that prompt; only the initial button label needs the conditional. Match the existing label string the file already uses for the non-NO_SIGNAL case.)

- [ ] **Step 4: Verify check + build**

Run: `npm run check`
Expected: 0 errors, 0 warnings.
Run: `npm run build`
Expected: build completes with no errors.

- [ ] **Step 5: Commit**

```bash
git add src/components/DetectionTree.svelte
git commit -m "feat(nosignal): Auto/Manual badge + Revert-to-auto label in the screen editor"
```

---

## Task 13: Docs + full-suite verification

**Files:**
- Modify: `docs/ipc-protocol.md`, `docs/config-reference.md`, `CLAUDE.md`

- [ ] **Step 1: Document the IPC additions**

In `docs/ipc-protocol.md`, add to the inbound-commands section:

```markdown
### `reset_nosignal_auto`
Revert the NO_SIGNAL screen tell to auto mode: drops any persisted
`tell_tree_NO_SIGNAL` override and re-derives the active preset template from
the configured `camera_device` name. No fields.
```

And to the outbound-events section:

```markdown
### `nosignal_mode`
NO_SIGNAL detection mode for the editor badge.
`{ "type": "nosignal_mode", "auto": bool, "brand": "elgato" | "ugreen" | null }`
`auto=true` → template auto-picked from the device name (`brand` = matched preset,
or `null` for the Elgato default). `auto=false` → user hand-edited (manual).
```

- [ ] **Step 2: Document the config behaviour**

In `docs/config-reference.md`, add a short note (near the screen-detection / tell section):

```markdown
### NO_SIGNAL template (auto-selected, no stored key)
The NO_SIGNAL screen's template is chosen automatically from the `camera_device`
name (`elgato`/`ugreen` substring; no match → Elgato default). There is no stored
preset key: "manual" mode is simply the presence of a `tell_tree_NO_SIGNAL`
override (written by editing the NO_SIGNAL node in Edit Screens). "Revert to auto"
deletes that override.
```

- [ ] **Step 3: Update CLAUDE.md**

In `CLAUDE.md`, in the "Screen Detection" subsection, add a sentence:

```markdown
A `NO_SIGNAL` screen detects the capture card's "no signal" graphic (grayscale text-region template; Elgato/UGREEN presets auto-selected from the `camera_device` name, editable in Edit Screens with "revert to auto"). It is a universal detection candidate (added in `_candidate_screens()`, scanned only on a confirm-miss); entering it tears down like an app restart - silently discards the active run (no `run_finalized`) and clears all selections.
```

- [ ] **Step 4: Run the full Python suite**

Run: `python -m pytest -q`
Expected: all tests pass (existing suite + the new `test_nosignal*.py` files). If a pre-existing unrelated test fails, confirm it also fails on `main` before investigating.

- [ ] **Step 5: Run the full JS suite + check + build**

Run: `npm run test:js`
Expected: all vitest suites pass (including `nosignal`).
Run: `npm run check`
Expected: 0 errors, 0 warnings.
Run: `npm run build`
Expected: build completes.

- [ ] **Step 6: Commit**

```bash
git add docs/ipc-protocol.md docs/config-reference.md CLAUDE.md
git commit -m "docs(nosignal): IPC, config, and CLAUDE.md notes for NO_SIGNAL detection"
```

---

## Manual validation (after implementation)

These need the live app + capture hardware (out of automated scope):

1. With the Elgato selected, unplug/cut the source → the rail shows **No Signal**, selections clear, and any active run vanishes with no review popup.
2. Switch the device to UGREEN in Settings → the NO_SIGNAL node badge reads "Auto · matched UGREEN" and detection fires on the UGREEN graphic.
3. Edit the NO_SIGNAL node (capture your own) → badge flips to "Manual (custom)"; "Revert to auto" restores the device-matched preset.
4. Confirm steady-state fps is unchanged on RACING/menus (NO_SIGNAL is only matched on confirm-miss frames).

## Self-review notes (done during planning)

- **Spec coverage:** every spec section maps to a task — assets/gen (T2), enum/tell/presets (T1, T3), universal candidate (T5), auto/manual/revert (T1 `auto_*`, T4 `set_*`, T9 orchestration, T12 UI), discard teardown (T6 `reset`, T7 lifecycle, T10 emit guard), IPC (T8, T9), frontend (T11, T12), docs (T13).
- **Type consistency:** `set_nosignal_region` / `auto_nosignal_preset` / `_apply_nosignal_auto` / `emit_nosignal_mode` / `nosignalBadgeLabel` / `nosignalMode` used identically across tasks; `NO_SIGNAL_PRESETS[k] = {"image_path","roi"}` consistent in T1/T2/T3/T4/T9.
- **No placeholders:** the only deferred numbers are the two preset ROIs, which are concrete literals in T1 and validated by T2's bright-pixel gate (with an explicit adjust-and-re-run instruction) — not a TBD.
