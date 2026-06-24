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


def generate(crops_path: str, captures_root: str, lang: str, out_root: str,
             chip_px: Optional[int] = None):
    """Cut every mapped capture into out_root/<category>/<name>.png. Returns (written, skipped)."""
    with open(crops_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    chip_px = chip_px or int((spec.get("meta") or {}).get("chip_px", 96))
    written: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []
    for category in CATEGORIES:
        src_dir = os.path.join(captures_root, lang, category)
        if not os.path.isdir(src_dir):
            continue
        for fn in sorted(os.listdir(src_dir)):
            if not fn.lower().endswith(".png"):
                continue
            name = fn[:-4]
            rect = resolve_rect(spec, category, name)
            img = cv2.imread(os.path.join(src_dir, fn)) if rect is not None else None
            if rect is None or img is None:
                skipped.append((category, name))
                continue
            out_dir = os.path.join(out_root, category)
            os.makedirs(out_dir, exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, fn), crop_chip(img, rect, chip_px))
            written.append((category, name))
    return written, skipped


def main():
    p = argparse.ArgumentParser(description="Export activity-feed chips from SDR captures.")
    p.add_argument("--lang", default="en_uk")
    p.add_argument("--captures", default="captures_sdr")
    p.add_argument("--crops", default=os.path.join("tools", "chips.crops.json"))
    p.add_argument("--out", default=os.path.join("web", "public", "chips"))
    p.add_argument("--chip-px", type=int, default=None, dest="chip_px")
    a = p.parse_args()
    written, skipped = generate(a.crops, a.captures, a.lang, a.out, a.chip_px)
    print(f"[gen-chips] wrote {len(written)} chips -> {a.out}")
    if skipped:
        print(f"[gen-chips] skipped {len(skipped)} unmapped/unreadable:")
        for cat, name in skipped:
            print(f"  {cat}/{name}")


if __name__ == "__main__":
    main()
