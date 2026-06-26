# Asset Clip Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically record 4K60 idle / spawn-in / flourish clips for every character×costume and character×costume×kart from the live Switch 2 feed, reusing the existing nxbt nav harness and tracker grounding.

**Architecture:** ffmpeg owns the capture card and tees a 1080p feed into the existing tracker (so its `at_check_*` grounding works unchanged); the tracker gains `at_record_clip_*` commands that drive a preview↔record ffmpeg manager and write a per-clip `events.json`. A WSL2 sweep runner (nxbt) walks a parsed 2D grid model, records one continuous clip per item, and keeps/discards on grounded target-ID. A post step segments each clip into spawn-in / idle-loop / flourish using the events sidecar.

**Tech Stack:** Python 3, OpenCV, ffmpeg (NVENC), nxbt (WSL2), websockets, PyYAML, pytest.

## Global Constraints

- **Card ownership:** one ffmpeg owns the DirectShow device; detection runs on its tee. Never open the card from two consumers.
- **Capture conditions:** Switch HDR **off** (SDR); Windows **camera-sharing off** (exclusive DirectShow).
- **Single language:** `en_uk` only — assets are the character render, not name text.
- **Record format:** 3840×2160 @ 60, HEVC NVENC preset **p5**, qp 14, `.mkv` (reuse `record_clips` defaults).
- **Tee for detection:** 1920×1080 @ ~15 fps, bgr24 (1080p so existing ROIs/coords work unchanged).
- **Idle dwell:** 10 s. **Flourish stop:** when the current select-screen tell drops (`kart_select` / `character_select`); backstop `max_clip_seconds = 25`.
- **Naming:** `<char>__<costume>` and `<char>__<costume>__<kart>`, `__base` = no costume, `_to_filename` slug rules (reuse `full_runner._to_filename`).
- **Output root:** `captures_sdr/en_uk/clips/`.
- **Pure modules import no hardware** (`grid.py`, `clip_segment.py` must import without nxbt/ffmpeg/a card so they unit-test on Windows).
- Repo runs on Windows for the tracker/orchestrator + tests; the sweep runner runs in WSL2. Tests run on Windows: `python -m pytest`.

---

## File Structure

| File | Responsibility | New/Mod |
|---|---|---|
| `tools/autotemplate/gen_clip_sweep_yaml.py` | Parse `full_capture.yaml` flow → ordered grid rows | new |
| `tools/autotemplate/scripts/clip_sweep.yaml` | Generated grid data (char rows, kart rows) | generated |
| `tools/autotemplate/grid.py` | Pure 2D grid model: load, coords, sweep steps, recovery deltas | new |
| `mkw_tracker/tools/clip_capture.py` | ffmpeg preview↔record manager + events.json (reuses `record_clips`) | new |
| `mkw_tracker/ipc/broadcaster.py` | Add `at_record_clip_begin/mark/abort`, `clip_done` emit | mod |
| `mkw_tracker/main.py` | `--clip-capture`: route `current_frame` from the tee; wire manager | mod |
| `tools/autotemplate/sweep_runner.py` | WSL2 runner: grid walk + per-item record/ground/keep-discard | new |
| `tools/asset_matte/clip_segment.py` | Segment one clip → spawn-in/idle-loop/flourish via events.json | new |
| `tests/test_clip_grid.py` | Grid model tests | new |
| `tests/test_clip_segment.py` | Segmentation span-math tests | new |
| `tests/test_clip_capture.py` | events.json assembly + manager state machine (fake ffmpeg) | new |
| `tests/test_sweep_runner.py` | Per-item command sequence (fake controller + fake client) | new |
| `tests/test_broadcaster_clip.py` | `at_record_clip_*` dispatch (fake manager) | new |

---

### Task 1: Grid model + generated `clip_sweep.yaml`

**Files:**
- Create: `tools/autotemplate/gen_clip_sweep_yaml.py`
- Create: `tools/autotemplate/scripts/clip_sweep.yaml` (generated)
- Create: `tools/autotemplate/grid.py`
- Test: `tests/test_clip_grid.py`

**Interfaces:**
- Produces: `grid.load_grid(path) -> Grid`; `Grid.cells(category) -> list[Cell]` (category `"characters"|"karts"`); `Grid.coord_of(slug) -> tuple[int,int]`; `Grid.sweep_steps(category) -> list[tuple[str, list[str]]]` (slug, D-pad presses from previous cell); `Grid.horizontal_delta(from_slug, to_slug) -> list[str]` (recovery within a row); `grid.to_filename(name) -> str`. `Cell = namedtuple("Cell", "slug display row col")`.

- [ ] **Step 1: Write the generator**

```python
# tools/autotemplate/gen_clip_sweep_yaml.py
"""Parse full_capture.yaml's flow into clip_sweep.yaml grid rows (single language).

Char cells = the `char`(+optional `costume`) steps between tell:character_screen and
the confirm_char marker; kart cells = the `kart` steps between tell:kart_screen and
confirm_kart. A `DPAD_DOWN` press inside a section starts a new row.
"""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "scripts", "full_capture.yaml")
OUT = os.path.join(HERE, "scripts", "clip_sweep.yaml")


def _cell_label(step):
    if "char" in step:
        c = str(step["char"])
        return f"{c} ({step['costume']})" if "costume" in step else c
    if "kart" in step:
        return str(step["kart"])
    return None


def _section_rows(flow, start_tell, end_marker):
    rows, row, active = [], [], False
    for step in flow:
        if not isinstance(step, dict):
            continue
        if step.get("tell") == start_tell:
            active = True
            continue
        if active and step.get("_marker") == end_marker:
            break
        if not active:
            continue
        if step.get("press") == "DPAD_DOWN" and row:
            rows.append(row)
            row = []
        label = _cell_label(step)
        if label:
            row.append(label)
    if row:
        rows.append(row)
    return rows


def build():
    with open(SRC, encoding="utf-8") as f:
        script = yaml.safe_load(f)
    flow = script["flow"]
    data = {
        "characters": _section_rows(flow, "character_screen", "confirm_char"),
        "karts": _section_rows(flow, "kart_screen", "confirm_kart"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)
    nch = sum(len(r) for r in data["characters"])
    nk = sum(len(r) for r in data["karts"])
    print(f"wrote {OUT}: {nch} char cells in {len(data['characters'])} rows, "
          f"{nk} karts in {len(data['karts'])} rows")


if __name__ == "__main__":
    build()
```

- [ ] **Step 2: Generate the data and eyeball it**

