"""Grayscale common-support digit matching."""
import cv2
import numpy as np
import pytest

from mkw_tracker.race.laps import load_digit_templates, read_digit_roi

DIGIT_DIR = "images/digits"


@pytest.fixture(scope="module")
def templates():
    t = load_digit_templates(DIGIT_DIR, 42)
    assert len(t) == 10
    return t


def slot_with(tmpl, slot_w=42, slot_h=52, bg=60, jitter=(0, 0)):
    """Place a template into a synthetic slot crop (BGR) at centre + jitter."""
    canvas = np.full((slot_h, slot_w), bg, dtype=np.uint8)
    th, tw = tmpl.shape[:2]
    oy = (slot_h - th) // 2 + jitter[1]
    ox = (slot_w - tw) // 2 + jitter[0]
    canvas[oy:oy + th, ox:ox + tw] = tmpl
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


def test_templates_share_one_canvas(templates):
    shapes = {t.shape for t in templates.values()}
    assert len(shapes) == 1


def test_each_digit_self_matches(templates):
    for name, tmpl in templates.items():
        frame = slot_with(tmpl)
        d, s = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                              templates, threshold=0.6)
        assert d == int(name), f"digit {name} misread as {d}"
        assert s > 0.9


def test_jitter_tolerated(templates):
    tmpl = templates["8"]
    for j in ((2, 1), (-2, -1), (1, 2)):
        frame = slot_with(tmpl, jitter=j)
        d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                              templates, threshold=0.6)
        assert d == 8


def test_crushed_eight_is_eight_or_none_never_one(templates):
    """Washout simulation: crush mid-tones (what binarize-at-170 amplified)."""
    tmpl = templates["8"].astype(np.float32)
    crushed = np.clip((tmpl - 90) * 1.6 + 150, 0, 255).astype(np.uint8)
    frame = slot_with(crushed, bg=170)
    d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6)
    assert d in (8, None)
    assert d != 1


def test_ambiguous_blend_returns_none(templates):
    blend = cv2.addWeighted(templates["3"], 0.5, templates["9"], 0.5, 0)
    frame = slot_with(blend)
    d, _ = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6)
    assert d is None


def test_reconfirm_fast_path(templates):
    tmpl = templates["5"]
    frame = slot_with(tmpl)
    d, s = read_digit_roi(frame, (0, 0, frame.shape[1], frame.shape[0]),
                          templates, threshold=0.6,
                          reconfirm_digit=5, reconfirm_threshold=0.85)
    assert d == 5 and s >= 0.85
