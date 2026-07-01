"""Derive a SAM2 prompt from a birefnet rough mask, and pick the SAM2 candidate that best agrees
with birefnet. Pure numpy (no torch/cv2/sam2 at import) so it unit-tests under build python and
imports cleanly in every venv. The one SAM2-touching function takes an already-built predictor."""
import numpy as np


def mask_bbox(mask, pad_frac=0.04):
    """Padded, frame-clamped [x1,y1,x2,y2] float bbox of nonzero `mask` (HxW). Pads by pad_frac of
    the box's own width/height. Raises ValueError if the mask is empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise ValueError("empty mask")
    h, w = mask.shape
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    pw, ph = (x2 - x1) * pad_frac, (y2 - y1) * pad_frac
    return np.array([max(0, x1 - pw), max(0, y1 - ph),
                     min(w - 1, x2 + pw), min(h - 1, y2 + ph)], dtype=np.float32)


def _interior(mask):
    """Boolean HxW of pixels whose 4-neighbours are all set (cheap 1px erosion, numpy-only)."""
    m = mask.astype(bool)
    e = m.copy()
    e[1:, :] &= m[:-1, :]; e[:-1, :] &= m[1:, :]
    e[:, 1:] &= m[:, :-1]; e[:, :-1] &= m[:, 1:]
    return e


def positive_points(mask, n=3, seed=0):
    """`n` (x,y) interior points, spatially spread by greedy farthest-point. Deterministic given
    seed. Falls back to the raw mask if the interior is empty; returns fewer than n if scarce."""
    e = _interior(mask)
    ys, xs = np.nonzero(e if e.any() else mask)
    pts = np.stack([xs, ys], 1).astype(np.float32)
    if len(pts) <= n:
        return pts
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(len(pts)))]
    d = np.full(len(pts), np.inf)
    for _ in range(1, n):
        d = np.minimum(d, ((pts - pts[chosen[-1]]) ** 2).sum(1))
        chosen.append(int(np.argmax(d)))
    return pts[chosen]


def corner_points(h, w, inset=6):
    """The 4 frame corners (x,y), inset a few px — negatives that reject in-box background."""
    i = inset
    return np.array([[i, i], [w - 1 - i, i], [i, h - 1 - i], [w - 1 - i, h - 1 - i]], np.float32)


def build_prompt(mask, pad_frac=0.04, n_pos=3, seed=0):
    """SAM2 prompt dict from a birefnet binary mask: padded box + n_pos positive interior points
    (label 1) + 4 corner negatives (label 0)."""
    pos = positive_points(mask, n_pos, seed)
    neg = corner_points(*mask.shape)
    coords = np.concatenate([pos, neg], 0).astype(np.float32)
    labels = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))]).astype(np.int32)
    return {"box": mask_bbox(mask, pad_frac), "point_coords": coords, "point_labels": labels}


def iou(a, b):
    """IoU of two binary masks (HxW); 0.0 if the union is empty."""
    a = a.astype(bool); b = b.astype(bool)
    u = int((a | b).sum())
    return float((a & b).sum() / u) if u else 0.0


def select_best(candidates, ref):
    """Index of the candidate (list of HxW binaries) with highest IoU vs the birefnet reference —
    agrees with birefnet where it's confident, free to fill its blind spots elsewhere."""
    return int(np.argmax([iou(c, ref) for c in candidates]))


def sam2_anchor_mask(predictor, image_rgb, biref_mask, pad_frac=0.04, n_pos=3, seed=0):
    """Prompt a built SAM2 `predictor` (set_image + predict) from the birefnet mask and return the
    best-IoU candidate as HxW uint8 (0/255). `image_rgb` is HxWx3 uint8 RGB."""
    ref = np.asarray(biref_mask).astype(bool)
    p = build_prompt(ref, pad_frac, n_pos, seed)
    predictor.set_image(image_rgb)
    masks, scores, _ = predictor.predict(
        point_coords=p["point_coords"], point_labels=p["point_labels"],
        box=p["box"], multimask_output=True)
    masks = np.asarray(masks).astype(bool)
    if masks.ndim == 4:                     # (1,C,H,W) if the box batches -> drop batch dim
        masks = masks[0]
    best = select_best(list(masks), ref)
    return (masks[best].astype(np.uint8) * 255)
