import inspect
import numpy as np
import pre_darken as pd        # FLAT import — conftest adds tools/asset_matte to sys.path


def _plate():
    # A 64x64 frame with a semi-transparent plate rectangle (t=0.5, C=70) inside [20:44, 10:54],
    # over a known clean background A. Returns (t, C, A, mask).
    h, w = 64, 64
    t = np.full((h, w), 0.5)
    C = np.full((h, w), 70.0)
    A = np.zeros((h, w, 3)) + np.array([120.0, 130.0, 140.0])
    mask = np.zeros((h, w)); mask[20:44, 10:54] = 1.0
    return t, C, A, mask


def test_defaults_are_locked_to_user_tuned_values():
    # process() and the tuner call pre_darken on BARE defaults, so guard them. These are the user's
    # browser-tuner-validated values (2026-06-29): KEY_THR=60 subject/bg discriminator, CSUB=0.75
    # partial recovery (the user judged it cleaner than the old full-1.0), TFLOOR=0.01, and YELLOW_S=
    # 250 / BRIGHT_V=255 which deliberately DISABLE the yellow-text and bright-V rules so more of the
    # kart survives — the opaque core (t < T_OPAQUE) still carries the text/badge removal.
    d = inspect.signature(pd.pre_darken).parameters
    assert d["KEY_THR"].default == 60
    assert d["CSUB"].default == 0.75
    assert d["TFLOOR"].default == 0.01
    assert d["YELLOW_S"].default == 250
    assert d["BRIGHT_V"].default == 255
    assert pd.T_OPAQUE == 0.20


def test_empty_serration_becomes_clean_background():
    # Serration over nothing but the background: O = t*A + C. Full recovery returns A, so |S-A|≈0 <
    # KEY_THR -> not subject -> the whole footprint is painted to the clean background A (birefnet
    # then sees continuous background and drops it). Outside the plate is untouched.
    t, C, A, mask = _plate()
    O = np.clip(t[..., None] * A + C[..., None], 0, 255)
    frame = O.astype(np.uint8)
    out = pd.pre_darken(frame, t, C, A, mask)
    assert np.allclose(out[30, 30], A[30, 30], atol=3)     # serration erased to background
    assert np.array_equal(out[5, 5], frame[5, 5])          # outside the plate: untouched


def test_subject_behind_serration_is_recovered_and_kept():
    # A subject sits behind the serration. Its recovered colour differs from the background by far
    # more than KEY_THR, it is desaturated and dark in the raw frame (not text, not badge), so it is
    # stamped back un-darkened. CSUB=1.0 here tests the FULL-recovery inverse-transform math exactly
    # (the default 0.75 only partially un-darkens). Outside the plate stays as the raw frame.
    t, C, A, mask = _plate()
    subject = np.array([60.0, 64.0, 70.0])                 # dark, low-saturation, far from A
    O = np.clip(t[..., None] * subject + C[..., None], 0, 255)
    frame = O.astype(np.uint8)
    out = pd.pre_darken(frame, t, C, A, mask, CSUB=1.0)
    assert np.allclose(out[30, 30], subject, atol=3)       # serration recovered to the subject
    assert np.array_equal(out[5, 5], frame[5, 5])          # outside the plate: untouched


def test_yellow_text_is_painted_to_background():
    # The yellow name text inside the footprint is detected (HSV S > YELLOW_S) and erased to the
    # clean background — NOT passed through (the old behaviour left it as opaque yellow, which
    # birefnet then kept at a collision). Outside the plate is untouched.
    t, C, A, mask = _plate()
    frame = np.zeros((64, 64, 3), np.uint8); frame[:] = (10, 200, 220)   # saturated yellow (BGR)
    out = pd.pre_darken(frame, t, C, A, mask, YELLOW_S=60, BRIGHT_V=255)  # only the yellow rule active
    assert np.allclose(out[30, 30], A[30, 30], atol=3)     # text erased to background
    assert np.array_equal(out[5, 5], frame[5, 5])          # outside the plate: untouched


def test_bright_badge_is_painted_to_background():
    # The bright 1-UP badge inside the footprint is detected (HSV V > BRIGHT_V) and erased to the
    # clean background. Outside the plate is untouched.
    t, C, A, mask = _plate()
    frame = np.full((64, 64, 3), 235, np.uint8)            # bright, low-saturation (badge-like)
    out = pd.pre_darken(frame, t, C, A, mask, YELLOW_S=255, BRIGHT_V=200)  # only the bright rule active
    assert np.allclose(out[30, 30], A[30, 30], atol=3)     # badge erased to background
    assert np.array_equal(out[5, 5], frame[5, 5])          # outside the plate: untouched


def test_opaque_plate_core_is_painted_to_background():
    # Where t < T_OPAQUE (opaque glyph core) the badge rule fires -> erased to background, not kept.
    t, C, A, mask = _plate()
    t[:] = 0.10                                            # t below T_OPAQUE everywhere
    frame = np.full((64, 64, 3), 100, np.uint8)
    out = pd.pre_darken(frame, t, C, A, mask, YELLOW_S=255, BRIGHT_V=255)  # isolate the opaque rule
    assert np.allclose(out[30, 30], A[30, 30], atol=3)     # opaque core erased to background
    assert np.array_equal(out[5, 5], frame[5, 5])          # outside the plate: untouched
