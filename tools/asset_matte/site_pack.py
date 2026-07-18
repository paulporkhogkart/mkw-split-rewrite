"""Site-pack encode core: matte PNG frames -> WebP sprite sheets + manifest entries.

Pure functions over PIL images; no D:\\ paths in here (the CLI wires those).
Recipe knobs (scale/fps/quality/alpha bits) are always parameters — the A/B lab
locks their production values, nothing is hardcoded.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

MAX_SHEET_SIDE = 4096  # GPU-texture-safe cap per spec


def encode_size(src_w: int, src_h: int, scale: float) -> tuple[int, int]:
    return (round(src_w * scale), round(src_h * scale))


def subsample_step(fps: int) -> int:
    """Source is 60fps; we keep every step-th frame."""
    if fps <= 0 or 60 % fps:
        raise ValueError(f"fps must divide 60, got {fps}")
    return 60 // fps


def premul_resize(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Premultiply -> Lanczos -> unpremultiply, so RGB hidden under alpha=0 can't fringe."""
    a = np.asarray(im.convert("RGBA"), dtype=np.float32)
    alpha = a[..., 3:4] / 255.0
    a[..., :3] *= alpha
    pm = Image.fromarray(a.astype("uint8"), "RGBA").resize(size, Image.LANCZOS)
    b = np.asarray(pm, dtype=np.float32)
    al = b[..., 3:4]
    b[..., :3] = np.where(al > 0, b[..., :3] * 255.0 / np.maximum(al, 1e-6), 0).clip(0, 255)
    return Image.fromarray(b.astype("uint8"), "RGBA")


def quant_alpha(im: Image.Image, bits: int) -> Image.Image:
    """Quantize the alpha plane (lossless in WebP -> fewer levels = smaller), snapping
    near-transparent to 0 and near-opaque to 255."""
    if bits >= 8:
        return im
    a = np.asarray(im.convert("RGBA")).copy()
    al = a[..., 3].astype(np.int32)
    step = 255 // ((1 << bits) - 1)
    q = ((al + step // 2) // step * step).clip(0, 255)
    q[al < 6] = 0
    q[al > 249] = 255
    a[..., 3] = q.astype(np.uint8)
    return Image.fromarray(a, "RGBA")
