"""Edge-template loading and matching helpers for selection detection.

Every selection category (characters, karts, courses, costumes) is matched the same
way: a grayscale ROI crop on disk -> Canny edges (``prepare_text_edges``, which is
background-agnostic) -> ``matchTemplate`` slid over a padded live crop.  Costumes
additionally carry synthetic background variants (``synth_bg_variants``) so their
variable name-banner background cannot collapse the score.
"""
import os
import cv2
import numpy as np
from typing import Dict, Optional

from ..utils.paths import resource_path


def prepare_text_edges(
    bgr: np.ndarray,
    blur_ksize: int = 3,
    canny_low: int = 30,
    canny_high: int = 100,
) -> np.ndarray:
    """
    Extract Canny edges from a BGR image - background-agnostic text detection.
    Templates and live ROI crops must both use this function for matching to work.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    return cv2.Canny(blurred, canny_low, canny_high)


def _text_mask(gray: np.ndarray) -> np.ndarray:
    """Mask of the outlined text (bright fill OR dark outline), dilated to keep the
    anti-aliased halo.  Works on any uniform background because outlined game text
    always self-contrasts: white fill stands out on a dark plate, the dark outline
    stands out on a bright one."""
    m = ((gray >= 225) | (gray <= 120)).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=1)


def synth_bg_variants(gray: np.ndarray) -> Dict[str, np.ndarray]:
    """Return ``{suffix: gray_crop}`` keeping the text and replacing the background.

    Costume name banners vary wildly in background (very bright, very dark, split),
    which collapses a single edge template's score.  Cutting a few synthetic
    background variants from the one capture and matching best-of restores the score
    without re-capturing (the text edges are shared; only the background differs).
    """
    keep = _text_mask(gray) > 0
    h, w = gray.shape[:2]
    split = np.empty((h, w), np.uint8)
    split[:, :w // 2] = 30
    split[:, w // 2:] = 230
    return {
        "":         gray,
        "bgdark":   np.where(keep, gray, np.uint8(30)),
        "bgbright": np.where(keep, gray, np.uint8(245)),
        "bgsplit":  np.where(keep, gray, split),
    }


def purge_tight_pngs(directory: str):
    """Delete all _tight.png cache files in *directory* (legacy binary-path caches)."""
    directory = resource_path(directory)
    if not os.path.exists(directory):
        return
    count = 0
    for filename in os.listdir(directory):
        if filename.lower().endswith('_tight.png'):
            os.remove(os.path.join(directory, filename))
            count += 1
    if count:
        print(f"[templates] Purged {count} _tight.png file(s) from {directory}")


def load_edge_template_groups(directory: str) -> Dict[str, list]:
    """Load Canny-edge selection templates grouped by display name.

    A file ``<base>__<variant>.png`` is a synthetic background variant of ``<base>``
    (costume augmentation); all variants of a base collapse to one display name
    mapping to a *list* of edge templates, matched best-of by ``match_variants``.
    A plain ``<base>.png`` yields a one-element list.
    """
    groups: Dict[str, list] = {}
    directory = resource_path(directory)
    if not os.path.exists(directory):
        return groups
    for filename in sorted(os.listdir(directory)):
        low = filename.lower()
        if not low.endswith(".png") or low.endswith("_tight.png"):
            continue
        base = filename[:-4].split("__")[0]          # strip variant suffix
        name = base.replace("_", " ").title()
        src = cv2.imread(os.path.join(directory, filename), cv2.IMREAD_COLOR)
        if src is None:
            print(f"[WARN] Could not load template: {filename}")
            continue
        groups.setdefault(name, []).append(prepare_text_edges(src))
    return groups


def _accept_match(best: float, second: float, threshold: float,
                  rel_margin: float, min_abs: float) -> bool:
    """Accept the top candidate if it clears the absolute *threshold*, or if it is a
    clear winner - leading the runner-up by >= *rel_margin* as a FRACTION of its own
    score (``best - second >= rel_margin * best``) while scoring >= *min_abs*.

    The *relative* gap is the point: a semi-transparent name plate over a different
    game background scales every score down together, so the *absolute* gap shrinks
    (Mario leads Wario by only ~0.19 on a dark stage) while the *fraction* is
    background-stable (~0.38 dark vs ~0.41 on the clean stage).  *min_abs* rejects
    pure noise, which can show a large relative gap by chance at tiny absolute scores.
    """
    if best >= threshold:
        return True
    return rel_margin > 0.0 and best >= min_abs and (best - second) >= rel_margin * best


def match_variants(
    prepared: np.ndarray,
    templates: Dict[str, list],
    threshold: float = 0.7,
    reconfirm_name: Optional[str] = None,
    reconfirm_threshold: Optional[float] = None,
    rel_margin: float = 0.0,
    min_abs: float = 0.0,
) -> tuple:
    """Score *prepared* against name -> [templates], taking the best variant per name.

    Returns ``(best_name_or_None, best_score, scores_map)``.  *scores_map* is the
    full ``{name: best_variant_score}`` (for ranked candidates).  If *reconfirm_name*
    still clears *reconfirm_threshold* it is returned without overriding it with a
    higher scorer - the cheap hysteresis that stops the readout flickering.

    Acceptance (``_accept_match``): the top name is returned when it clears
    *threshold*, OR - when *rel_margin* > 0 - when it leads the runner-up by that
    fraction of its own score and scores >= *min_abs*, so a clearly-ranked match
    survives a background-depressed absolute score.
    """
    def _best(tmpls: list) -> float:
        s = 0.0
        for t in tmpls:
            if t.shape[0] <= prepared.shape[0] and t.shape[1] <= prepared.shape[1]:
                s = max(s, float(cv2.minMaxLoc(cv2.matchTemplate(
                    prepared, t, cv2.TM_CCOEFF_NORMED))[1]))
        return s

    scores = {name: _best(tmpls) for name, tmpls in templates.items()}

    rt = reconfirm_threshold if reconfirm_threshold is not None else threshold
    if reconfirm_name and scores.get(reconfirm_name, 0.0) >= rt:
        return reconfirm_name, scores[reconfirm_name], scores
    if not scores:
        return None, 0.0, scores
    best_name = max(scores, key=scores.get)
    best_score = scores[best_name]
    second = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0.0
    accepted = _accept_match(best_score, second, threshold, rel_margin, min_abs)
    return (best_name if accepted else None), best_score, scores