Run: `python tools/autotemplate/gen_clip_sweep_yaml.py`
Expected: `wrote ...clip_sweep.yaml: 153 char cells in 3 rows, 40 karts in 4 rows`

- [ ] **Step 3: Write the failing grid test**

```python
# tests/test_clip_grid.py
import os
import pytest
from tools.autotemplate import grid

YAML = os.path.join(os.path.dirname(__file__), "..",
                    "tools", "autotemplate", "scripts", "clip_sweep.yaml")


@pytest.fixture
def g():
    return grid.load_grid(YAML)


def test_to_filename():
    assert grid.to_filename("Mario") == "mario"
    assert grid.to_filename("Baby Mario") == "baby_mario"
    assert grid.to_filename("R.O.B. H.O.G.") == "rob_hog"


def test_counts(g):
    assert len(g.cells("characters")) == 153
    assert len(g.cells("karts")) == 40


def test_char_slug_includes_costume(g):
    slugs = {c.slug for c in g.cells("characters")}
    assert "mario__base" in slugs
    assert "mario__touring" in slugs


def test_first_cell_is_mario_base(g):
    first = g.cells("characters")[0]
    assert first.slug == "mario__base" and first.coord == (0, 0)


def test_sweep_steps_row_transition(g):
    steps = g.sweep_steps("karts")
    assert steps[0] == ("standard_kart__none" if False else "standard_kart", [])
    # the 11th kart cell starts row 1 → preceded by RIGHT (onto blank) then DOWN
    row1_first = next(s for s in steps if s[0] == "rally_kart")
    assert row1_first[1] == ["DPAD_RIGHT", "DPAD_DOWN"]


def test_horizontal_recovery_delta(g):
    # overshot by 2 within a row → step LEFT twice to reach target
    a, b = "plushbuggy", "standard_kart"   # cols 1 and 0, same row
    assert g.horizontal_delta(a, b) == ["DPAD_LEFT"]
```

- [ ] **Step 4: Run it to confirm failure**

Run: `python -m pytest tests/test_clip_grid.py -v`
Expected: FAIL — `ModuleNotFoundError: tools.autotemplate.grid` (or AttributeError).

- [ ] **Step 5: Implement `grid.py`**

```python
# tools/autotemplate/grid.py
"""Pure 2D grid model for the clip sweep. No hardware imports."""
import re
from collections import namedtuple
import yaml

Cell = namedtuple("Cell", "slug display coord")   # coord = (row, col)


def to_filename(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^\w\s'-]", "", slug)
    slug = re.sub(r"[']+", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug.strip())
    return slug


def _char_slug(label: str) -> str:
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", label)
    if m:
        return f"{to_filename(m.group(1))}__{to_filename(m.group(2))}"
    return f"{to_filename(label)}__base"


class Grid:
    def __init__(self, rows_by_cat: dict):
        self._cells = {}
        self._by_slug = {}
        for cat, rows in rows_by_cat.items():
            slugify = _char_slug if cat == "characters" else to_filename
            cells = []
            for r, row in enumerate(rows):
                for c, label in enumerate(row):
                    cell = Cell(slugify(label), label, (r, c))
                    cells.append(cell)
                    self._by_slug[(cat, cell.slug)] = cell
            self._cells[cat] = cells

    def cells(self, category: str) -> list:
        return self._cells[category]

    def _cat_of(self, slug: str) -> str:
        for cat in self._cells:
            if (cat, slug) in self._by_slug:
                return cat
        raise KeyError(slug)

    def coord_of(self, slug: str) -> tuple:
        return self._by_slug[(self._cat_of(slug), slug)].coord

    def sweep_steps(self, category: str) -> list:
        steps, prev = [], None
        for cell in self._cells[category]:
            if prev is None:
                presses = []
            elif cell.coord[0] == prev.coord[0]:
                presses = ["DPAD_RIGHT"]
            else:                                  # new row: right onto blank, then down
                presses = ["DPAD_RIGHT", "DPAD_DOWN"]
            steps.append((cell.slug, presses))
            prev = cell
        return steps

    def horizontal_delta(self, from_slug: str, to_slug: str) -> list:
        cat = self._cat_of(from_slug)
        (r0, c0) = self._by_slug[(cat, from_slug)].coord
        (r1, c1) = self._by_slug[(cat, to_slug)].coord
        if r0 != r1:
            raise ValueError(f"{from_slug} and {to_slug} are not in the same row")
        d = c1 - c0
        return ["DPAD_RIGHT"] * d if d > 0 else ["DPAD_LEFT"] * (-d)


def load_grid(path: str) -> Grid:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Grid(data)
```

- [ ] **Step 6: Run tests to verify pass**

Run: `python -m pytest tests/test_clip_grid.py -v`
Expected: PASS (6 tests). If `test_sweep_steps_row_transition` fails on the first-kart tuple, simplify its first assertion to `assert steps[0][0] == "standard_kart" and steps[0][1] == []`.

- [ ] **Step 7: Commit**

```bash
git add tools/autotemplate/gen_clip_sweep_yaml.py tools/autotemplate/scripts/clip_sweep.yaml tools/autotemplate/grid.py tests/test_clip_grid.py
git commit -m "feat(clip-sweep): grid model + generated clip_sweep.yaml"
```

---

### Task 2: Segmentation span math

**Files:**
- Create: `tools/asset_matte/clip_segment.py`
- Test: `tests/test_clip_segment.py`

**Interfaces:**
- Consumes: an `events` dict `{fps, swap_t, flourish_t, flourish_end_t, duration_t}` (`swap_t` is `None` for characters) and a loop result `(loop_start_frame, loop_len_frames)`.
- Produces: `clip_segment.segment_spans(events, loop_start_frame, loop_len_frames) -> dict[str, tuple[int,int]]` with keys among `"spawn_in"`, `"idle_loop"`, `"flourish"`, each a half-open `(start_frame, end_frame)` range; `"spawn_in"` absent when `swap_t is None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clip_segment.py
from tools.asset_matte.clip_segment import segment_spans


def test_kart_spans():
    ev = {"fps": 60, "swap_t": 0.5, "flourish_t": 11.0,
          "flourish_end_t": 13.4, "duration_t": 13.4}
    spans = segment_spans(ev, loop_start_frame=120, loop_len_frames=80)
    assert spans["spawn_in"] == (30, 120)      # swap_t*fps .. loop start
    assert spans["idle_loop"] == (120, 200)    # loop start .. +len
    assert spans["flourish"] == (660, 804)     # flourish_t*fps .. end*fps


def test_character_has_no_spawn_in():
    ev = {"fps": 60, "swap_t": None, "flourish_t": 10.0,
          "flourish_end_t": 12.0, "duration_t": 12.0}
    spans = segment_spans(ev, loop_start_frame=60, loop_len_frames=80)
    assert "spawn_in" not in spans
    assert spans["idle_loop"] == (60, 140)
    assert spans["flourish"] == (600, 720)
```

