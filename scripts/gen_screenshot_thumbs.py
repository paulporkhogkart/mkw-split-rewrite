"""Downscale the reference screenshots into small graph-node thumbnails for bundling.

The edit-mode screen graph shows one reference screenshot per node, loaded at
runtime from ``screenshots/<lang>/<file>`` via ``utils.paths.resource_path`` (see
the ``get_screen_thumbs`` handler in ``mkw_tracker/main.py``).  The full-res
``screenshots/`` tree is ~589 MB (18 languages x full 1080p PNGs) - far too large
to bundle into the PyInstaller sidecar, and the runtime only ever renders these at
``240`` px wide.

This script mirrors ``screenshots/<lang>/<file>.png`` into ``<out>/<lang>/<file>.png``
at a fixed thumbnail width (default 240 px, matching the runtime), turning ~589 MB
into ~20 MB.  ``mkw_tracker.spec`` calls :func:`generate_thumbs` at build time and
bundles the output under the ``screenshots/`` name the runtime expects, so packaged
builds show the same graph thumbnails that a dev source launch does.

Usage:
    python scripts/gen_screenshot_thumbs.py                       # -> build/screenshot_thumbs/
    python scripts/gen_screenshot_thumbs.py --width 240 --dry-run # report only
    python scripts/gen_screenshot_thumbs.py --out some/dir        # custom output dir
"""
import argparse
import os

import cv2

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Must match the runtime thumbnail width in mkw_tracker/main.py (get_screen_thumbs).
DEFAULT_WIDTH = 240


def generate_thumbs(src_dir: str, out_dir: str,
                    width: int = DEFAULT_WIDTH, dry_run: bool = False) -> int:
    """Downscale every ``src_dir/<lang>/<file>.png`` to ``width`` px wide (aspect
    preserved, never upscaled) and write it to ``out_dir/<lang>/<file>.png``.

    Returns the number of thumbnails written.  A missing ``src_dir`` yields 0
    rather than raising, so callers can decide how to treat an empty result.
    """
    if not os.path.isdir(src_dir):
        return 0

    count = 0
    for lang in sorted(os.listdir(src_dir)):
        lang_dir = os.path.join(src_dir, lang)
        if not os.path.isdir(lang_dir):
            continue
        for fname in sorted(os.listdir(lang_dir)):
            if not fname.lower().endswith(".png"):
                continue
            img = cv2.imread(os.path.join(lang_dir, fname), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            if w <= 0 or h <= 0:
                continue
            tw = min(width, w)                       # never upscale
            th = max(1, int(round(h * tw / w)))
            small = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
            dst = os.path.join(out_dir, lang, fname)
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                cv2.imwrite(dst, small)
            count += 1
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=os.path.join(ROOT, "screenshots"),
                    help="Source screenshots root (default: screenshots/)")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "screenshot_thumbs"),
                    help="Output directory (default: build/screenshot_thumbs/)")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                    help=f"Thumbnail width in px (default: {DEFAULT_WIDTH})")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = ap.parse_args()

    n = generate_thumbs(args.src, args.out, args.width, args.dry_run)
    verb = "would be written" if args.dry_run else "written"
    print(f"{n} thumbnails {verb} to {args.out} ({args.width}px wide, from {args.src})")
    if n == 0:
        print("  WARNING: no thumbnails produced - is the screenshots/ tree present?")


if __name__ == "__main__":
    main()
