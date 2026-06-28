import numpy as np
import pre_darken as pd        # FLAT import — conftest adds tools/asset_matte to sys.path


def test_full_recovery_returns_the_scene_behind_the_plate():
    # The plate maps the scene as O = t*scene + C. Full recovery (CSUB=1.0) returns the scene
    # inside the semi-transparent serration; outside the plate the frame is untouched.
    h, w = 64, 64
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0)
    mask = np.zeros((h, w)); mask[20:44, 10:54] = 1.0
    scene = np.array([120.0, 130.0, 140.0])
    O = np.clip(t[..., None] * scene + C[..., None], 0, 255)
    frame = O.astype(np.uint8)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, TFLOOR=0.05, YELLOW_S=60, BRIGHT_V=200)
    assert np.allclose(out[30, 30], scene, atol=4)        # serration recovered to the scene
    assert np.array_equal(out[5, 5], frame[5, 5])         # outside the plate: untouched


def test_yellow_text_is_left_as_ui():
    h, w = 32, 32
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0); mask = np.ones((h, w))
    frame = np.zeros((h, w, 3), np.uint8); frame[:] = (10, 200, 220)   # saturated yellow (BGR)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, YELLOW_S=60, BRIGHT_V=255)  # only the yellow rule active
    assert np.array_equal(out, frame)                     # passed through (birefnet will drop it)


def test_bright_badge_is_left_as_ui():
    h, w = 32, 32
    t = np.full((h, w), 0.5); C = np.full((h, w), 70.0); mask = np.ones((h, w))
    frame = np.full((h, w, 3), 235, np.uint8)             # bright, low-saturation (badge-like)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0, YELLOW_S=255, BRIGHT_V=200)  # only the bright rule active
    assert np.array_equal(out, frame)


def test_opaque_plate_core_not_recovered():
    # Where t < T_OPAQUE (opaque glyph), the serration rule excludes it -> passed through.
    h, w = 32, 32
    t = np.full((h, w), 0.10); C = np.full((h, w), 70.0); mask = np.ones((h, w))   # t below T_OPAQUE
    frame = np.full((h, w, 3), 100, np.uint8)
    out = pd.pre_darken(frame, t, C, mask, CSUB=1.0)
    assert np.array_equal(out, frame)