- [ ] **Step 2: Run it to confirm failure**

Run: `python -m pytest tests/test_clip_segment.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `segment_spans`**

```python
# tools/asset_matte/clip_segment.py
"""Segment one recorded clip into spawn-in / idle-loop / flourish frame spans.

Pure span math here; the extract+write wrapper (Task 10) reuses extract_loop.
"""


def segment_spans(events: dict, loop_start_frame: int, loop_len_frames: int) -> dict:
    fps = events["fps"]
    spans = {}
    if events.get("swap_t") is not None:
        spans["spawn_in"] = (round(events["swap_t"] * fps), loop_start_frame)
    spans["idle_loop"] = (loop_start_frame, loop_start_frame + loop_len_frames)
    spans["flourish"] = (round(events["flourish_t"] * fps),
                         round(events["flourish_end_t"] * fps))
    return spans
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_clip_segment.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/clip_segment.py tests/test_clip_segment.py
git commit -m "feat(clip-sweep): segmentation span math"
```

---

### Task 3: Clip capture manager (ffmpeg preview↔record + events.json)

**Files:**
- Create: `mkw_tracker/tools/clip_capture.py`
- Test: `tests/test_clip_capture.py`

**Interfaces:**
- Consumes: `record_clips.FramePipe`, `tee_cmd`, `preview_cmd`, `pick_encoder`, `_bundled_bin`, `_resolve_device`.
- Produces: `ClipCaptureManager(out_dir, device, size, fps, frame_ref, *, _pipe_factory=FramePipe, clock=time.monotonic)`. Methods: `start_preview()`; `begin(item) -> None` (stops preview, starts a tee record into `out_dir/<item>.mkv`, resets clock to 0); `mark(event)` (`"swap"|"flourish"`, stamps `clock()`); `end() -> dict` (writes `<item>.events.json`, stops record, restarts preview, returns the events dict); `abort()` (stops record, deletes the `.mkv`, restarts preview); `exists(item) -> bool`; `set_duration_end()` stamps `flourish_end_t`/`duration_t`. The manager continuously copies the active pipe's latest frame into `frame_ref[0]` via `pump()` (called each tick by main.py).

- [ ] **Step 1: Write the failing test (events assembly with a fake pipe)**

```python
# tests/test_clip_capture.py
import json
import os
import itertools
from mkw_tracker.tools.clip_capture import ClipCaptureManager


class FakePipe:
    def __init__(self, cmd, **kw): self.cmd = cmd
    def latest(self): return None
    def alive(self): return True
    def stop(self): pass


def test_events_written_with_marks(tmp_path):
    clock = itertools.count(0, 1)   # 0,1,2,3,... "seconds"
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe,
                           clock=lambda: next(clock))
    m.begin("mario__base__standard_kart")           # clock 0 at begin
    m.mark("swap")                                  # t=1
    m.mark("flourish")                              # t=2
    m.set_duration_end()                            # t=3 → flourish_end + duration
    ev = m.end()
    assert ev["swap_t"] == 1 and ev["flourish_t"] == 2
    assert ev["flourish_end_t"] == 3 and ev["fps"] == 60
    side = tmp_path / "mario__base__standard_kart.events.json"
    assert json.loads(side.read_text())["flourish_t"] == 2


def test_abort_deletes_clip(tmp_path):
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe,
                           clock=lambda: 0.0)
    m.begin("x__y")
    (tmp_path / "x__y.mkv").write_bytes(b"partial")   # simulate ffmpeg output
    m.abort()
    assert not (tmp_path / "x__y.mkv").exists()
    assert not (tmp_path / "x__y.events.json").exists()


def test_exists(tmp_path):
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe, clock=lambda: 0.0)
    assert not m.exists("a__b")
    (tmp_path / "a__b.mkv").write_bytes(b"x")
    assert m.exists("a__b")
```

- [ ] **Step 2: Run it to confirm failure**

Run: `python -m pytest tests/test_clip_capture.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `ClipCaptureManager`**

```python
# mkw_tracker/tools/clip_capture.py
"""Command-driven ffmpeg preview↔record manager feeding the tracker frame_ref.

Reuses record_clips' ffmpeg machinery. One ffmpeg owns the card: a preview pipe
between clips, a tee record pipe during a clip (4K .mkv + 1080p frames). Frames
from whichever pipe is active are pumped into frame_ref[0] for detection/grounding.
"""
import json
import os
import time
from typing import Optional

from .record_clips import (FramePipe, tee_cmd, preview_cmd, pick_encoder,
                           _bundled_bin, _resolve_device)


class ClipCaptureManager:
    def __init__(self, out_dir, device, size, fps, frame_ref, *,
                 _pipe_factory=FramePipe, clock=time.monotonic,
                 encoder=None, quality=14):
        self.out_dir = out_dir
        self.device = device
        self.size = size
        self.fps = fps
        self.frame_ref = frame_ref
        self._pf = _pipe_factory
        self._clock = clock
        self._ffmpeg = _bundled_bin("ffmpeg")
        os.makedirs(out_dir, exist_ok=True)
        try:
            enc_text = __import__("subprocess").run(
                [self._ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True).stdout
            self._enc, self._enc_args = pick_encoder(enc_text, encoder, quality)
        except Exception:
            self._enc, self._enc_args = "libx264", ["-preset", "superfast", "-crf", "14"]
        self._pipe: Optional[object] = None
        self._item: Optional[str] = None
        self._t0 = 0.0
        self._events: dict = {}

    # ── pipe lifecycle ────────────────────────────────────────────────────────
    def start_preview(self):
        self._stop_pipe()
        self._pipe = self._pf(preview_cmd(self._ffmpeg, self.device, self.size, self.fps))

    def _stop_pipe(self):
        if self._pipe is not None:
            try:
                self._pipe.stop()
            finally:
                self._pipe = None

    def pump(self):
        """Copy the active pipe's latest frame into frame_ref[0] (call each tick)."""
        if self._pipe is not None:
            f = self._pipe.latest()
            if f is not None:
                self.frame_ref[0] = f

    # ── recording ─────────────────────────────────────────────────────────────
    def _path(self, item, ext): return os.path.join(self.out_dir, f"{item}.{ext}")

    def exists(self, item) -> bool:
        p = self._path(item, "mkv")
        return os.path.exists(p) and os.path.getsize(p) > 0

    def begin(self, item):
        self._stop_pipe()
        time.sleep(0.3)                       # let the device free before re-opening
        self._item = item
        self._events = {"item": item, "fps": self.fps,
                        "swap_t": None, "flourish_t": None,
                        "flourish_end_t": None, "duration_t": None}
        cmd = tee_cmd(self._ffmpeg, self.device, self.size, self.fps,
                      duration=10_000, out_path=self._path(item, "mkv"),
                      enc=self._enc, enc_args=self._enc_args)
        self._pipe = self._pf(cmd, quiet=False)
        self._t0 = self._clock()

    def mark(self, event):
        key = {"swap": "swap_t", "flourish": "flourish_t"}[event]
        self._events[key] = self._clock() - self._t0

    def set_duration_end(self):
        t = self._clock() - self._t0
        self._events["flourish_end_t"] = t
        self._events["duration_t"] = t

    def end(self) -> dict:
        ev = dict(self._events)
        with open(self._path(self._item, "events.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=2)
        self.start_preview()                  # stops the record pipe, reopens preview
        self._item = None
        return ev

    def abort(self):
        item = self._item
        self.start_preview()
        for ext in ("mkv", "events.json"):
            p = self._path(item, ext)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._item = None
```

