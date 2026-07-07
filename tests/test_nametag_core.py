import numpy as np
import nametag_core as nc        # FLAT import — conftest adds tools/asset_matte to sys.path

def test_constants():
    # Crop widened 2026-07-07 (user-picked on peach-aviator/yoshi-dread_sled stills):
    # full frame top + right edge so tall flourish peaks (bowser's fist-pump) and wide
    # poses (DK's gauntlets, big_horn's horns) stop clipping. Chip output 1024x1080.
    assert nc.PROD_CROP_4K == (2128, 0, 3840, 1806)
    assert (nc.OUT_W, nc.OUT_H) == (1024, 1080)
    assert nc.CHAR_ROI == (2378, 1604, 1178, 226)
    assert nc.PLATE_ROI == (2360, 1602, 1378, 226)
    assert nc.FULL_4K == (3840, 2160)
    assert nc.NAMEPLATE_HERO_ROI == (1064, 0, 1920, 903)


def test_extract_loop_crop_matches_prod_crop():
    # The undark mask + t,C are placed via PROD_CROP_4K; extract_loop crops
    # scale_roi(NAMEPLATE_HERO_ROI) instead. They MUST resolve to the same 4K region or the
    # mask misaligns with the matte frames pixel-for-pixel. Guard the invariant the two paths
    # share so a future NAMEPLATE_HERO_ROI edit can't silently desync them.
    from mkw_tracker.tools.loop_probe import scale_roi
    assert scale_roi(nc.NAMEPLATE_HERO_ROI, 3840, 2160) == nc.PROD_CROP_4K

def test_classify_presence_splits_dark_and_light():
    luma = np.array([40, 42, 41, 200, 198, 201, 39, 43])  # dark=present, light=absent
    pres = nc.classify_presence(luma, smooth=1)
    assert pres[:3].all() and pres[6:].all() and not pres[3:6].any()

def test_solve_tc_recovers_planted_transform():
    # A = known background, P = t*A + C with planted t=0.5, C=30 -> solve recovers them.
    rng = np.random.default_rng(0)
    A = rng.uniform(20, 220, (8, 8, 3))
    t_true, C_true = 0.5, 30.0
    P = t_true * A + C_true
    t, C = nc.solve_tc(P, A)
    assert np.allclose(t, t_true, atol=0.05) and np.allclose(C, C_true, atol=3.0)

def test_prod_crop_shape_and_alignment():
    canvas = np.zeros((nc.FULL_4K[1], nc.FULL_4K[0]), np.float64)
    x, y, w, h = nc.CHAR_ROI
    canvas[y:y+h, x:x+w] = 1.0                       # mark the char plate footprint
    out = nc.prod_crop(canvas)
    assert out.shape == (nc.OUT_H, nc.OUT_W)
    assert out.max() > 0.5                            # the footprint survives the crop
    assert out[: nc.OUT_H // 3].max() < 0.5           # and sits in the lower part of the crop

def test_diff_to_alpha_planted_footprint():
    A = np.full((20, 40, 3), 100.0)
    P = A.copy(); P[5:15, 10:30] += 60               # a darker/brighter plate patch
    a = nc.diff_to_alpha(P, A, floor=0.05)
    assert a[10, 20] > 0.8 and a[1, 1] == 0.0
