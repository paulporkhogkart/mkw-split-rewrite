"""Regenerate edge-match templates for every selection category from captures.

Characters, karts, courses, and costumes are all matched the same way: a grayscale
ROI crop on disk -> Canny edges (``prepare_text_edges``) -> ``matchTemplate``, which
is background-agnostic (see mkw_tracker/detection/templates.py).  This script cuts
those grayscale crops from the per-item full-screenshot captures in
``captures/<lang>/<category>/<item>.png`` at each category's selection ROI (read
from settings, exactly as ``SelectionTracker`` reads it) and writes them to
``images/<category>/<lang>/``.

Costumes additionally get **synthetic background variants** (``<item>__bgdark.png``
etc.): their name banner's background varies wildly (very bright / very dark /
split), which collapses a single edge template's score.  ``synth_bg_variants`` keeps
the text and swaps the background, and the matcher takes the best variant - so a
costume still scores high whatever the live background does, without re-capturing.

Usage:
    python scripts/gen_selection_templates.py                      # all langs + categories
    python scripts/gen_selection_templates.py --lang en_uk
    python scripts/gen_selection_templates.py --category costumes
    python scripts/gen_selection_templates.py --dry-run
"""
import argparse
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mkw_tracker.detection.selection import (  # noqa: E402
    CHAR_NAME_ROI, KART_NAME_ROI, COURSE_NAME_ROI, COSTUME_ROI,
)
from mkw_tracker.detection.templates import synth_bg_variants  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REF_W, REF_H = 1920, 1080

# category -> (settings ROI key, fallback constant)
CATEGORIES = {
    "characters": ("char_name_roi",   CHAR_NAME_ROI),
    "karts":      ("kart_name_roi",   KART_NAME_ROI),
    "courses":    ("course_name_roi", COURSE_NAME_ROI),
    "costumes":   ("costume_roi",     COSTUME_ROI),
}
# Categories whose background varies at runtime -> emit synthetic bg variants.
AUGMENT = {"costumes"}


def _resize_1080p(frame):
    if (frame.shape[1], frame.shape[0]) != (REF_W, REF_H):
        return cv2.resize(frame, (REF_W, REF_H), interpolation=cv2.INTER_LINEAR)
    return frame


def _outputs(category, gray):
    """Return ``{suffix: gray_crop}`` to write for one item ('' is the base file)."""
    if category in AUGMENT:
        return synth_bg_variants(gray)
    return {"": gray}


def regen_category(category, roi, captures_root, lang, dry_run):
    """Cut one category's templates for one language. Returns (files_written, skipped)."""
    cap_dir = os.path.join(captures_root, lang, category)
    out_dir = os.path.join(ROOT, "images", category, lang)
    if not os.path.isdir(cap_dir):
        print(f"  [skip] {category}: no captures dir {cap_dir}")
        return 0, 0

    x1, y1, x2, y2 = roi
    written = skipped = 0
    for filename in sorted(os.listdir(cap_dir)):
        if not filename.lower().endswith(".png"):
            continue
        base = filename[:-4]
        frame = cv2.imread(os.path.join(cap_dir, filename), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"  [skip] {category}/{base}: unreadable capture")
            skipped += 1
            continue
        crop = _resize_1080p(frame)[y1:y2, x1:x2]
        if crop.size == 0:
            print(f"  [skip] {category}/{base}: empty crop at {roi}")
            skipped += 1
            continue
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        outs = _outputs(category, gray)
        if dry_run:
            extra = f" (+{len(outs) - 1} bg variants)" if len(outs) > 1 else ""
            print(f"  [dry ] {category}/{base}.png  <- {filename}{list(roi)}  "
                  f"({gray.shape[1]}x{gray.shape[0]}){extra}")
            written += len(outs)
            continue
        os.makedirs(out_dir, exist_ok=True)
        # Drop stale variants/tight caches so a changed variant set leaves no orphans.
        for stale in glob.glob(os.path.join(out_dir, f"{base}__*.png")):
            os.remove(stale)
        tight = os.path.join(out_dir, f"{base}_tight.png")
        if os.path.exists(tight):
            os.remove(tight)
        for suffix, img in outs.items():
            out_name = f"{base}.png" if suffix == "" else f"{base}__{suffix}.png"
            cv2.imwrite(os.path.join(out_dir, out_name), img)
            written += 1
    return written, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", help="Single language code (default: all dirs under captures/)")
    ap.add_argument("--category", choices=sorted(CATEGORIES),
                    help="Single category (default: all four)")
    ap.add_argument("--captures", default=os.path.join(ROOT, "captures"),
                    help="Captures root (default: repo-root captures/)")
    ap.add_argument("--dry-run", action="store_true", help="Report what would be written")
    args = ap.parse_args()

    from mkw_tracker.config.settings import get_settings
    from mkw_tracker.database.migrations import apply_migrations
    apply_migrations()
    settings = get_settings()

    if args.lang:
        langs = [args.lang]
    elif os.path.isdir(args.captures):
        langs = sorted(d for d in os.listdir(args.captures)
                       if os.path.isdir(os.path.join(args.captures, d)))
    else:
        langs = []
    if not langs:
        print(f"No languages found under {args.captures}")
        return

    cats = [args.category] if args.category else list(CATEGORIES)
    total_w = total_s = 0
    for lang in langs:
        print(f"== {lang} ==")
        for cat in cats:
            roi_key, const = CATEGORIES[cat]
            roi = tuple(settings.get(roi_key, list(const)))
            print(f"  {cat}: roi {roi} (from {roi_key})")
            w, s = regen_category(cat, roi, args.captures, lang, args.dry_run)
            print(f"    {w} file(s) {'(dry-run) ' if args.dry_run else ''}written, {s} skipped")
            total_w += w
            total_s += s
    print(f"\nDone: {total_w} file(s){' (dry-run)' if args.dry_run else ''} "
          f"across {len(langs)} language(s), {total_s} skipped.")


if __name__ == "__main__":
    main()
