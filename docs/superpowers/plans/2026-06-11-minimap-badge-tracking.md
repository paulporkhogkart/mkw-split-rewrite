# Minimap Badge Tracking (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the minimap interior-face identity scorer with the measured "badge" (face + ring) masked-NCC template, publishing the correlation argmax so the position stops shaking.

**Architecture:** New `mkw_tracker/minimap/badge.py` holds the template (build / score / seed-centre refinement) as a focused, unit-testable module. `tracker.py` keeps the ring-first state machine but delegates identity + position refinement to the badge; gates move to the NCC scale; a v5 DB migration wipes old-scale `minimap_thresholds`. Spec with all measured evidence: `docs/superpowers/specs/2026-06-11-minimap-badge-tracking-design.md`.

**Tech Stack:** Python, OpenCV (masked `TM_CCOEFF_NORMED`, verified exact vs reference NCC on OpenCV 4.13), numpy, pytest, SQLite.

**Repo rules:** stage files explicitly (`git add <paths>`) - `race/finish.py`, `src-tauri/Cargo.toml`, `src/lib/raceTimerBuffer.js` carry unrelated WIP. Commit messages end with the Claude Code co-author line.

---

### Task 1: Commit the spec and this plan

**Files:**
- Commit: `docs/superpowers/specs/2026-06-11-minimap-badge-tracking-design.md`
- Commit: `docs/superpowers/plans/2026-06-11-minimap-badge-tracking.md`

- [ ] **Step 1: Commit**

```bash
git add docs/superpowers/specs/2026-06-11-minimap-badge-tracking-design.md docs/superpowers/plans/2026-06-11-minimap-badge-tracking.md
git commit -m "docs(minimap): badge-tracking design spec + implementation plan

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `badge.py` - template build + masked-NCC score

**Files:**
- Create: `mkw_tracker/minimap/badge.py`
- Test: `tests/test_minimap_badge.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the minimap badge template (build / score / refinement)."""
import cv2
import numpy as np
import pytest

from mkw_tracker.minimap.badge import (
    BadgeTemplate, refine_seed_centre, BADGE_HALF, BADGE_PAD,
)


def make_roi(w=342, h=389, seed=7):
    """Textured terrain background (seeded noise, blurred like map art)."""
    rng = np.random.default_rng(seed)
    roi = rng.integers(120, 200, (h, w, 3), dtype=np.uint8)
    return cv2.GaussianBlur(roi, (7, 7), 0)


def draw_badge(roi, cx, cy):
    """Synthetic player badge: dark halo, white ring, face blob with features."""
    cv2.circle(roi, (cx, cy), 22, (40, 40, 40), 2, cv2.LINE_AA)
    cv2.circle(roi, (cx, cy), 20, (245, 245, 245), 3, cv2.LINE_AA)
    cv2.circle(roi, (cx, cy), 12, (60, 200, 230), -1, cv2.LINE_AA)
    cv2.circle(roi, (cx - 4, cy - 3), 3, (30, 30, 30), -1)
    cv2.circle(roi, (cx + 4, cy - 3), 3, (30, 30, 30), -1)
    return roi


def test_build_then_self_score_is_one():
    roi = draw_badge(make_roi(), 170, 190)
    b = BadgeTemplate()
    assert b.build(roi, 170, 190)
    assert b.ready
    score, pos = b.score(roi, 170, 190)
    assert score == pytest.approx(1.0, abs=1e-4)
    assert pos == (170, 190)


def test_score_recovers_offset_centre():
    """Search centred 5,-3 off the badge still finds the exact badge centre."""
    roi = draw_badge(make_roi(), 170, 190)
    b = BadgeTemplate()
    assert b.build(roi, 170, 190)
    score, pos = b.score(roi, 175, 187)
    assert score == pytest.approx(1.0, abs=1e-4)
    assert pos == (170, 190)


def test_score_at_roi_edge_does_not_crash():
    """Replicate padding keeps edge positions scoreable (no exception, no None)."""
    roi = draw_badge(make_roi(), 12, 12)   # badge hugging the corner
    b = BadgeTemplate()
    assert b.build(roi, 12, 12)
    score, pos = b.score(roi, 12, 12)
    assert score > 0.8
    assert pos == (12, 12)


def test_plain_terrain_scores_below_accept_gate():
    roi_a = draw_badge(make_roi(seed=7), 170, 190)
    b = BadgeTemplate()
    assert b.build(roi_a, 170, 190)
    roi_b = make_roi(seed=8)               # different terrain, no badge
    score, _ = b.score(roi_b, 170, 190)
    assert score < 0.45


def test_flat_window_yields_zero_not_nan():
    roi = draw_badge(make_roi(), 170, 190)
    b = BadgeTemplate()
    assert b.build(roi, 170, 190)
    flat = np.full_like(roi, 180)
    score, _ = b.score(flat, 170, 190)
    assert np.isfinite(score)
    assert score <= 0.0


def test_not_ready_returns_zero():
    b = BadgeTemplate()
    score, pos = b.score(make_roi(), 100, 100)
    assert score == 0.0 and pos is None
    b.clear()
    assert not b.ready
```

(Leave `refine_seed_centre` imported but untested here; Task 3 adds its tests to this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_minimap_badge.py -v`
Expected: collection error - `ModuleNotFoundError: No module named 'mkw_tracker.minimap.badge'`

- [ ] **Step 3: Write `mkw_tracker/minimap/badge.py`**

```python
"""Whole-marker "badge" template for the minimap player marker.

