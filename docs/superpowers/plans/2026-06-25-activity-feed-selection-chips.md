# Activity-feed Selection Chips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render small parallelogram character/kart/course "chips" on the website activity feed, sourced from in-game screenshots cropped by a dedicated tool.

**Architecture:** Three phases. (1) An **asset pipeline**: a combo-capture mode on the existing Python capture tool produces SDR full-frame screenshots; an HTML crop tool authors a version-controlled `chips.crops.json`; a Python export script cuts the chips into `web/public/chips/`. (2) A **server change** attaches the run's character/kart/costume (display names) to `pb`/`rank` activity events. (3) The **frontend** slugifies those and renders a `clip-path` chip cluster at the right edge of each feed row, degrading silently when an asset is missing.

**Tech Stack:** Python (cv2 + numpy, pytest, stdlib `http.server`); Pi server (TypeScript, vitest, tsx, `node:sqlite`, Hono); Web (Svelte 4 + Vite 5 + vitest).

## Global Constraints

- **Slug authority:** `web/src/lib/chips.js:slugify` mirrors `pi/src/db/slug.ts:slugify` EXACTLY — `lowercase` → drop apostrophes (`‘ ’ '`) → `[^a-z0-9]+` to `_` → trim leading/trailing `_`. Existing capture basenames already match this (`bowsers_castle`, `koopa_troopa`); a unit test pins parity.
- **Server passes DISPLAY names** (`character`/`kart`/`costume`) on `pb`/`rank` payloads — mirroring how `session` payloads already carry display `character`/`costume`. Slugification happens once, on the client. (Refines the spec, which mentioned server-side slugify; keeping one slug authority on the client avoids drift between session and milestone paths.)
- **`web/public/chips/**` is committed as NORMAL files — NEVER Git LFS.** The Pi deploy has no `git lfs pull`; LFS would serve 132-byte pointer stubs (the known `web/public/players/*.gif` gotcha).
- **All capture/crop coordinates are full 1920×1080 pixels.**
- **SDR only:** the asset-capture session runs with **HDR off on the Switch**, output to a separate **`captures_sdr/`** folder (the HDR `captures/` template set is left untouched; detection is unaffected).
- **Silent degradation:** a missing chip asset → `<img on:error>` hides that one chip; the row text never breaks.
- **Combo naming:** a character×costume capture is `combos/<char_base>__<costume_base>.png`; the **base/default outfit** (when `SelectionState.costume is None`) is `<char_base>__base.png`, which is also the render fallback.
- **Test commands:** Python `python -m pytest tests/<file>.py -v`; Pi `npm --prefix pi test`; Web `npm --prefix web test` and `npm --prefix web run check`.

---

## PHASE 1 — Asset pipeline

### Task 1: Combo-capture gate (pure logic)

Add character×costume *pair* gating to `CaptureGate`, parallel to the existing per-field gate. Base outfit = `costume is None` → `<char>__base`.

**Files:**
- Modify: `mkw_tracker/tools/capture_sources.py` (the `CaptureGate` class, ~lines 91-173)
- Test: `tests/test_capture_sources.py` (append)

**Interfaces:**
- Consumes: `NameResolver.resolve(category, display)`, `Screen.CHARACTER_SELECT`, `SelectionState` fields `character`, `character_conf`, `costume`, `costume_conf` (costume is `None` for the default outfit).
- Produces:
  - `CaptureGate.observe_combo(screen, state) -> list[tuple[str, str]]` — returns `[("combos", "<char>__<cost>")]` once a confident pair holds `hold` scans; `[]` otherwise. Fires once per combo (dedup).
  - `CaptureGate.current_combo(screen, state) -> tuple[str, str, float, str] | None` — `(category, base, conf, status)` for the HUD / force / skip; read-only.
  - `CaptureGate.mark_combo_captured(base)`, `unmark_combo(base)`, `skip_combo(base)`; sets `combo_captured`, `combo_skipped`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_capture_sources.py`:

```python
# ---------------------------------------------------------------------------
# CaptureGate — combo mode (character x costume pairs)
# ---------------------------------------------------------------------------

def test_combo_fires_for_costumed_pair_after_hold(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"], "costumes": ["pro_racer"]},
                 min_conf=0.8, hold=2)
    st = _state(character="Mario", character_conf=0.95, costume="Pro Racer", costume_conf=0.9)
    assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == []
    assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == [("combos", "mario__pro_racer")]
    assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == []   # deduped


def test_combo_base_outfit_uses_base_suffix(tmp_path):
    """Default outfit: costume is None -> '<char>__base' (the fallback asset)."""
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    st = _state(character="Mario", character_conf=0.95, costume=None)
    assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == [("combos", "mario__base")]


def test_combo_low_costume_conf_does_not_fire(tmp_path):
    """A costume present but below min_conf is ambiguous: do not capture."""
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"], "costumes": ["pro_racer"]},
                 min_conf=0.8, hold=1)
    st = _state(character="Mario", character_conf=0.95, costume="Pro Racer", costume_conf=0.4)
    for _ in range(4):
        assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == []


