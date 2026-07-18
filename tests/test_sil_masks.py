"""Sil tearout masks: deterministic seeded jagged cut around the frame silhouette."""
import numpy as np
from PIL import Image

from tools.asset_matte import sil_masks as sm


def _blob(w=100, h=108, cx=50, cy=60, r=25):
    """Opaque disc on transparent — a stand-in character silhouette."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y, x = np.ogrid[:h, :w]
    mask = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    a = np.zeros((h, w, 4), np.uint8)
    a[mask] = (200, 50, 50, 255)
    a[..., :3][mask] = 200
    out = Image.fromarray(a, "RGBA")
    return out


def test_keyframe_indices_spread():
    assert sm.keyframe_indices(60) == [0, 20, 39, 59]
    assert sm.keyframe_indices(4) == [0, 1, 2, 3]


def test_keyframe_indices_always_four_even_for_tiny_anims(tmp_path):
    assert sm.keyframe_indices(2) == [0, 0, 1, 1]
    paths = sm.write_sil_masks([_blob(), _blob(cx=52)], "t__base", "spawn", str(tmp_path))
    assert len(paths) == 4  # sil_k0..k3 all written


def test_mask_same_size_and_covers_silhouette():
    f = _blob()
    m = np.asarray(sm.sil_mask(f, "combo__idle"))
    assert m.shape[:2] == (108, 100)
    fa = np.asarray(f)[..., 3] > 0
    covered = (m[..., 3] > 0) | ~fa
    assert covered.mean() > 0.995  # cut (incl. margin) contains ~the whole silhouette


def test_mask_is_jagged_not_full_frame():
    m = np.asarray(sm.sil_mask(_blob(), "combo__idle"))[..., 3] > 0
    assert 0.15 < m.mean() < 0.95  # a cut, not everything / nothing


def test_same_seed_same_jags_different_pose_differs():
    a = np.asarray(sm.sil_mask(_blob(cx=50), "combo__idle"))
    b = np.asarray(sm.sil_mask(_blob(cx=50), "combo__idle"))
    c = np.asarray(sm.sil_mask(_blob(cx=58), "combo__idle"))
    d = np.asarray(sm.sil_mask(_blob(cx=50), "other__idle"))
    assert (a == b).all()            # deterministic
    assert not (a == c).all()        # pose moves the cut
    assert not (a == d).all()        # different seed key -> different jags


def test_write_sil_masks_names(tmp_path):
    frames = [_blob(cx=48 + i) for i in range(8)]
    paths = sm.write_sil_masks(frames, "a__base", "idle", str(tmp_path))
    assert [p.split("\\")[-1].split("/")[-1] for p in paths] == [
        f"a__base__idle__sil_k{i}.png" for i in range(4)
    ]
    with Image.open(paths[0]) as im:
        assert im.size == frames[0].size
