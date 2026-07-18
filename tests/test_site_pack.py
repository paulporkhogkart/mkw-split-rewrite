"""Site-pack encode core: frame ops, grids, sheets, manifest. Pure PIL/numpy — no D: access."""
import numpy as np
import pytest
from PIL import Image

from tools.asset_matte import site_pack as sp


def _rgba(w, h, rgba):
    return Image.new("RGBA", (w, h), rgba)


class TestFrameOps:
    def test_encode_size_rounds(self):
        assert sp.encode_size(1024, 1080, 0.2) == (205, 216)
        assert sp.encode_size(1024, 1080, 0.15) == (154, 162)

    def test_subsample_step(self):
        assert sp.subsample_step(60) == 1
        assert sp.subsample_step(30) == 2
        with pytest.raises(ValueError):
            sp.subsample_step(45)  # must divide 60

    def test_premul_resize_keeps_size_and_mode(self):
        out = sp.premul_resize(_rgba(64, 68, (200, 40, 40, 255)), (32, 34))
        assert out.size == (32, 34) and out.mode == "RGBA"

    def test_premul_resize_no_fringe_from_hidden_rgb(self):
        # Bright green hidden under alpha=0 next to an opaque red block must not
        # tint the red edge after downscale (the reason we premultiply).
        im = _rgba(64, 64, (0, 255, 0, 0))
        for x in range(32):
            for y in range(64):
                im.putpixel((x, y), (255, 0, 0, 255))
        out = np.asarray(sp.premul_resize(im, (32, 32)))
        edge = out[16, 15]  # just inside the red half
        assert edge[0] > 150 and edge[1] < 60  # red stays red, no green bleed

    def test_quant_alpha_levels_and_snaps(self):
        grad = Image.new("RGBA", (256, 1))
        grad.putdata([(120, 120, 120, a) for a in range(256)])
        out = np.asarray(sp.quant_alpha(grad, 5))[0, :, 3]
        assert len(np.unique(out)) <= 32 + 2          # ≤2^5 levels (+snapped 0/255)
        assert all(out[a] == 0 for a in range(6))     # <6 -> 0
        assert all(out[a] == 255 for a in range(250, 256))  # >249 -> 255