The badge is the character face plus the white ring the game draws around the
local player. One masked, zero-mean NCC template over Lab pixels: HDR-flattened
capture shifts gain/offset per window, which TM_CCOEFF_NORMED cancels, and the
ring contributes washout-resistant structure that map terrain cannot imitate.
Measured margins and the design rationale live in
docs/superpowers/specs/2026-06-11-minimap-badge-tracking-design.md.
"""
import cv2
import numpy as np
from typing import Optional, Tuple

BADGE_HALF   = 22   # template half-side -> 44x44 crop
BADGE_PAD    = 8    # slide reach around the search centre (covers Hough error;
                    # the ring's thin annulus makes the peak ~4px sharp)
BADGE_MASK_R = 21   # circular mask: face + ring, halo/corner terrain excluded

_ANNULUS_RADII = (19, 21, 23)
_ANNULUS_THICK = 3


def _lab(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32) / 255.0


def _crop_padded(roi: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    """Crop roi[y1:y2, x1:x2], replicate-padding any part outside the image."""
    hh, ww = roi.shape[:2]
    lpad = max(0, -x1); tpad = max(0, -y1)
    rpad = max(0, x2 - ww); bpad = max(0, y2 - hh)
    crop = roi[max(0, y1):min(hh, y2), max(0, x1):min(ww, x2)]
    if lpad or tpad or rpad or bpad:
        crop = cv2.copyMakeBorder(crop, tpad, bpad, lpad, rpad,
                                  cv2.BORDER_REPLICATE)
    return crop


def _make_annulus(r: int, pad: int = 4) -> np.ndarray:
    size = 2 * (r + pad) + 1
    img = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), r, 255, _ANNULUS_THICK, cv2.LINE_AA)
    return img


def refine_seed_centre(roi: np.ndarray, cx: int, cy: int,
                       window: int = 16) -> Tuple[int, int]:
    """Snap a stored seed point onto the actual ring centre.

    Stored seeds are hand-captured once and sit a few px off the live badge
    (start position varies per setup); an off-centre seed bakes that offset
    into the template for the whole race. One annulus-NCC pass over a
    +/-window box fixes it. Returns the input unchanged when nothing
    ring-like is found.
    """
    reach = window + max(_ANNULUS_RADII) + 4
    x1, y1 = cx - reach, cy - reach
    crop = _crop_padded(roi, x1, y1, cx + reach + 1, cy + reach + 1)
    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    best, bx, by = -1.0, cx, cy
    for r in _ANNULUS_RADII:
        tpl = _make_annulus(r)
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best:
            half = tpl.shape[0] // 2
            best, bx, by = mx, x1 + loc[0] + half, y1 + loc[1] + half
    if best < 0.2:
        return cx, cy
    # clamp to the window: a stronger ring elsewhere must not steal the seed
    bx = max(cx - window, min(cx + window, bx))
    by = max(cy - window, min(cy + window, by))
    return bx, by


class BadgeTemplate:
    """Masked Lab template of the player badge, locked once per race at seed."""

    def __init__(self):
        self._tpl:  Optional[np.ndarray] = None   # (44, 44, 3) float32 Lab
        self._mask: Optional[np.ndarray] = None   # (44, 44, 3) float32 0/1

    @property
    def ready(self) -> bool:
        return self._tpl is not None

    def clear(self):
        self._tpl = None
        self._mask = None

    def build(self, roi: np.ndarray, cx: int, cy: int) -> bool:
        h = BADGE_HALF
        crop = _crop_padded(roi, cx - h, cy - h, cx + h, cy + h)
        if crop.shape[:2] != (2 * h, 2 * h):
            return False
        self._tpl = _lab(crop)
        m = np.zeros((2 * h, 2 * h), dtype=np.float32)
        cv2.circle(m, (h, h), BADGE_MASK_R, 1.0, -1)
        self._mask = np.repeat(m[:, :, None], 3, axis=2)
        return True

    def score(self, roi: np.ndarray, cx: int, cy: int):
        """Masked zero-mean NCC slid +/-BADGE_PAD around (cx, cy).

        Returns (best_score, (rx, ry)) where (rx, ry) is the correlation
        argmax - the position to publish. (0.0, None) when not ready or the
        centre is outside the ROI.
        """
        if not self.ready:
            return 0.0, None
        hh, ww = roi.shape[:2]
        if not (0 <= cx < ww and 0 <= cy < hh):
            return 0.0, None
        h, pad = BADGE_HALF, BADGE_PAD
        big = _crop_padded(roi, cx - h - pad, cy - h - pad,
                           cx + h + pad, cy + h + pad)
        res = cv2.matchTemplate(_lab(big), self._tpl, cv2.TM_CCOEFF_NORMED,
                                mask=self._mask)
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), (cx - pad + loc[0], cy - pad + loc[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_minimap_badge.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/minimap/badge.py tests/test_minimap_badge.py
git commit -m "feat(minimap): badge template - masked Lab NCC with slide + argmax

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Seed self-centring tests

**Files:**
- Modify: `tests/test_minimap_badge.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_minimap_badge.py`)

```python
def test_refine_seed_centre_recovers_offset():
    """An 8px-off stored seed snaps onto the drawn ring centre."""
    roi = draw_badge(make_roi(), 170, 190)
    assert refine_seed_centre(roi, 178, 187) == (170, 190)


def test_refine_seed_centre_no_ring_is_identity():
    roi = make_roi(seed=9)
    assert refine_seed_centre(roi, 100, 120) == (100, 120)


def test_refine_seed_centre_clamps_to_window():
    """A ring far outside the window cannot steal the seed."""
    roi = draw_badge(make_roi(), 170, 190)
    cx, cy = refine_seed_centre(roi, 250, 190, window=16)
    assert abs(cx - 250) <= 16 and abs(cy - 190) <= 16
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_minimap_badge.py -v`
Expected: 9 passed (refine was implemented in Task 2; these tests pin its contract). If `test_refine_seed_centre_recovers_offset` is off by 1px, the synthetic ring's argmax landed on a neighbouring pixel: assert `abs(cx-170) <= 1 and abs(cy-190) <= 1` instead - the production tolerance is the +/-8 slide.

- [ ] **Step 3: Commit**

```bash
git add tests/test_minimap_badge.py
git commit -m "test(minimap): pin refine_seed_centre contract

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Integrate the badge into `MinimapTracker`

**Files:**
- Modify: `mkw_tracker/minimap/tracker.py`
- Test: `tests/test_minimap_tracker_badge.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Closed-loop MinimapTracker tests on synthetic frames (badge pipeline)."""
import cv2
import numpy as np

