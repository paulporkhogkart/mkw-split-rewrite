"""SelectionTracker, SelectionState, KNOWN_COURSES, KNOWN_COSTUMES."""
import re
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .screen import Screen
from .templates import (
    load_edge_template_groups, prepare_text_edges, match_variants, purge_tight_pngs,
)

# ---------------------------------------------------------------------------
# Known data
# ---------------------------------------------------------------------------

KNOWN_COURSES = [
    "Bowser's Castle", "Dry Bones Burnout", "Acorn Heights", "Boo Cinema",
    "Starview Peak", "Sky-High Sundae", "Wario's Galleon", "Peach Beach",
    "Great ? Block Ruins", "Dino Dino Jungle", "Koopa Troopa Beach",
    "DK Spaceport", "Whistlestop Summit", "Desert Hills", "Shy Guy Bazaar",
    "Wario Stadium", "Toad's Factory", "Mario Circuit", "Dandelion Depths",
    "DK Pass", "Salty Salty Speedway", "Faraway Oasis", "Rainbow Road",
    "Crown City", "Mario Bros. Circuit", "Choco Mountain", "Moo Moo Meadows",
    "Cheep Cheep Falls", "Peach Stadium", "Airship Fortress",
]

KNOWN_COSTUMES: Dict[str, list] = {
    "Baby Daisy":    ['Touring', 'Pro Racer', 'Sailor', 'Explorer'],
    "Baby Luigi":    ['Pro Racer', 'Work Crew'],
    "Baby Mario":    ['Pro Racer', 'Swimwear', 'Work Crew'],
    "Baby Peach":    ['Touring', 'Pro Racer', 'Sailor', 'Explorer'],
    "Baby Rosalina": ['Touring', 'Pro Racer', 'Sailor', 'Explorer'],
    "Birdo":         ['Pro Racer', 'Vacation'],
    "Bowser":        ['Pro Racer', 'Supercharged', 'Biker', 'All-Terrain'],
    "Bowser Jr":     ['Pro Racer', 'Biker Jr', 'Explorer'],
    "Cataquack": [], "Chargin' Chuck": [], "Cheep Cheep": [], "Coin Coffer": [],
    "Conkdor": [], "Cow": [],
    "Daisy":         ['Touring', 'Pro Racer', 'Oasis', 'Swimwear', 'Aero', 'Vacation'],
    "Dolphin": [],
    "Donkey Kong":   ['All-Terrain'],
    "Dry Bones": [], "Fish Bone": [], "Goomba": [], "Hammer Bro": [],
    "King Boo":      ['Pro Racer', 'Aristocrat', 'Pirate'],
    "Koopa Troopa":  ['Runner', 'Pro Racer', 'Sailor', 'All-Terrain', 'Work Crew'],
    "Lakitu":        ['Pit Crew', 'Fisherman'],
    "Luigi":         ['Touring', 'Pro Racer', 'Mechanic', 'Oasis', 'Farmer', 'Happi', 'All-Terrain', 'Gondolier'],
    "Mario":         ['Touring', 'Pro Racer', 'Mechanic', 'Dune Rider', 'Cowboy', 'Sightseeing', 'Aviator', 'Happi', 'All-Terrain'],
    "Monty Mole": [], "Nabbit": [], "Para-Biddybud": [],
    "Pauline":       ['Aero'],
    "Peach":         ['Touring', 'Pro Racer', 'Farmer', 'Sightseeing', 'Aviator', 'Yukata', 'Aero', 'Vacation'],
    "Peepa": [], "Penguin": [], "Pianta": [], "Piranha Plant": [], "Pokey": [],
    "Rocky Wrench": [],
    "Rosalina":      ['Touring', 'Pro Racer', 'Aurora', 'Aero'],
    "Shy Guy":       ['Pit Crew', 'Slope Styler'],
    "Sidestepper": [], "Snowman": [], "Spike": [], "Stingby": [], "Swoop": [],
    "Toad":          ['Pro Racer', 'Engineer', 'Burger Bud', 'Explorer'],
    "Toadette":      ['Pro Racer', 'Conductor', 'Soft Server', 'Explorer'],
    "Waluigi":       ['Pro Racer', 'Wampire', 'Mariachi', 'Biker', 'Road Ruffian'],
    "Wario":         ['Pro Racer', 'Oasis', 'Wicked Wasp', 'Biker', 'Pirate', 'Road Ruffian', 'Work Crew'],
    "Wiggler": [],
    "Yoshi":         ['Touring', 'Pro Racer', 'Aristocrat', 'Soft Server', 'Biker', 'Swimwear', 'Matsuri', 'Food Slinger'],
}


