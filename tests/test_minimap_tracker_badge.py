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
