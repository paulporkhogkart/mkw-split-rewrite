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
