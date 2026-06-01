"""Edge-based selection matching: bg augmentation, variant loading/scoring, and
capture-backed discrimination / shift / stickiness.

All four selection categories (characters, karts, courses, costumes) are matched on
Canny edges of the name ROI, slid over a padded live crop.  The capture-backed tests
skip when the LFS ``captures/`` data is absent (so CI without it still passes).
"""
import numpy as np


def test_synth_bg_variants_keeps_text_swaps_background():
    """Background-augmentation keeps the text (bright fill / dark outline) and
    replaces only the background, so costume templates survive a variable banner."""
    from mkw_tracker.detection.templates import synth_bg_variants
    g = np.full((12, 24), 206, np.uint8)   # mid background (as captured)
    g[4:6, 4:20] = 245                     # bright text fill
    g[6:8, 4:20] = 60                      # dark text outline
    out = synth_bg_variants(g)
    assert "" in out and "bgdark" in out and "bgbright" in out
    assert np.array_equal(out[""], g)              # original kept verbatim
    assert out["bgdark"][0, 0] == 30               # far-corner background swapped
    assert out["bgbright"][0, 0] == 245
    assert out["bgdark"][4, 12] == 245             # bright text preserved
    assert out["bgdark"][7, 12] == 60              # dark text preserved


def test_load_edge_template_groups_groups_variants_by_name(tmp_path):
    """Variant files (name__bg*.png) collapse to one display name -> list of edge
    templates; a plain name yields a one-element list."""
    import cv2
    from mkw_tracker.detection.templates import load_edge_template_groups
    g = np.full((20, 40), 206, np.uint8); g[8:12, 4:36] = 245
    cv2.imwrite(str(tmp_path / "touring.png"), g)
    cv2.imwrite(str(tmp_path / "touring__bgdark.png"), g)
    cv2.imwrite(str(tmp_path / "pro_racer.png"), g)
    groups = load_edge_template_groups(str(tmp_path))
    assert set(groups.keys()) == {"Touring", "Pro Racer"}
    assert len(groups["Touring"]) == 2
    assert len(groups["Pro Racer"]) == 1
    assert groups["Pro Racer"][0].ndim == 2        # an edge image


def test_match_variants_takes_max_and_reconfirms():
    import cv2
    from mkw_tracker.detection.templates import match_variants
    prepared = np.zeros((30, 40), np.uint8); prepared[10:20, 10:30] = 255
    a_match = prepared.copy()                       # identical -> ~1.0
    a_bad = np.zeros((30, 40), np.uint8); a_bad[0:5, 0:5] = 255
    b = np.zeros((30, 40), np.uint8); b[24:29, 34:39] = 255
    templates = {"A": [a_bad, a_match], "B": [b]}

    name, score, scores = match_variants(prepared, templates, threshold=0.5)
    assert name == "A"
    assert scores["A"] >= 0.99                       # best over A's variants
    assert scores["A"] > scores["B"]

    # incumbent B is held when it clears the reconfirm gate, even though A scores higher
    name2, _, _ = match_variants(prepared, templates, threshold=0.5,
                                 reconfirm_name="B", reconfirm_threshold=0.0)
    assert name2 == "B"

    # nothing clears a high threshold -> no match
    name3, _, _ = match_variants(prepared, {"B": [b]}, threshold=0.99)
    assert name3 is None


# ---------------------------------------------------------------------------
# Capture-backed discrimination (skips without the LFS captures/ data)
# ---------------------------------------------------------------------------

import os
import pytest
import cv2

_LANG = "en_uk"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CAPTURES = os.path.join(_ROOT, "captures", _LANG)

# category -> (capture subdir, tracker update method, tracker scores attribute)
_CATS = {
    "characters": ("characters", "_update_character", "_char_scores"),
    "karts":      ("karts",      "_update_kart",      "_kart_scores"),
    "courses":    ("courses",    "_update_course",    "_course_scores"),
}

_HAVE_CAPTURES = all(os.path.isdir(os.path.join(_CAPTURES, sub))
                     for sub, _, _ in _CATS.values())


def _translate(frame, dx, dy):
    """Shift frame content by (dx, dy) px, replicating the border.  Simulates the
    small positional offset between capture setups that the search pad absorbs."""
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(frame, M, (frame.shape[1], frame.shape[0]),
                          borderMode=cv2.BORDER_REPLICATE)