# ---------------------------------------------------------------------------
# Costume name canonicalisation
# ---------------------------------------------------------------------------
# Costume template keys are derived from filenames via `_`->space + .title()
# (templates.load_edge_template_groups), which can't reproduce a literal hyphen: e.g.
# all_terrain.png -> "All Terrain", but KNOWN_COSTUMES lists "All-Terrain".
# _rebuild_costume_subset filters by exact membership in KNOWN_COSTUMES, so the
# mismatch silently drops the costume and it can never be detected.  Remapping
# each loaded key to its canonical KNOWN_COSTUMES name fixes detection and makes
# the reported name canonical.  (Characters/courses share this latent issue but
# are intentionally left alone for now - they need a DB migration first.)

def _norm_name(s: str) -> str:
    """Lowercase + strip non-alphanumerics for separator/case-insensitive name
    comparison ('All Terrain' == 'All-Terrain' == 'all_terrain')."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


_COSTUME_CANON: Dict[str, str] = {
    _norm_name(c): c
    for costumes in KNOWN_COSTUMES.values()
    for c in costumes
}


def _canonicalize_costumes(templates: Dict[str, list]) -> Dict[str, list]:
    """Remap filename-derived costume keys to their canonical KNOWN_COSTUMES name.
    Keys with no canonical match are kept unchanged."""
    return {_COSTUME_CANON.get(_norm_name(k), k): tmpl for k, tmpl in templates.items()}


# ROIs for selection screen scanning (full 1080p coords)
CHAR_NAME_ROI   = (1210, 830, 1770, 894)
COSTUME_ROI     = (1210, 916, 1770, 958)
KART_NAME_ROI   = (1240, 830, 1740, 894)
COURSE_NAME_ROI = (163,  387, 647,  462)

SELECTION_MATCH_THRESHOLD = 0.7   # screen-confidence gate (not a template score)

# All four selection categories match the same way: a grayscale ROI crop on disk ->
# Canny edges (background-agnostic) slid over a live crop padded +/- this many px.
# The pad gives a full-ROI template room to slide, so a few-px capture-setup offset
# can't tank the score (a 5px offset otherwise collapses it - see
# test_capture_shift_robustness).  Matching on edges (not grayscale) strips the
# shared name-plate background that made grayscale cross-scores between similar names
# (Mario/Wario, Peach/Peepa, Wario/Peach Stadium) sit at ~0.89; on edges they fall
# to ~0.5-0.66, so similar names no longer nearly tie.
SELECTION_SEARCH_PAD = 8

# Minimum score to accept a match.  Characters commit only after CHAR_CONFIRM_FRAMES,
# so their floor can sit low; karts/courses commit on a single frame, so theirs sits
# above the worst cross-score.  Costumes are intentionally low: their name banner's
# background varies (bright/dark/split), so even with background-augmented templates
# a correct match can score modestly - and discrimination (it never *misreads*, only
# under-scores) is what protects them.
SELECTION_CHAR_FLOOR    = 0.60
SELECTION_KART_FLOOR    = 0.70
SELECTION_COURSE_FLOOR  = 0.70
SELECTION_COSTUME_FLOOR = 0.30

# "Incumbent still matches?" hysteresis: keep the current selection (skipping the
# full scan) while it still scores >= this.  Set above the worst cross-score between
# similar names so a genuine switch always forces a rescan, and below the self-score
# so the incumbent holds while unchanged.  Costumes use a lower value to match their
# softer scores.
SELECTION_RECONFIRM_THRESHOLD         = 0.80
SELECTION_COSTUME_RECONFIRM_THRESHOLD = 0.50

# Background-robust acceptance.  The semi-transparent name plate lets the game scene
# bleed through, so the absolute edge score swings with the background - a name can
# drop to ~0.5 on a dark stage, and Mario's absolute lead over Wario shrinks to ~0.19.
# But that lead is ~38% of the score on the dark stage and ~41% on the clean one: the
# *fraction* is background-stable even though the absolute gap is not.  So accept a
# clear winner - leading the runner-up by >= SELECTION_REL_MARGIN of its own score
# while scoring >= SELECTION_MIN_ABS - even when it is below the per-category floor.
# REL_MARGIN sits below the worst real relative gap (course Wario/Peach Stadium 0.34);
# a transition frame has no clear winner, so it is still correctly rejected.
SELECTION_REL_MARGIN = 0.25
SELECTION_MIN_ABS    = 0.30


def top_candidates(score_map: Dict[str, float], n: int = 5) -> list:
    """Return the top-N entries from a ``{name: score}`` map as a sorted list.

    Returns a list of ``{"name": str, "score": float}`` dicts sorted by score
    descending, capped at *n* entries.  Scores are rounded to 4 decimal places.
    Returns an empty list when *score_map* is empty.
    """
    if not score_map:
        return []
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)[:n]
    return [{"name": name, "score": round(score, 4)} for name, score in ranked]


# ---------------------------------------------------------------------------
# SelectionState
# ---------------------------------------------------------------------------

@dataclass
class SelectionState:
    character: Optional[str] = None
    character_conf: float    = 0.0
    costume:   Optional[str] = None
    costume_conf: float      = 0.0
    kart:      Optional[str] = None
    kart_conf: float         = 0.0
    course:    Optional[str] = None
    course_conf: float       = 0.0


# ---------------------------------------------------------------------------
# SelectionTracker
# ---------------------------------------------------------------------------

class SelectionTracker:
    """Tracks selected character/costume/kart/course by scanning ROIs at 10Hz."""

    # Consecutive non-matching scans (~0.1s each) before a costume clears to Base.
    # Set instantly, cleared slowly: this rides over a momentary score dip from the
    # variable costume banner background so the readout doesn't flicker off.
    COSTUME_LOSS_FRAMES: int = 4

    def __init__(
        self,
        char_dir:    str = None,
        costume_dir: str = None,
        kart_dir:    str = None,
        course_dir:  str = None,
        switch2_language: str = None,
        on_selection_change: Optional[Callable[[SelectionState], None]] = None,
        scan_interval: float = 0.1,
        purge_tight: bool = False,
    ):
        lang = switch2_language or ""
        if char_dir is None:
            char_dir    = f"images/characters/{lang}" if lang else "images/characters"
        if costume_dir is None:
            costume_dir = f"images/costumes/{lang}"   if lang else "images/costumes"
        if kart_dir is None:
            kart_dir    = f"images/karts/{lang}"      if lang else "images/karts"
        if course_dir is None:
            course_dir  = f"images/courses/{lang}"    if lang else "images/courses"
        self.on_selection_change = on_selection_change
        self.scan_interval = scan_interval
        self.state = SelectionState()

        if purge_tight:
            for d in (char_dir, costume_dir, kart_dir, course_dir):
                purge_tight_pngs(d)

        self._char_templates    = load_edge_template_groups(char_dir)
        self._costume_templates = _canonicalize_costumes(load_edge_template_groups(costume_dir))
        self._kart_templates    = load_edge_template_groups(kart_dir)
        self._course_templates  = load_edge_template_groups(course_dir)

        # ROIs - read from settings so wizard edits take effect after restart
        from ..config.settings import get_settings as _gs
        _s = _gs()
        self._char_name_roi   = tuple(_s.get('char_name_roi',   list(CHAR_NAME_ROI)))
        self._costume_roi     = tuple(_s.get('costume_roi',     list(COSTUME_ROI)))
        self._kart_name_roi   = tuple(_s.get('kart_name_roi',   list(KART_NAME_ROI)))
        self._course_name_roi = tuple(_s.get('course_name_roi', list(COURSE_NAME_ROI)))

        self._last_scan: float = 0.0
        self._relevant_costumes: Dict[str, list] = {}
        if self.state.character:
            self._rebuild_costume_subset(self.state.character)

        self._costume_loss_streak: int = 0

        # Per-field score maps: {name: score} from the most recent full scan.
        # Populated by _update_*; empty until the first scan of that screen.
        self._char_scores:    Dict[str, float] = {}
        self._kart_scores:    Dict[str, float] = {}
        self._course_scores:  Dict[str, float] = {}
        self._costume_scores: Dict[str, float] = {}

        print(f"[SelectionTracker] {len(self._char_templates)} characters, "
              f"{len(self._costume_templates)} costumes, "
              f"{len(self._kart_templates)} karts, "
              f"{len(self._course_templates)} courses")

    # ------------------------------------------------------------------
    @property
    def score_maps(self) -> dict:
        """Return ranked candidates for each selection field.

        Returns a dict::

            {
                "char":    [{"name": str, "score": float}, ...],
                "kart":    [...],
                "course":  [...],
                "costume": [...],
            }

        Each list is sorted by score descending and contains at most 5 entries.
        Lists are empty until the first scan of the relevant selection screen.
        """
        return {
            "char":    top_candidates(self._char_scores),
            "kart":    top_candidates(self._kart_scores),
            "course":  top_candidates(self._course_scores),
            "costume": top_candidates(self._costume_scores),
        }

    # ------------------------------------------------------------------
    def option_lists(self) -> dict:
        """Sorted canonical names per category, as emitted in ``selection_update``.

        Built from the loaded template-dict keys (costumes already canonicalized to
        KNOWN_COSTUMES names) so the run-review popup's dropdowns always contain the
        value the detector reported, in the active Switch language.
        """
        return {
            "characters": sorted(self._char_templates.keys()),
            "karts":      sorted(self._kart_templates.keys()),
            "courses":    sorted(self._course_templates.keys()),
            "costumes":   sorted(self._costume_templates.keys()),
        }

    # ------------------------------------------------------------------
    def _rebuild_costume_subset(self, character: str):
        valid = KNOWN_COSTUMES.get(character, [])
        self._relevant_costumes = {k: v for k, v in self._costume_templates.items()
                                   if k in valid}

    def _crop_padded(self, frame: np.ndarray, roi: tuple, pad: int) -> np.ndarray:
        """Crop roi expanded by `pad` px on each side (clamped to frame bounds),
        giving matchTemplate room to slide a full-width template so a small
        capture-setup positional offset doesn't tank the score."""
        x1, y1, x2, y2 = roi
        h, w = frame.shape[:2]
        return frame[max(0, y1 - pad):min(h, y2 + pad),
                     max(0, x1 - pad):min(w, x2 + pad)]

    # ------------------------------------------------------------------
    def update(self, frame: np.ndarray, screen: Screen,
               current_score: float = 1.0) -> SelectionState:
        now = time.perf_counter()
        if (now - self._last_scan) < self.scan_interval:
            return self.state
        self._last_scan = now

        changed = False
        if screen == Screen.CHARACTER_SELECT and current_score >= SELECTION_MATCH_THRESHOLD:
            changed |= self._update_character(frame)
        elif screen == Screen.KART_SELECT and current_score >= SELECTION_MATCH_THRESHOLD:
            changed |= self._update_kart(frame)
        elif screen == Screen.COURSE_SELECT and current_score >= SELECTION_MATCH_THRESHOLD:
            changed |= self._update_course(frame)

        if changed and self.on_selection_change:
            self.on_selection_change(self.state)
        return self.state

    # ------------------------------------------------------------------
    def _update_character(self, frame: np.ndarray) -> bool:
        changed = False
        char_crop = prepare_text_edges(
            self._crop_padded(frame, self._char_name_roi, SELECTION_SEARCH_PAD))
        name, conf, self._char_scores = match_variants(
            char_crop, self._char_templates,
            threshold=SELECTION_CHAR_FLOOR, reconfirm_name=self.state.character,
            reconfirm_threshold=SELECTION_RECONFIRM_THRESHOLD,
            rel_margin=SELECTION_REL_MARGIN, min_abs=SELECTION_MIN_ABS,
        )

        if name and name != self.state.character:
            self.state.character      = name
            self.state.character_conf = conf
            self.state.costume        = None
            self.state.costume_conf   = 0.0
            self._costume_loss_streak = 0
            changed = True
            print(f"  Character: {name} ({conf:.3f})")
            self._rebuild_costume_subset(name)
        elif name:
            self.state.character_conf = conf

        if self.state.character and self._relevant_costumes:
            cos_crop = prepare_text_edges(
                self._crop_padded(frame, self._costume_roi, SELECTION_SEARCH_PAD))
            cname, cconf, self._costume_scores = match_variants(
                cos_crop, self._relevant_costumes,
                threshold=SELECTION_COSTUME_FLOOR, reconfirm_name=self.state.costume,
                reconfirm_threshold=SELECTION_COSTUME_RECONFIRM_THRESHOLD,
                rel_margin=SELECTION_REL_MARGIN, min_abs=SELECTION_MIN_ABS,
            )
            if cname and cname != self.state.costume:
                self._costume_loss_streak = 0
                self.state.costume      = cname
                self.state.costume_conf = cconf
                changed = True
                print(f"  Costume:   {cname} ({cconf:.3f})")
            elif cname:
                self._costume_loss_streak = 0
                self.state.costume_conf = cconf
            else:
                self._costume_loss_streak += 1
                if self._costume_loss_streak >= self.COSTUME_LOSS_FRAMES:
                    if self.state.costume is not None:
                        self.state.costume      = None
                        self.state.costume_conf = 0.0
                        changed = True
                        print("  Costume:   Base")

        return changed

    # ------------------------------------------------------------------
    def _update_kart(self, frame: np.ndarray) -> bool:
        kart_crop = prepare_text_edges(
            self._crop_padded(frame, self._kart_name_roi, SELECTION_SEARCH_PAD))
        name, conf, self._kart_scores = match_variants(
            kart_crop, self._kart_templates,
            threshold=SELECTION_KART_FLOOR, reconfirm_name=self.state.kart,
            reconfirm_threshold=SELECTION_RECONFIRM_THRESHOLD,
            rel_margin=SELECTION_REL_MARGIN, min_abs=SELECTION_MIN_ABS,
        )
        if name and name != self.state.kart:
            self.state.kart      = name
            self.state.kart_conf = conf
            print(f"  Kart:      {name} ({conf:.3f})")
            return True
        elif name:
            self.state.kart_conf = conf
        return False

    # ------------------------------------------------------------------
    def _update_course(self, frame: np.ndarray) -> bool:
        course_crop = prepare_text_edges(
            self._crop_padded(frame, self._course_name_roi, SELECTION_SEARCH_PAD))
        name, conf, self._course_scores = match_variants(
            course_crop, self._course_templates,
            threshold=SELECTION_COURSE_FLOOR, reconfirm_name=self.state.course,
            reconfirm_threshold=SELECTION_RECONFIRM_THRESHOLD,
            rel_margin=SELECTION_REL_MARGIN, min_abs=SELECTION_MIN_ABS,
        )
        if name and name != self.state.course:
            self.state.course      = name
            self.state.course_conf = conf
            print(f"  Course:    {name} ({conf:.3f})")
            return True
        elif name:
            self.state.course_conf = conf
        return False

    # ------------------------------------------------------------------
    def reload_language(self, switch2_language: str):
        """Hot-reload all template directories for a new Switch 2 language."""
        lang = switch2_language or ""
        char_dir    = f"images/characters/{lang}" if lang else "images/characters"
        costume_dir = f"images/costumes/{lang}"   if lang else "images/costumes"
        kart_dir    = f"images/karts/{lang}"      if lang else "images/karts"
        course_dir  = f"images/courses/{lang}"    if lang else "images/courses"
        self._char_templates    = load_edge_template_groups(char_dir)
        self._costume_templates = _canonicalize_costumes(load_edge_template_groups(costume_dir))
        self._kart_templates    = load_edge_template_groups(kart_dir)
        self._course_templates  = load_edge_template_groups(course_dir)
        if self.state.character:
            self._rebuild_costume_subset(self.state.character)
        print(f"[SelectionTracker] reloaded for lang={lang!r}: "
              f"{len(self._char_templates)} chars, {len(self._costume_templates)} costumes, "
              f"{len(self._kart_templates)} karts, {len(self._course_templates)} courses")
