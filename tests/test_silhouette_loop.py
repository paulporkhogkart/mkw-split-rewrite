"""Synthetic-signal tests for the silhouette loop-period detector.

The decode path needs real 4K clips, so these test the math directly: build masks
with a PLANTED bob period (the body's vertical centroid oscillates) plus a CONSTANT
filled blob standing in for a spinning wheel (a spinning wheel's silhouette is a
constant filled disk). The detector must recover the planted period and the constant
blob must NOT create a confident false period.
"""
import cv2
import numpy as np

from mkw_tracker.tools.silhouette_loop import silhouette, signal_from_masks, autocorr
from mkw_tracker.tools.loop_probe import find_period


def _bobbing_masks(n=300, size=64, period=40, amp=6.0, with_wheel=True):
    """Body block whose vertical centre oscillates at `period`; optional constant bottom blob."""
    masks = np.zeros((n, size, size), np.uint8)
    for t in range(n):
        center = int(round(size * 0.4 + amp * np.sin(2 * np.pi * t / period)))
        masks[t, max(0, center - 8):center + 8, 24:40] = 1          # body, bobs vertically
        if with_wheel:
            masks[t, size - 14:size - 2, 26:38] = 1                 # constant 'wheel' (filled, static)
    return masks


def test_centroid_recovers_planted_period_despite_constant_wheel():
    lags, scores = autocorr(signal_from_masks(_bobbing_masks(period=40), "centroid"), 10, 120)
    best, conf, _ = find_period(lags, scores)
    assert best is not None and abs(best - 40) <= 2          # recovered the bob period
    assert conf > 0.5                                        # confidently (wheel offset removed)


def test_constant_silhouette_has_no_confident_period():
    # A spinning wheel == a constant filled disk in silhouette space -> centroid is flat.
    masks = np.zeros((300, 64, 64), np.uint8)
    masks[:, 48:60, 26:38] = 1
    lags, scores = autocorr(signal_from_masks(masks, "centroid"), 10, 120)
    _, conf, _ = find_period(lags, scores)
    assert conf < 0.2                                        # no real motion -> no confident period


def test_rowprofile_and_mask_signals_also_recover_period():
    masks = _bobbing_masks(period=50)
    for sig in ("rowprofile", "mask"):
        lags, scores = autocorr(signal_from_masks(masks, sig), 10, 120)
        best, conf, _ = find_period(lags, scores)
        assert best is not None and abs(best - 50) <= 2, sig
        assert conf > 0.5, sig


def test_fundamental_not_harmonic():
    # autocorr peaks at P, 2P, 3P; find_period must return the fundamental, not a multiple.
    lags, scores = autocorr(signal_from_masks(_bobbing_masks(period=30), "centroid"), 8, 130)
    best, _, _ = find_period(lags, scores)
    assert abs(best - 30) <= 2                               # 30, not 60/90


def test_silhouette_segments_saturated_blob_on_smooth_background():
    crop = np.full((200, 200, 3), (200, 180, 170), np.uint8)        # smooth bluish-grey bg
    cv2.rectangle(crop, (70, 60), (130, 150), (0, 0, 230), -1)      # saturated red blob, central
    m = silhouette(crop, 64)
    assert m.sum() > 0
    ys, xs = np.where(m)
    assert 0.2 < ys.mean() / 64 < 0.9 and 0.2 < xs.mean() / 64 < 0.8   # mask sits on the blob
    assert m.mean() < 0.6                                              # not the whole frame