def _measure(category, shift=(0, 0)):
    """Score every capture in *category* against all templates via the real
    tracker method.  Returns a list of per-item dicts with the correct-item score,
    the best wrong-item score, and whether the correct item ranked #1.

    *shift* translates each frame by (dx, dy) px before scoring."""
    from mkw_tracker.detection.selection import SelectionTracker, _norm_name
    sub, method, scores_attr = _CATS[category]
    tracker = SelectionTracker(switch2_language=_LANG)
    update = getattr(tracker, method)

    cap_dir = os.path.join(_CAPTURES, sub)
    rows = []
    for filename in sorted(os.listdir(cap_dir)):
        if not filename.lower().endswith(".png"):
            continue
        base = filename[:-4]
        frame = cv2.imread(os.path.join(cap_dir, filename), cv2.IMREAD_COLOR)
        if frame.shape[1] != 1920 or frame.shape[0] != 1080:
            frame = cv2.resize(frame, (1920, 1080))
        if shift != (0, 0):
            frame = _translate(frame, shift[0], shift[1])
        update(frame)
        scores = dict(getattr(tracker, scores_attr))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        want = _norm_name(base)
        self_score = next((s for n, s in scores.items() if _norm_name(n) == want), None)
        wrong = [s for n, s in scores.items() if _norm_name(n) != want]
        top_name = ranked[0][0] if ranked else None
        rows.append({
            "base": base,
            "top": top_name,
            "correct": top_name is not None and _norm_name(top_name) == want,
            "self": self_score,
            "best_wrong": max(wrong) if wrong else 0.0,
        })
    return rows


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
@pytest.mark.parametrize("category", list(_CATS))
def test_capture_discrimination_top1(category):
    """Every capture's correct item must rank #1 against all templates in its
    category, with a positive margin over the best wrong item."""
    rows = _measure(category)
    assert rows, f"no captures found for {category}"

    wrong = [r for r in rows if not r["correct"]]
    self_min = min(r["self"] for r in rows if r["self"] is not None)
    wrong_max = max(r["best_wrong"] for r in rows)
    margin_min = min((r["self"] - r["best_wrong"]) for r in rows if r["self"] is not None)
    print(f"\n[{category}] n={len(rows)}  self_min={self_min:.3f}  "
          f"best_wrong_max={wrong_max:.3f}  margin_min={margin_min:.3f}")

    assert not wrong, (
        f"{len(wrong)}/{len(rows)} {category} captures misranked: "
        + ", ".join(f"{r['base']}->({r['top']})" for r in wrong[:10])
    )
    assert margin_min > 0.0


# Small offsets in both axes, all within SELECTION_SEARCH_PAD so the padded edge
# match can slide to recover them.  A same-size (no-slide) match cannot follow the
# shift and its self-score collapses.
_SHIFTS = [(5, 4), (-5, -4), (6, 0), (0, -6)]


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
@pytest.mark.parametrize("category", list(_CATS))
def test_capture_shift_robustness(category):
    """With a few-px positional offset, every capture's correct item must still
    rank #1 and keep a strong self-score - the point of the search pad.  Without
    slide room the self-score collapses, so a healthy floor here is what proves
    the grayscale+slack migration actually bought positional robustness."""
    floor = 0.80
    worst = 1.0
    worst_at = None
    misranked = []
    for shift in _SHIFTS:
        for r in _measure(category, shift=shift):
            if r["self"] is not None and r["self"] < worst:
                worst, worst_at = r["self"], (r["base"], shift)
            if not r["correct"]:
                misranked.append((r["base"], shift, r["top"]))
    print(f"\n[{category}] shift self_min={worst:.3f} at {worst_at}  "
          f"floor={floor}  misranked={len(misranked)}")
    assert not misranked, f"{category} misranked under shift: {misranked[:10]}"
    assert worst >= floor, (
        f"{category} self-score collapsed to {worst:.3f} (< {floor}) under shift "
        f"at {worst_at} - no slide room")


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
def test_character_switch_not_sticky():
    """Selecting one character then a confusable one must actually switch.

    Mario and Wario are the closest character pair; if the reconfirm threshold ever
    sits below their cross-score the incumbent re-confirms on the new character's
    frame forever and the selection sticks.  Edges keep that cross-score low (~0.53),
    so this guards that the reconfirm threshold stays comfortably above it."""
    from mkw_tracker.detection.selection import SelectionTracker
    tracker = SelectionTracker(switch2_language=_LANG)

    def _frame(base):
        f = cv2.imread(os.path.join(_CAPTURES, "characters", f"{base}.png"), cv2.IMREAD_COLOR)
        return cv2.resize(f, (1920, 1080)) if (f.shape[1], f.shape[0]) != (1920, 1080) else f

    mario, wario = _frame("mario"), _frame("wario")
    tracker._update_character(mario)
    assert tracker.state.character == "Mario"
    tracker._update_character(wario)
    assert tracker.state.character == "Wario", "selection stuck on the previous character"


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
def test_character_commits_on_first_frame():
    """A confident character match commits immediately - the multi-frame 'pending'
    confirmation was removed once edge matching made the character signal strong."""
    from mkw_tracker.detection.selection import SelectionTracker
    tracker = SelectionTracker(switch2_language=_LANG)
    frame = cv2.imread(os.path.join(_CAPTURES, "characters", "mario.png"), cv2.IMREAD_COLOR)
    tracker._update_character(frame)
    assert tracker.state.character == "Mario"