from mkw_tracker.detection.screen import Screen
from mkw_tracker.minimap.tracker import MinimapTracker

ROI = (1442, 251, 466, 796)   # tracker default MINIMAP_ROI
BX, BY = 1700, 600            # badge position (full-frame px)


def make_frame(cx, cy, seed=7):
    rng = np.random.default_rng(seed)
    frame = rng.integers(120, 200, (1080, 1920, 3), dtype=np.uint8)
    frame = cv2.GaussianBlur(frame, (7, 7), 0)
    cv2.circle(frame, (cx, cy), 22, (40, 40, 40), 2, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 20, (245, 245, 245), 3, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), 12, (60, 200, 230), -1, cv2.LINE_AA)
    cv2.circle(frame, (cx - 4, cy - 3), 3, (30, 30, 30), -1)
    cv2.circle(frame, (cx + 4, cy - 3), 3, (30, 30, 30), -1)
    return frame


def seeded_tracker(conf=None):
    tr = MinimapTracker()
    tr.seed(BX, BY, 0, frame=make_frame(BX, BY), confident_score=conf)
    return tr


def test_seed_default_radius_is_inside_hough_band():
    tr = seeded_tracker()
    assert 17 <= tr.state.radius <= 25


def test_seed_refines_offcentre_point():
    """Seeding 6px off the drawn badge centres the template + state on it."""
    tr = MinimapTracker()
    tr.seed(BX + 6, BY - 4, 0, frame=make_frame(BX, BY))
    assert abs(tr.state.cx - BX) <= 1 and abs(tr.state.cy - BY) <= 1


