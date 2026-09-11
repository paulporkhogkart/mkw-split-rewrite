"""SeedSession: the pure core of tools/minimap_calib (the ported seed-clicker).

Persistence + ring refinement are injected so no camera, DB or cv2 window is needed.
"""
import numpy as np
import pytest

from mkw_tracker.tools.minimap_calib import SeedSession, DEFAULT_RADIUS


class _Store:
    def __init__(self):
        self.seeds, self.rois = {}, {}

    def get_seed(self, c):
        return dict(self.seeds[c]) if c in self.seeds else None

    def set_seed(self, c, cx, cy, radius=0, conf=None):
        self.seeds[c] = {"cx": cx, "cy": cy, "radius": radius, "conf": conf}

    def get_roi(self, c):
        return dict(self.rois[c]) if c in self.rois else None

    def set_roi(self, c, x, y, w, h):
        self.rois[c] = {"x": x, "y": y, "w": w, "h": h}


def _session(store, refine=None):
    return SeedSession(store.get_seed, store.set_seed, store.get_roi, store.set_roi, refine=refine)


def test_click_seed_snaps_via_refine_and_persists_with_default_radius():
    st = _Store()
    s = _session(st, refine=lambda frame, x, y: (x + 3, y - 2))
    frame = np.zeros((1080, 1920, 3), np.uint8)
    assert s.click_seed("Snes Mario Circuit 1", 1700, 800, frame) == (1703, 798)
    assert st.seeds["Snes Mario Circuit 1"] == {"cx": 1703, "cy": 798, "radius": DEFAULT_RADIUS, "conf": None}
    assert s.touched == ["Snes Mario Circuit 1"] and s.last_saved == "Snes Mario Circuit 1"


def test_click_seed_without_frame_or_refine_stores_the_raw_click():
    st = _Store()
    s = _session(st, refine=lambda frame, x, y: (0, 0))
    assert s.click_seed("A", 10, 20) == (10, 20)            # no frame -> no snap
    assert _session(st).click_seed("B", 11, 21, np.zeros((4, 4, 3), np.uint8)) == (11, 21)


def test_drag_roi_normalises_corners_and_rejects_tiny_boxes():
    st = _Store()
    s = _session(st)
    assert s.drag_roi("A", 1900, 900, 1500, 500) == {"x": 1500, "y": 500, "w": 400, "h": 400}
    assert st.rois["A"] == {"x": 1500, "y": 500, "w": 400, "h": 400}
    assert s.drag_roi("A", 10, 10, 15, 300) is None       # narrower than MIN_ROI_SIDE
    assert st.rois["A"]["w"] == 400                        # untouched by the rejected drag
    assert s.touched == ["A"]                              # touched once, not twice


def test_migration_rows_match_the_shipped_seed_v2_shape():
    st = _Store()
    s = _session(st)
    s.click_seed("Snes Mario Circuit 1", 1700, 800)
    s.drag_roi("Snes Mario Circuit 1", 1500, 500, 1900, 1000)
    s.click_seed("Toad's Factory", 1, 2)                   # apostrophe must be SQL-escaped
    sql = s.migration_rows()
    assert sql == (
        "INSERT OR IGNORE INTO minimap_seeds (course, cx, cy, radius) VALUES\n"
        "    ('Snes Mario Circuit 1',  1700,  800,  20),\n"
        "    ('Toad''s Factory',          1,    2,  20);\n"
        "\n"
        "INSERT OR IGNORE INTO minimap_rois (course, x, y, w, h) VALUES\n"
        "    ('Snes Mario Circuit 1',  1500,  500,  400,  500);"
    )
    assert s.migration_rows([]) == ""


def test_migration_rows_can_export_any_course_list():
    st = _Store()
    st.set_seed("Rainbow Road", 1759, 507, 20)
    s = _session(st)
    assert "('Rainbow Road',          1759,  507,  20)" in s.migration_rows(["Rainbow Road"])
    assert s.touched == []
