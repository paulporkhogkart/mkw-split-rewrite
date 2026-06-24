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