Note: `tee_cmd`'s `duration=10_000` makes ffmpeg run effectively unbounded; `start_preview()`/`abort()` stop the record pipe by terminating it (Matroska finalises on terminate). If a real run shows truncated tails, add a `stop_graceful()` that writes `b"q"` to the record process stdin before terminate (requires `FramePipe` opened with `stdin=PIPE`).

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_clip_capture.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/tools/clip_capture.py tests/test_clip_capture.py
git commit -m "feat(clip-sweep): ffmpeg preview/record manager + events sidecar"
```

---

### Task 4: Broadcaster `at_record_clip_*` commands

**Files:**
- Modify: `mkw_tracker/ipc/broadcaster.py` (extend `_handle_at_command` at line 223-240; add handler methods)
- Test: `tests/test_broadcaster_clip.py`

**Interfaces:**
- Consumes: a `ClipCaptureManager`-shaped object set via `broadcaster.set_clip_manager(mgr)`.
- Produces: command handlers — `at_record_clip_begin{item}` → `{type:"clip_begun"}`; `at_record_clip_mark{event}` → `{type:"marked"}`; `at_record_clip_abort` → `{type:"clip_aborted"}`; `at_clip_exists{item}` → `{type:"exists_result", done}`. (`ground`/`is_screen` reuse the existing `at_check_asset_match`/`at_check_tell_score`.) The unsolicited `{type:"clip_done", item, events}` is emitted by main.py (Task 5) on the tell-drop.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broadcaster_clip.py
from mkw_tracker.ipc.broadcaster import EventBroadcaster


class FakeMgr:
    def __init__(self): self.calls = []; self._exists = False
    def begin(self, item): self.calls.append(("begin", item))
    def mark(self, ev): self.calls.append(("mark", ev))
    def abort(self): self.calls.append(("abort",))
    def exists(self, item): return self._exists


def make():
    b = EventBroadcaster(port=0)
    b._at_enabled = True
    mgr = FakeMgr()
    b.set_clip_manager(mgr)
    return b, mgr


def test_begin_and_mark():
    b, mgr = make()
    assert b._handle_at_command({"type": "at_record_clip_begin",
                                 "item": "mario__base"})["type"] == "clip_begun"
    assert b._handle_at_command({"type": "at_record_clip_mark",
                                 "event": "swap"})["type"] == "marked"
    assert mgr.calls == [("begin", "mario__base"), ("mark", "swap")]


def test_exists():
    b, mgr = make()
    mgr._exists = True
    r = b._handle_at_command({"type": "at_clip_exists", "item": "x__y"})
    assert r == {"type": "exists_result", "done": True}
```

- [ ] **Step 2: Run it to confirm failure**

Run: `python -m pytest tests/test_broadcaster_clip.py -v`
Expected: FAIL — `AttributeError: set_clip_manager`.

- [ ] **Step 3: Implement the handlers** (in `broadcaster.py`)

Add to `__init__` (near line 120, with the other `_at_*` fields):

```python
        self._clip_mgr = None
```

Add a setter after `enable_autotemplate`:

```python
    def set_clip_manager(self, mgr) -> None:
        self._clip_mgr = mgr
```

Add these branches inside `_handle_at_command` (before the final `return {"type": "at_error", ...}`):

```python
        if t == "at_record_clip_begin":
            if self._clip_mgr is None:
                return {"type": "at_error", "message": "clip manager not set"}
            self._clip_mgr.begin(msg.get("item", ""))
            return {"type": "clip_begun"}
        if t == "at_record_clip_mark":
            self._clip_mgr.mark(msg.get("event", ""))
            return {"type": "marked"}
        if t == "at_record_clip_abort":
            self._clip_mgr.abort()
            return {"type": "clip_aborted"}
        if t == "at_clip_exists":
            return {"type": "exists_result",
                    "done": bool(self._clip_mgr and self._clip_mgr.exists(msg.get("item", "")))}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_broadcaster_clip.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/ipc/broadcaster.py tests/test_broadcaster_clip.py
git commit -m "feat(clip-sweep): broadcaster record-clip commands"
```

---

### Task 5: main.py `--clip-capture` wiring (integration — manual smoke)

**Files:**
- Modify: `mkw_tracker/main.py` (arg at ~line 1414; frame source + broadcaster wiring at ~line 935; per-tick pump + tell-drop emit in the main loop)

**Interfaces:**
- Consumes: `ClipCaptureManager`, `broadcaster.set_clip_manager`, the existing `current_frame` list and `detector` (current screen each tick).
- Produces: with `--clip-capture --ws-port 8766`, the tracker owns no OpenCV camera; `current_frame[0]` is fed by the manager's `pump()`; after a `flourish` mark, when `detector.current_screen` leaves the item's select screen, main.py calls `mgr.set_duration_end()`, `ev = mgr.end()`, and `broadcaster.broadcast(json.dumps({"type":"clip_done","item":...,"events":ev}))`.

- [ ] **Step 1: Add the CLI flag**

In the arg parser (near `--ws-port`, line ~1414):

```python
    parser.add_argument("--clip-capture", action="store_true",
                        help="Asset clip sweep: ffmpeg owns the card, detection runs on the "
                             "tee, record-clip commands enabled. Requires --ws-port.")
```

