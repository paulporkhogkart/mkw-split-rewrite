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


def _geometry():
    h, w = 60, 80
    mask = np.zeros((h, w)); mask[20:50, 10:70] = 1.0          # footprint
    t = np.ones((h, w));     t[30:36, 30:50] = 0.1             # template glyph rows 30..35
    return t, mask


def test_char_text_band_rows_padded_full_span():
    t, mask = _geometry()
    band = pd.char_text_band(t, mask)
    assert band[30, 15] and band[35, 65]        # glyph rows, full footprint x-span
    assert band[23, 40] and band[43, 40]        # +-8 rows (22..43), inside footprint
    assert not band[21, 40]                     # 30-8=22 is the first band row
    assert not band[30, 5]                      # outside footprint x
    assert not band[10, 40]                     # far row


def test_char_text_mask_is_yellow_in_band_dilated():
    t, mask = _geometry()
    band = pd.char_text_band(t, mask)
    med = np.full((60, 80, 3), 40, np.float32)
    med[31:34, 35:45] = (30, 190, 250)          # live yellow text inside the band
    med[25, 12] = (30, 190, 250)                # yellow inside band -> caught too
    med[10, 40] = (30, 190, 250)                # yellow OUTSIDE band -> excluded
    m = pd.char_text_mask(med, band)
    assert m[32, 40]
    assert m[32, 47]                            # dilation (7//2=3 px) covers the AA ring
    assert not m[10, 40]
    assert pd.CHAR_TEXT_DILATE == 7
