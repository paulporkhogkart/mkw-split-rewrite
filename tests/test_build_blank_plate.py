# tests/test_build_blank_plate.py
"""Char-mode builder pure helpers (kart default path untouched)."""
import json
import numpy as np
import cv2
import build_blank_plate as bbp


def test_standalone_names_filters_two_part(tmp_path):
    for n in ("mario__base.mkv", "mario__base__b_dasher.mkv", "peepa__base.mkv", "junk.txt"):
        (tmp_path / n).write_bytes(b"x")
    assert bbp.standalone_names(str(tmp_path)) == ["mario__base", "peepa__base"]


def test_nan_body_masks_dilated_alpha(tmp_path):
    f = np.zeros((20, 20, 3), np.float32)
    rgba = np.zeros((20, 20, 4), np.uint8); rgba[10, 10, 3] = 255
    p = tmp_path / "000.png"; cv2.imwrite(str(p), rgba)
    bbp.nan_body(f, str(p), dilate=5)
    assert np.isnan(f[10, 10, 0]) and np.isnan(f[12, 12, 0])   # dilated
    assert not np.isnan(f[0, 0, 0])


def test_nan_body_missing_alpha_is_noop(tmp_path):
    f = np.zeros((8, 8, 3), np.float32)
    bbp.nan_body(f, str(tmp_path / "nope.png"))
    assert not np.isnan(f).any()


def test_finish_median_ignores_nan_and_fills_all_nan(tmp_path):
    in_plate = np.zeros((4, 4), bool); in_plate[1:3, 1:3] = True
    a = np.full((4, 4, 3), 10, np.float16)
    b = np.full((4, 4, 3), 20, np.float16); b[1, 1] = np.nan
    c = np.full((4, 4, 3), 30, np.float16)
    c[2, 2] = np.nan; a[2, 2] = np.nan; b[2, 2] = np.nan       # all-NaN pixel
    out = bbp.finish_median([a, b, c], in_plate)
    assert out.dtype == np.float32
    assert abs(float(out[1, 1, 0]) - 20.0) < 0.1               # median of {10,30}
    assert not np.isnan(out).any()                             # all-NaN filled


def test_char_cut_for_uses_cache_without_decoding():
    cache = {"mario__base": 681}
    assert bbp.char_cut_for("D:/nowhere/mario__base.mkv", cache) == 681


def test_finish_median_empty_stack_raises_loudly():
    import pytest
    in_plate = np.zeros((4, 4), bool)
    with pytest.raises(RuntimeError, match="check --clips"):
        bbp.finish_median([], in_plate)
