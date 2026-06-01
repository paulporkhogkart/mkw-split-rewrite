"""Verify captured template-source screenshots against the real matchers.

For every PNG under captures/<lang>/<category>/, re-run the SAME matching the
SelectionTracker uses (same templates for that language, same ROIs from settings,
same prepare functions) and check two things:

  * labeled score  - how well the saved frame matches the template it is NAMED as
  * best match      - which template actually scores highest for that frame

A clean capture has best == label with a high labeled score.  A frame grabbed
mid-transition / moved too fast shows up as a low labeled score or, worse, as a
different template scoring higher (MISS).

Run:
    python -m mkw_tracker.tools.verify_captures [--out DIR] [--lang LANG] [--all]
"""
import argparse
import os
import statistics
from typing import List, Optional, Tuple

import cv2

from ..detection.selection import SelectionTracker, _norm_name, SELECTION_SEARCH_PAD
from ..detection.templates import prepare_text_edges, match_variants
from ..utils.paths import data_dir
from .capture_sources import CATEGORIES

# category -> (SelectionTracker templates attr, ROI attr).  All four categories use
# the same edge + slack matcher now, so there is no per-category prepare mode.
_CAT_SPEC = {
    "characters": ("_char_templates",    "_char_name_roi"),
    "karts":      ("_kart_templates",    "_kart_name_roi"),
    "courses":    ("_course_templates",  "_course_name_roi"),
    "costumes":   ("_costume_templates", "_costume_roi"),
}

# Below this labeled score a confirmed match is still flagged for eyeballing.
# Costumes match on Canny edges and score lower than binary-text categories.
_WEAK = {"characters": 0.70, "karts": 0.70, "courses": 0.70, "costumes": 0.50}


def _ranked(tracker: SelectionTracker, category: str, frame) -> List[Tuple[str, float]]:
    """Ranked [(display_name, score)] for *frame* in *category* (whole template set),
    using the exact edge + slack matcher the SelectionTracker runs live."""
    templ_attr, roi_attr = _CAT_SPEC[category]
    templ = getattr(tracker, templ_attr)
    roi = getattr(tracker, roi_attr)
    if not templ:
        return []
    crop = prepare_text_edges(tracker._crop_padded(frame, roi, SELECTION_SEARCH_PAD))
    _, _, scores = match_variants(crop, templ)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _labeled_score(ranked: List[Tuple[str, float]], base: str) -> Optional[float]:
    key = _norm_name(base)
    for name, score in ranked:
        if _norm_name(name) == key:
            return score
    return None


def _classify(category: str, base: str, ranked: List[Tuple[str, float]]) -> dict:
    """Turn a ranked match list + the file's label into a status dict.

    OK      - the labeled template is the top match and scores >= the category's
              weak threshold.
    WEAK    - labeled template is the top match but scores below that threshold.
    MISS    - a different template scores higher than the labeled one.
    NOLABEL - the filename matches no template in this category.
    NOTEMPL - no templates loaded / empty ranked list.
    """
    if not ranked:
        return {"base": base, "status": "NOTEMPL", "labeled": None,
                "best": None, "best_score": 0.0, "runner": None}
    best_name, best_score = ranked[0]
    labeled = _labeled_score(ranked, base)
    runner = ranked[1] if len(ranked) > 1 else None
    if labeled is None:
        status = "NOLABEL"                       # filename matches no template
    elif _norm_name(best_name) != _norm_name(base):
        status = "MISS"                          # a different template wins
    elif labeled < _WEAK[category]:
        status = "WEAK"                           # correct but low-confidence
    else:
        status = "OK"
    return {"base": base, "status": status, "labeled": labeled,
            "best": best_name, "best_score": best_score, "runner": runner}


def verify_one(tracker, category, base, frame):
    """Return dict describing how *frame* (labeled *base*) matches its category."""
    return _classify(category, base, _ranked(tracker, category, frame))


def _verify_lang(out_root: str, lang: str) -> List[dict]:
    print(f"\n===== captures/{lang} =====")
    tracker = SelectionTracker(switch2_language=lang)
    problems: List[dict] = []
    for category in CATEGORIES:
        directory = os.path.join(out_root, lang, category)
        if not os.path.isdir(directory):
            continue
        files = sorted(f for f in os.listdir(directory) if f.lower().endswith(".png"))
        rows = []
        for filename in files:
            base = filename[:-4]
            frame = cv2.imread(os.path.join(directory, filename))
            if frame is None:
                rows.append({"base": base, "status": "UNREADABLE", "labeled": None,
                             "best": None, "best_score": 0.0, "runner": None})
                continue
            rows.append(verify_one(tracker, category, base, frame))

        ok = sum(r["status"] == "OK" for r in rows)
        scored = [r["labeled"] for r in rows if r["labeled"] is not None]
        rng = (f"min {min(scored):.2f} / med {statistics.median(scored):.2f} / "
               f"max {max(scored):.2f}") if scored else "n/a"
        bad = [r for r in rows if r["status"] != "OK"]
        print(f"  {category:<11} {len(rows):>3} files: {ok} OK, {len(bad)} to review"
              f"   (labeled score {rng})")
        for r in bad:
            r["category"] = category
            r["lang"] = lang
            problems.append(r)
    return problems


def main():
    p = argparse.ArgumentParser(
        description="Verify captured screenshots match the templates they are named as.")
    p.add_argument("--out", default=None, help="Captures root (default: <data_dir>/captures).")
    p.add_argument("--lang", default=None, help="Only verify this language subfolder.")
    p.add_argument("--all", action="store_true",
                   help="Print every file's result, not just the ones to review.")
    args = p.parse_args()

    from ..database.migrations import apply_migrations
    apply_migrations()

    out_root = args.out or str(data_dir() / "captures")
    if not os.path.isdir(out_root):
        print(f"[verify] no captures directory at {out_root!r}")
        return

    if args.lang:
        langs = [args.lang]
    else:
        langs = sorted(d for d in os.listdir(out_root)
                       if os.path.isdir(os.path.join(out_root, d)))
    if not langs:
        print(f"[verify] no language subfolders under {out_root!r}")
        return

    all_problems: List[dict] = []
    for lang in langs:
        all_problems.extend(_verify_lang(out_root, lang))

    print("\n===== REVIEW NEEDED =====")
    if not all_problems:
        print("  none - every capture matches its labeled template cleanly.")
    else:
        order = {"UNREADABLE": 0, "NOTEMPL": 1, "NOLABEL": 2, "MISS": 3, "WEAK": 4}
        all_problems.sort(key=lambda r: (order.get(r["status"], 9), r["labeled"] or 0.0))
        for r in all_problems:
            lab = f"{r['labeled']:.2f}" if r["labeled"] is not None else "  - "
            if r["status"] == "MISS":
                runner = f"  (this frame matches '{r['best']}' {r['best_score']:.2f} better)"
            elif r["status"] == "WEAK":
                runner = f"  (best {r['best']} {r['best_score']:.2f})"
            else:
                runner = ""
            print(f"  {r['status']:<10} {r['lang']}/{r['category']}/{r['base']}.png"
                  f"   labeled={lab}{runner}")
    print(f"\n[verify] {len(all_problems)} file(s) to review.")


if __name__ == "__main__":
    main()