def test_update_publishes_argmax_position():
    """Badge moved +3,+2: published position is the exact new badge centre."""
    tr = seeded_tracker()
    st = tr.update(make_frame(BX + 3, BY + 2), Screen.RACING)
    assert st.tracking
    assert (st.cx, st.cy) == (BX + 3, BY + 2)
    assert st.last_score > 0.9
    assert st.track_state == "tracking"


def test_high_conf_gate_gives_ring_only_not_loss():
    """Score below an extreme confident gate but above accept: still published."""
    tr = seeded_tracker(conf=1.01)   # unreachable on purpose
    st = tr.update(make_frame(BX + 1, BY), Screen.RACING)
    assert st.tracking
    assert st.track_state == "ring_only"


def test_empty_map_goes_lost_and_unpublishes():
    tr = seeded_tracker()
    rng = np.random.default_rng(11)
    empty = cv2.GaussianBlur(
        rng.integers(120, 200, (1080, 1920, 3), dtype=np.uint8), (7, 7), 0)
    st = tr.state
    for _ in range(40):                     # > _MM_LOST_FRAMES misses
        st = tr.update(empty, Screen.RACING)
    assert st.track_state == "lost"
    assert not st.tracking


def test_reset_clears_badge():
    tr = seeded_tracker()
    tr.reset()
    st = tr.update(make_frame(BX, BY), Screen.RACING)
    assert not st.tracking                  # no template -> tracker inert
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_minimap_tracker_badge.py -v`
Expected: `test_seed_default_radius_is_inside_hough_band` FAILS (radius 27); `test_update_publishes_argmax_position` FAILS (publishes Hough centre, not argmax; HSV score scale); others may incidentally pass/fail - record the baseline.

- [ ] **Step 3: Modify `mkw_tracker/minimap/tracker.py`**

3a. Module docstring (replace lines 1-17):

```python
"""MinimapTracker - Hough-first ring detection, badge-NCC identity + position.

Each frame during RACING:

  1. Find the player ring with HoughCircles in the search window.
  2. Score the badge template (face + ring, masked Lab NCC) slid around the
     ring centre; the correlation argmax is the position candidate.
  3. Classify by score:
       >= confident_score  ->  TRACKING   (full confidence, calibrate)
       >= accept_score     ->  RING_ONLY  (ring found, face swapped/washed)
       <  accept_score     ->  reject     (probably another player's ring)
  4. No ring found         ->  miss

Ring-first means markers without a ring (the TT ghost) are rejected at step 1.
Publishing the badge argmax instead of the raw Hough centre is what keeps the
published position pixel-stable (see the 2026-06-11 design spec).
"""
```

3b. Imports - add after the existing relative imports:

```python
from .badge import BadgeTemplate, refine_seed_centre
```

3c. Constants - replace the four `_MM_CHAR_*` lines and the gate/calib values:

```python
# (delete _MM_CHAR_W_F, _MM_CHAR_H_F, _MM_CHAR_W_PX, _MM_CHAR_H_PX)
_MM_ACCEPT_SCORE      = 0.45   # badge-NCC floor: below = wrong target
_MM_CONFIDENT_SCORE   = 0.65   # default confidence threshold (auto-calibrated)
...
_MM_CALIB_MIN         = 0.55
_MM_CALIB_MAX         = 0.90
```

3d. `__init__` - replace `self._char_template` / `self._char_mask` / `self._clahe` with:

```python
        self._badge = BadgeTemplate()
        self._last_refined: Optional[tuple] = None