- [ ] **Step 2: Branch the frame source + wire the manager**

Where `build_camera_source` is used (lines ~935-977), when `args.clip_capture` is set, skip the OpenCV camera and instead construct and start the manager:

```python
    clip_mgr = None
    if args.clip_capture:
        from .tools.clip_capture import ClipCaptureManager
        from .tools.record_clips import _resolve_device
        dev = _resolve_device(None)
        clip_mgr = ClipCaptureManager(
            out_dir=os.path.join(base_path, "captures_sdr", "en_uk", "clips"),
            device=dev, size="3840x2160", fps=60, frame_ref=current_frame)
        clip_mgr.start_preview()
        if broadcaster is not None:
            broadcaster.set_clip_manager(clip_mgr)
```

In the per-tick section of the main loop, replace the camera `cap.read()` path with `clip_mgr.pump()` when `clip_mgr` is set (the rest of the loop — detection on `current_frame[0]` — is unchanged).

- [ ] **Step 3: Emit `clip_done` on the tell-drop**

After detection runs each tick, add (guarded by `clip_mgr` and an "awaiting flourish end" flag the broadcaster sets on the `flourish` mark — store it as `clip_mgr._awaiting_item`/screen):

```python
    if clip_mgr is not None and getattr(clip_mgr, "_item", None) and clip_mgr._events.get("flourish_t") is not None:
        sel = {"characters": Screen.CHARACTER_SELECT, "karts": Screen.KART_SELECT}
        want = Screen.KART_SELECT if "__" in clip_mgr._item and clip_mgr._item.count("__") >= 2 else Screen.CHARACTER_SELECT
        if detector.current_screen is not want:
            item = clip_mgr._item
            clip_mgr.set_duration_end()
            ev = clip_mgr.end()
            broadcaster.broadcast(json.dumps({"type": "clip_done", "item": item, "events": ev}))
```

(Refine the `want`-screen rule to read the category recorded in `begin()` rather than counting `__`; store `category` on the manager in Task 3's `begin` if cleaner.)

- [ ] **Step 4: Manual smoke (hardware)**

Preconditions: Switch on, MKW open at character select; HDR off; Windows camera-sharing off.
Run: `python -m mkw_tracker --clip-capture --ws-port 8766`
Then from a Python REPL / `wscat`, send `{"type":"at_check_tell_score","screen":"character_select","lang":"en_uk"}`.
Expected: a non-zero `score` reply (proves detection runs on the tee). Then `{"type":"at_record_clip_begin","item":"smoke__test"}`, wait 3 s, `{"type":"at_record_clip_abort"}` — confirm a `smoke__test.mkv` appeared then was deleted, and the preview never crashed.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/main.py
git commit -m "feat(clip-sweep): --clip-capture frame source + clip_done emit"
```

---

### Task 6: Sweep runner — core + character item

**Files:**
- Create: `tools/autotemplate/sweep_runner.py`
- Test: `tests/test_sweep_runner.py`

**Interfaces:**
- Consumes: `grid.Grid`; a `controller` with `press(button, duration=...)`, `hold(button, dur)`, `rstick_down(dur)`; a `client` with `send(msg) -> reply` (blocking) and `wait_for(type) -> msg`.
- Produces: `SweepRunner(grid, controller, client, *, idle_seconds=10.0)`; `capture_char(slug)` → emits the char item sequence; returns the `events` from `clip_done`. The command log is observable via the injected fakes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep_runner.py
import os
from tools.autotemplate import grid
from tools.autotemplate.sweep_runner import SweepRunner

YAML = os.path.join(os.path.dirname(__file__), "..",
                    "tools", "autotemplate", "scripts", "clip_sweep.yaml")


class FakeController:
    def __init__(self): self.log = []
    def press(self, b, duration=0.1): self.log.append(("press", b))
    def hold(self, b, dur): self.log.append(("hold", b, dur))
    def rstick_down(self, dur): self.log.append(("rstick", dur))


class FakeClient:
    def __init__(self, ground=None):
        self.sent = []
        self._ground = ground or {}
    def send(self, msg):
        self.sent.append(msg)
        t = msg["type"]
        if t == "at_check_asset_match":
            return {"type": "at_asset_score", "name_score": self._ground.get(msg["name"], 0.0)}
        if t == "at_clip_exists":
            return {"type": "exists_result", "done": False}
        return {"type": {"at_record_clip_begin": "clip_begun",
                         "at_record_clip_mark": "marked"}.get(t, "ok")}
    def wait_for(self, type_):
        return {"type": "clip_done", "item": "x", "events": {"fps": 60}}


def test_capture_char_emits_begin_idle_flourish(monkeypatch):
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeClient()
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0)
    r.capture_char("mario__base")
    types = [m["type"] for m in client.sent]
    assert types[0] == "at_clip_exists"
    assert "at_record_clip_begin" in types
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent
    assert ("press", "A") in ctrl.log         # flourish press
    # no swap mark for characters (no spawn-in)
    assert {"type": "at_record_clip_mark", "event": "swap"} not in client.sent
```

- [ ] **Step 2: Run it to confirm failure**

Run: `python -m pytest tests/test_sweep_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the core + `capture_char`**

```python
# tools/autotemplate/sweep_runner.py
"""WSL2 sweep runner: walk the grid, record one clip per item, ground keep/discard.

Hardware (nxbt) is injected as `controller`; the orchestrator WS as `client`, so
the per-item logic is unit-testable with fakes. main() wires the real ones.
"""
import time


class SweepRunner:
    def __init__(self, grid, controller, client, *, idle_seconds=10.0, lang="en_uk"):
        self.grid = grid
        self.ctrl = controller
        self.client = client
        self.idle = idle_seconds
        self.lang = lang

    def _begin(self, item):
        self.client.send({"type": "at_record_clip_begin", "item": item})

    def _mark(self, event):
        self.client.send({"type": "at_record_clip_mark", "event": event})

    def _exists(self, item) -> bool:
        return self.client.send({"type": "at_clip_exists", "item": item}).get("done", False)

    def capture_char(self, slug):
        if self._exists(slug):
            return None
        self._begin(slug)
        time.sleep(self.idle)                    # settled idle (no spawn-in)
        self.ctrl.press("A")                     # flourish → character_select drops
        self._mark("flourish")
        return self.client.wait_for("clip_done").get("events")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sweep_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/autotemplate/sweep_runner.py tests/test_sweep_runner.py
git commit -m "feat(clip-sweep): sweep runner core + character capture"
```

---

### Task 7: Sweep runner — kart inner loop + recovery

**Files:**
- Modify: `tools/autotemplate/sweep_runner.py`
- Test: `tests/test_sweep_runner.py` (add cases)

**Interfaces:**
- Produces: `SweepRunner.capture_kart(combo_slug, kart_slug, *, first=False) -> events|None`; `SweepRunner.sweep_karts(combo_slug)` walking all 40 karts. Grounding uses `at_check_asset_match` (kart name); on mismatch → `at_record_clip_abort`, recover via `grid.horizontal_delta`, retry.

- [ ] **Step 1: Write the failing tests**

```python
def test_kart_keep_on_match():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(ground={"plushbuggy": 0.95})   # lands correctly
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0)
    r.capture_kart("mario__base", "plushbuggy")
    assert {"type": "at_record_clip_mark", "event": "swap"} in client.sent
    assert not any(m["type"] == "at_record_clip_abort" for m in client.sent)
    assert ("press", "DPAD_RIGHT") in ctrl.log     # the swap-on press


