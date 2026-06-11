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


def test_refine_seed_centre_recovers_offset():
    """An 8px-off stored seed snaps onto the drawn ring centre."""
    roi = draw_badge(make_roi(), 170, 190)
    cx, cy = refine_seed_centre(roi, 178, 187)
    assert abs(cx - 170) <= 1 and abs(cy - 190) <= 1


def test_refine_seed_centre_no_ring_is_identity():
    roi = make_roi(seed=9)
    assert refine_seed_centre(roi, 100, 120) == (100, 120)


def test_refine_seed_centre_clamps_to_window():
    """A ring far outside the window cannot steal the seed."""
    roi = draw_badge(make_roi(), 170, 190)
    cx, cy = refine_seed_centre(roi, 250, 190, window=16)
    assert abs(cx - 250) <= 16 and abs(cy - 190) <= 16
