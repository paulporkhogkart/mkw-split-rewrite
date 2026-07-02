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
CARVE_RUN = 4           # spawn/flourish: a pixel must match the backdrop for >= this many
                        # CONSECUTIVE frames to be carvable — single-frame threshold noise
                        # (the source of carve flicker) never survives a run requirement


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


def _stable_runs(matched, run):
    """Keep only True values inside runs of >= `run` consecutive frames (per pixel).
    matched: T x H x W bool. Returns the same shape with short runs zeroed."""
    T = matched.shape[0]
    if T < run:
        return np.zeros_like(matched)
    m = matched.astype(np.uint8)
    # window t..t+run-1 all matched  ->  every frame of that window is carvable
    csum = np.cumsum(np.concatenate([np.zeros((1,) + m.shape[1:], np.int16), m]), axis=0)
    full = (csum[run:] - csum[:-run]) == run          # T-run+1 window starts
    keep = np.zeros_like(matched)
    for s in range(full.shape[0]):
        w = full[s]
        keep[s:s + run] |= w
    return keep


def _apply(alpha, keep):
    if keep.any():
        soft = cv2.GaussianBlur(keep.astype(np.float32), (3, 3), 0)
        return np.minimum(alpha, 1.0 - soft)
    return alpha.copy()


def carve_gaps(alphas, raws, backdrop, temporal_gate,
               thr=CARVE_THR, med_diff=CARVE_MED_DIFF, std_thr=CARVE_STD_THR,
               comp_std=CARVE_COMP_STD, min_area=CARVE_MIN_AREA, run=CARVE_RUN):
    """Carve backdrop-showing gaps out of a segment's alpha stack.

    alphas: list of float01 HxW alpha maps (modified copies are returned).
    raws:   list of RAW (pre-predark) BGR uint8 frames, same length/size — predark repaints
            the nameplate band, so the backdrop comparison must use the untouched pixels.
    backdrop: the per-clip `clean_backdrop` plate (HxWx3 float/uint8) or None (no-op).
    temporal_gate: True for the idle loop.

    Every carve decision is made ONCE PER SEGMENT, never per frame — per-frame thresholding
    made borderline dark parts (near the backdrop colour) pop in and out with codec noise:

    - IDLE (`temporal_gate=True`): one global mask from temporal aggregates — per-pixel
      MEDIAN diff <= thr, temporally static (std < std_thr), any-frame alpha support; then
      component gates (median-of-median <= med_diff, mean std < comp_std, area >= min_area).
      The identical mask applies to every frame, so the carve cannot flicker and the gap
      (whose engine alpha DID flicker) becomes stable.
    - SPAWN/FLOURISH: a pixel qualifies only while it matches the backdrop for >= `run`
      consecutive frames (noise can't sustain a run), and each SPATIAL component (projected
      over the segment) gets one keep/drop verdict from the median diff over its whole
      space-time support. Pixels enter/leave only at real occlusion boundaries.

    A component is carved only if its MEDIAN diff is at the clip's noise floor
    (CARVE_MED_DIFF): a true gap shows the very pixels of the plate, while a dark kart part
    that merely lands within CARVE_THR of a dark backdrop sits measurably higher.

    Returns (new_alphas, carved_px_per_frame).
    """
    if backdrop is None or not alphas:
        return list(alphas), [0] * len(alphas)
    B = np.asarray(backdrop, dtype=np.float32)
    diffs = np.stack([_backdrop_diff(r, B) for r in raws]).astype(np.float16)
    asup = np.stack([a > 1.0 / 255.0 for a in alphas])

    if temporal_gate:                                  # ── idle: one global mask ──────────
        dmed = np.median(diffs.astype(np.float32), axis=0)
        std = temporal_std(raws)
        cand = (dmed <= thr) & (std < std_thr) & asup.any(axis=0)
        n, lab, st, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), 8)
        keep = np.zeros(cand.shape, bool)
        for c in range(1, n):
            if st[c, cv2.CC_STAT_AREA] < min_area:
                continue
            m = lab == c
            if float(np.median(dmed[m])) > med_diff or float(std[m].mean()) >= comp_std:
                continue
            keep |= m
        out = [_apply(a, keep) for a in alphas]
        return out, [int(keep.sum())] * len(alphas)

    # ── spawn/flourish: run-stabilized pixels, one verdict per projected component ────────
    stable = _stable_runs(diffs <= thr, run) & asup
    proj = stable.any(axis=0)
    n, lab, st, _ = cv2.connectedComponentsWithStats(proj.astype(np.uint8), 8)
    keepc = np.zeros(proj.shape, bool)
    for c in range(1, n):
        if st[c, cv2.CC_STAT_AREA] < min_area:
            continue
        m = lab == c
        sup = stable[:, m]                             # component's space-time support
        if float(np.median(diffs[:, m][sup].astype(np.float32))) > med_diff:
            continue
        keepc |= m
    out, carved = [], []
    for t, alpha in enumerate(alphas):
        keep = stable[t] & keepc
        out.append(_apply(alpha, keep))
        carved.append(int(keep.sum()))
    return out, carved