def test_combo_ignores_non_character_screen_and_low_char_conf(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    good = _state(character="Mario", character_conf=0.95, costume=None)
    assert gate.observe_combo(Screen.KART_SELECT, good) == []
    weak = _state(character="Mario", character_conf=0.5, costume=None)
    assert gate.observe_combo(Screen.CHARACTER_SELECT, weak) == []


def test_combo_current_and_skip(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    st = _state(character="Mario", character_conf=0.9, costume=None)
    assert gate.current_combo(Screen.CHARACTER_SELECT, st) == ("combos", "mario__base", 0.9, "NEW")
    gate.skip_combo("mario__base")
    for _ in range(3):
        assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == []
    assert gate.current_combo(Screen.CHARACTER_SELECT, st)[3] == "SKIPPED"
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_capture_sources.py -k combo -v`
Expected: FAIL — `AttributeError: 'CaptureGate' object has no attribute 'observe_combo'`.

- [ ] **Step 3: Implement** — in `CaptureGate.__init__` (after `self._streak = ...`) add:

```python
        self.combo_captured: Set[str] = set()
        self.combo_skipped:  Set[str] = set()
        self._combo_last:   Optional[str] = None
        self._combo_streak: int = 0
```

Then add these methods to `CaptureGate`:

```python
    def _combo_base(self, state) -> Optional[str]:
        """Resolve the '<char>__<cost>' base for the current pair, or None if ambiguous.

        Base outfit is reported by the tracker as costume=None -> '<char>__base'. A costume
        that is present but below min_conf is ambiguous and yields None (do not capture)."""
        char = getattr(state, "character", None)
        if not char or getattr(state, "character_conf", 0.0) < self.min_conf:
            return None
        cost = getattr(state, "costume", None)
        if cost is None:
            cost_base = "base"
        elif getattr(state, "costume_conf", 0.0) >= self.min_conf:
            cost_base = self.resolver.resolve("costumes", cost)
        else:
            return None
        return f"{self.resolver.resolve('characters', char)}__{cost_base}"

    def observe_combo(self, screen, state) -> List[Tuple[str, str]]:
        """Feed one frame on CHARACTER_SELECT; fire once a confident pair holds `hold` scans."""
        from ..detection.screen import Screen
        base = self._combo_base(state) if screen == Screen.CHARACTER_SELECT else None
        if base is None:
            self._combo_last = None
            self._combo_streak = 0
            return []
        if base == self._combo_last:
            self._combo_streak += 1
        else:
            self._combo_last = base
            self._combo_streak = 1
        if (self._combo_streak >= self.hold
                and base not in self.combo_captured
                and base not in self.combo_skipped):
            self.combo_captured.add(base)
            return [("combos", base)]
        return []

    def current_combo(self, screen, state) -> Optional[Tuple[str, str, float, str]]:
        """(category, base, conf, status) for the current pair, read-only; None if not detected."""
        from ..detection.screen import Screen
        if screen != Screen.CHARACTER_SELECT:
            return None
        base = self._combo_base(state)
        if base is None:
            return None
        conf = getattr(state, "character_conf", 0.0)
        if getattr(state, "costume", None) is not None:
            conf = min(conf, getattr(state, "costume_conf", 0.0))
        status = ("SKIPPED" if base in self.combo_skipped
                  else "CAPTURED" if base in self.combo_captured else "NEW")
        return ("combos", base, conf, status)

    def mark_combo_captured(self, base: str) -> None:
        self.combo_captured.add(base)

    def unmark_combo(self, base: str) -> None:
        self.combo_captured.discard(base)

    def skip_combo(self, base: str) -> None:
        self.combo_skipped.add(base)
```

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_capture_sources.py -k combo -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/tools/capture_sources.py tests/test_capture_sources.py
git commit -m "feat(capture): combo (character x costume) capture gate"
```

---

### Task 2: Wire combo mode into the capture loop

Add `--combos` to the tool: capture combos on CHARACTER_SELECT (instead of separate char/costume), keep karts/courses, prime combos from disk, show combo count in the HUD, and force/skip the current combo.

**Files:**
- Modify: `mkw_tracker/tools/capture_sources.py` (`scan_existing_captures`, a new `prime_combos_from_disk`, `_draw_hud`, `run`, `main`)
- Test: `tests/test_capture_sources.py` (append `prime_combos_from_disk` test)

**Interfaces:**
- Produces: `prime_combos_from_disk(gate, out_root, lang)` — marks every `combos/*.png` base as captured. `run(args)` honours `args.combos`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_prime_combos_from_disk_prevents_recapture(tmp_path):
    from mkw_tracker.detection.screen import Screen
    from mkw_tracker.tools.capture_sources import prime_combos_from_disk
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    out = tmp_path / "captures_sdr"
    _touch(str(out / "en_uk" / "combos" / "mario__base.png"))
    prime_combos_from_disk(gate, str(out), "en_uk")
    st = _state(character="Mario", character_conf=0.95, costume=None)
    for _ in range(3):
        assert gate.observe_combo(Screen.CHARACTER_SELECT, st) == []
    assert "mario__base" in gate.combo_captured
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_capture_sources.py -k prime_combos -v`
Expected: FAIL — `ImportError: cannot import name 'prime_combos_from_disk'`.

- [ ] **Step 3: Implement** — add the helper after `prime_gate_from_disk`:

```python
def prime_combos_from_disk(gate: CaptureGate, out_root: str, lang: str) -> None:
    """Mark every already-saved combo capture as captured so it is not re-grabbed."""
    directory = os.path.join(out_root, lang, "combos")
    if not os.path.isdir(directory):
        return
    for filename in os.listdir(directory):
        if filename.lower().endswith(".png"):
            gate.mark_combo_captured(filename[:-4])
```

In `run`, after `prime_gate_from_disk(gate, out_root, lang)` add:

```python
    if args.combos:
        prime_combos_from_disk(gate, out_root, lang)
        print(f"[capture]   combos      {len(gate.combo_captured)} already on disk")
```

Replace the per-frame observe block (the `for cat, base in gate.observe(screen, state):` loop) with:

```python
            if t - last_observe >= _OBSERVE_INTERVAL:
                last_observe = t
                if args.combos:
                    fired = gate.observe_combo(screen, state)
                    if screen in (Screen.KART_SELECT, Screen.COURSE_SELECT):
                        fired = fired + gate.observe(screen, state)
                else:
                    fired = gate.observe(screen, state)
                for cat, base in fired:
                    try:
                        path = _save_capture(out_root, lang, cat, base, frame)
                        _beep(not args.no_sound)
                        flash_text, flash_until = f"SAVED {cat}/{base}", t + 0.6
                        print(f"[capture] saved {path}")
                    except Exception as e:
                        (gate.unmark_combo(base) if cat == "combos" else gate.unmark(cat, base))
                        flash_text, flash_until = f"SAVE FAILED {cat}/{base}", t + 1.5
                        print(f"[capture] SAVE FAILED {cat}/{base}: {type(e).__name__}: {e}")
```

In `_draw_hud`, after the `progress:` per-category loop, add a combo line:

```python
    if getattr(gate, "combo_captured", None):
        _put(display, f"  {'combos':<11} {len(gate.combo_captured):>3} saved", (14, y),
             (200, 220, 200), 0.45)
        y += 22
```

Make SPACE / `s` operate on the current combo when in combo mode. At the top of the SPACE handler (`elif key == ord(" "):`) and the skip handler (`elif key == ord("s"):`), special-case combos first:

```python
            elif key == ord(" "):
                combo = gate.current_combo(screen, state) if args.combos else None
                if combo:
                    cat, base, _conf, _stat = combo
                    try:
                        path = _save_capture(out_root, lang, cat, base, frame)
                        gate.mark_combo_captured(base)
                        _beep(not args.no_sound)
                        flash_text, flash_until = f"FORCED {cat}/{base}", time.perf_counter() + 0.6
                        print(f"[capture] forced {path}")
                    except Exception as e:
                        flash_text, flash_until = f"SAVE FAILED {cat}/{base}", time.perf_counter() + 1.5
                        print(f"[capture] FORCE SAVE FAILED {cat}/{base}: {type(e).__name__}: {e}")
                else:
                    for cat, base, conf, stat in gate.current_targets(screen, state):
                        try:
                            path = _save_capture(out_root, lang, cat, base, frame)
                            gate.mark_captured(cat, base)
                            _beep(not args.no_sound)
                            flash_text, flash_until = f"FORCED {cat}/{base}", time.perf_counter() + 0.6
                            print(f"[capture] forced {path}")
                        except Exception as e:
                            flash_text, flash_until = f"SAVE FAILED {cat}/{base}", time.perf_counter() + 1.5
                            print(f"[capture] FORCE SAVE FAILED {cat}/{base}: {type(e).__name__}: {e}")
            elif key == ord("s"):
                combo = gate.current_combo(screen, state) if args.combos else None
                if combo:
                    gate.skip_combo(combo[1])
                    print(f"[capture] skipped combos/{combo[1]}")
                else:
                    for cat, base, conf, stat in gate.current_targets(screen, state):
                        gate.skip(cat, base)
                        print(f"[capture] skipped {cat}/{base}")
```

In `main`, add the flag and default the SDR out-dir when combos are on:

```python
    p.add_argument("--combos", action="store_true",
                   help="Capture character x costume COMBOS (for chips) into combos/; "
                        "defaults --out to captures_sdr/.")
```

and at the end of `run`, change the `out_root` default line to prefer `captures_sdr` in combo mode:

```python
    out_root = args.out or str(data_dir() / ("captures_sdr" if args.combos else "captures"))
```

(Move/keep this assignment before `prime_gate_from_disk`. `Screen` is already imported at module top.)

- [ ] **Step 4: Run, expect PASS** (and the existing suite stays green)

Run: `python -m pytest tests/test_capture_sources.py -v`
Expected: PASS (all, including `prime_combos`).

- [ ] **Step 5: Manual smoke (optional, needs capture card)** — with **HDR off on the Switch**:

Run: `python -m mkw_tracker.tools.capture_sources --combos --lang en_uk`
Hover a character, cycle costumes; confirm `captures_sdr/en_uk/combos/<char>__<costume>.png` appear and the HUD shows the combo count. Capture karts on KART_SELECT and courses on COURSE_SELECT.

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/tools/capture_sources.py tests/test_capture_sources.py
git commit -m "feat(capture): --combos mode (SDR combos + karts + courses, prime, HUD, keys)"
```

> **The capture grind can now run in parallel with the rest of the plan.**

---

### Task 3: Chip export core (rect resolution + crop)

Pure functions for the export script: resolve an item's crop rect from the spec (override → character default → course default) and cut a chip.

**Files:**
- Create: `scripts/gen_chips.py`
- Test: `tests/test_gen_chips.py`

**Interfaces:**
- Produces:
  - `resolve_rect(spec: dict, category: str, name: str) -> tuple[int,int,int,int] | None` — category in `{'combos','karts','courses'}`. Combos fall back to `defaults.character[<char>]`; courses to `defaults.course`.
  - `crop_chip(img: np.ndarray, rect, chip_px: int) -> np.ndarray` — BGR sub-image resized to `chip_px` tall (width keeps the rect's aspect).

- [ ] **Step 1: Write the failing tests** — `tests/test_gen_chips.py`:

```python
import numpy as np


SPEC = {
    "meta": {"crop_aspect": 1.0, "chip_px": 64},
    "defaults": {"character": {"mario": {"x": 10, "y": 20, "w": 100, "h": 100}},
                 "course": {"x": 0, "y": 0, "w": 200, "h": 200}},
    "combos": {"mario__aero": {"x": 5, "y": 6, "w": 80, "h": 80}},
    "karts": {"baby_blooper": {"x": 1, "y": 2, "w": 50, "h": 50}},
    "courses": {},
}


def test_resolve_rect_prefers_explicit_override():
    from scripts.gen_chips import resolve_rect
    assert resolve_rect(SPEC, "combos", "mario__aero") == (5, 6, 80, 80)
    assert resolve_rect(SPEC, "karts", "baby_blooper") == (1, 2, 50, 50)


def test_resolve_rect_combo_falls_back_to_character_default():
    from scripts.gen_chips import resolve_rect
    assert resolve_rect(SPEC, "combos", "mario__base") == (10, 20, 100, 100)


def test_resolve_rect_course_falls_back_to_course_default():
    from scripts.gen_chips import resolve_rect
    assert resolve_rect(SPEC, "courses", "acorn_heights") == (0, 0, 200, 200)


def test_resolve_rect_none_when_unmapped():
    from scripts.gen_chips import resolve_rect
    assert resolve_rect(SPEC, "combos", "luigi__base") is None
    assert resolve_rect(SPEC, "karts", "unknown_kart") is None


def test_crop_chip_resizes_to_chip_px_tall():
    from scripts.gen_chips import crop_chip
    img = np.zeros((300, 400, 3), dtype=np.uint8)
    out = crop_chip(img, (10, 20, 100, 50), 64)   # aspect 100:50 = 2:1
    assert out.shape == (64, 128, 3)               # 64 tall, width keeps 2:1


def test_crop_chip_clamps_to_frame_bounds():
    from scripts.gen_chips import crop_chip
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    out = crop_chip(img, (90, 90, 40, 40), 32)     # rect spills past the edge
    assert out.shape[0] == 32 and out.shape[2] == 3
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_gen_chips.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.gen_chips'` (or no `resolve_rect`).

- [ ] **Step 3: Implement** — `scripts/gen_chips.py`:

```python
"""Export activity-feed chips from SDR captures + a crop spec.

Reads tools/chips.crops.json (authored by tools/chip-cropper.html) and the SDR
captures under captures_sdr/<lang>/{combos,karts,courses}/, cuts each item's crop,
resizes to a standard chip height, and writes web/public/chips/<category>/<name>.png.

Output filenames preserve the capture basename, which already matches slugify()
(the website's chip-URL builder slugifies display names to the same form). Items
with no crop rect are skipped and reported.

Run: python scripts/gen_chips.py [--lang en_uk] [--captures captures_sdr]
     [--crops tools/chips.crops.json] [--out web/public/chips] [--chip-px 96]
"""
import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import cv2

CATEGORIES: Tuple[str, ...] = ("combos", "karts", "courses")
Rect = Tuple[int, int, int, int]


def _as_rect(d: dict) -> Rect:
    return (int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]))


def resolve_rect(spec: dict, category: str, name: str) -> Optional[Rect]:
    """Crop rect for an item: explicit override, else a category default, else None."""
    explicit = (spec.get(category) or {}).get(name)
    if explicit:
        return _as_rect(explicit)
    defaults = spec.get("defaults") or {}
    if category == "combos":
        char = name.split("__", 1)[0]
        d = (defaults.get("character") or {}).get(char)
        if d:
            return _as_rect(d)
    if category == "courses":
        d = defaults.get("course")
        if d:
            return _as_rect(d)
    return None


def crop_chip(img, rect: Rect, chip_px: int):
    """Cut `rect` (clamped to the frame) and resize to `chip_px` tall, keeping aspect."""
    x, y, w, h = rect
    H, W = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        raise ValueError(f"empty crop for rect={rect} on frame {W}x{H}")
    out_w = max(1, round(chip_px * w / h))
    return cv2.resize(sub, (out_w, chip_px), interpolation=cv2.INTER_AREA)
```

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_gen_chips.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_chips.py tests/test_gen_chips.py
git commit -m "feat(chips): chip export core (rect resolution + crop)"
```

---

### Task 4: Chip export CLI shell

Walk the captures, write the chips, report skips.

**Files:**
- Modify: `scripts/gen_chips.py` (add `generate` + `main`)
- Test: `tests/test_gen_chips.py` (append)

**Interfaces:**
- Produces: `generate(crops_path, captures_root, lang, out_root, chip_px=None) -> tuple[list, list]` — `(written, skipped)` lists of `(category, name)`; writes `out_root/<category>/<name>.png`.

- [ ] **Step 1: Write the failing test** — append:

```python
def test_generate_writes_mapped_skips_unmapped(tmp_path):
    import json, cv2, os
    from scripts.gen_chips import generate
    cap = tmp_path / "captures_sdr" / "en_uk"
    (cap / "combos").mkdir(parents=True)
    (cap / "karts").mkdir(parents=True)
    cv2.imwrite(str(cap / "combos" / "mario__base.png"),
                np.full((1080, 1920, 3), 120, dtype=np.uint8))
    cv2.imwrite(str(cap / "karts" / "unknown_kart.png"),
                np.full((1080, 1920, 3), 80, dtype=np.uint8))
    spec = {"meta": {"chip_px": 48},
            "defaults": {"character": {"mario": {"x": 100, "y": 100, "w": 200, "h": 200}}},
            "combos": {}, "karts": {}, "courses": {}}
    crops = tmp_path / "chips.crops.json"
    crops.write_text(json.dumps(spec))
    out = tmp_path / "chips"
    written, skipped = generate(str(crops), str(tmp_path / "captures_sdr"), "en_uk", str(out))
    assert ("combos", "mario__base") in written
    assert ("karts", "unknown_kart") in skipped
    chip = cv2.imread(str(out / "combos" / "mario__base.png"))
    assert chip is not None and chip.shape[0] == 48
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_gen_chips.py -k generate -v`
Expected: FAIL — `ImportError: cannot import name 'generate'`.

- [ ] **Step 3: Implement** — append to `scripts/gen_chips.py`:

```python
def generate(crops_path: str, captures_root: str, lang: str, out_root: str,
             chip_px: Optional[int] = None):
    """Cut every mapped capture into out_root/<category>/<name>.png. Returns (written, skipped)."""
    with open(crops_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    chip_px = chip_px or int((spec.get("meta") or {}).get("chip_px", 96))
    written: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []
    for category in CATEGORIES:
        src_dir = os.path.join(captures_root, lang, category)
        if not os.path.isdir(src_dir):
            continue
        for fn in sorted(os.listdir(src_dir)):
            if not fn.lower().endswith(".png"):
                continue
            name = fn[:-4]
            rect = resolve_rect(spec, category, name)
            img = cv2.imread(os.path.join(src_dir, fn)) if rect is not None else None
            if rect is None or img is None:
                skipped.append((category, name))
                continue
            out_dir = os.path.join(out_root, category)
            os.makedirs(out_dir, exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, fn), crop_chip(img, rect, chip_px))
            written.append((category, name))
    return written, skipped


def main():
    p = argparse.ArgumentParser(description="Export activity-feed chips from SDR captures.")
    p.add_argument("--lang", default="en_uk")
    p.add_argument("--captures", default="captures_sdr")
    p.add_argument("--crops", default=os.path.join("tools", "chips.crops.json"))
    p.add_argument("--out", default=os.path.join("web", "public", "chips"))
    p.add_argument("--chip-px", type=int, default=None, dest="chip_px")
    a = p.parse_args()
    written, skipped = generate(a.crops, a.captures, a.lang, a.out, a.chip_px)
    print(f"[gen-chips] wrote {len(written)} chips -> {a.out}")
    if skipped:
        print(f"[gen-chips] skipped {len(skipped)} unmapped/unreadable:")
        for cat, name in skipped:
            print(f"  {cat}/{name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_gen_chips.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_chips.py tests/test_gen_chips.py
git commit -m "feat(chips): gen_chips CLI shell (walk captures -> web/public/chips)"
```

---

### Task 5: Crop-tool local server

A stdlib `http.server` that serves the captures + the crop tool and persists `chips.crops.json`.

**Files:**
- Create: `scripts/chip_cropper_server.py`
- Test: `tests/test_chip_cropper_server.py`

**Interfaces:**
- Produces:
  - `list_captures(captures_root, lang) -> list[dict]` — `[{category, name, url}]` for combos/karts/courses, `url` = `/captures/<lang>/<category>/<file>`.
  - `load_crops(path) -> dict` — the spec, or a fresh skeleton if absent.
  - `save_crops(path, data) -> None` — writes pretty JSON.
  - `serve(captures_root, lang, crops_path, html_path, port=8777)` — runs the blocking HTTP server (manual).

- [ ] **Step 1: Write the failing tests** — `tests/test_chip_cropper_server.py`:

```python
def test_list_captures_enumerates_categories(tmp_path):
    from scripts.chip_cropper_server import list_captures
    root = tmp_path / "captures_sdr" / "en_uk"
    (root / "combos").mkdir(parents=True)
    (root / "karts").mkdir(parents=True)
    (root / "combos" / "mario__base.png").write_bytes(b"")
    (root / "karts" / "baby_blooper.png").write_bytes(b"")
    items = list_captures(str(tmp_path / "captures_sdr"), "en_uk")
    names = {(i["category"], i["name"]) for i in items}
    assert ("combos", "mario__base") in names
    assert ("karts", "baby_blooper") in names
    combo = next(i for i in items if i["name"] == "mario__base")
    assert combo["url"] == "/captures/en_uk/combos/mario__base.png"


def test_load_crops_returns_skeleton_when_absent(tmp_path):
    from scripts.chip_cropper_server import load_crops
    spec = load_crops(str(tmp_path / "nope.json"))
    assert spec["combos"] == {} and spec["karts"] == {} and spec["courses"] == {}
    assert "meta" in spec and "defaults" in spec


def test_save_then_load_roundtrips(tmp_path):
    from scripts.chip_cropper_server import save_crops, load_crops
    path = tmp_path / "chips.crops.json"
    data = {"meta": {"chip_px": 96}, "defaults": {"character": {}, "course": None},
            "combos": {"mario__base": {"x": 1, "y": 2, "w": 3, "h": 4}},
            "karts": {}, "courses": {}}
    save_crops(str(path), data)
    assert load_crops(str(path))["combos"]["mario__base"] == {"x": 1, "y": 2, "w": 3, "h": 4}
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_chip_cropper_server.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** — `scripts/chip_cropper_server.py`:

```python
"""Local server for tools/chip-cropper.html.

Serves the SDR captures and the crop tool, and persists the crop spec. Pure helpers
(list_captures / load_crops / save_crops) are unit-tested; the HTTP shell is manual.

Run: python scripts/chip_cropper_server.py [--lang en_uk] [--captures captures_sdr]
     [--crops tools/chips.crops.json] [--port 8777]
Then open http://localhost:8777/
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List

CATEGORIES = ("combos", "karts", "courses")


def list_captures(captures_root: str, lang: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for category in CATEGORIES:
        directory = os.path.join(captures_root, lang, category)
        if not os.path.isdir(directory):
            continue
        for fn in sorted(os.listdir(directory)):
            if fn.lower().endswith(".png"):
                items.append({"category": category, "name": fn[:-4],
                              "url": f"/captures/{lang}/{category}/{fn}"})
    return items


def load_crops(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"meta": {"crop_aspect": 1.0, "chip_px": 96},
            "defaults": {"character": {}, "course": None},
            "combos": {}, "karts": {}, "courses": {}}


def save_crops(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def serve(captures_root: str, lang: str, crops_path: str, html_path: str, port: int = 8777):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(html_path, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if self.path == "/api/captures":
                return self._send(200, json.dumps(list_captures(captures_root, lang)).encode())
            if self.path == "/api/crops":
                return self._send(200, json.dumps(load_crops(crops_path)).encode())
            if self.path.startswith("/captures/"):
                rel = self.path[len("/captures/"):].split("?", 1)[0]
                fpath = os.path.join(captures_root, *rel.split("/"))
                if os.path.isfile(fpath):
                    with open(fpath, "rb") as fh:
                        return self._send(200, fh.read(), "image/png")
                return self._send(404, b"not found", "text/plain")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/crops":
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
                save_crops(crops_path, data)
                return self._send(200, b'{"ok":true}')
            return self._send(404, b"not found", "text/plain")

        def log_message(self, *a):   # quiet
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[cropper] http://localhost:{port}/  (lang={lang}, crops={crops_path})")
    httpd.serve_forever()


def main():
    p = argparse.ArgumentParser(description="Local server for the chip crop tool.")
    p.add_argument("--lang", default="en_uk")
    p.add_argument("--captures", default="captures_sdr")
    p.add_argument("--crops", default=os.path.join("tools", "chips.crops.json"))
    p.add_argument("--html", default=os.path.join("tools", "chip-cropper.html"))
    p.add_argument("--port", type=int, default=8777)
    a = p.parse_args()
    serve(a.captures, a.lang, a.crops, a.html, a.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_chip_cropper_server.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/chip_cropper_server.py tests/test_chip_cropper_server.py
git commit -m "feat(chips): local server for the chip crop tool"
```

---

### Task 6: Crop tool (HTML)

The interactive tool: pick an item, drag/resize a fixed-aspect crop box, see a live chip preview using the real chip CSS, and save. Per-character default inherited by combos with per-combo override; karts per-item; courses default-or-override. Deliverable verified manually (no unit test for canvas/DOM).

**Files:**
- Create: `tools/chip-cropper.html`

- [ ] **Step 1: Create the file** — `tools/chip-cropper.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>MKW chip cropper</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 13px/1.4 system-ui, sans-serif; background: #0e0f12; color: #ddd;
         display: grid; grid-template-columns: 1fr 320px; height: 100vh; }
  #stage { position: relative; overflow: hidden; display: grid; place-items: center; background: #000; }
  #frame { position: relative; }
  #frame img { display: block; max-width: 100%; max-height: 100vh; user-select: none; -webkit-user-drag: none; }
  #box { position: absolute; border: 1.5px solid #5cf; box-shadow: 0 0 0 9999px rgba(0,0,0,.45);
         cursor: move; }
  #box .h { position: absolute; right: -7px; bottom: -7px; width: 14px; height: 14px;
            background: #5cf; border-radius: 2px; cursor: nwse-resize; }
  aside { padding: 16px; background: #15171c; overflow: auto; }
  h1 { font-size: 14px; margin: 0 0 10px; }
  .row { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
  button { background: #23262d; color: #ddd; border: 1px solid #333; border-radius: 6px;
           padding: 6px 10px; cursor: pointer; }
  button.primary { background: #2b6; border-color: #2b6; color: #052; font-weight: 600; }
  .pill { font-size: 11px; color: #8af; }
  .muted { color: #888; }
  /* The REAL feed chip CSS, reused so the preview is exact (keep in sync with ActivityLog.svelte). */
  .chip-wrap { filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.55)); line-height: 0; display: inline-block; }
  .chip { display: inline-block; height: 48px; aspect-ratio: 1/1; overflow: hidden;
          clip-path: polygon(16% 0, 100% 0, 84% 100%, 0 100%);
          box-shadow: inset 0 0 0 1px rgba(255,255,255,.16); background: #15171c; position: relative; }
  .chip img { position: absolute; left: 0; top: 0; max-width: none; }
  .preview-bg { background: #1b1d22; padding: 14px; border-radius: 8px; display: grid; place-items: center; }
</style>
</head>
<body>
  <div id="stage"><div id="frame"><img id="img" alt="" /><div id="box"><div class="h"></div></div></div></div>
  <aside>
    <h1>chip cropper</h1>
    <div class="row"><button id="prev">‹ prev</button><button id="next">next ›</button>
      <button id="nextUnset">next unset</button></div>
    <div class="row"><span id="counter" class="muted"></span></div>
    <div class="row"><strong id="itemName"></strong> <span id="itemCat" class="pill"></span></div>
    <div class="preview-bg"><span class="chip-wrap"><span class="chip"><img id="pvImg" alt="" /></span></span></div>
    <div class="row" id="comboControls">
      <button id="setCharDefault">set as character default</button>
      <label><input type="checkbox" id="overrideCombo" /> override this combo only</label>
    </div>
    <div class="row" id="courseControls" hidden>
      <button id="setCourseDefault">set as course default</button>
      <label><input type="checkbox" id="overrideCourse" /> override this course only</label>
    </div>
    <div class="row"><button id="save" class="primary">save</button>
      <span id="saved" class="muted"></span></div>
    <p class="muted">Drag the box to move, drag the corner to resize (aspect locked).
       Combos inherit the character default unless overridden.</p>
  </aside>
<script>
const CAP_W = 1920, CAP_H = 1080;
let items = [], spec = null, idx = 0, scale = 1;

const $ = (id) => document.getElementById(id);
const img = $("img"), box = $("box"), frame = $("frame");

async function boot() {
  items = await (await fetch("/api/captures")).json();
  spec = await (await fetch("/api/crops")).json();
  spec.defaults = spec.defaults || { character: {}, course: null };
  spec.defaults.character = spec.defaults.character || {};
  for (const c of ["combos", "karts", "courses"]) spec[c] = spec[c] || {};
  if (items.length) show(0);
}

function aspect() { return (spec.meta && spec.meta.crop_aspect) || 1.0; }
function charOf(name) { return name.split("__")[0]; }
function cur() { return items[idx]; }

function effRect(it) {
  // explicit override -> character/course default -> a centered starting box
  const ov = spec[it.category][it.name];
  if (ov) return { ...ov };
  if (it.category === "combos") {
    const d = spec.defaults.character[charOf(it.name)];
    if (d) return { ...d };
  }
  if (it.category === "courses" && spec.defaults.course) return { ...spec.defaults.course };
  const h = 360, w = Math.round(h * aspect());
  return { x: Math.round((CAP_W - w) / 2), y: Math.round((CAP_H - h) / 2), w, h };
}

function show(i) {
  idx = (i + items.length) % items.length;
  const it = cur();
  img.src = it.url;
  img.onload = () => {
    scale = img.clientWidth / CAP_W;
    drawBox(effRect(it));
    layoutForCategory(it);
    renderPreview();
  };
  $("itemName").textContent = it.name;
  $("itemCat").textContent = it.category;
  $("counter").textContent = `${idx + 1} / ${items.length}`;
  $("saved").textContent = "";
}

let rect = { x: 0, y: 0, w: 0, h: 0 };
function drawBox(r) {
  rect = clampRect(r);
  box.style.left = rect.x * scale + "px";
  box.style.top = rect.y * scale + "px";
  box.style.width = rect.w * scale + "px";
  box.style.height = rect.h * scale + "px";
  renderPreview();
}
function clampRect(r) {
  const w = Math.max(20, Math.min(r.w, CAP_W));
  const h = Math.round(w / aspect());
  const x = Math.max(0, Math.min(r.x, CAP_W - w));
  const y = Math.max(0, Math.min(r.y, CAP_H - h));
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) };
}

function renderPreview() {
  const pv = $("pvImg"), chipH = 48, s = chipH / rect.h;
  pv.src = cur().url;
  pv.style.width = CAP_W * s + "px";
  pv.style.height = CAP_H * s + "px";
  pv.style.transform = `translate(${-rect.x * s}px, ${-rect.y * s}px)`;
}

function layoutForCategory(it) {
  $("comboControls").hidden = it.category !== "combos";
  $("courseControls").hidden = it.category !== "courses";
  if (it.category === "combos") $("overrideCombo").checked = !!spec.combos[it.name];
  if (it.category === "courses") $("overrideCourse").checked = !!spec.courses[it.name];
}

// Persist the current rect into the spec per the category + override toggles.
function commitCurrent() {
  const it = cur(), r = { ...rect };
  if (it.category === "karts") { spec.karts[it.name] = r; return; }
  if (it.category === "courses") {
    if ($("overrideCourse").checked) spec.courses[it.name] = r;
    else { spec.defaults.course = r; delete spec.courses[it.name]; }
    return;
  }
  // combos
  if ($("overrideCombo").checked) spec.combos[it.name] = r;
  else delete spec.combos[it.name];   // inherit the character default
}

// --- drag/resize ---
let drag = null;
box.addEventListener("pointerdown", (e) => {
  e.preventDefault(); box.setPointerCapture(e.pointerId);
  const resize = e.target.classList.contains("h");
  drag = { resize, sx: e.clientX, sy: e.clientY, r0: { ...rect } };
});
box.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = (e.clientX - drag.sx) / scale, dy = (e.clientY - drag.sy) / scale;
  if (drag.resize) drawBox({ ...drag.r0, w: drag.r0.w + dx });
  else drawBox({ ...drag.r0, x: drag.r0.x + dx, y: drag.r0.y + dy });
});
box.addEventListener("pointerup", () => { drag = null; commitCurrent(); });

$("prev").onclick = () => { commitCurrent(); show(idx - 1); };
$("next").onclick = () => { commitCurrent(); show(idx + 1); };
$("nextUnset").onclick = () => {
  commitCurrent();
  for (let k = 1; k <= items.length; k++) {
    const j = (idx + k) % items.length, it = items[j];
    const has = spec[it.category][it.name]
      || (it.category === "combos" && spec.defaults.character[charOf(it.name)])
      || (it.category === "courses" && spec.defaults.course);
    if (!has) { show(j); return; }
  }
  show(idx);
};
$("setCharDefault").onclick = () => {
  spec.defaults.character[charOf(cur().name)] = { ...rect };
  $("overrideCombo").checked = false; delete spec.combos[cur().name];
};
$("setCourseDefault").onclick = () => {
  spec.defaults.course = { ...rect };
  $("overrideCourse").checked = false; delete spec.courses[cur().name];
};
$("overrideCombo").onchange = commitCurrent;
$("overrideCourse").onchange = commitCurrent;
$("save").onclick = async () => {
  commitCurrent();
  await fetch("/api/crops", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec) });
  $("saved").textContent = "saved ✓";
};
boot();
</script>
</body>
</html>
```

- [ ] **Step 2: Manual verify** — capture a couple of items first (Task 2), then:

Run: `python scripts/chip_cropper_server.py --lang en_uk`
Open `http://localhost:8777/`. Confirm: image loads; box drags/resizes with locked aspect; the chip preview updates live and looks like the feed chip; "set as character default" makes the next same-character combo inherit; Save writes `tools/chips.crops.json` (verify the file). Then run `python scripts/gen_chips.py --lang en_uk` and confirm PNGs in `web/public/chips/`.

- [ ] **Step 3: Commit**

```bash
git add tools/chip-cropper.html
git commit -m "feat(chips): interactive crop tool with live chip preview"
```

---

## PHASE 2 — Server: surface character/kart/costume on run events

### Task 7: Cascade payload carries character/kart/costume

**Files:**
- Modify: `pi/src/activity/cascade.ts` (`RunCascadeArgs` ~6-12; `pb` payload ~20-22; `rank` payload ~25-27)
- Test: `pi/src/activity/cascade.test.ts` (append)

**Interfaces:**
- Produces: `RunCascadeArgs` gains optional `character?: string | null; kart?: string | null; costume?: string | null` (DISPLAY names). `pb` and `rank` event payloads gain `character`, `kart`, `costume`.

- [ ] **Step 1: Write the failing test** — append to `pi/src/activity/cascade.test.ts`:

```typescript
  it('pb and rank payloads carry the mover display character/kart/costume', () => {
    const before = [row(2, 108221, 1), row(1, 108600, 2)];
    const after = [row(1, 107980, 1), row(2, 108221, 2)];
    const out = buildRunCascade({
      ts: 1000, seasonId: 1, cc: 150, courseId: 1, moverId: 1, moverName: 'P1',
      before, after, beforeWr: null, afterWr: null, prevPbMs: 108410,
      character: 'Koopa Troopa', kart: 'Baby Blooper', costume: null,
    });
    const pb = out.find(e => e.type === 'pb')!;
    expect(pb.payload).toMatchObject({ character: 'Koopa Troopa', kart: 'Baby Blooper', costume: null });
    const rank = out.find(e => e.type === 'rank')!;
    expect(rank.payload).toMatchObject({ character: 'Koopa Troopa', kart: 'Baby Blooper' });
  });
```

- [ ] **Step 2: Run, expect FAIL**

Run: `npm --prefix pi test -- cascade`
Expected: FAIL — payload lacks `character`/`kart`/`costume`.

- [ ] **Step 3: Implement** — in `pi/src/activity/cascade.ts`:

Add to `RunCascadeArgs` (after `prevPbMs`):

```typescript
  character?: string | null; kart?: string | null; costume?: string | null;
```

Inside `buildRunCascade`, after the `const base = {...}` line add:

```typescript
  const sel = { character: a.character ?? null, kart: a.kart ?? null, costume: a.costume ?? null };
```

Spread `sel` into the `pb` and `rank` payloads:

```typescript
  out.push({ ...base, type: 'pb', player_id: a.moverId,
    payload: { time_ms: mine.total_time_ms, time_str: mine.total_time_str,
               delta_ms: a.prevPbMs != null ? mine.total_time_ms - a.prevPbMs : null, ...sel } });

  for (const g of rankGains(a.before, a.after, a.moverId))
    out.push({ ...base, type: 'rank', player_id: a.moverId,
      payload: { place: g.place, rival_id: g.rivalId, rival_name: g.rivalName,
                 rival_time_ms: g.rivalTimeMs, gap_ms: g.rivalTimeMs - mine.total_time_ms, ...sel } });
```

- [ ] **Step 4: Run, expect PASS**

Run: `npm --prefix pi test -- cascade`
Expected: PASS (existing cascade tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add pi/src/activity/cascade.ts pi/src/activity/cascade.test.ts
git commit -m "feat(activity): pb/rank events carry character/kart/costume"
```

---

### Task 8: Wire both cascade call sites

Pass the run's character/kart/costume into the cascade — live (`runs.ts`, from the posted payload) and historical (`backfill.ts`, from the run row).

**Files:**
- Modify: `pi/src/api/runs.ts:110-114` (the `buildRunCascade({...})` call)
- Modify: `pi/src/activity/backfill.ts` (`RunRow` type ~11-20, the `SELECT` ~37-43, the `buildRunCascade({...})` call ~90-102)

**Interfaces:**
- Consumes: `RunCascadeArgs.character/kart/costume` (Task 7). `AttemptPayload` already has `character?/kart?/costume?` (`pi/src/db/types.ts:8`). The `runs` table has `character`/`kart`/`costume` columns.

- [ ] **Step 1: Modify `runs.ts`** — extend the cascade args:

```typescript
      const inputs = buildRunCascade({
        ts: Date.now(), seasonId, cc, courseId, moverId: playerId, moverName: playerName,
        before: beforeBoard, after: lb, beforeWr: wrMs, afterWr: wrMs,
        prevPbMs: prevMineMs,
        character: p.character ?? null, kart: p.kart ?? null, costume: p.costume ?? null,
      });
```

- [ ] **Step 2: Modify `backfill.ts`** — add the columns to `RunRow`:

```typescript
type RunRow = {
  id: number;
  season_id: number;
  player_id: number;
  course_id: number;
  cc: number;
  total_time_ms: number;
  total_time_str: string | null;
  ended_at: string;
  character: string | null;
  kart: string | null;
  costume: string | null;
};
```

Add them to the SELECT:

```typescript
  const runs = db.prepare(
    `SELECT id, season_id, player_id, course_id, cc, total_time_ms, total_time_str, ended_at,
            character, kart, costume
     FROM runs
     WHERE status='finished' AND provenance != 'carryover'
       AND total_time_ms IS NOT NULL AND ended_at IS NOT NULL
     ORDER BY ended_at ASC, id ASC`
  ).all() as RunRow[];
```

Pass them into the cascade call (after `prevPbMs,`):

```typescript
      prevPbMs,
      character: run.character, kart: run.kart, costume: run.costume,
```

- [ ] **Step 3: Verify the package is green + typechecks**

Run: `npm --prefix pi test`
Expected: PASS (whole pi suite).
Run: `npm --prefix pi run typecheck`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add pi/src/api/runs.ts pi/src/activity/backfill.ts
git commit -m "feat(activity): thread run character/kart/costume into both cascade call sites"
```

---

## PHASE 3 — Frontend: render the chips

### Task 9: Chip mapping (`chips.js`)

Slugify display names (matching `pi/src/db/slug.ts`) and map a store row to ready-to-render chips.

**Files:**
- Create: `web/src/lib/chips.js`
- Test: `web/src/lib/chips.test.js`

**Interfaces:**
- Produces:
  - `slugify(name) -> string`
  - `chipsFor(row) -> Array<{ src, fallback, alt }>` — ordered course, kart, character; `[]` for rows without chips.
  - `chipUrl(category, slug) -> string` (`/chips/<category>/<slug>.png`).

- [ ] **Step 1: Write the failing tests** — `web/src/lib/chips.test.js`:

```javascript
import { describe, it, expect } from "vitest";
import { slugify, chipsFor, chipUrl } from "./chips.js";

describe("slugify (parity with pi/src/db/slug.ts)", () => {
  it("drops apostrophes and underscores non-alnum runs", () => {
    expect(slugify("Bowser's Castle")).toBe("bowsers_castle");
    expect(slugify("Koopa Troopa")).toBe("koopa_troopa");
    expect(slugify("Mario Bros. Circuit")).toBe("mario_bros_circuit");
    expect(slugify("DK Pass")).toBe("dk_pass");
  });
});

describe("chipUrl", () => {
  it("builds a public chips path", () => {
    expect(chipUrl("courses", "dk_pass")).toBe("/chips/courses/dk_pass.png");
  });
});

describe("chipsFor", () => {
  const pbRow = (payload, courseSlug = "dk_pass") => ({
    kind: "event",
    event: { type: "pb", course: { slug: courseSlug, name: "DK Pass" }, payload },
  });

  it("pb: course, kart, character (combo with base costume)", () => {
    const chips = chipsFor(pbRow({ character: "Koopa Troopa", kart: "Baby Blooper", costume: null }));
    expect(chips.map(c => c.src)).toEqual([
      "/chips/courses/dk_pass.png",
      "/chips/karts/baby_blooper.png",
      "/chips/combos/koopa_troopa__base.png",
    ]);
    expect(chips[2].fallback).toBe("/chips/combos/koopa_troopa__base.png");
  });

  it("pb with a costume: combo uses the costume, fallback is __base", () => {
    const chips = chipsFor(pbRow({ character: "Peach", kart: "Hot Rod", costume: "Aero" }));
    const combo = chips.find(c => c.src.includes("combos"));
    expect(combo.src).toBe("/chips/combos/peach__aero.png");
    expect(combo.fallback).toBe("/chips/combos/peach__base.png");
  });

  it("racing session: course + character, no kart", () => {
    const chips = chipsFor({ kind: "session", cls: "racing",
      course: { slug: "crown_city", name: "Crown City" }, character: "Mario", costume: null });
    expect(chips.map(c => c.src)).toEqual([
      "/chips/courses/crown_city.png",
      "/chips/combos/mario__base.png",
    ]);
  });

  it("turf/wr: course only; presence/off-track: none", () => {
    expect(chipsFor({ kind: "event", event: { type: "wr",
      course: { slug: "dk_pass", name: "DK Pass" }, payload: {} } }).map(c => c.src))
      .toEqual(["/chips/courses/dk_pass.png"]);
    expect(chipsFor({ kind: "event", event: { type: "presence", course: null, payload: {} } })).toEqual([]);
    expect(chipsFor({ kind: "session", cls: "menus", course: null })).toEqual([]);
  });
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `npm --prefix web test -- chips`
Expected: FAIL — `chips.js` not found.

- [ ] **Step 3: Implement** — `web/src/lib/chips.js`:

```javascript
// Map an activity store row to render-ready chips. The single slug authority for chip
// filenames: mirrors pi/src/db/slug.ts:slugify exactly (capture basenames already match it).
// Server events carry DISPLAY names; we slugify here. Missing assets are hidden at render.

export function slugify(name) {
  return String(name ?? "")
    .toLowerCase()
    .replace(/[‘’']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function chipUrl(category, slug) {
  return `/chips/${category}/${slug}.png`;
}

function comboSlug(character, costume) {
  const c = slugify(character);
  if (!c) return null;
  const k = costume ? slugify(costume) : "base";
  return `${c}__${k}`;
}

function courseChip(course) {
  return course?.slug
    ? [{ src: chipUrl("courses", course.slug), fallback: null, alt: course.name ?? "" }]
    : [];
}

function characterChip(character, costume) {
  const combo = comboSlug(character, costume);
  if (!combo) return [];
  const base = comboSlug(character, null);
  return [{ src: chipUrl("combos", combo), fallback: chipUrl("combos", base), alt: character ?? "" }];
}

/** Ordered chips (course, kart, character) for a store row; [] when none apply. */
export function chipsFor(row) {
  if (!row) return [];
  if (row.kind === "session") {
    if (row.cls !== "racing") return [];
    return [...courseChip(row.course), ...characterChip(row.character, row.costume)];
  }
  const e = row.event;
  if (!e) return [];
  switch (e.type) {
    case "pb":
    case "rank": {
      const pay = e.payload || {};
      const kart = pay.kart
        ? [{ src: chipUrl("karts", slugify(pay.kart)), fallback: null, alt: pay.kart }]
        : [];
      return [...courseChip(e.course), ...kart, ...characterChip(pay.character, pay.costume)];
    }
    case "turf_claim":
    case "turf_fire":
    case "turf_waver":
    case "wr":
      return courseChip(e.course);
    default:
      return [];
  }
}
```

- [ ] **Step 4: Run, expect PASS**

Run: `npm --prefix web test -- chips`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/chips.js web/src/lib/chips.test.js
git commit -m "feat(web): chip mapping + slug parity for the activity feed"
```

---

### Task 10: Attach chips in `activityFormat`

`toRow` returns a `chips` array alongside the render spans.

**Files:**
- Modify: `web/src/lib/activityFormat.js` (`toRow`, ~61-63; add import)
- Test: `web/src/lib/activityFormat.test.js` (append)

**Interfaces:**
- Consumes: `chipsFor` (Task 9). Produces: each `toRow(...)` result carries `chips: Array<{src,fallback,alt}>`.

- [ ] **Step 1: Write the failing test** — append to `web/src/lib/activityFormat.test.js`:

```javascript
import { toRow } from "./activityFormat.js";

describe("toRow chips", () => {
  it("attaches pb chips (course, kart, character)", () => {
    const row = { kind: "event", key: "evt:1",
      event: { id: 1, ts: 0, type: "pb", course: { slug: "dk_pass", name: "DK Pass" },
               player: { name: "P", color: null },
               payload: { time_ms: 1000, delta_ms: null, character: "Mario", kart: "Hot Rod", costume: null } } };
    const out = toRow(row, 0);
    expect(out.chips.map(c => c.src)).toEqual([
      "/chips/courses/dk_pass.png", "/chips/karts/hot_rod.png", "/chips/combos/mario__base.png",
    ]);
  });

  it("presence rows carry no chips", () => {
    const row = { kind: "event", key: "evt:2",
      event: { id: 2, ts: 0, type: "presence", course: null,
               player: { name: "P", color: null }, payload: { online: true } } };
    expect(toRow(row, 0).chips).toEqual([]);
  });
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `npm --prefix web test -- activityFormat`
Expected: FAIL — `out.chips` is undefined.

- [ ] **Step 3: Implement** — in `web/src/lib/activityFormat.js`:

Add the import near the top (after the header comment):

```javascript
import { chipsFor } from "./chips.js";
```

Replace `toRow`:

```javascript
/** A normalized store row -> a structured render row the component draws span-by-span. */
export function toRow(row, now) {
  const out = row.kind === "session" ? sessionRow(row, now) : milestoneRow(row, now);
  return out ? { ...out, chips: chipsFor(row) } : out;
}
```

- [ ] **Step 4: Run, expect PASS**

Run: `npm --prefix web test -- activityFormat`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/activityFormat.js web/src/lib/activityFormat.test.js
git commit -m "feat(web): attach chips to activity rows in toRow"
```

---

### Task 11: Render the chip cluster in `ActivityLog.svelte`

Draw `r.chips` at the right edge of each row with the parallelogram chip CSS; hide a chip whose asset (and fallback) is missing.

**Files:**
- Modify: `web/src/ActivityLog.svelte` (markup ~20-25; `<style>` ~30-52)

- [ ] **Step 1: Implement the markup** — add a 5th grid cell: a sibling `.chips` div right after `<div class="what">…</div>` (keep `.what` exactly as-is so its text flow is untouched). The row's children become when/who/where/what/chips:

```svelte
        <div class="what">{#each r.what as s, i (i)}<span class={s.cls} style={s.color ? `color:${s.color}` : ""}>{s.text}</span>{/each}</div>
        <div class="chips">{#if r.chips?.length}{#each r.chips as c (c.src)}<span class="chip-wrap"><span class="chip"><img src={c.src} alt={c.alt} loading="lazy" on:error={(e) => onChipErr(e, c.fallback)} /></span></span>{/each}{/if}</div>
```

- [ ] **Step 2: Add the error handler** — in the `<script>` block (after the `$: rows = …` line):

```javascript
  function onChipErr(e, fallback) {
    const img = e.currentTarget;
    if (fallback && !img.dataset.fb) { img.dataset.fb = "1"; img.src = fallback; return; }
    const wrap = img.closest(".chip-wrap");
    if (wrap) wrap.style.display = "none";
  }
```

- [ ] **Step 3: Add the styles** — add a trailing `auto` column to the grid and the chip CSS (leave `.what` unchanged so text flow is preserved):

Change the `.row` rule's `grid-template-columns` (add the trailing `auto`):

```css
  .row { display: grid; grid-template-columns: 112px 74px 150px 1fr auto; align-items: baseline; column-gap: 12px;
         padding: 7px 14px 7px 12px; border-bottom: 1px solid var(--bd-soft); border-left: 2px solid transparent;
         background: var(--panel); font-size: 12.5px; }
```

Append the chip CSS:

```css
  .chips { align-self: center; display: flex; gap: 4px; flex: none; }
  .chip-wrap { filter: drop-shadow(0 1px 1.5px rgba(0,0,0,.55)); line-height: 0; display: inline-block; }
  .chip { display: inline-block; height: 26px; aspect-ratio: 1 / 1; overflow: hidden;
          clip-path: polygon(16% 0, 100% 0, 84% 100%, 0 100%);
          box-shadow: inset 0 0 0 1px rgba(255,255,255,.16); background: #15171c; }
  .chip :global(img) { width: 100%; height: 100%; object-fit: cover; display: block; }
```

- [ ] **Step 4: Typecheck + build**

Run: `npm --prefix web run check`
Expected: 0 errors / 0 warnings.
Run: `npm --prefix web run build`
Expected: build succeeds.

- [ ] **Step 5: Manual verify** — with chips exported (Task 6) and the server running, `npm --prefix web run dev`, open the activity feed: PB/rank rows show course+kart+character chips at the right; turf/wr show the course; presence rows show none; a missing asset leaves a gap (no broken-image icon), the row text intact.

- [ ] **Step 6: Commit**

```bash
git add web/src/ActivityLog.svelte
git commit -m "feat(web): render selection chips on activity feed rows"
```

---

## End-to-end verification

1. **Capture (HDR off on the Switch):** `python -m mkw_tracker.tools.capture_sources --combos --lang en_uk` → combos in `captures_sdr/en_uk/combos/`, karts/courses in their folders.
2. **Crop:** `python scripts/chip_cropper_server.py --lang en_uk`, open `http://localhost:8777/`, frame each item (set character defaults, override outliers), Save → `tools/chips.crops.json`.
3. **Export:** `python scripts/gen_chips.py --lang en_uk` → PNGs in `web/public/chips/{combos,karts,courses}/`. Confirm they are NOT LFS pointers (`git check-attr filter web/public/chips/combos/*.png` shows no `lfs`).
4. **Data:** restart the Pi server; a PB upload yields a `pb` event whose payload includes `character`/`kart`/`costume` (inspect `GET /v1/activity`).
5. **Render:** `npm --prefix web run dev` → chips appear on the feed.
6. **Tests:** `python -m pytest tests/test_capture_sources.py tests/test_gen_chips.py tests/test_chip_cropper_server.py -v`; `npm --prefix pi test`; `npm --prefix web test`; `npm --prefix web run check`.

## Notes / deferred

- **Slug contract:** chip filenames = capture basenames = `slugify(display)`. If a name ever diverges, the chip silently hides (fallback then `display:none`) — non-fatal; fix by renaming the capture or adding an override.
- Character chip on `turf_claim` (claimer's run) is deferred — v1 turf/wr rows are course-only.
- The HDR `captures/` template set is untouched; only `captures_sdr/` feeds chips.
- `captures_sdr/` (bulky) is dev-source — track via Git LFS or keep local; never needed by the Pi.
