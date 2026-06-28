"""Pure cv2/numpy core for nametag-plate removal, ported from the validated
temp/asset_eyetest/nametag prototype and generalized to the production crop."""
import numpy as np
import cv2

PLATE_ROI = (2360, 1602, 1378, 226)        # kart-screen plate, native 4K x,y,w,h
CHAR_ROI = (2378, 1604, 1178, 226)         # character-screen plate (narrower, no 1-UP badge)
FULL_4K = (3840, 2160)                     # w, h
PROD_CROP_4K = (2100, 36, 3720, 1806)      # x1,y1,x2,y2 — validated combo crop at 4K
OUT_W, OUT_H = 988, 1080                   # production loopframe/matte size
NAMEPLATE_HERO_ROI = (1050, 18, 1860, 903)  # PROD_CROP at 1080p ref (for extract_loop.scale_roi)


def _majority(mask, win):
    n = len(mask); half = win // 2; out = mask.copy()
    for i in range(n):
        out[i] = mask[max(0, i - half):min(n, i + half + 1)].mean() >= 0.5
    return out


def classify_presence(luma_series, smooth=5):
    s = np.asarray(luma_series, dtype=np.float64)
    lo, hi = float(s.min()), float(s.max())
    if hi - lo < 1e-6:
        return np.ones(len(s), dtype=bool)
    remap = ((s - lo) / (hi - lo) * 255.0).astype(np.uint8)
    thr, _ = cv2.threshold(remap, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    present = remap <= thr
    if smooth and smooth > 1:
        present = _majority(present, smooth)
    return present


def median_reduce(rois):
    return np.median(np.stack([r.astype(np.float64) for r in rois], axis=0), axis=0)


def diff_to_alpha(P, A, floor=0.05, pct=95.0):
    d = np.abs(np.asarray(P, float) - np.asarray(A, float)).max(axis=2)
    scale = float(np.percentile(d, pct))
    if scale < 1e-6:
        return np.zeros(d.shape, dtype=np.float64)
    a = np.clip(d / scale, 0.0, 1.0)
    a[a < floor] = 0.0
    return a


def solve_tc(P, A):
    """Per-pixel grayscale-plate least squares P_ch = t*A_ch + C; ratio fallback on flat bg."""
    xb = A.mean(axis=2); yb = P.mean(axis=2)
    dx = A - xb[..., None]; dy = P - yb[..., None]
    sxx = (dx * dx).sum(axis=2); sxy = (dx * dy).sum(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(sxx > 1.0, sxy / sxx, yb / np.maximum(xb, 1e-3))
        C = yb - t * xb
    C = np.where(sxx > 1.0, C, 0.0)
    t = np.clip(t, 0.0, 1.6)
    return t, C


def place_in_canvas(img_roi, roi):
    """Place an ROI image (HxW or HxWx3) at `roi` into a zeroed full-4K canvas."""
    x, y, w, h = roi
    W, H = FULL_4K
    chan = () if img_roi.ndim == 2 else (img_roi.shape[2],)
    canvas = np.zeros((H, W) + chan, dtype=img_roi.dtype)
    canvas[y:y + h, x:x + w] = img_roi
    return canvas


def prod_crop(canvas_4k):
    """Crop PROD_CROP_4K from a full-4K image and resize to (OUT_W, OUT_H), INTER_AREA."""
    x1, y1, x2, y2 = PROD_CROP_4K
    sub = canvas_4k[y1:y2, x1:x2]
    return cv2.resize(sub, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
