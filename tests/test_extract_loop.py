"""Segmentation invariants for extract_loop: the spawn->idle handoff must be
frame-contiguous, and the flourish must end before the scene fade-out."""
import numpy as np
import extract_loop as el        # FLAT import — conftest adds tools/asset_matte to sys.path


def _periodic_features(n=500, P=50, dims=8, seed=7):
    """Synthetic feature track: pure idle cycle with a little noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)[:, None]
    phase = 2 * np.pi * t / P + np.linspace(0, np.pi, dims)[None, :]
    return (np.sin(phase) + 0.01 * rng.standard_normal((n, dims))).astype(np.float32)


def test_seam_start_stays_in_first_idle_cycle():
    # The loop must start where the spawn segment can hand off to it: within the
    # FIRST cycle after the idle-band start (any seam phase already exists there).
    F = _periodic_features()
    a, b, P = 40, 460, 50
    s = el.seam_start(F, a, b, P)
    assert a <= s <= a + P


def test_seam_start_degenerate_band_returns_band_start():
    F = _periodic_features(n=100)
    a, b, P = 40, 52, 10          # barely one cycle in the band
    s = el.seam_start(F, a, b, P)
    assert a <= s <= b - P


def test_fade_start_detects_backdrop_change_after_band():
    # Both backdrop corners static through the band, then the scene fades.
    n, a, b, fade = 300, 30, 220, 262
    bg = np.full((n, 2), 100.0)
    ramp = np.arange(n - fade)
    bg[fade:, 0] -= 0.4 * ramp    # gradual fade in both corners
    bg[fade:, 1] -= 0.4 * ramp
    assert el.fade_start(bg, a, b) == fade + 2   # 0.4/frame crosses the 0.5 gate on frame 2


def test_fade_start_ignores_single_corner_occlusion():
    # The flourish jump can put the subject over ONE corner; that is not a fade.
    n, a, b = 300, 30, 220
    bg = np.full((n, 2), 100.0)
    bg[230:250, 0] += 30.0        # subject crosses the left corner only
    assert el.fade_start(bg, a, b) == n


def test_fade_start_no_fade_in_window_returns_length():
    bg = np.full((200, 2), 100.0)
    assert el.fade_start(bg, 30, 150) == 200
