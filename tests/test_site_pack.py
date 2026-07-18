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


class TestSheet:
    def test_grid_near_square(self):
        assert sp.grid_for(60, 205, 216) == (8, 8)
        assert sp.grid_for(13, 205, 216) == (4, 4)
        assert sp.grid_for(120, 205, 216) == (11, 11)

    def test_grid_respects_max_side(self):
        cols, rows = sp.grid_for(120, 500, 216, max_side=4096)
        assert cols * 500 <= 4096 and rows * 216 <= 4096 and cols * rows >= 120

    def test_grid_impossible_raises(self):
        with pytest.raises(ValueError):
            sp.grid_for(4000, 500, 500, max_side=4096)

    def test_grid_finds_wide_packing_for_tall_frames(self):
        cols, rows = sp.grid_for(100, 10, 1000, max_side=4096)
        assert cols * 10 <= 4096 and rows * 1000 <= 4096 and cols * rows >= 100

    def test_build_sheet_places_frames_row_major(self):
        frames = [_rgba(10, 12, (i * 20, 0, 0, 255)) for i in range(5)]
        sheet = sp.build_sheet(frames, 10, 12)
        cols, rows = sp.grid_for(5, 10, 12)
        assert sheet.size == (cols * 10, rows * 12)
        px = np.asarray(sheet)
        assert tuple(px[0, 0][:3]) == (0, 0, 0)          # frame 0 at (0,0)
        assert px[0, 2 * 10][0] == 40                    # frame 2 in row 0 (col=20)
        assert px[12, 0][0] == 60                        # frame 3 wraps to row 1
        assert px[12, 2 * 10][3] == 0                    # unused cell transparent

    def test_encode_anim_writes_webp(self, tmp_path):
        frames = [_rgba(10, 12, (200, 0, 0, 255)) for _ in range(4)]
        n = sp.encode_anim(frames, str(tmp_path / "x.webp"), quality=60)
        assert n > 0
        with Image.open(tmp_path / "x.webp") as im:
            assert im.format == "WEBP" and getattr(im, "n_frames", 1) == 1

    def test_manifest_anim_entry(self):
        assert sp.manifest_anim_entry(60, 205, 216) == {"frames": 60, "cols": 8, "rows": 8}

    def test_encode_idle_resume(self):
        assert sp.encode_idle_resume(103, step=2, n_encoded=60) == 51
        assert sp.encode_idle_resume(103, step=1, n_encoded=120) == 103
        assert sp.encode_idle_resume(119, step=2, n_encoded=60) == 59  # clamped in range
