# Setup Revamp: Node-Driven Screen Editing + Boolean-Tree Tells — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the linear setup wizard with a node-driven screen editor (click a graph node → edit its detection tell, selection/HUD ROIs, and templates), remove calibration from the UI, and redesign the tell model into a boolean tree (AND of groups, OR within group).

**Architecture:** Backend first — a new `Region`/`Tell` boolean-tree model in `detection/screen.py` with `detect_tell` = `min(group)·max(region)` scoring, persisted as one `tell_tree_<SCREEN>` JSON blob with a one-time migration from the legacy six-key format. Then the Svelte frontend (`src/App.svelte`) is reorganized: calibration deleted, a slim Settings panel and a new "Edit Screens" split-pane view added, the footer status graph made click-through, and the tell editor rebuilt around `(group, region)` indices. Detection *matching* math (`_match_tell`, `_detect_dark_loading`) is preserved byte-for-byte; only the combination layer changes.

**Tech Stack:** Python 3 + OpenCV + numpy (sidecar), pytest 9.x (tests), Svelte + Vite + Tauri (frontend), SQLite (config persistence).

**Spec:** `docs/superpowers/specs/2026-05-31-setup-revamp-node-editing-design.md`

---

## File Structure

**Backend (Python):**
- `mkw_tracker/detection/screen.py` — MODIFY: add `Region` dataclass; rewrite `Tell` to hold `groups`; rewrite `detect_tell`/`load`/`all_rois`; rewrite `TELLS` as trees; rewrite serialization + mutation methods (`get_tells_config`, `update_region`, `add_region`, `remove_region`, `add_group`, `remove_group`, `capture_region_template`, `test_region`, `get_region_images`); keep `_match_tell`, `_detect_dark_loading`, `TELL_ALIAS_GROUPS`, `TRANSITIONS`, `ScreenDetector.update` unchanged.
- `mkw_tracker/database/tell_repo.py` — CREATE: `serialize_tell(tell)`, `tree_from_legacy(screen_name)`, `migrate_tells_to_tree()`, load/save helpers for `tell_tree_<SCREEN>`.
- `mkw_tracker/database/migrations.py` — MODIFY: add a v3 step calling `migrate_tells_to_tree()`.
- `mkw_tracker/main.py` — MODIFY: replace tell-edit IPC handlers with region ops; replace startup tell-override apply loop with tree load; replace `_persist_tell_structure` with `_persist_tell_tree`; delete calibration handlers.
- `mkw_tracker/ipc/protocol.py` — MODIFY: drop calibration message dataclasses if present (optional; unused).

**Tests (Python):**
- `tests/__init__.py`, `tests/conftest.py` — CREATE: in-memory SQLite fixture.
- `tests/test_tell_tree.py` — CREATE: `detect_tell` combine logic + `Region.load` + serialization.
- `tests/test_tell_migration.py` — CREATE: legacy→tree migration.

**Frontend (Svelte):**
- `src/App.svelte` — MODIFY: delete calibration; add `edit` view + slim Settings panel; make footer graph click-through; rebuild tell editor around `(group, region)`.

---

## Phase 1 — Backend Boolean-Tree Tell Model

### Task 1: Test harness + in-memory DB fixture

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`

- [ ] **Step 1: Create the package marker**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 2: Write the DB fixture**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures."""
import pytest
from mkw_tracker.database import connection as _conn


@pytest.fixture
def memdb(tmp_path, monkeypatch):
    """Point the DB connection at a fresh temp SQLite file, schema applied."""
    db_file = tmp_path / "test.db"
    # Force a brand-new connection bound to the temp file.
    _conn._connection = None  # reset any cached singleton
    monkeypatch.setattr(_conn, "_DB_PATH", str(db_file), raising=False)
    from mkw_tracker.database.migrations import apply_migrations
    apply_migrations(str(db_file))
    yield str(db_file)
    _conn._connection = None
```

- [ ] **Step 3: Verify the fixture imports cleanly**

Run: `python -m pytest tests/conftest.py -q`
Expected: PASS (no tests collected, no import errors). If `connection.py` uses a different cached-connection attribute name, adjust the `monkeypatch`/reset lines to match (read `mkw_tracker/database/connection.py` first and use its actual globals).

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add pytest harness with in-memory DB fixture"
```

---

### Task 2: `Region` dataclass + `score_region`

**Files:**
- Modify: `mkw_tracker/detection/screen.py` (add `Region` after the image-encoding helpers, ~line 64)
- Test: `tests/test_tell_tree.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tell_tree.py`:

```python
import numpy as np
from mkw_tracker.detection.screen import Region, score_region


def _solid(w, h, val):
    return np.full((h, w), val, np.uint8)


def test_template_region_scores_high_on_match():
    frame = _solid(40, 30, 200)
    tmpl = _solid(20, 14, 200)
    r = Region(kind="template", roi=(5, 5, 25, 19), grayscale=True, search_pad=2)
    r.template = tmpl
    assert score_region(frame, r, 0.9) >= 0.9


def test_dark_loading_region_scores_one_when_dark_and_icon_bright():
    frame = np.zeros((1080, 1920), np.uint8)        # dark everywhere
    frame[930:1020, 1720:1850] = 220                # bright mascot in icon_roi
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 1.0


def test_dark_loading_region_scores_zero_without_icon():
    frame = np.zeros((1080, 1920), np.uint8)        # dark, no bright icon
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: FAIL with `ImportError: cannot import name 'Region'`.

- [ ] **Step 3: Add `Region` and `score_region`**

In `mkw_tracker/detection/screen.py`, after `_encode_crop` (~line 64) and before the `Screen` enum, add:

```python
@dataclass
class Region:
    """One detectable region inside a Tell's boolean tree."""
    kind: str = "template"               # "template" | "dark_loading"
    roi: tuple = (0, 0, 0, 0)
    image_path: Optional[str] = None     # template kind
    thresh: int = 170                    # binarisation level (binary path only)
    grayscale: bool = True
    search_pad: int = 6
    icon_roi: Optional[tuple] = None     # dark_loading kind
    template: Optional[np.ndarray] = field(default=None, repr=False)


def score_region(frame: np.ndarray, region: "Region", match_threshold: float) -> float:
    """Return a region's match score in [0, 1]."""
    if region.kind == "dark_loading":
        detected, _ = _detect_dark_loading(frame, region.roi, region.icon_roi)
        return 1.0 if detected else 0.0
    return _match_tell(frame, region.roi, region.template,
                       region.thresh, region.grayscale, region.search_pad)
```

`_match_tell` and `_detect_dark_loading` are defined lower in the file; `score_region` only calls them at runtime, so forward reference is fine.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_tell_tree.py
git commit -m "feat: add Region dataclass and score_region for boolean-tree tells"
```

---

### Task 3: Rewrite `Tell` to hold `groups` + new `detect_tell`

**Files:**
- Modify: `mkw_tracker/detection/screen.py` (`Tell` dataclass ~lines 146-229; `detect_tell` ~lines 430-455)
- Test: `tests/test_tell_tree.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tell_tree.py`:

```python
from mkw_tracker.detection.screen import Tell, Screen, detect_tell


