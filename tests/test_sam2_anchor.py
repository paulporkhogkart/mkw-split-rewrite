import numpy as np
import sam2_anchor as sa      # FLAT import — conftest adds tools/asset_matte to sys.path


def _box_mask(h=20, w=30, x1=5, y1=4, x2=15, y2=12):
    m = np.zeros((h, w), np.uint8)
    m[y1:y2, x1:x2] = 255
    return m


def test_mask_bbox_tight_and_clamped():
    m = _box_mask()
    b = sa.mask_bbox(m, pad_frac=0.0)
    assert list(b) == [5, 4, 14, 11]          # inclusive max index, no pad
    # heavy pad clamps inside the frame
    b2 = sa.mask_bbox(m, pad_frac=5.0)
    assert b2[0] >= 0 and b2[1] >= 0 and b2[2] <= 29 and b2[3] <= 19


def test_mask_bbox_empty_raises():
    try:
        sa.mask_bbox(np.zeros((5, 5), np.uint8))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_positive_points_inside_mask():
    m = _box_mask()
    pts = sa.positive_points(m, n=3, seed=1)
    assert 1 <= len(pts) <= 3
    for x, y in pts:
        assert m[int(y), int(x)] == 255


def test_corner_points_are_four_corners():
    c = sa.corner_points(20, 30, inset=2)
    assert set(map(tuple, c.astype(int))) == {(2, 2), (27, 2), (2, 17), (27, 17)}


def test_iou_known_values():
    a = np.zeros((4, 4), np.uint8); a[:2, :2] = 1
    b = np.zeros((4, 4), np.uint8); b[:2, :2] = 1
    assert sa.iou(a, b) == 1.0
    b2 = np.zeros((4, 4), np.uint8); b2[2:, 2:] = 1
    assert sa.iou(a, b2) == 0.0
    assert sa.iou(np.zeros((4, 4), np.uint8), np.zeros((4, 4), np.uint8)) == 0.0


def test_select_best_picks_highest_overlap():
    ref = _box_mask()
    good = _box_mask()                          # identical -> IoU 1
    bad = np.zeros_like(ref); bad[0:2, 0:2] = 255
    assert sa.select_best([bad, good], ref) == 1


def test_build_prompt_labels_positive_then_negative():
    p = sa.build_prompt(_box_mask(), n_pos=3, seed=0)
    labels = p["point_labels"]
    assert labels.tolist() == [1] * (len(labels) - 4) + [0, 0, 0, 0]
    assert p["box"].shape == (4,)
    assert p["point_coords"].shape[0] == len(labels)