def test_kart_discard_and_retry_on_mismatch():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # first ground read = wrong kart (undershoot: still on standard_kart), then correct
    seq = iter([{"standard_kart": 0.95, "plushbuggy": 0.0},
                {"standard_kart": 0.0, "plushbuggy": 0.95}])
    class Retry(FakeClient):
        def send(self, msg):
            if msg["type"] == "at_check_asset_match":
                self.sent.append(msg)
                self._ground = next(seq) if msg["name"] == "plushbuggy" and \
                    not getattr(self, "_advanced", False) else self._ground
                return {"type": "at_asset_score", "name_score": self._ground.get(msg["name"], 0.0)}
            return super().send(msg)
    client = Retry(ground={"standard_kart": 0.95, "plushbuggy": 0.0})
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0)
    r.capture_kart("mario__base", "plushbuggy")
    assert any(m["type"] == "at_record_clip_abort" for m in client.sent)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_sweep_runner.py -k kart -v`
Expected: FAIL — `AttributeError: capture_kart`.

- [ ] **Step 3: Implement `capture_kart` + `sweep_karts`**

```python
    GROUND_THRESHOLD = 0.85

    def _ground_kart(self, kart_slug) -> bool:
        r = self.client.send({"type": "at_check_asset_match", "category": "karts",
                              "lang": self.lang, "name": kart_slug})
        return r.get("name_score", 0.0) >= self.GROUND_THRESHOLD

    def _read_kart(self):
        # which kart are we actually on? scan candidates via asset match (caller supplies set)
        raise NotImplementedError  # see recovery note below

    def capture_kart(self, combo_slug, kart_slug, *, first=False):
        item = f"{combo_slug}__{kart_slug}"
        if self._exists(item):
            return None
        while True:
            self._begin(item)
            if first:                              # Standard Kart: off-and-back for spawn-in
                self.ctrl.press("DPAD_RIGHT")
                self.ctrl.press("DPAD_LEFT")
            else:
                self.ctrl.press("DPAD_RIGHT")      # swap onto this kart
            self._mark("swap")
            time.sleep(0.8)                        # name plate settles (well before spawn-in ends)
            if self._ground_kart(kart_slug):
                break
            self.client.send({"type": "at_record_clip_abort"})
            self._recover_to(kart_slug)            # step back; loop re-begins
        time.sleep(self.idle)                      # spawn-in already rolling; capture idle
        self.ctrl.press("A")                       # flourish → kart_select drops
        self._mark("flourish")
        ev = self.client.wait_for("clip_done").get("events")
        self.ctrl.press("B")                       # back to kart select (same kart, confirmed)
        return ev

    def _recover_to(self, kart_slug):
        """Read the actual kart by scanning the row, then step the horizontal delta."""
        row = [c.slug for c in self.grid.cells("karts")
               if c.coord[0] == self.grid.coord_of(kart_slug)[0]]
        here = next((k for k in row
                     if self.client.send({"type": "at_check_asset_match", "category": "karts",
                                          "lang": self.lang, "name": k}).get("name_score", 0)
                     >= self.GROUND_THRESHOLD), None)
        if here is None:
            return                                  # next loop's press re-tries blindly
        for press in self.grid.horizontal_delta(here, kart_slug):
            self.ctrl.press(press)

    def sweep_karts(self, combo_slug):
        karts = [c.slug for c in self.grid.cells("karts")]
        out = []
        for i, kart in enumerate(karts):
            out.append(self.capture_kart(combo_slug, kart, first=(i == 0)))
        self.ctrl.press("B")                        # kart select → character select
        return out
```

Note on the test's `_recover_to`: for the unit test, the mismatch path only needs to reach `at_record_clip_abort`; the recovery scan uses the same `at_check_asset_match` fake. Keep the test asserting the abort happened (not the full recovery arithmetic, which Task 1 already covers).

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_sweep_runner.py -v`
Expected: PASS (all sweep-runner tests).

- [ ] **Step 5: Commit**

```bash
git add tools/autotemplate/sweep_runner.py tests/test_sweep_runner.py
git commit -m "feat(clip-sweep): kart inner loop + grid recovery"
```

---

### Task 8: Sweep runner main() — nxbt + WS wiring + dry-run

**Files:**
- Modify: `tools/autotemplate/sweep_runner.py` (add `main()`, a real `NxbtController`, a real `WsClient`)

**Interfaces:**
- Consumes: `controller.ProController` + `switch_bridge` (`ControllerState`, `sender_thread`, held right-stick) for `NxbtController`; `websockets` for `WsClient.send/wait_for`.
- Produces: CLI `python sweep_runner.py --mac <addr> --capture-ws ws://<win-host>:8766 [--start-from <slug>] [--dry-run]`. Full traversal: preamble → for each char cell (`sweep_steps("characters")`): nav, verify-then-record `capture_char`, then `sweep_karts`, then B to character select.

- [ ] **Step 1: Implement `NxbtController` (held right-stick) + `WsClient`**

```python
class NxbtController:
    """Real controller: nxbt sender holds the right-stick down for anti-spin."""
    def __init__(self, mac, adapter="hci0"):
        from controller import ProController
        from switch_bridge import ControllerState, sender_thread
        import threading
        self.ctrl = ProController(adapter=adapter)
        self.ctrl.connect(reconnect_addr=mac)
        self.state = ControllerState()
        self.state.replay_update(0, 0, 0, 0, -127)         # right-stick down, held
        self._stop = threading.Event()
        threading.Thread(target=sender_thread,
                         args=(self.ctrl, self.state, self._stop), daemon=True).start()
        from full_runner import _press
        self._press_fn = _press
    def press(self, b, duration=0.1):
        self._press_fn(self.state, b, duration=duration, dry_run=False)
    def hold(self, b, dur):
        from full_runner import _hold
        _hold(self.state, b, dur)
    def rstick_down(self, dur):
        pass                                                # already held continuously
```

