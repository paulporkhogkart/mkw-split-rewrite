import numpy as np
import undark as ud        # FLAT import — conftest adds tools/asset_matte to sys.path

def _rgba(h=1080, w=988):
    a = np.zeros((h, w, 4), np.uint8); a[..., 3] = 0
    return a

def test_drop_nameplate_drops_blob_inside_footprint_keeps_big_subject():
    mask = np.zeros((1080, 988), np.float64)
    mask[900:1000, 300:700] = 1.0                       # nameplate footprint (lower strip)
    rgba = _rgba()
    rgba[920:980, 350:650] = (200, 200, 200, 255)       # detached blob INSIDE the footprint
    rgba[100:800, 400:600] = (180, 120, 90, 255)        # big subject spanning far outside
    out, n = ud.drop_nameplate(rgba, mask)
    assert n == 1                                        # blob dropped
    assert out[940, 500, 3] == 0                         # blob alpha cleared
    assert out[400, 500, 3] == 255                       # subject untouched

def test_undark_rgba_lightens_darkened_strip():
    # t<1 (plate darkens): a subject pixel under the plate should get lighter after undark.
    t = np.full((1080, 988), 0.5); C = np.zeros((1080, 988))
    mask = np.zeros((1080, 988)); mask[950:1000, 400:600] = 1.0
    rgba = _rgba(); rgba[960:990, 450:550] = (80, 80, 80, 255)   # darkened subject in the strip
    out = ud.undark_rgba(rgba, t, C, mask)
    assert out[975, 500, :3].mean() > 80                 # recovered brighter
    assert out[975, 500, 3] == 255                       # still opaque (t=0.5 > T_OPAQUE)

def test_undark_rgba_cuts_opaque_text():
    t = np.full((1080, 988), 0.5); C = np.zeros((1080, 988))
    t[950:1000, 400:600] = 0.1                           # opaque badge/text region (t<0.20)
    mask = np.zeros((1080, 988)); mask[950:1000, 400:600] = 1.0
    rgba = _rgba(); rgba[950:1000, 400:600] = (50, 50, 50, 255)
    out = ud.undark_rgba(rgba, t, C, mask)
    assert out[975, 500, 3] == 0                          # opaque plate content cut

def test_locked_params():
    assert (ud.ALPHA_GAIN, ud.STRENGTH, ud.CSUB, ud.TFLOOR) == (5.0, 1.02, 0.69, 0.05)
    assert (ud.T_OPAQUE, ud.PRESENT_LUMA, ud.NAMEPLATE_OUT_FRAC) == (0.20, 125.0, 0.30)
