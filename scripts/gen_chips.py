"""Export activity-feed chips from SDR captures + a crop spec.

Reads tools/chips.crops.json (authored by tools/chip-cropper.html) and the SDR
captures under captures_sdr/<lang>/{combos,karts,courses}/, cuts each item's crop,
resizes to a standard chip height, and writes web/public/chips/<category>/<name>.png.

Output filenames preserve the capture basename, which already matches slugify()
(the website's chip-URL builder slugifies display names to the same form). Items
with no crop rect are skipped and reported.

Run: python scripts/gen_chips.py [--lang en_uk] [--captures captures_sdr]
     [--crops tools/chips.crops.json] [--out web/public/chips] [--chip-px 96]
"""
import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

import cv2

CATEGORIES: Tuple[str, ...] = ("combos", "karts", "courses")
Rect = Tuple[int, int, int, int]


def _as_rect(d: dict) -> Rect:
    return (int(d["x"]), int(d["y"]), int(d["w"]), int(d["h"]))


def resolve_rect(spec: dict, category: str, name: str) -> Optional[Rect]:
    """Crop rect for an item: explicit override, else a category default, else None."""
    explicit = (spec.get(category) or {}).get(name)
    if explicit:
        return _as_rect(explicit)
    defaults = spec.get("defaults") or {}
    if category == "combos":
        char = name.split("__", 1)[0]
        d = (defaults.get("character") or {}).get(char)
        if d:
            return _as_rect(d)
    if category == "courses":
        d = defaults.get("course")
        if d:
            return _as_rect(d)
    return None


def crop_chip(img, rect: Rect, chip_px: int):
    """Cut `rect` (clamped to the frame) and resize to `chip_px` tall, keeping aspect."""
    x, y, w, h = rect
    H, W = img.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    sub = img[y0:y1, x0:x1]
    if sub.size == 0:
        raise ValueError(f"empty crop for rect={rect} on frame {W}x{H}")
    out_w = max(1, round(chip_px * w / h))
    return cv2.resize(sub, (out_w, chip_px), interpolation=cv2.INTER_AREA)
