"""Gap-carve post-pass: carve see-through holes the matte wrongly filled (spec
2026-07-02-gap-carve-design). Pure numpy/cv2 — importable without the GPU stack.

Semantic matters (birefnet, SAM2, and MatAnyone2 propagating their anchors) systematically
treat a kart's enclosed see-through gaps (e.g. under the hot_rod rear spoiler) as subject,
freezing a patch of the capture backdrop into the chip. The backdrop is the one signal they
don't have: it is FROZEN and exactly known (`clean_backdrop`). A true gap pixel equals the
backdrop plate (codec noise apart) and — on the idle loop — stays pixel-static while the
whole kart putters (measured 10-15x temporal-std separation). This is the inverse of
`matte_blankplate._repair_holes`: that pass ADDS wrongly-cut subject; this one SUBTRACTS
wrongly-kept background.
"""
import cv2
import numpy as np

CARVE_THR = 12.0        # max per-pixel L2 RGB distance to the backdrop plate (levels)
CARVE_MED_DIFF = 4.0    # component MEDIAN diff gate: a true gap IS the plate, so its median
                        # tracks the clip's codec-noise floor (measured 2.45-3.74 across
                        # hot_rod idle/flourish, floors 2.45-3.0); coincidental dark-part
                        # matches (engine slots, grille shadows) measured >= 4.4
CARVE_STD_THR = 2.0     # idle temporal-static gate: max per-pixel grayscale std
CARVE_COMP_STD = 0.8    # idle component-mean std gate (true gap 0.66; every false idle
                        # component >= 0.90 — deep shadows are ~still but not THIS still)
CARVE_MIN_AREA = 25     # drop carve components smaller than this (speckle)


def temporal_std(raws):
    """Per-pixel grayscale temporal std over a list of BGR uint8 frames (float32 HxW)."""
    s = s2 = None
    for raw in raws:
        g = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY).astype(np.float64)
        if s is None:
            s, s2 = g.copy(), g * g
        else:
            s += g
            s2 += g * g
    n = len(raws)
    var = np.maximum(s2 / n - (s / n) ** 2, 0.0)
    return np.sqrt(var).astype(np.float32)


def _backdrop_diff(raw, backdrop):
    d = raw.astype(np.float32) - backdrop
    return np.sqrt((d * d).sum(axis=2))


def carve_gaps(alphas, raws, backdrop, temporal_gate,
               thr=CARVE_THR, med_diff=CARVE_MED_DIFF, std_thr=CARVE_STD_THR,
               comp_std=CARVE_COMP_STD, min_area=CARVE_MIN_AREA):
    """Carve backdrop-showing gaps out of a segment's alpha stack.

    alphas: list of float01 HxW alpha maps (modified copies are returned).
    raws:   list of RAW (pre-predark) BGR uint8 frames, same length/size — predark repaints
            the nameplate band, so the backdrop comparison must use the untouched pixels.
    backdrop: the per-clip `clean_backdrop` plate (HxWx3 float/uint8) or None (no-op).
    temporal_gate: True for the idle loop — additionally require the pixel to be temporally
            STATIC across the stack (a real subject pixel wobbles with the kart; frozen
            backdrop doesn't) and the component to be static on average (CARVE_COMP_STD).
            Spawn/flourish move through the gap region, so they rely on the per-frame colour
            match + the component gates alone.

    A candidate component is carved only if its MEDIAN diff is at the clip's noise floor
    (CARVE_MED_DIFF): a true gap shows the very pixels of the plate, while a dark kart part
    that merely lands within CARVE_THR of a dark backdrop sits measurably higher.

    Returns (new_alphas, carved_px_per_frame).
    """
    if backdrop is None or not alphas:
        return list(alphas), [0] * len(alphas)
    B = np.asarray(backdrop, dtype=np.float32)
    std = temporal_std(raws) if temporal_gate else None
    out, carved = [], []
    for alpha, raw in zip(alphas, raws):
        diff = _backdrop_diff(raw, B)
        cand = (diff <= thr) & (alpha > 1.0 / 255.0)
        if std is not None:
            cand &= std < std_thr
        n, lab, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), 8)
        keep = np.zeros(alpha.shape, bool)
        for c in range(1, n):
            if st[c, cv2.CC_STAT_AREA] < min_area:
                continue
            m = lab == c
            if float(np.median(diff[m])) > med_diff:
                continue
            if std is not None and float(std[m].mean()) >= comp_std:
                continue
            keep |= m
        if keep.any():
            soft = cv2.GaussianBlur(keep.astype(np.float32), (3, 3), 0)
            a = np.minimum(alpha, 1.0 - soft)
        else:
            a = alpha.copy()
        out.append(a)
        carved.append(int(keep.sum()))
    return out, carved