```

3e. `reset()` - replace the two `_char_*` lines with:

```python
        self._badge.clear()
        self._last_refined     = None
```

3f. `seed()` - radius default becomes the Hough band midpoint, and the
template block at the end is replaced:

```python
        if radius == 0:
            radius = (_MM_HOUGH_R_MIN + _MM_HOUGH_R_MAX) // 2   # stay inside the Hough band
```

```python
        if frame is not None:
            roi = frame[self._roi_y:self._roi_y + self._roi_h,
                        self._roi_x:self._roi_x + self._roi_w]
            cx_r, cy_r = refine_seed_centre(
                roi, cx_full - self._roi_x, cy_full - self._roi_y)
            if self._badge.build(roi, cx_r, cy_r):
                # keep the published point consistent with the template centre
                self.state.cx        = cx_r + self._roi_x
                self.state.cy        = cy_r + self._roi_y
                self.state.cx_smooth = float(self.state.cx)
                self.state.cy_smooth = float(self.state.cy)
                print("  [MinimapTracker] Badge template locked from seed frame")
            else:
                print("  [MinimapTracker] WARNING: could not build badge template at seed point")
```

3g. `update()` - the early-out and the scoring call:

```python
        if not self._badge.ready:
            return self.state
```

(keep the existing `r = hr if hr > 0 else ...` block - `r` still feeds the
published radius) and replace the `_score_at` call:

```python
        score = self._score_at(roi, hx, hy)
```

3h. Replace `_score_at` entirely (and drop its `radius` parameter):

```python
    def _score_at(self, roi: np.ndarray, cx_r: int, cy_r: int) -> float:
        """Badge NCC at the ring centre; stashes the argmax for publishing."""
        score, refined = self._badge.score(roi, cx_r, cy_r)
        self._last_refined = refined
        return score
```

3i. `_on_confirmed_hit` - first lines become:

```python
    def _on_confirmed_hit(self, cx_r: int, cy_r: int, radius: int, score: float):
        if self._last_refined is not None:
            cx_r, cy_r = self._last_refined
        dist      = self._dist_from_smooth(cx_r, cy_r)
```

3j. Delete `_make_template`, `_make_circle_mask`, `_crop_interior` (the badge
module owns template construction now).

- [ ] **Step 4: Grep for dangling references**

Run: `python -m pytest tests/ -x -q` and
`grep -rn "_char_template\|_make_template\|_crop_interior\|_make_circle_mask\|_MM_CHAR" mkw_tracker/ tests/ --include=*.py`
Expected: no hits outside `temp/` (the lab harness subclasses old internals and is updated in Task 6); full suite passes.

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_minimap_tracker_badge.py tests/test_minimap_badge.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add mkw_tracker/minimap/tracker.py tests/test_minimap_tracker_badge.py
git commit -m "feat(minimap): badge-NCC identity + argmax publish in MinimapTracker

Gates move to the NCC scale (accept 0.45, confident 0.65, calib 0.55-0.90);
seed self-centres via annulus refine; seed radius default now inside the
Hough band; HSV face-template path removed.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: DB migration v5 - clear old-scale thresholds

**Files:**
- Modify: `mkw_tracker/database/migrations.py` (append after the v4 block)
- Test: `tests/test_minimap_badge.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/test_minimap_badge.py`)

```python
def test_migration_v5_wipes_old_scale_thresholds(memdb):
    from mkw_tracker.database.connection import get_connection
    from mkw_tracker.database.migrations import apply_migrations
    conn = get_connection()
    conn.execute("INSERT INTO minimap_thresholds(course, character, costume, threshold)"
                 " VALUES ('X', 'Y', '', 0.9)")
    conn.execute("UPDATE schema_version SET version=4")
    conn.commit()
    apply_migrations(memdb)
    assert conn.execute("SELECT COUNT(*) FROM minimap_thresholds").fetchone()[0] == 0
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_minimap_badge.py::test_migration_v5_wipes_old_scale_thresholds -v`
Expected: FAIL - count is 1, version is 4

- [ ] **Step 3: Implement** (append to `migrations.py`; add `_SCHEMA_V5` next to the other schema strings)

```python
# v5: minimap identity scores moved from raw-CCORR to badge-NCC scale; stored
# per-combo confident thresholds are meaningless on the new scale and would
# lock races into ring_only. Auto-calibration repopulates them per race.
_SCHEMA_V5 = "DELETE FROM minimap_thresholds;"
```

and at the end of `apply_migrations`:

```python
    cur.execute("SELECT version FROM schema_version")
    row = cur.fetchone()
    current = row[0] if row else 0
    if current < 5:
        conn.executescript(_SCHEMA_V5)
        conn.execute("UPDATE schema_version SET version=5")
        conn.commit()
        print("[DB] v5: cleared minimap_thresholds (badge score rescale)")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_minimap_badge.py tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add mkw_tracker/database/migrations.py tests/test_minimap_badge.py
