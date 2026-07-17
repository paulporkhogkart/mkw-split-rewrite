"""Char blank-plate predark unit tests (spec 2026-07-17-char-nameplate-blankplate-design)."""
import numpy as np
import cv2
import nametag_core as nc          # FLAT imports — conftest adds tools/asset_matte to sys.path
import pre_darken as pd
import extract_loop as el


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


def test_char_plate_depart_is_recorder_anchored():
    # 11/11 measurable chars on the 2026-07-17 survey: slide onset exactly cut-9.
    assert el.CHAR_PLATE_DEPART == 9
    # raw tail must stay derivable and positive: export ends at cut - CHAR_CUT_GUARD
    assert el.CHAR_PLATE_DEPART - el.CHAR_CUT_GUARD == 7


def test_predark_frame_count_partition():
    assert pd.predark_frame_count(78, 7) == 71
    assert pd.predark_frame_count(78, 0) == 78
    assert pd.predark_frame_count(5, 7) == 0     # tiny segment: all raw
    assert pd.predark_frame_count(78, -3) == 78  # negative clamps to no tail


def _char_assets():
    """Synthetic self-consistent world: tinted bg, plate = t*bg (t=0.5, C=0 on the solve's
    covariance path because bg is tinted), footprint [20:50, 10:70]."""
    h, w = 60, 80
    bg = np.zeros((h, w, 3)) + np.array([120.0, 130.0, 140.0])
    in_plate = np.zeros((h, w), bool); in_plate[20:50, 10:70] = True
    blank = bg.copy(); blank[in_plate] = 0.5 * bg[in_plate]
    T_B, C_B = nc.solve_tc(blank.astype(np.float32), bg)
    badge = (T_B < pd.T_OPAQUE) & in_plate
    band = np.zeros((h, w), bool); band[30:40, 10:70] = True
    return {"T_B": T_B, "C_B": C_B, "badge": badge, "bg": bg,
            "in_plate": in_plate, "text_band": band}


def test_char_predark_empty_plate_recovers_to_bg():
    a = _char_assets()
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    out = pd.char_predark(frame.astype(np.uint8), np.zeros_like(a["in_plate"]), a)
    assert np.allclose(out[35, 40], a["bg"][35, 40], atol=4)   # recovered ~ bg
    assert np.array_equal(out[5, 5], frame.astype(np.uint8)[5, 5])  # outside untouched


def test_char_predark_subject_behind_plate_kept_full_s():
    a = _char_assets()
    subject = np.array([60.0, 200.0, 40.0])                    # far from bg
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    frame[30:40, 30:50] = 0.5 * subject                        # subject behind serration
    out = pd.char_predark(frame.astype(np.uint8), np.zeros_like(a["in_plate"]), a)
    assert np.allclose(out[35, 40], subject, atol=6)           # full-S stamp keeps it
    # no razor boundary: neighbouring empty plate still ~bg, not painted flat around subject
    assert np.allclose(out[35, 60], a["bg"][35, 60], atol=4)


def test_char_predark_text_painted_out_and_gone():
    a = _char_assets()
    frame = a["bg"].copy(); frame[a["in_plate"]] = 0.5 * a["bg"][a["in_plate"]]
    frame[32:36, 30:44] = (30.0, 190.0, 250.0)                 # opaque yellow text
    text = np.zeros_like(a["in_plate"]); text[30:38, 28:46] = True
    out = pd.char_predark(frame.astype(np.uint8), text, a)
    assert not nc.yellow_text_mask(out)[32:36, 30:44].any()    # yellow gone
    # text region ends near bg level (painted to bg, possibly TELEA-smoothed)
    assert abs(float(out[34, 36].mean()) - float(a["bg"][34, 36].mean())) < 25