# ---------------------------------------------------------------------------
# Costumes: subset discrimination + variable-background robustness
# ---------------------------------------------------------------------------

def _costume_eval(crop_xform=None):
    """Score every costume's capture within its character's costume subset (the real
    runtime scenario).  *crop_xform* optionally rewrites the padded BGR costume crop
    to simulate a different banner background.  Returns
    ``[(character, costume, top_name, self_score, correct), ...]``."""
    from mkw_tracker.detection.selection import (
        SelectionTracker, KNOWN_COSTUMES, _norm_name, SELECTION_SEARCH_PAD)
    from mkw_tracker.detection.templates import prepare_text_edges, match_variants
    tracker = SelectionTracker(switch2_language=_LANG)
    cap_dir = os.path.join(_CAPTURES, "costumes")
    bases = {_norm_name(f[:-4]): f[:-4] for f in os.listdir(cap_dir) if f.endswith(".png")}
    frames, roi = {}, tracker._costume_roi

    def _frame(base):
        if base not in frames:
            frames[base] = cv2.imread(os.path.join(cap_dir, f"{base}.png"), cv2.IMREAD_COLOR)
        return frames[base]

    rows = []
    for char, costumes in KNOWN_COSTUMES.items():
        if len(costumes) < 2:
            continue
        tracker._rebuild_costume_subset(char)
        if len(tracker._relevant_costumes) < 2:
            continue
        for disp in costumes:
            base = bases.get(_norm_name(disp))
            if base is None:
                continue
            pad_bgr = tracker._crop_padded(_frame(base), roi, SELECTION_SEARCH_PAD)
            if crop_xform is not None:
                pad_bgr = crop_xform(pad_bgr)
            _, _, scores = match_variants(prepare_text_edges(pad_bgr),
                                          tracker._relevant_costumes)
            top = max(scores, key=scores.get) if scores else None
            self_v = next((v for n, v in scores.items()
                           if _norm_name(n) == _norm_name(disp)), 0.0)
            rows.append((char, disp, top, self_v,
                         top is not None and _norm_name(top) == _norm_name(disp)))
    return rows


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
def test_costume_subset_discrimination():
    """Each costume's capture must rank #1 within its character's costume subset."""
    rows = _costume_eval()
    assert rows, "no costume subsets evaluated"
    wrong = [(c, d, t) for c, d, t, _, ok in rows if not ok]
    assert not wrong, f"costume misranked within subset: {wrong[:10]}"


@pytest.mark.skipif(not _HAVE_CAPTURES, reason="captures/en_uk LFS data not present")
def test_costume_survives_unseen_background():
    """Under a gradient background (NOT one of the stored dark/bright/split template
    variants), every costume still ranks #1 in its subset with a usable self-score -
    the background augmentation generalising to an unseen banner."""
    from mkw_tracker.detection.templates import _text_mask

    def gradient_bg(pad_bgr):
        g = cv2.cvtColor(pad_bgr, cv2.COLOR_BGR2GRAY)
        keep = _text_mask(g) > 0
        h, w = g.shape
        bg = np.tile(np.linspace(20, 240, w).astype(np.uint8), (h, 1))
        return cv2.cvtColor(np.where(keep, g, bg).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    rows = _costume_eval(crop_xform=gradient_bg)
    assert rows
    wrong = [(c, d, t) for c, d, t, _, ok in rows if not ok]
    self_min = min(r[3] for r in rows)
    print(f"\n[costume/unseen-bg] n={len(rows)} self_min={self_min:.2f} misrank={len(wrong)}")
    assert not wrong, f"costume misranked under gradient bg: {wrong[:10]}"
    assert self_min >= 0.45
