"""Char blank-plate predark unit tests (spec 2026-07-17-char-nameplate-blankplate-design)."""
import numpy as np
import cv2
import nametag_core as nc          # FLAT imports — conftest adds tools/asset_matte to sys.path
import pre_darken as pd


def test_yellow_text_mask_catches_text_not_plate():
    img = np.full((20, 20, 3), 40, np.uint8)           # dark neutral plate
    img[5:10, 5:15] = (30, 190, 250)                   # saturated yellow text (BGR)
    m = nc.yellow_text_mask(img)
    assert m[7, 10] and not m[2, 2]
    # dark yellow-hue but low V (drop shadow) is NOT caught — dilation downstream covers it
    img2 = np.full((4, 4, 3), 0, np.uint8); img2[:] = (10, 60, 80)
    assert not nc.yellow_text_mask(img2).any()
