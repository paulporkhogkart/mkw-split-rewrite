"""Pure-math tests for the idle-loop probe (no video decode needed).

Validates that the self-similarity period finder recovers a known loop length
from a synthetic periodic feature matrix, picks the fundamental over harmonics,
and stays quiet on non-periodic input.
"""
import numpy as np

from mkw_tracker.tools.loop_probe import (
    autocorr_by_lag, find_period, scale_roi, temporal_residual,
)


def _periodic_features(period: int, cycles: int, dim: int = 64, noise: float = 0.05,
                       seed: int = 0) -> np.ndarray:
    """N×dim matrix that repeats every `period` frames, plus a static pose + noise."""
    rng = np.random.default_rng(seed)
    pattern = rng.standard_normal((period, dim))          # one loop of motion
    pose = rng.standard_normal((1, dim)) * 3.0            # constant pose (no period info)
    n = period * cycles
    F = pose + pattern[np.arange(n) % period]
    F += rng.standard_normal((n, dim)) * noise
    return F.astype(np.float32)


def test_recovers_known_period():
    F = _periodic_features(period=37, cycles=8)
    lags, scores = autocorr_by_lag(F, lo=5, hi=120)
    best, conf, _ = find_period(lags, scores)
    assert best == 37 or abs(best - 37) <= 1     # exact or off-by-one
    assert conf > 0.5


def test_picks_fundamental_not_harmonic():
    # A clean period scores ~equally at P, 2P, 3P; the finder must return P.
    F = _periodic_features(period=20, cycles=10, noise=0.01)
    lags, scores = autocorr_by_lag(F, lo=3, hi=90)
    best, _, _ = find_period(lags, scores)
    assert abs(best - 20) <= 1


def test_residual_removes_static_pose():
    # A huge constant pose must not dominate; residual rows are unit-norm.
    F = _periodic_features(period=15, cycles=6)
    R = temporal_residual(F)
    norms = np.linalg.norm(R, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_non_periodic_has_low_confidence():
    rng = np.random.default_rng(1)
    F = rng.standard_normal((300, 64)).astype(np.float32)   # pure noise, no loop
    lags, scores = autocorr_by_lag(F, lo=5, hi=120)
    _, conf, _ = find_period(lags, scores)
    assert conf < 0.5


def test_scale_roi_4k():
    # 1080p hero ROI scaled to 4K doubles every coordinate.
    assert scale_roi((1075, 30, 1800, 845), 3840, 2160) == (2150, 60, 3600, 1690)
