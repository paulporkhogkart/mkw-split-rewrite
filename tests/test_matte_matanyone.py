import numpy as np
import matte_matanyone as mm      # FLAT import — conftest adds tools/asset_matte to sys.path


def _const(v, n):
    return [np.full((2, 2), v, dtype=np.float32) for _ in range(n)]


def test_merge_weights_forward_early_backward_late():
    # fwd all 1.0, bwd all 0.0 -> weight w=1-t/(N-1): first frame=1.0, last frame=0.0, mid=0.5
    fwd, bwd = _const(1.0, 3), _const(0.0, 3)
    out = mm.merge_bidir(fwd, bwd)
    assert np.allclose(out[0], 1.0)
    assert np.allclose(out[1], 0.5)
    assert np.allclose(out[2], 0.0)


def test_merge_single_frame_is_forward_only():
    # N=1 -> w=1.0 (max(1,N-1) guard, no divide-by-zero), backward ignored
    out = mm.merge_bidir(_const(0.7, 1), _const(0.2, 1))
    assert np.allclose(out[0], 0.7)


def test_merge_clips_to_unit_range():
    out = mm.merge_bidir([np.full((2, 2), 2.0, np.float32)], [np.full((2, 2), -1.0, np.float32)])
    assert out[0].max() <= 1.0 and out[0].min() >= 0.0