def _match_region(score_val):
    """A region whose template is a solid block matching a solid frame at score_val≈1."""
    r = Region(kind="template", roi=(0, 0, 10, 10), grayscale=True, search_pad=0)
    r.template = _solid(10, 10, 200 if score_val else 0)
    return r


def test_single_group_single_region_and():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.TITLE, groups=[[_match_region(True)]])
    matched, score = detect_tell(frame, t)
    assert matched and score >= 0.9


def test_two_groups_and_fails_when_one_group_fails():
    # group 1 matches (solid 200), group 2 cannot (template is solid 0 vs frame 200)
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.RACING,
             groups=[[_match_region(True)], [_match_region(False)]])
    matched, score = detect_tell(frame, t)
    assert not matched
    assert score < 0.9          # AND-limiting group drags the score down


def test_or_within_group_passes_when_any_region_matches():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.HOME,
             groups=[[_match_region(False), _match_region(True)]])
    matched, score = detect_tell(frame, t)
    assert matched and score >= 0.9


def test_empty_groups_never_matches():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.TITLE, groups=[])
    assert detect_tell(frame, t) == (False, 0.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: FAIL — `Tell.__init__` rejects `groups=` (old signature), or `detect_tell` returns wrong shape.

- [ ] **Step 3: Replace the `Tell` dataclass**

Replace the entire `@dataclass class Tell` block (~lines 146-229) with:

```python
@dataclass
class Tell:
    """Boolean-tree description of how to detect a screen.

    groups is an AND of groups; each group is an OR of Regions.  A tell matches
    when every group matches, and a group matches when any region in it matches.
    """
    screen: Screen
    groups: list = field(default_factory=list)   # list[list[Region]]
    match_threshold: float = 0.9

    def load(self, switch2_language: str = None):
        from ..utils.paths import data_dir, resource_path
        import os

        def _load_one(rel_path: str) -> Optional[np.ndarray]:
            if not rel_path:
                return None
            if switch2_language:
                lang_path = _inject_language(rel_path, switch2_language)
                user_lang = str(data_dir() / lang_path)
                if os.path.exists(user_lang):
                    img = cv2.imread(user_lang, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        return img
                return cv2.imread(resource_path(lang_path), cv2.IMREAD_GRAYSCALE)
            user = str(data_dir() / rel_path)
            if os.path.exists(user):
                img = cv2.imread(user, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    return img
            return cv2.imread(resource_path(rel_path), cv2.IMREAD_GRAYSCALE)

        for group in self.groups:
            for region in group:
                if region.kind != "template":
                    continue
                region.template = _load_one(region.image_path)
                if region.template is None:
                    print(f"[WARN] Could not load template: {region.image_path}")

    def all_rois(self) -> list:
        rois = []
        for group in self.groups:
            for region in group:
                rois.append(region.roi)
                if region.icon_roi is not None:
                    rois.append(region.icon_roi)
        return rois
```

- [ ] **Step 4: Replace `detect_tell`**

Replace the entire `def detect_tell(...)` body (~lines 430-455) with:

```python
def detect_tell(frame: np.ndarray, tell: Tell) -> tuple:
    """Return (detected: bool, best_score: float) for a boolean-tree tell."""
    if not tell.groups:
        return False, 0.0
    group_scores = []
    for group in tell.groups:
        if not group:
            return False, 0.0
        group_scores.append(max(score_region(frame, r, tell.match_threshold) for r in group))
    overall = min(group_scores)
    return overall >= tell.match_threshold, overall
```

- [ ] **Step 5: Update `update()` / `_full_candidate_scan()` tell-count math**

In `ScreenDetector`, two lines compute `1 + len(current_tell.required_also)`. `required_also` no longer exists. Replace both occurrences (in `update`, ~line 553, and `_full_candidate_scan`, ~line 595) with a region count:

```python
tells_evaluated += sum(len(g) for g in current_tell.groups)
```
and
```python
tells_evaluated += sum(len(g) for g in tell.groups)
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: PASS (7 passed). If `ScreenDetector` import triggers errors from `TELLS` still using the old kwargs, that is expected until Task 4 — run only the new tests by node id if needed: `python -m pytest tests/test_tell_tree.py::test_single_group_single_region_and -q`.

- [ ] **Step 7: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_tell_tree.py
git commit -m "feat: rewrite Tell as boolean tree with min(max) detect_tell"
```

---

### Task 4: Rewrite the default `TELLS` registry as trees

**Files:**
- Modify: `mkw_tracker/detection/screen.py` (`TELLS` list ~lines 236-304)

- [ ] **Step 1: Replace the `TELLS` list**

Replace the entire `TELLS: list = [ ... ]` block with the tree form below. Each former primary/alt becomes OR regions in one group; coin+flag becomes two groups; dark-loading becomes a single `dark_loading` region.

```python
def _tmpl(image_path, roi, thresh=170, grayscale=True):
    return Region(kind="template", image_path=image_path, roi=roi,
                  thresh=thresh, grayscale=grayscale)


TELLS: list = [
    Tell(screen=Screen.TITLE, groups=[[
        _tmpl("images/screens/title.png", (833, 156, 1082, 360), thresh=75)]]),
    Tell(screen=Screen.HOME, groups=[[
        _tmpl("images/screens/home.png",  (1110, 805, 1312, 877), thresh=55),
        _tmpl("images/screens/home2.png", (1361, 803, 1548, 875), thresh=55)]]),
    Tell(screen=Screen.START_TIME_TRIAL, groups=[[
        _tmpl("images/screens/starttimetrial.png", (671, 312, 1267, 359), thresh=199)]]),
    Tell(screen=Screen.START_REPLAY, groups=[[
        _tmpl("images/screens/startreplay.png", (726, 317, 1209, 356), thresh=222)]]),
    Tell(screen=Screen.RESET, groups=[[
        Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))]]),
    Tell(screen=Screen.GHOST_RESET, groups=[[
        Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))]]),
    Tell(screen=Screen.UNKNOWN_RESET, groups=[[
        Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))]]),
    Tell(screen=Screen.POST_TIME_TRIAL, groups=[[
        _tmpl("images/screens/posttimetrial.png",  (1364, 798, 1458, 825), thresh=190),
        _tmpl("images/screens/posttimetrial2.png", (1209, 664, 1618, 691), thresh=190)]]),
    Tell(screen=Screen.MAIN_MENU, groups=[[
        _tmpl("images/screens/mainmenu.png",      (554, 784, 612, 838), thresh=168),
        _tmpl("images/screens/main_menu-alt.png", (392, 800, 447, 854), thresh=117)]]),
    Tell(screen=Screen.CHARACTER_SELECT, groups=[[
        _tmpl("images/screens/character_screen.png", (1768, 1027, 1887, 1055), thresh=208)]]),
    Tell(screen=Screen.KART_SELECT, groups=[[
        _tmpl("images/screens/kart_screen.png", (1288, 1032, 1462, 1055), thresh=195)]]),
    Tell(screen=Screen.COURSE_SELECT, groups=[[
        _tmpl("images/screens/course_select.png",  (267, 916, 553, 945), thresh=226),
        _tmpl("images/screens/track-sel-alt.png",  (279, 814, 540, 858), thresh=214)]]),
    Tell(screen=Screen.RACING, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.GHOST, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.UNKNOWN_RACE_ACTIVE, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.RACE_MENU, groups=[[
        _tmpl("images/screens/racemenu.png",     (781, 649, 1142, 674), thresh=180),
        _tmpl("images/screens/racemenu-alt.png", (810, 530, 1111, 552), thresh=190)]]),
    Tell(screen=Screen.REPLAY_MENU, groups=[[
        _tmpl("images/screens/ghostmenu.png", (770, 590, 1160, 615), thresh=190)]]),
    Tell(screen=Screen.REPLAY_RACE_AGAINST, groups=[[
        _tmpl("images/screens/ghostmenu-red.png", (762, 590, 1160, 615), thresh=190)]]),
    Tell(screen=Screen.GALLERY, groups=[[
        _tmpl("images/screens/gallery.png", (106, 191, 181, 484), thresh=151)]]),
    Tell(screen=Screen.SINGLEPLAYER_MENU, groups=[[
        _tmpl("images/screens/singleplayer.png", (110, 562, 183, 628), thresh=187)]]),
    Tell(screen=Screen.TIME_TRIALS, groups=[[
        _tmpl("images/screens/timetrials.png", (110, 562, 183, 628), thresh=204)]]),
]
```

Note: the former RESET-family tells kept `grayscale` defaulting True but `dark_loading` ignores it. POST_TIME_TRIAL and the others keep `grayscale=True` (the prior default). The `SCREENSHOT_FILES` provenance dict and `TELL_ALIAS_GROUPS` below are unchanged.

- [ ] **Step 2: Verify the module imports and the detector constructs**

Run: `python -c "from mkw_tracker.detection.screen import ScreenDetector; d=ScreenDetector(); print(len(d._tells_by_screen), 'tells')"`
Expected: prints `20 tells` with no exceptions.

- [ ] **Step 3: Run the full tell-tree test file**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: PASS (7 passed).

- [ ] **Step 4: Commit**

```bash
git add mkw_tracker/detection/screen.py
git commit -m "feat: express default TELLS registry as boolean trees"
```

---

### Task 5: Region-indexed serialization + mutation methods

**Files:**
- Modify: `mkw_tracker/detection/screen.py` (`ScreenDetector`: replace `_roi_key_parts`, `test_tell_by_name`, `get_template_images`, `get_tells_config`, `update_tell`, `_tell_to_dict`, `_propagate_structure`, `add_required_also`, `remove_required_also`, `add_alt`, `remove_alt`, `capture_and_save_template`)
- Test: `tests/test_tell_tree.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tell_tree.py`:

```python
from mkw_tracker.detection.screen import ScreenDetector


def test_serialize_get_tells_config_round_trip():
    d = ScreenDetector()
    cfg = {e["screen"]: e for e in d.get_tells_config()}
    racing = cfg["RACING"]
    assert len(racing["groups"]) == 2                 # coin AND flag
    assert len(racing["groups"][0]) == 1
    assert racing["groups"][0][0]["kind"] == "template"
    assert "aliases" in racing                        # GHOST, UNKNOWN_RACE_ACTIVE
    home = cfg["HOME"]
    assert len(home["groups"]) == 1 and len(home["groups"][0]) == 2  # OR


def test_add_and_remove_group_propagates_to_aliases():
    d = ScreenDetector()
    d.add_group("RACING", roi=[10, 10, 50, 50])
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    ghost  = next(e for e in d.get_tells_config() if e["screen"] == "GHOST")
    assert len(racing["groups"]) == 3
    assert len(ghost["groups"]) == 3                  # alias kept in sync
    d.remove_group("RACING", 2)
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    assert len(racing["groups"]) == 2


def test_add_region_adds_or_alternative():
    d = ScreenDetector()
    d.add_region("HOME", group=0, roi=[1, 2, 3, 4])
    home = next(e for e in d.get_tells_config() if e["screen"] == "HOME")
    assert len(home["groups"][0]) == 3


def test_update_region_sets_roi_and_thresh():
    d = ScreenDetector()
    d.update_region("TITLE", group=0, region=0, roi=[1, 2, 30, 40], thresh=88)
    title = next(e for e in d.get_tells_config() if e["screen"] == "TITLE")
    assert title["groups"][0][0]["roi"] == [1, 2, 30, 40]
    assert title["groups"][0][0]["thresh"] == 88
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: FAIL — `get_tells_config` still emits the old `roi`/`alt`/`required_also` shape and `add_group`/`add_region`/`update_region` don't exist.

- [ ] **Step 3: Replace the serialization + mutation block**

In `mkw_tracker/detection/screen.py`, delete the methods `_roi_key_parts`, `get_tells_config`, `update_tell`, `_tell_to_dict`, `_propagate_structure`, `add_required_also`, `remove_required_also`, `add_alt`, `remove_alt`, `capture_and_save_template`, `test_tell_by_name`, `get_template_images` (the whole region ~lines 657-998) and replace with:

```python
    # ── tree access helpers ───────────────────────────────────────────────
    def _region_at(self, tell, group: int, region: int):
        if tell is None or group >= len(tell.groups):
            return None
        grp = tell.groups[group]
        if region >= len(grp):
            return None
        return grp[region]

    def _region_to_dict(self, r) -> dict:
        return {
            "kind":       r.kind,
            "roi":        list(r.roi),
            "image_path": r.image_path,
            "thresh":     r.thresh,
            "grayscale":  r.grayscale,
            "search_pad": r.search_pad,
            "icon_roi":   list(r.icon_roi) if r.icon_roi else None,
        }

    def _tell_to_dict(self, screen: Screen, tell) -> dict:
        entry = {
            "screen":          screen.name,
            "match_threshold": tell.match_threshold,
            "groups":          [[self._region_to_dict(r) for r in g] for g in tell.groups],
        }
        if screen in TELL_ALIAS_GROUPS:
            entry["aliases"] = [s.name for s in TELL_ALIAS_GROUPS[screen]]
        return entry

    def get_tells_config(self) -> list:
        return [self._tell_to_dict(s, t) for s, t in self._tells_by_screen.items()]

    def _propagate_tree(self, screen: Screen) -> None:
        """Deep-copy the canonical screen's groups onto its alias screens."""
        tell = self._tells_by_screen.get(screen)
        if tell is None:
            return
        for alias_screen in TELL_ALIAS_GROUPS.get(screen, []):
            alias = self._tells_by_screen.get(alias_screen)
            if alias is None:
                continue
            alias.groups = copy.deepcopy(tell.groups)
            alias.load(self._switch2_language)

    def update_region(self, screen_name: str, group: int, region: int,
                      roi=None, thresh=None, grayscale=None,
                      kind=None, icon_roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None:
            return None
        if roi is not None and len(roi) >= 4:
            r.roi = tuple(int(v) for v in roi)
        if thresh is not None:
            r.thresh = int(thresh)
        if grayscale is not None:
            r.grayscale = bool(grayscale)
        if kind is not None:
            r.kind = str(kind)
        if icon_roi is not None and len(icon_roi) >= 4:
            r.icon_roi = tuple(int(v) for v in icon_roi)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def add_region(self, screen_name: str, group: int, roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None or group >= len(tell.groups):
            return None
        sn_lower = screen_name.lower()
        lang = self._switch2_language or ""
        pfx = f"images/screens/{lang}/" if lang else "images/screens/"
        new_roi = tuple(int(v) for v in roi) if roi and len(roi) >= 4 else tell.groups[group][0].roi
        n = sum(len(g) for g in tell.groups)
        tell.groups[group].append(Region(
            kind="template", image_path=f"{pfx}{sn_lower}-r{group}-{n}.png",
            roi=new_roi))
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def remove_region(self, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if self._region_at(tell, group, region) is None:
            return None
        tell.groups[group].pop(region)
        if not tell.groups[group]:                 # dropped last region → drop group
            tell.groups.pop(group)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def add_group(self, screen_name: str, roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None:
            return None
        sn_lower = screen_name.lower()
        lang = self._switch2_language or ""
        pfx = f"images/screens/{lang}/" if lang else "images/screens/"
        g = len(tell.groups)
        new_roi = tuple(int(v) for v in roi) if roi and len(roi) >= 4 else (935, 515, 985, 565)
        tell.groups.append([Region(kind="template",
                                   image_path=f"{pfx}{sn_lower}-g{g}.png", roi=new_roi)])
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def remove_group(self, screen_name: str, group: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None or group >= len(tell.groups):
            return None
        tell.groups.pop(group)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def test_region(self, frame, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None or frame is None:
            return None
        score = score_region(frame, r, tell.match_threshold)
        if r.kind == "dark_loading":
            live = _encode_crop_roi(frame, r.roi, None, grayscale=True)
            tmpl_img = None
        else:
            live = _encode_crop_roi(frame, r.roi, r.thresh, r.grayscale)
            tmpl_img = _encode_img(r.template)
        return {
            "screen": screen_name, "group": group, "region": region,
            "score": round(score, 4), "threshold": tell.match_threshold,
            "matched": score >= tell.match_threshold,
            "roi": list(r.roi), "template_img": tmpl_img, "live_crop": live,
        }

    def get_region_images(self, frame, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None:
            return None
        live = None
        if frame is not None:
            live = (_encode_crop_roi(frame, r.roi, None, grayscale=True)
                    if r.kind == "dark_loading"
                    else _encode_crop_roi(frame, r.roi, r.thresh, r.grayscale))
        return {
            "screen": screen_name, "group": group, "region": region,
            "template_img": _encode_img(r.template) if r.kind == "template" else None,
            "live_crop": live,
        }

    def capture_region_template(self, frame, screen_name: str,
                                group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None or frame is None or r.kind != "template" or not r.image_path:
            return None
        x1, y1, x2, y2 = r.roi
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop.copy()
        if r.grayscale:
            processed = gray
        elif r.thresh is not None:
            _, processed = cv2.threshold(gray, r.thresh, 255, cv2.THRESH_BINARY)
        else:
            _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        from ..utils.paths import data_dir
        import os
        save_rel = _inject_language(r.image_path, self._switch2_language or "")
        save_path = str(data_dir() / save_rel)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cv2.imwrite(save_path, processed)
        tell.load(self._switch2_language)
        self._propagate_tree(screen)
        score = score_region(frame, r, tell.match_threshold)
        return {
            "screen": screen_name, "group": group, "region": region,
            "score": round(score, 4), "threshold": tell.match_threshold,
            "matched": score >= tell.match_threshold,
        }
```

(`copy` is already imported at the top of the file.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_tell_tree.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/detection/screen.py tests/test_tell_tree.py
git commit -m "feat: region-indexed tell serialization and mutation methods"
```

---

### Task 6: Tell-tree persistence repo + legacy migration

**Files:**
- Create: `mkw_tracker/database/tell_repo.py`
- Test: `tests/test_tell_migration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tell_migration.py`:

```python
from mkw_tracker.database.config_repo import set_config, get_config
from mkw_tracker.database.tell_repo import migrate_tells_to_tree


def test_legacy_or_screen_migrates_to_one_group_two_regions(memdb):
    # HOME-style: primary + alt at distinct ROIs → 1 group, 2 OR regions
    set_config("tell_roi_HOME", [1110, 805, 1312, 877])
    set_config("tell_thresh_HOME", 55)
    set_config("tell_alt_HOME", ["images/screens/home2.png", [1361, 803, 1548, 875]])
    set_config("tell_alt_thresh_HOME", 55)
    migrate_tells_to_tree()
    tree = get_config("tell_tree_HOME")
    assert len(tree) == 1 and len(tree[0]) == 2
    assert get_config("tell_roi_HOME") is None          # legacy keys removed
    assert get_config("tell_alt_HOME") is None


def test_legacy_and_screen_migrates_to_two_groups(memdb):
    set_config("tell_roi_RACING", [78, 987, 96, 1015])
    set_config("tell_thresh_RACING", 173)
    set_config("tell_req_also_RACING", [["images/screens/racing-flag.png", [245, 991, 269, 1011]]])
    set_config("tell_and_thresh_RACING", [170])
    migrate_tells_to_tree()
    tree = get_config("tell_tree_RACING")
    assert len(tree) == 2 and len(tree[0]) == 1 and len(tree[1]) == 1


def test_screen_without_overrides_is_untouched(memdb):
    migrate_tells_to_tree()
    assert get_config("tell_tree_TITLE") is None         # no legacy keys → no blob
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tell_migration.py -q`
Expected: FAIL — `mkw_tracker.database.tell_repo` does not exist.

- [ ] **Step 3: Write `tell_repo.py`**

Create `mkw_tracker/database/tell_repo.py`:

```python
"""Persistence + legacy migration for boolean-tree tells (config table)."""
from .config_repo import get_config, set_config, delete_configs_like, get_all_config

_LEGACY_KEYS = ("tell_roi_", "tell_thresh_", "tell_req_also_",
                "tell_alt_", "tell_and_thresh_", "tell_alt_thresh_")


def _region(kind, roi, image_path=None, thresh=170, icon_roi=None):
    return {"kind": kind, "roi": list(roi), "image_path": image_path,
            "thresh": int(thresh), "grayscale": True, "search_pad": 6,
            "icon_roi": list(icon_roi) if icon_roi else None}


def tree_from_legacy(screen_name: str) -> list | None:
    """Build a groups tree from legacy tell_* keys, or None if none are present."""
    roi   = get_config(f"tell_roi_{screen_name}")
    th    = get_config(f"tell_thresh_{screen_name}")
    alt   = get_config(f"tell_alt_{screen_name}")          # [path,[roi]] | False | None
    altth = get_config(f"tell_alt_thresh_{screen_name}")
    req   = get_config(f"tell_req_also_{screen_name}")      # [[path,[roi]], ...]
    reqth = get_config(f"tell_and_thresh_{screen_name}")    # [int, ...]
    if roi is None and alt is None and req is None and th is None:
        return None
    if roi is None:
        return None    # can't rebuild without a primary roi; leave for defaults
    primary_group = [_region("template", roi, thresh=th if th is not None else 170)]
    if isinstance(alt, list) and len(alt) >= 2 and alt[0]:
        primary_group.append(_region("template", alt[1], image_path=alt[0],
                                      thresh=altth if altth is not None else 170))
    groups = [primary_group]
    if isinstance(req, list):
        for i, item in enumerate(req):
            if isinstance(item, list) and len(item) >= 2 and len(item[1]) >= 4:
                t = reqth[i] if isinstance(reqth, list) and i < len(reqth) else 170
                groups.append([_region("template", item[1], image_path=item[0], thresh=t)])
    return groups


def migrate_tells_to_tree() -> int:
    """One-time: convert any legacy tell_* overrides to tell_tree_<SCREEN> blobs.
    Returns the number of screens migrated."""
    all_cfg = get_all_config()
    screens = set()
    for key in all_cfg:
        for pfx in _LEGACY_KEYS:
            if key.startswith(pfx):
                screens.add(key[len(pfx):])
    migrated = 0
    for sn in screens:
        if get_config(f"tell_tree_{sn}") is not None:
            continue
        tree = tree_from_legacy(sn)
        if tree:
            set_config(f"tell_tree_{sn}", tree)
            migrated += 1
    for pfx in _LEGACY_KEYS:
        delete_configs_like(f"{pfx}%")
    return migrated
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_tell_migration.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire the migration into schema versioning**

In `mkw_tracker/database/migrations.py`, after the `current < 2` block (~line 171), append:

```python
    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 3:
        from .tell_repo import migrate_tells_to_tree
        n = migrate_tells_to_tree()
        conn.execute("UPDATE schema_version SET version=3")
        conn.commit()
        print(f"[DB] Migrated {n} tell override(s) to boolean-tree format (v3)")
```

- [ ] **Step 6: Verify migration runs at startup without error**

Run: `python -c "from mkw_tracker.database.migrations import apply_migrations; apply_migrations()"`
Expected: prints schema messages including the v3 migration line, no traceback. (Safe on the real `mkw_tracker.db`; with no legacy keys it migrates 0.)

- [ ] **Step 7: Commit**

```bash
git add mkw_tracker/database/tell_repo.py mkw_tracker/database/migrations.py tests/test_tell_migration.py
git commit -m "feat: persist tell trees and migrate legacy tell overrides (schema v3)"
```

---

### Task 7: Apply persisted trees at startup + persist on edit

**Files:**
- Modify: `mkw_tracker/main.py` (startup apply loop ~lines 703-738; `_persist_tell_structure` ~lines 581-607)

- [ ] **Step 1: Add a `serialize_tell` + `apply_tree` helper to `tell_repo.py`**

Append to `mkw_tracker/database/tell_repo.py`:

```python
def serialize_groups(tell) -> list:
    """Serialise a Tell's in-memory groups to the JSON-storable blob shape."""
    out = []
    for group in tell.groups:
        g = []
        for r in group:
            g.append({"kind": r.kind, "roi": list(r.roi), "image_path": r.image_path,
                      "thresh": int(r.thresh), "grayscale": bool(r.grayscale),
                      "search_pad": int(r.search_pad),
                      "icon_roi": list(r.icon_roi) if r.icon_roi else None})
        out.append(g)
    return out


def groups_from_blob(blob: list):
    """Rebuild a list[list[Region]] from a stored blob. Import Region lazily."""
    from ..detection.screen import Region
    groups = []
    for g in blob or []:
        regions = []
        for rd in g:
            regions.append(Region(
                kind=rd.get("kind", "template"),
                roi=tuple(rd.get("roi", (0, 0, 0, 0))),
                image_path=rd.get("image_path"),
                thresh=int(rd.get("thresh", 170)),
                grayscale=bool(rd.get("grayscale", True)),
                search_pad=int(rd.get("search_pad", 6)),
                icon_roi=tuple(rd["icon_roi"]) if rd.get("icon_roi") else None))
        groups.append(regions)
    return groups
```

- [ ] **Step 2: Replace the startup override-apply loop in `main.py`**

Replace the entire loop at ~lines 703-738 (`for _screen_enum, _tell in detector._tells_by_screen.items(): ...` through the trailing `_tell.load(...)` loop) with:

```python
    # Apply any persisted boolean-tree overrides from the editor.  Stored as one
    # tell_tree_<SCREEN> JSON blob per screen (see database/tell_repo.py).
    from .database.config_repo import get_config as _get_config_direct
    from .database.tell_repo import groups_from_blob
    for _screen_enum, _tell in detector._tells_by_screen.items():
        _blob = _get_config_direct(f"tell_tree_{_screen_enum.name}")
        if _blob:
            _tell.groups = groups_from_blob(_blob)
    for _tell in detector._tells_by_screen.values():
        _tell.load(switch2_language)
```

(If `_get_config_direct` is already imported earlier in `run()`, drop the duplicate import.)

- [ ] **Step 3: Replace `_persist_tell_structure` with `_persist_tell_tree`**

Replace the whole `_persist_tell_structure` function (~lines 581-607) with:

```python
def _persist_tell_tree(settings, screen_name: str, detector) -> None:
    """Persist a canonical screen's full groups tree (and its aliases)."""
    from .detection.screen import Screen as _Scr, TELL_ALIAS_GROUPS as _TAG
    from .database.tell_repo import serialize_groups
    try:
        canon = _Scr[screen_name]
    except KeyError:
        return
    tell = detector._tells_by_screen.get(canon)
    if tell is None:
        return
    blob = serialize_groups(tell)
    for sn in [screen_name] + [a.name for a in _TAG.get(canon, [])]:
        settings.update(f"tell_tree_{sn}", blob)
```

- [ ] **Step 4: Verify the sidecar boots**

Run: `python -m mkw_tracker --no-ipc --no-display --video temp/aiden.mp4 --video-fps 0 --video-once`
Expected: runs to completion (or exits cleanly on missing video) with no traceback referencing `required_also`, `alt_roi`, or `tell_roi_`. If `temp/aiden.mp4` is absent, instead run `python -c "import mkw_tracker.main"` and confirm no import error.

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/main.py mkw_tracker/database/tell_repo.py
git commit -m "feat: load and persist tell trees at startup/on-edit"
```

---

### Task 8: Swap tell-edit IPC handlers to region ops; delete calibration handlers

**Files:**
- Modify: `mkw_tracker/main.py` (dispatch block ~lines 372-458; calibration handlers ~lines 270-310 and any `capture_calib_frame`/`clear_calib_frames`/`get_calibration`/`reset_calibration`/`solve_calibration` cases)

- [ ] **Step 1: Replace the tell-edit dispatch cases**

Replace the dispatch cases `capture_template`, `add_required_also`, `remove_required_also`, `add_alt`, `remove_alt`, `update_tell` (and `test_template`/`get_template_images` wherever they appear) with these region-based cases. Keep `list_tells`, `list_rois`, and the selection/HUD `update_config` cases as-is.

```python
    elif t == "update_region":
        sn = msg.get("screen", "")
        res = detector.update_region(
            sn, int(msg.get("group", 0)), int(msg.get("region", 0)),
            roi=msg.get("roi"), thresh=msg.get("thresh"),
            grayscale=msg.get("grayscale"), kind=msg.get("kind"),
            icon_roi=msg.get("icon_roi"))
        if res is not None:
            _persist_tell_tree(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t in ("add_region", "remove_region", "add_group", "remove_group"):
        sn = msg.get("screen", "")
        if t == "add_region":
            res = detector.add_region(sn, int(msg.get("group", 0)), roi=msg.get("roi"))
        elif t == "remove_region":
            res = detector.remove_region(sn, int(msg.get("group", 0)), int(msg.get("region", 0)))
        elif t == "add_group":
            res = detector.add_group(sn, roi=msg.get("roi"))
        else:
            res = detector.remove_group(sn, int(msg.get("group", 0)))
        if res is not None:
            _persist_tell_tree(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "capture_region_template":
        frame = current_frame[0]
        if frame is not None:
            res = detector.capture_region_template(
                frame, msg.get("screen", ""),
                int(msg.get("group", 0)), int(msg.get("region", 0)))
            if res:
                _persist_tell_tree(settings, msg.get("screen", ""), detector)
                ipc.emit(emit_template_saved(**res))
            else:
                ipc.emit(emit_error(f"Failed to capture region for {msg.get('screen')!r}"))

    elif t == "test_region":
        frame = current_frame[0]
        res = detector.test_region(frame, msg.get("screen", ""),
                                   int(msg.get("group", 0)), int(msg.get("region", 0)))
        if res is not None:
            ipc.emit(emit_template_test(**res))

    elif t == "get_region_images":
        frame = current_frame[0]
        res = detector.get_region_images(frame, msg.get("screen", ""),
                                         int(msg.get("group", 0)), int(msg.get("region", 0)))
        if res is not None:
            ipc.emit(emit_template_images(**res))
```

Check the existing emit helper names for the old `test_template`/`get_template_images` responses (search `emit_template_test`, `emit_template_images` in `mkw_tracker/ipc/`). Reuse them; if their signatures take `roi_key`, add `group`/`region` params to those emit helpers and the frontend reader, or pass through `**res` which already carries `group`/`region`.

- [ ] **Step 2: Delete the calibration dispatch cases**

Remove the dispatch cases for `get_calibration`, `capture_calib_frame`, `solve_calibration`, `clear_calib_frames`, `reset_calibration`, and the `_apply_calibration_result` helper + its calls (~lines 270-310, 610-640). Leave the `Normalizer` and `calib_*` config reads intact (out of scope).

- [ ] **Step 3: Grep for stragglers**

Run: `python - <<'PY'
import re, pathlib
src = pathlib.Path("mkw_tracker/main.py").read_text()
for bad in ("required_also", "add_alt", "remove_alt", "update_tell", "_persist_tell_structure", "roi_key"):
    assert bad not in src, f"stray reference: {bad}"
print("clean")
PY`
Expected: prints `clean`. Fix any stray reference it reports.

- [ ] **Step 4: Verify import + a quick detector round-trip**

Run: `python -c "import mkw_tracker.main; from mkw_tracker.detection.screen import ScreenDetector; d=ScreenDetector(); d.add_group('TITLE',[1,2,3,4]); print('ok', len(d.get_tells_config()))"`
Expected: prints `ok 20`, no traceback.

- [ ] **Step 5: Run the whole backend test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/main.py mkw_tracker/ipc
git commit -m "feat: region-based tell-edit IPC; remove calibration handlers"
```

---

## Phase 2 — Frontend: Edit Screens View + Boolean-Tree Editor

> `src/App.svelte` is one 3363-line file. Each task below is a focused edit. The ROI-canvas drag code (`drawRoi`, mouse handlers, `liveRoiCrop`) and the template/asset-capture markup are **preserved verbatim** — only re-parented and re-sourced from the new state model. Verification is by running the app (`npm run tauri dev`) since there is no Svelte test harness.

### Task 9: Frontend state model + delete wizard-step plumbing

**Files:**
- Modify: `src/App.svelte` (state declarations ~lines 97-183; wizard step arrays)

- [ ] **Step 1: Replace step arrays and add the editor model**

Replace `FIRST_TIME_STEPS`/`RERUN_STEPS`/`STEP_LABELS`/`STEPS` (~lines 177-183) with:

```js
  // First-time setup is now language → camera → done only.
  const FIRST_TIME_STEPS = ["language", "camera", "done"];
  const STEP_LABELS = { language: "Language", camera: "Camera", done: "Done" };
  $: STEPS = FIRST_TIME_STEPS;
```

Add near the other editor state (~line 101):

```js
  // ── Edit Screens model ──────────────────────────────────────────────────
  let selectedNode = null;                 // Screen name currently open, or null
  let activeTab = "detection";             // "detection" | "selection" | "hud" | "templates"
  let activeRegion = { group: 0, region: 0 };  // Detection tab selection
  let settingsOpen = false;                // slim Settings panel (language + camera)
```

- [ ] **Step 2: Add the `edit` view to the `view` reactive**

Change the `view` reactive (~line 34) so an explicit edit flag wins:

```js
  let editMode = false;
  $: view = setupComplete === null ? "startup"
          : setupComplete === false ? "setup"
          : editMode ? "edit" : "main";
```

- [ ] **Step 3: Verify dev build compiles**

Run: `npm run build` (Vite build of the UI)
Expected: builds with no Svelte compile errors about the removed `RERUN_STEPS`/`STEP_LABELS` keys. Fix references the compiler flags (there will be several — proceed to Task 10/11 which remove them).

- [ ] **Step 4: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ui): editor state model; shrink first-time steps"
```

---

### Task 10: Delete calibration from the frontend

**Files:**
- Modify: `src/App.svelte` (calib state ~lines 121-133; handlers ~lines 1110-1133; `calibration_result` case ~lines 630-644; markup; nav links ~lines 1171, 1983)

- [ ] **Step 1: Delete calibration state, handlers, inbound case**

Remove: the `// ── Calibration state` block (`calibStatus`, `calibFitQuality`, `calibError`, `calibResetTellOverrides`, `CALIB_SLOTS`, `calibCapturedSlots`, `calibValues`); the handlers `doSolveCalibration`, `doResetCalibration`, the calib-frame capture fn, `_setCalibValue`; the `case "calibration_result":` block in `handleMsg`; and the `if (step==="calibration") { ... send({type:"get_calibration"}) }` branch in `goStep`.

- [ ] **Step 2: Delete calibration markup + nav links**

Remove the entire calibration wizard-step markup block, and change the two nav links: in `prevStep` (~line 1171) `goStep("calibration")` → `goStep("camera")`; the camera step's `Next: Calibration →` button (~lines 1983-1984) → `Next: Done →` calling `goStep("done")`.

- [ ] **Step 3: Grep for stragglers**

Run: `grep -ni "calib" src/App.svelte`
Expected: no matches (or only unrelated words). Remove any remaining.

- [ ] **Step 4: Verify build**

Run: `npm run build`
Expected: no errors referencing `calib*`.

- [ ] **Step 5: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ui): remove calibration from setup UI"
```

---

### Task 11: Slim Settings panel + Edit Screens entry

**Files:**
- Modify: `src/App.svelte` (header buttons ~line 1498-1500; main view markup)

- [ ] **Step 1: Replace the header `⚙ Setup` button**

Where the main-view header renders `⚙ Setup` (~line 1500), render two buttons:

```svelte
<button class="btn-hdr btn-edit" on:click={() => { editMode = true; if (!selectedNode) openNode("CHARACTER_SELECT"); }}>Edit Screens</button>
<button class="btn-hdr btn-setup" on:click={() => settingsOpen = true}>⚙ Settings</button>
```

- [ ] **Step 2: Add `openNode` + Settings handlers**

Near the other functions, add:

```js
  function openNode(screenName) {
    selectedNode = screenName;
    activeTab = "detection";
    activeRegion = { group: 0, region: 0 };
    editMode = true;
    send({ type: "list_tells" });
    send({ type: "list_rois" });
  }
  function closeEdit()     { editMode = false; }
  function closeSettings() { settingsOpen = false; }
```

- [ ] **Step 3: Add the Settings panel markup**

After the main-grid markup, add a modal that reuses the existing language + camera step bodies (move the `{#if wizardStep === "language"}` and `"camera"` inner content into a shared snippet or duplicate the language/camera form markup). Gate it with `{#if settingsOpen}`. It must contain only language selects (`appLanguage`, `switch2Language` with `onAppLanguageChange`/`onSwitch2LanguageChange`) and the camera/audio device pickers, plus a Close button calling `closeSettings()`.

- [ ] **Step 4: Verify build + manual smoke**

Run: `npm run build`, then `npm run tauri dev`
Expected: UI builds; `⚙ Settings` opens the panel with language + camera only; `Edit Screens` switches to a (still-empty in this task) edit view. Detailed edit content lands in Tasks 12-13.

- [ ] **Step 5: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ui): slim Settings panel + Edit Screens entry button"
```

---

### Task 12: Edit Screens split-pane + interactive/click-through graph

**Files:**
- Modify: `src/App.svelte` (footer graph block ~lines 1758-1823; add `edit` view block after the `main` block)

- [ ] **Step 1: Make footer graph nodes click-through**

On the footer graph `<g transform=...>` node group (~line 1790), add a click handler and pointer cursor:

```svelte
<g transform="translate({node.x},{node.y})" style="cursor:pointer"
   on:click={() => openNode(node.id)} role="button" tabindex="-1">
```

`openNode` already sets `editMode = true`, so clicking a footer node enters the edit view on that node.

- [ ] **Step 2: Add the `edit` view block**

After the `{:else if view === "setup"}` … block and before the final close, add `{:else if view === "edit"}` rendering a split pane:

```svelte
{:else if view === "edit"}
<div class="edit-view">
  <header class="edit-bar">
    <button class="btn-hdr" on:click={closeEdit}>← Back</button>
    <span class="edit-title">Editing: {SCREEN_LABELS[selectedNode] ?? selectedNode}</span>
  </header>
  <div class="edit-split">
    <div class="edit-graph">
      <!-- reuse the same SVG graph; nodes call openNode(node.id);
           highlight node.id === selectedNode -->
    </div>
    <div class="edit-pane">
      <!-- tab bar + tab body (Task 13) -->
    </div>
  </div>
</div>
```

Copy the existing `<svg ...>` graph markup into `.edit-graph`, enlarged (drop the fixed tiny font sizes / scale the viewBox via CSS `width:100%`), with each node `on:click={() => openNode(node.id)}` and a selected-state stroke when `node.id === selectedNode`.

- [ ] **Step 3: Add the tab bar**

Inside `.edit-pane`, compute the visible tabs from the node and render the bar:

```svelte
{@const tabs = tabsForNode(selectedNode)}
<nav class="edit-tabs">
  {#each tabs as tabKey}
    <button class:active={activeTab===tabKey} on:click={() => activeTab=tabKey}>
      {TAB_LABELS[tabKey]}
    </button>
  {/each}
</nav>
```

Add the helper + labels near the asset data:

```js
  const TAB_LABELS = { detection:"Detection", selection:"Selection", hud:"HUD", templates:"Templates" };
  const NODE_SELECTION = { CHARACTER_SELECT:["char_name","costume"], KART_SELECT:["kart_name"], COURSE_SELECT:["course_name"] };
  const NODE_HUD       = { RACING:["lap_current","lap_total","coin_left","coin_right","finish","mushroom"] };
  const NODE_TEMPLATES = { CHARACTER_SELECT:["characters","costumes"], KART_SELECT:["karts"], COURSE_SELECT:["courses"], RACING:["mushrooms"] };
  function tabsForNode(n) {
    const t = ["detection"];
    if (NODE_SELECTION[n]) t.push("selection");
    if (NODE_HUD[n])       t.push("hud");
    if (NODE_TEMPLATES[n]) t.push("templates");
    return t;
  }
```

- [ ] **Step 4: Verify build + manual smoke**

Run: `npm run build`, then `npm run tauri dev`
Expected: `Edit Screens` shows the split pane; clicking nodes (in the edit graph or the main footer graph) selects them and shows the right tab set (CHARACTER_SELECT shows Detection/Selection/Templates; TITLE shows only Detection).

- [ ] **Step 5: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ui): Edit Screens split-pane with click-through graph and tabs"
```

---

### Task 13: Tab bodies — boolean-tree Detection + re-parented Selection/HUD/Templates

**Files:**
- Modify: `src/App.svelte` (tell editor functions ~lines 785-1075, 1139-1164, 1405-1428; tab body markup)

- [ ] **Step 1: Replace the tell-edit helpers with `(group, region)` versions**

Replace `getAllRoisForTell`, `activeRoiKey` usage, and the `update_tell`-based ROI-commit functions with region-based ones that read `currentTell.groups`. The `currentTell` reactive becomes:

```js
  $: currentTell = tells.find(t => t.screen === selectedNode) ?? null;
  $: activeRegionObj = currentTell?.groups?.[activeRegion.group]?.[activeRegion.region] ?? null;
```

ROI-commit (called by the drag handler on mouse-up) sends `update_region`:

```js
  function commitRegionRoi(roi) {
    if (!selectedNode) return;
    send({ type:"update_region", screen:selectedNode,
           group:activeRegion.group, region:activeRegion.region, roi });
  }
```

Threshold slider → `send({type:"update_region", screen:selectedNode, group, region, thresh})`.
Recapture → `send({type:"capture_region_template", screen:selectedNode, group:activeRegion.group, region:activeRegion.region})`.
Live preview/test → `send({type:"test_region", ...})` / `get_region_images`.
Region/group add-remove buttons → `add_region`/`remove_region`/`add_group`/`remove_group`.
Kind dropdown → `update_region` with `kind` (and `icon_roi` when `dark_loading`).

Keep the `drawRoi`/mouse-drag canvas code unchanged; only its "which ROI" source switches to `activeRegionObj.roi`, and on commit it calls `commitRegionRoi`.

- [ ] **Step 2: Detection tab markup (boolean tree)**

Render the tree from `currentTell.groups`:

```svelte
{#if activeTab === "detection" && currentTell}
  <div class="tree-editor">
    <div class="tree-label">Detected when ALL match:</div>
    {#each currentTell.groups as group, gi}
      {#if gi > 0}<div class="tree-and">— AND —</div>{/if}
      <div class="tree-group">
        <div class="tree-group-hd">GROUP {gi+1} · any of</div>
        {#each group as region, ri}
          <button class="tree-region" class:sel={activeRegion.group===gi && activeRegion.region===ri}
                  on:click={() => { activeRegion={group:gi,region:ri}; send({type:'get_region_images',screen:selectedNode,group:gi,region:ri}); }}>
            <span>{region.kind === "dark_loading" ? "dark-loading" : `region ${ri+1}`}</span>
          </button>
        {/each}
        <button class="tree-add-or" on:click={() => send({type:'add_region',screen:selectedNode,group:gi})}>+ alternative image (OR)</button>
        {#if currentTell.groups.length > 1 || group.length > 1}
          <button class="tree-del" on:click={() => send({type:'remove_region',screen:selectedNode,group:gi,region:activeRegion.region})}>🗑 region</button>
        {/if}
      </div>
    {/each}
    <button class="tree-add-and" on:click={() => send({type:'add_group',screen:selectedNode})}>+ add condition group (AND)</button>
    <!-- selected-region controls: kind dropdown, threshold slider, Recapture -->
  </div>
{/if}
```

The feed/ROI canvas (left of this panel) draws every region's ROI by iterating `currentTell.groups` and highlights `activeRegion`.

- [ ] **Step 3: Selection / HUD / Templates tab bodies**

Move the existing `selection`, `hud`, and `templates` wizard-step markup into `{#if activeTab === "selection"}` / `"hud"` / `"templates"` blocks, scoped to the current node: iterate `NODE_SELECTION[selectedNode]` / `NODE_HUD[selectedNode]` for the ROI list (using `SELECTION_ROI_CONFIG_KEYS`/`HUD_ROI_CONFIG_KEYS` + `update_config`, unchanged), and `NODE_TEMPLATES[selectedNode]` for the asset categories. The asset capture flow (`capture_asset_template`/`get_asset_template`) is unchanged.

- [ ] **Step 4: Update the template-images reactive**

The reactive that requested template images on step/index change (~line 1407, 1428) keys off the new model:

```js
  $: if (view === "edit" && activeTab === "detection" && selectedNode)
       send({ type:"get_region_images", screen:selectedNode,
              group:activeRegion.group, region:activeRegion.region });
```

And the `template_images` inbound case (~line 524) matches on `selectedNode` + `activeRegion` instead of `currentScreenName`/`activeRoiKey`.

- [ ] **Step 5: Verify build + full manual smoke**

Run: `npm run build`, then `npm run tauri dev`. With the sidecar running and a feed (or `--video`):
Expected:
- CHARACTER_SELECT → Detection shows one group/one region; dragging the ROI on the feed updates it; Recapture saves and the score badge updates; Selection tab edits char_name/costume; Templates tab captures a character.
- RACING → Detection shows two groups (coin AND flag); adding a region to a group shows an OR alternative; HUD tab shows all six ROIs.
- Edits survive a sidecar restart (persisted as `tell_tree_*`).

- [ ] **Step 6: Commit**

```bash
git add src/App.svelte
git commit -m "feat(ui): boolean-tree Detection editor + re-parented Selection/HUD/Templates"
```

---

## Phase 3 — Cleanup + Docs

### Task 14: Update docs + remove dead wizard CSS

**Files:**
- Modify: `docs/ipc-protocol.md`, `docs/config-reference.md`, `CLAUDE.md` (screen-detection note)
- Modify: `src/App.svelte` (delete now-unused wizard CSS classes if clearly orphaned)

- [ ] **Step 1: Document the new IPC**

In `docs/ipc-protocol.md`, replace the `update_tell`/`add_alt`/`add_required_also`/`capture_template`/`test_template` entries with `update_region`, `add_region`, `remove_region`, `add_group`, `remove_group`, `capture_region_template`, `test_region`, `get_region_images` (payloads: `screen`, `group`, `region`, plus `roi`/`thresh`/`grayscale`/`kind`/`icon_roi` where relevant). Remove the calibration message section.

- [ ] **Step 2: Document the persistence change**

In `docs/config-reference.md` (and `docs/database-schema.md` if it lists config keys), replace the six `tell_*` keys with `tell_tree_<SCREEN>` (one JSON blob per screen) and note schema version 3 migrates the old keys.

- [ ] **Step 3: Update the detection note in `detection/screen.py` docstring + CLAUDE.md**

Update the `Screen Detection` paragraph in `CLAUDE.md` to describe the boolean-tree model (AND of groups, OR within group; `dark_loading` region kind) instead of "primary ROI + optional alt_roi + optional required_also".

- [ ] **Step 4: Run the full test suite once more**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs CLAUDE.md mkw_tracker src
git commit -m "docs: boolean-tree tells, region IPC, tell_tree persistence"
```

---

## Self-Review Notes (coverage map)

- Spec §"Remove calibration" → Tasks 8 (backend handlers), 10 (frontend).
- Spec §"First-time wizard shrink" → Task 9.
- Spec §"Slim Settings panel" → Task 11.
- Spec §"Edit Screens view" + asset→node mapping → Tasks 12, 13.
- Spec §"Footer graph click-through" → Task 12 Step 1.
- Spec §"Boolean tree" data model + detection → Tasks 2, 3, 4.
- Spec §"IPC changes" → Tasks 5, 8.
- Spec §"Persistence + migration" → Tasks 6, 7.
- Spec §"Testing" → Tasks 1-6 (unit + migration); 12-13 (frontend smoke).
- Spec §"Suggested phasing" → Phases 1/2/3.

**Memory note for the executing agent:** verify `mkw_tracker/database/connection.py`'s real connection-caching globals before relying on the `conftest.py` reset (Task 1 Step 3), and verify the actual `emit_template_test`/`emit_template_images`/`emit_template_saved` signatures in `mkw_tracker/ipc/` before wiring Task 8 Step 1.
```