git commit -m "feat(db): v5 migration clears old-scale minimap thresholds

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Harness regression + docs

**Files:**
- Modify: `temp/mm_lab.py` (lab only, uncommitted dir is fine if temp/ is ignored; otherwise commit)
- Modify: `CLAUDE.md` (minimap paragraph)

- [ ] **Step 1: Point the lab at the production tracker**

In `temp/mm_lab.py`: the `LabTracker` overrides reference removed internals
only inside `identity2` branches (`MinimapTracker._crop_interior`,
`super()._make_template`, `super()._score_at(roi, cx, cy, radius)`). Update:
- `VARIANTS` - add `"prod": dict(conf=0.65)` as the first entry.
- In `LabTracker._score_at`, the non-id2/non-badge fallthrough becomes
  `score = super()._score_at(roi, cx_r, cy_r)` (3-arg signature) and the
  refined publish is now production behaviour, so record it:
  `self.rec["score"] = score; return score`.
- Delete the `identity2`-mode methods (`_crop_interior`, `_make_template`,
  the manual-loop `_score_at` branch) and the `id2`/`clahe_id2`/`full`
  VARIANTS entries - they exercised internals that no longer exist; `badge`
  (manual reference impl) and `prod` (production path) remain comparable.

- [ ] **Step 2: Run the regression**

Run:
```bash
python temp/mm_lab.py --clip bootest prod
python temp/mm_lab.py --clip koops prod
python temp/mm_lab.py --clip short prod
```
Acceptance (vs the prototype `badge` numbers in the spec):
- bootest: raw p90 <= 1.5px, p99 <= 2.0px, tracking >= 95%, teleports 0, ms_med <= 4
- koops:   raw p99 <= 3px, teleports 0, published = frames
- short:   raw p99 <= 2px, teleports 0
If a number regresses, diff `prod` vs `badge` rows JSON before touching gates.

- [ ] **Step 3: Update `CLAUDE.md`**

Replace the "Minimap Tracking" paragraph body with:

```markdown
### Minimap Tracking (`minimap/tracker.py`)
`seed()` snaps the stored per-course seed onto the live ring (annulus NCC) and
locks a **badge template** (`minimap/badge.py`): a 44x44 masked Lab crop of the
face + white ring. Per-frame gatekeeping: HoughCircles finds the ring in the
search window -> the badge template slides +/-8px around it (masked
`TM_CCOEFF_NORMED`, gain/offset-invariant - survives HDR washout) -> the
correlation **argmax** is published, so Hough centre wobble never reaches the
UI/recorder. Score gates: accept 0.45 (wrong-target reject) / confident 0.65
(auto-calibrated per course+character+costume by `calibrate_from_race`).
Jump gate (40px) -> re-acquire (4 frames) -> LOST after 36 misses. The TT
ghost has no ring and is rejected structurally. Measurement harness:
`temp/mm_lab.py` (see the 2026-06-11 design spec).
```

- [ ] **Step 4: Full suite once more**

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md temp/mm_lab.py
git commit -m "docs+lab: badge tracking notes; harness regression variant for prod path

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review notes

- Spec coverage: template/scoring (Task 2), seed self-centring (Tasks 3/4),
  gates + calib rescale + radius hygiene + old-path removal (Task 4),
  threshold migration (Task 5), harness regression + acceptance numbers +
  docs (Task 6). Phase 2/3 items are explicitly out of scope.
- Signatures consistent: `BadgeTemplate.build(roi, cx, cy) -> bool`,
  `.score(roi, cx, cy) -> (float, tuple|None)`, `_score_at(roi, cx, cy)`,
  `refine_seed_centre(roi, cx, cy, window=16)`.
- Live HDR test by the user is the Phase-1 exit criterion (not automatable).
