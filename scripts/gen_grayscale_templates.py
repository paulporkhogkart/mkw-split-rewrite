"""Regenerate grayscale screen-detection templates from clean reference screenshots.

Screen tells with ``grayscale=True`` match a continuous-tone template with a small
translation slack (see mkw_tracker/detection/screen.py).  This script cuts those
templates from the per-language reference captures in ``screenshots/<lang>/`` at
each tell's ROI(s) and writes them to ``images/screens/<lang>/``.

It is the source-of-truth regenerator for grayscale templates: run it after
changing a grayscale tell's ROI, or to (re)populate a language.  Binary tells
(RESET family, POST_TIME_TRIAL) are skipped — they have no reference screenshot
and stay on the fixed-threshold path.

Usage:
    python scripts/gen_grayscale_templates.py                 # all languages
    python scripts/gen_grayscale_templates.py --lang en_uk    # one language
    python scripts/gen_grayscale_templates.py --dry-run       # report only
"""
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mkw_tracker.detection.screen import (  # noqa: E402
    TELLS, SCREENSHOT_FILES, _inject_language,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REF_W, REF_H = 1920, 1080


def _tell_outputs(tell):
    """Yield (roi, relative_template_path) for every ROI of a grayscale tell."""
    yield tell.roi, tell.image_path
    if tell.alt_image_path and tell.alt_roi is not None:
        yield tell.alt_roi, tell.alt_image_path
    for path, roi in tell.required_also:
        yield roi, path


def _load_gray_screenshot(lang: str, filename: str):
    path = os.path.join(ROOT, "screenshots", lang, filename)
    if not os.path.exists(path):
        return None, path
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return None, path
    if (img.shape[1], img.shape[0]) != (REF_W, REF_H):
        img = cv2.resize(img, (REF_W, REF_H), interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), path


def regen_language(lang: str, dry_run: bool) -> tuple:
    written, skipped = 0, 0
    seen = set()
    shot_cache = {}
    for tell in TELLS:
        if not tell.grayscale:
            continue
        shot_file = SCREENSHOT_FILES.get(tell.screen)
        if shot_file is None:
            continue
        if shot_file not in shot_cache:
            shot_cache[shot_file] = _load_gray_screenshot(lang, shot_file)
        gray, shot_path = shot_cache[shot_file]
        if gray is None:
            print(f"  [skip] {tell.screen.name}: missing screenshot {shot_path}")
            skipped += 1
            continue
        for roi, rel_path in _tell_outputs(tell):
            out_rel = _inject_language(rel_path, lang)
            if out_rel in seen:           # shared template (e.g. racing-coin via aliases)
                continue
            seen.add(out_rel)
            x1, y1, x2, y2 = roi
            crop = gray[y1:y2, x1:x2]
            if crop.size == 0:
                print(f"  [skip] {os.path.basename(out_rel)}: empty crop at {roi}")
                skipped += 1
                continue
            out_abs = os.path.join(ROOT, out_rel)
            if dry_run:
                print(f"  [dry ] {out_rel}  <- {shot_file}{list(roi)}  ({crop.shape[1]}x{crop.shape[0]})")
            else:
                os.makedirs(os.path.dirname(out_abs), exist_ok=True)
                cv2.imwrite(out_abs, crop)
            written += 1
    return written, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lang", help="Single language code (default: all under screenshots/)")
    ap.add_argument("--dry-run", action="store_true", help="Report what would be written")
    args = ap.parse_args()

    shots_root = os.path.join(ROOT, "screenshots")
    if args.lang:
        langs = [args.lang]
    else:
        langs = sorted(d for d in os.listdir(shots_root)
                       if os.path.isdir(os.path.join(shots_root, d)))

    total_w = total_s = 0
    for lang in langs:
        print(f"== {lang} ==")
        w, s = regen_language(lang, args.dry_run)
        print(f"   {w} templates {'(dry-run) ' if args.dry_run else ''}written, {s} skipped")
        total_w += w
        total_s += s
    print(f"\nDone: {total_w} templates across {len(langs)} language(s), {total_s} skipped.")


if __name__ == "__main__":
    main()