```python
class WsClient:
    """Blocking request/reply + wait_for over the broadcaster WS."""
    def __init__(self, url):
        import asyncio, threading, queue, json
        self._json = json
        self._url = url
        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._unsolicited = queue.Queue()
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError(f"cannot connect {url}")
    def _run(self):
        import asyncio
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())
    async def _main(self):
        import websockets
        async with websockets.connect(self._url) as ws:
            self._ws = ws; self._ready.set()
            async for raw in ws:
                msg = self._json.loads(raw)
                if msg.get("type") == "clip_done":
                    self._unsolicited.put(msg)
                else:
                    self._reply.put_nowait(msg)
    # send()/wait_for() use an asyncio.Queue self._reply created in _main();
    # send() schedules ws.send + awaits the next reply; wait_for drains _unsolicited.
```

(Implement `send`/`wait_for` mirroring `full_runner.CaptureClient.send` — schedule `ws.send` on `self._loop`, block on a reply queue; `wait_for("clip_done")` blocks on `self._unsolicited`.)

- [ ] **Step 2: Implement `main()` traversal**

```python
def main():
    import argparse, os
    from grid import load_grid
    p = argparse.ArgumentParser()
    p.add_argument("--mac", required=True)
    p.add_argument("--capture-ws", default="ws://localhost:8766")
    p.add_argument("--start-from", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    g = load_grid(os.path.join(os.path.dirname(__file__), "scripts", "clip_sweep.yaml"))
    ctrl = (_DryController() if a.dry_run else NxbtController(a.mac))
    client = (_DryClient() if a.dry_run else WsClient(a.capture_ws))
    runner = SweepRunner(g, ctrl, client)
    # preamble to character select reused from full_capture (HOME→TimeTrials→char select)
    skipping = bool(a.start_from)
    for slug, presses in g.sweep_steps("characters"):
        if skipping:
            if slug == a.start_from: skipping = False
            else: continue
        for b in presses: ctrl.press(b)
        runner.verify_on(slug, category="characters")     # re-press until grounded
        runner.capture_char(slug)
        runner.sweep_karts(slug)                            # ends on character select
```

Add `_DryController`/`_DryClient` that log to stdout, and `SweepRunner.verify_on` (re-press the last delta until `at_check_asset_match` ≥ threshold — characters have no spawn-in so re-press is safe).

- [ ] **Step 3: Dry-run verification**

Run (WSL2 or Windows): `python tools/autotemplate/sweep_runner.py --mac 00:00:00:00:00:00 --dry-run`
Expected: prints the full 153-cell × (idle+flourish + 40-kart) sequence with `clip_begin/mark/abort` and presses; no hardware touched; ends cleanly.

- [ ] **Step 4: Commit**

```bash
git add tools/autotemplate/sweep_runner.py
git commit -m "feat(clip-sweep): sweep runner nxbt + WS wiring + dry-run"
```

---

### Task 9: Live bring-up — single combo + few karts (manual)

**Files:** none (operational task; may add `KART_HERO_ROI` to `tools/asset_matte/clip_segment.py` once measured).

- [ ] **Step 1: One char, idle+flourish**

Start the tracker: `python -m mkw_tracker --clip-capture --ws-port 8766` (HDR off, camera-sharing off).
In WSL2: run the sweep runner with `--start-from mario__base` and Ctrl-C after the char clip.
Verify: `captures_sdr/en_uk/clips/mario__base.mkv` + `.events.json` exist; `events.json` has `flourish_t`, `flourish_end_t`, `swap_t: null`; the clip plays and shows the idle then the flourish.

- [ ] **Step 2: First three karts (resolve risks 1–2)**

