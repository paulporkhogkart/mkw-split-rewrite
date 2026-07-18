"""Tearout silhouette masks (sil_k0..3) for the live-card two-ply scrapbook cut.

Locked tearout language (live-card.html header): 12-point radial jagged cut with
margins 18-34px in the 540px-tall reference space, jag seeds SHARED across the four
keyframes of an animation — pose is the only variance. White-opaque = keep.
"""
from __future__ import annotations

import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw


def keyframe_indices(n_frames: int) -> list[int]:
    return [round(i * (n_frames - 1) / 3) for i in range(4)]


def _jags(seed_key: str, points: int, margin_range: tuple[int, int]):
    rng = random.Random(f"sil:{seed_key}")
    margins = [rng.uniform(*margin_range) for _ in range(points)]
    jitters = [rng.uniform(-0.5, 0.5) * (2 * math.pi / points) * 0.6 for _ in range(points)]
    return margins, jitters


def sil_mask(frame: Image.Image, seed_key: str, margin_range=(18, 34),
             points: int = 12, ref_h: int = 540) -> Image.Image:
    """Jagged 12-gon around the frame's alpha silhouette. seed_key fixes the jags
    (share one key across an animation's keyframes)."""
    w, h = frame.size
    scale = h / ref_h  # margins are specified in 540px-reference pixels
    al = np.asarray(frame.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(al > 32)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if len(xs) == 0:
        return out
    cx, cy = xs.mean(), ys.mean()
    ang = np.arctan2(ys - cy, xs - cx)
    dist = np.hypot(xs - cx, ys - cy)
    margins, jitters = _jags(seed_key, points, margin_range)
    verts = []
    sector = 2 * math.pi / points
    for j in range(points):
        theta = -math.pi + (j + 0.5) * sector + jitters[j]
        # silhouette extent in this sector (max radius of any silhouette pixel)
        lo, hi = -math.pi + j * sector, -math.pi + (j + 1) * sector
        in_sector = (ang >= lo) & ((ang < hi) if j < points - 1 else (ang <= hi))
        r = dist[in_sector].max() if in_sector.any() else dist.max() * 0.4
        r += margins[j] * scale
        verts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
    ImageDraw.Draw(out).polygon(verts, fill=(255, 255, 255, 255))
    return out


def write_sil_masks(frames: list[Image.Image], name: str, anim: str, out_dir: str) -> list[str]:
    key = f"{name}__{anim}"
    paths = []
    for k, idx in enumerate(keyframe_indices(len(frames))):
        p = os.path.join(out_dir, f"{name}__{anim}__sil_k{k}.png")
        sil_mask(frames[idx], key).save(p, optimize=True)
        paths.append(p)
    return paths
