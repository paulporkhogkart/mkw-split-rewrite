"""Tests for the build-time screen-graph thumbnail generator + its spec wiring.

Regression context: packaged builds showed a blank edit-mode screen graph because
the PyInstaller spec bundled only images/ and never the screenshots/ tree that the
get_screen_thumbs handler reads via resource_path(). The fix downscales screenshots/
into small thumbnails (scripts/gen_screenshot_thumbs.py) and bundles those. These
tests cover the generator and guard the spec so the bundling can't silently regress.
"""
import importlib.util
import os

import cv2
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_gen():
    path = os.path.join(ROOT, "scripts", "gen_screenshot_thumbs.py")
    spec = importlib.util.spec_from_file_location("gen_screenshot_thumbs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_png(path: str, w: int, h: int):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 80, 120)
    cv2.imwrite(path, img)


def test_generate_thumbs_downscales_and_mirrors_tree(tmp_path):
    gen = _load_gen()
    src, out = tmp_path / "screenshots", tmp_path / "out"
    _write_png(str(src / "en_uk" / "title.png"), 1920, 1080)
    _write_png(str(src / "de" / "title.png"), 1920, 1080)

    n = gen.generate_thumbs(str(src), str(out), width=240)

    assert n == 2
    for lang in ("en_uk", "de"):
        p = out / lang / "title.png"
        assert p.exists(), f"missing {p}"
        im = cv2.imread(str(p))
        assert im.shape[1] == 240          # width matches the runtime thumbnail width
        assert im.shape[0] == 135          # aspect preserved: 1080 * 240 / 1920


def test_generate_thumbs_never_upscales(tmp_path):
    gen = _load_gen()
    src, out = tmp_path / "screenshots", tmp_path / "out"
    _write_png(str(src / "en_uk" / "small.png"), 100, 60)

    assert gen.generate_thumbs(str(src), str(out), width=240) == 1
    im = cv2.imread(str(out / "en_uk" / "small.png"))
    assert im.shape[1] == 100              # left at native width, not blown up to 240


def test_generate_thumbs_missing_src_returns_zero(tmp_path):
    gen = _load_gen()
    assert gen.generate_thumbs(str(tmp_path / "nope"), str(tmp_path / "out")) == 0


def test_generate_thumbs_dry_run_writes_nothing(tmp_path):
    gen = _load_gen()
    src, out = tmp_path / "screenshots", tmp_path / "out"
    _write_png(str(src / "en_uk" / "title.png"), 1920, 1080)

    assert gen.generate_thumbs(str(src), str(out), width=240, dry_run=True) == 1
    assert not out.exists()


def test_spec_bundles_generated_thumbnails():
    """The spec must generate thumbnails and bundle them under 'screenshots/'.

    This guards the exact regression that caused the blank packaged graph: dropping
    the bundling, or wiring the generator out, would make this fail.
    """
    spec_text = open(os.path.join(ROOT, "mkw_tracker.spec"), encoding="utf-8").read()
    assert "generate_thumbs" in spec_text
    assert "(_thumb_out, 'screenshots')" in spec_text