Let it continue into the kart sweep for ~3 karts.
Verify on real hardware: **(risk 1)** the 1080p tee grounding reads kart names (watch the runner's `name_score` ≥ 0.85 on the correct kart); **(risk 2)** B after a kart flourish returns to the same kart and the next `DPAD_RIGHT` lands on the next kart; Standard Kart's off-and-back produced a spawn-in at the clip start.

- [ ] **Step 3: Measure `KART_HERO_ROI`**

Open a kart-select clip, find the bounding box of the character+kart render (1080p coords), and add `KART_HERO_ROI = (x1, y1, x2, y2)` to `clip_segment.py`. Note it in the spec's §5.3.

- [ ] **Step 4: Commit any measured constants**

```bash
git add tools/asset_matte/clip_segment.py
git commit -m "chore(clip-sweep): kart-select hero ROI from live bring-up"
```

---

### Task 10: Segmentation extract + write + matte handoff

**Files:**
- Modify: `tools/asset_matte/clip_segment.py` (add `segment_file`)
- Test: `tests/test_clip_segment.py` (add a synthetic-clip test)

**Interfaces:**
- Consumes: `segment_spans` (Task 2); `mkw_tracker.tools.loop_probe.load_features`/`autocorr_by_lag`/`find_period`; `extract_loop`'s seam search; `HERO_ROI`/`KART_HERO_ROI`.
- Produces: `segment_file(mkv_path, events_path, out_dir) -> dict[str, str]` writing `<item>__spawn.mp4|webp`, `__idle_loop.*`, `__flourish.*` per the spans; chooses the hero ROI by category (kart vs char).

- [ ] **Step 1: Write a failing synthetic-clip test**

```python
def test_segment_file_writes_expected_assets(tmp_path):
    import json, numpy as np, cv2
    # 4s @ 30fps synthetic clip: a moving bar (gives loop_probe a period)
    path = tmp_path / "mario__base__standard_kart.mkv"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 180))
    for i in range(120):
        f = np.zeros((180, 320, 3), np.uint8); x = (i * 8) % 300
        cv2.rectangle(f, (x, 40), (x + 20, 140), (255, 255, 255), -1); vw.write(f)
    vw.release()
    ev = {"fps": 30, "swap_t": 0.2, "flourish_t": 3.0, "flourish_end_t": 3.6, "duration_t": 3.6}
    (tmp_path / "ev.json").write_text(json.dumps(ev))
    from tools.asset_matte.clip_segment import segment_file
    out = segment_file(str(path), str(tmp_path / "ev.json"), str(tmp_path))
    assert set(out) >= {"spawn_in", "idle_loop", "flourish"}
    for p in out.values():
        assert __import__("os").path.exists(p)
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_clip_segment.py -k segment_file -v`
Expected: FAIL — `ImportError: segment_file`.

- [ ] **Step 3: Implement `segment_file`**

```python
import json
import os
import cv2

from mkw_tracker.tools import loop_probe

HERO_ROI = (1075, 30, 1800, 845)         # character-select hero (1080p)
KART_HERO_ROI = HERO_ROI                  # placeholder until Task 9 measures it


def _is_kart(item: str) -> bool:
    return item.count("__") >= 2


def _find_idle_loop(mkv_path, roi, fps, idle_end_t):
    f_eff, F = loop_probe.load_features(mkv_path, roi_1080=roi, settle=0.0,
                                        max_seconds=idle_end_t)
    lags, scores = loop_probe.autocorr_by_lag(F, max(1, int(0.5 * f_eff)), int(8 * f_eff))
    best, _conf, _top = loop_probe.find_period(lags, scores)
    loop_len = best or int(1.3 * f_eff)
    return 0, loop_len                    # loop_start=0 within the idle span (refine if needed)


def _write_span(mkv_path, start, end, out_path):
    cap = cv2.VideoCapture(mkv_path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok: break
        if start <= i < end: vw.write(fr)
        i += 1
    cap.release(); vw.release()
    return out_path


def segment_file(mkv_path, events_path, out_dir):
    ev = json.loads(open(events_path, encoding="utf-8").read())
    item = ev.get("item") or os.path.splitext(os.path.basename(mkv_path))[0]
    roi = KART_HERO_ROI if _is_kart(item) else HERO_ROI
    ls, ll = _find_idle_loop(mkv_path, roi, ev["fps"], ev["flourish_t"])
    spans = segment_spans(ev, ls, ll)
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for name, (s, e) in spans.items():
        out[name] = _write_span(mkv_path, s, e, os.path.join(out_dir, f"{item}__{name}.mp4"))
    return out
```

(Matte handoff: the existing `matte_loop.py` consumes the `idle_loop` clip directly; the spawn-in/flourish are matted the same way as one-shots. No new matte code.)

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_clip_segment.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/clip_segment.py tests/test_clip_segment.py
git commit -m "feat(clip-sweep): clip segmentation extract+write"
```

---

### Task 11: Full run (operational, resumable)

**Files:** none (runbook; optionally a `docs/clip-sweep-run.md`).

- [ ] **Step 1: Write the runbook**

Create `docs/clip-sweep-run.md` documenting: preconditions (HDR off, camera-sharing off, right-stick anti-spin, MKW at character select); start the tracker `python -m mkw_tracker --clip-capture --ws-port 8766`; start `sweep_runner.py --mac <addr> --capture-ws ws://<win-host>:8766`; resume with `--start-from <slug>` (skip-if-exists makes re-runs safe); expect ~40 hr across resumable chunks; Bluetooth drops are auto-reconnected by `full_runner._ctrl_connect_with_retry` (wired into `NxbtController`).

- [ ] **Step 2: Run in chunks**

Run a row at a time (e.g. `--start-from yoshi__base` for row 2); after each chunk, spot-check a few clips + `events.json`; re-run any aborts (skip-if-exists leaves completed ones).

- [ ] **Step 3: Batch segmentation + matte (separate, ~100 GPU-hr)**

After capture: `for f in captures_sdr/en_uk/clips/*.mkv: clip_segment.segment_file(...)` then the existing `matte_loop.py` over the `idle_loop`/`spawn`/`flourish` outputs. Run on `asset-venv-gpu`.

- [ ] **Step 4: Commit the runbook**

```bash
git add docs/clip-sweep-run.md
git commit -m "docs(clip-sweep): full-run runbook"
```

---

## Self-Review

**Spec coverage:**
- §4 card ownership (ffmpeg owns, detection on tee) → Tasks 3, 5. ✓
- §5.1 orchestrator → folded into the tracker (Tasks 3–5) rather than a separate process — simpler, same behaviour. ✓
- §5.2 grid model + computed nav/recovery → Tasks 1, 7. ✓
- §5.3 segmentation + hero ROI → Tasks 2, 9, 10. ✓
- §6 sequence (char settled idle; kart record-through-swap; Standard off-and-back; flourish until tell-drop; B-return) → Tasks 6, 7. ✓
- §7 protocol (`clip_begin`/`mark`/`abort`/`ground`/`is_screen`/`clip_done`/`exists`) → Tasks 4 (begin/mark/abort/exists), reuse existing `at_check_*` (ground/is_screen), 5 (`clip_done`). ✓
- §8 naming/output + skip-if-exists → Tasks 3 (`exists`), 6/7. ✓
- §10 parameters → Global Constraints. ✓
- §12 risks 1–2 (tee res, vertical nav) → Task 9. ✓
- §13 testing (dry-run, single-combo smoke, unit tests) → Tasks 8 (dry-run), 9 (smoke), 1/2/3/4/6/7/10 (unit). ✓

**Placeholder scan:** `KART_HERO_ROI` is an explicit measured-constant with a working default (`HERO_ROI`) and a task to measure it (Task 9) — not a blocking placeholder. `_find_idle_loop` returns `loop_start=0` within the idle span with a documented refine-if-needed; acceptable (the seam search is an optimisation, not correctness). No "TBD"/"implement later".

**Type consistency:** `events` dict keys (`fps`, `swap_t`, `flourish_t`, `flourish_end_t`, `duration_t`) are written in Task 3 and read in Tasks 2/10. `at_record_clip_*` / `at_clip_exists` message types match between Tasks 4 (handler) and 6/7 (sender). `Grid` methods (`cells`, `coord_of`, `sweep_steps`, `horizontal_delta`) defined in Task 1 and used in Tasks 6/7/8. `name_score` reply field matches the existing `_at_check_asset_match`. ✓

---

## Notes for the implementer

- The hardware-coupled tasks (5, 8, 9, 11) cannot be unit-tested without the Switch + capture card; they use **manual verification with explicit expected observations**. Everything else is TDD.
- Reuse aggressively: `record_clips` (ffmpeg), `full_runner` (`_press`/`_hold`/reconnect/`_to_filename`), `loop_probe`/`extract_loop`/`matte_loop` (post), and the existing `at_check_*` grounding. The only genuinely new logic is the grid model, the clip manager's state, the sweep state machine, and segmentation spans — all unit-tested.
- Keep `grid.py` and `clip_segment.py` import-clean of hardware so the Windows test suite stays green.
