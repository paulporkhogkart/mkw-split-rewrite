"""SelectionTracker, SelectionState, KNOWN_COURSES, KNOWN_COSTUMES."""
import re
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .screen import Screen
from .templates import (
    load_template_dir, prepare_text_edges, prepare_roi,
    match_best, purge_tight_pngs,
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
# (templates.load_template_dir), which can't reproduce a literal hyphen: e.g.
# all_terrain.png -> "All Terrain", but KNOWN_COSTUMES lists "All-Terrain".
# _rebuild_costume_subset filters by exact membership in KNOWN_COSTUMES, so the
# mismatch silently drops the costume and it can never be detected.  Remapping
# each loaded key to its canonical KNOWN_COSTUMES name fixes detection and makes
# the reported name canonical.  (Characters/courses share this latent issue but
# are intentionally left alone for now — they need a DB migration first.)

def _norm_name(s: str) -> str:
    """Lowercase + strip non-alphanumerics for separator/case-insensitive name
    comparison ('All Terrain' == 'All-Terrain' == 'all_terrain')."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


_COSTUME_CANON: Dict[str, str] = {
    _norm_name(c): c
    for costumes in KNOWN_COSTUMES.values()
    for c in costumes
}


def _canonicalize_costumes(templates: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    """Remap filename-derived costume keys to their canonical KNOWN_COSTUMES name.
    Keys with no canonical match are kept unchanged."""
    return {_COSTUME_CANON.get(_norm_name(k), k): tmpl for k, tmpl in templates.items()}


# ROIs for selection screen scanning (full 1080p coords)
CHAR_NAME_ROI   = (1210, 830, 1770, 894)
COSTUME_ROI     = (1210, 916, 1770, 958)
KART_NAME_ROI   = (1240, 830, 1740, 894)
COURSE_NAME_ROI = (163,  387, 647,  462)

SELECTION_MATCH_THRESHOLD = 0.7


def _load_lang_dir(lang_dir: str, **kwargs) -> dict:
    """Load templates from the language-specific directory. No base-path fallback."""
    return load_template_dir(lang_dir, **kwargs)


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

    COSTUME_LOSS_FRAMES: int = 8
    CHAR_CONFIRM_FRAMES: int = 5

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

        self._char_templates    = _load_lang_dir(char_dir)
        self._costume_templates = _canonicalize_costumes(_load_lang_dir(costume_dir, white_text=True))
        self._kart_templates    = _load_lang_dir(kart_dir)
        self._course_templates  = _load_lang_dir(course_dir)

        # ROIs — read from settings so wizard edits take effect after restart
        from ..config.settings import get_settings as _gs
        _s = _gs()
        self._char_name_roi   = tuple(_s.get('char_name_roi',   list(CHAR_NAME_ROI)))
        self._costume_roi     = tuple(_s.get('costume_roi',     list(COSTUME_ROI)))
        self._kart_name_roi   = tuple(_s.get('kart_name_roi',   list(KART_NAME_ROI)))
        self._course_name_roi = tuple(_s.get('course_name_roi', list(COURSE_NAME_ROI)))

        self._last_scan: float = 0.0
        self._relevant_costumes: Dict[str, np.ndarray] = {}
        if self.state.character:
            self._rebuild_costume_subset(self.state.character)

        self._costume_loss_streak: int = 0
        self._char_pending:        str = ""
        self._char_confirm_streak: int = 0

        print(f"[SelectionTracker] {len(self._char_templates)} characters, "
              f"{len(self._costume_templates)} costumes, "
              f"{len(self._kart_templates)} karts, "
              f"{len(self._course_templates)} courses")

    # ------------------------------------------------------------------
    def _rebuild_costume_subset(self, character: str):
        valid = KNOWN_COSTUMES.get(character, [])
        self._relevant_costumes = {k: v for k, v in self._costume_templates.items()
                                   if k in valid}

    def _crop(self, frame: np.ndarray, roi: tuple) -> np.ndarray:
        x1, y1, x2, y2 = roi
        return frame[y1:y2, x1:x2]

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
        char_crop = prepare_roi(self._crop(frame, self._char_name_roi))
        if char_crop is None:
            return False

        name, conf = match_best(
            None, self._char_templates, _prepared=char_crop,
            threshold=0.4, reconfirm_name=self.state.character,
            reconfirm_threshold=0.80,
        )

        if name and name != self.state.character:
            if name == self._char_pending:
                self._char_confirm_streak += 1
            else:
                self._char_pending = name
                self._char_confirm_streak = 1

            if self._char_confirm_streak >= self.CHAR_CONFIRM_FRAMES:
                self.state.character      = name
                self.state.character_conf = conf
                self.state.costume        = None
                self.state.costume_conf   = 0.0
                self._costume_loss_streak = 0
                self._char_pending        = ""
                self._char_confirm_streak = 0
                changed = True
                print(f"  Character: {name} ({conf:.3f})")
                self._rebuild_costume_subset(name)
            else:
                print(f"  Character pending: {name} ({conf:.3f}) "
                      f"[{self._char_confirm_streak}/{self.CHAR_CONFIRM_FRAMES}]")
        elif name:
            self.state.character_conf  = conf
            self._char_pending         = ""
            self._char_confirm_streak  = 0
        else:
            self._char_pending         = ""
            self._char_confirm_streak  = 0

        if self.state.character and self._relevant_costumes:
            cos_crop = prepare_text_edges(self._crop(frame, self._costume_roi))
            cname, cconf = match_best(
                None, self._relevant_costumes,
                threshold=0.3, reconfirm_threshold=0.5,
                _prepared=cos_crop, reconfirm_name=self.state.costume,
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
        name, conf = match_best(
            self._crop(frame, self._kart_name_roi), self._kart_templates,
            reconfirm_name=self.state.kart, reconfirm_threshold=0.9,
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
        name, conf = match_best(
            self._crop(frame, self._course_name_roi), self._course_templates,
            reconfirm_name=self.state.course, reconfirm_threshold=0.95,
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
        self._char_templates    = _load_lang_dir(char_dir)
        self._costume_templates = _canonicalize_costumes(_load_lang_dir(costume_dir, white_text=True))
        self._kart_templates    = _load_lang_dir(kart_dir)
        self._course_templates  = _load_lang_dir(course_dir)
        if self.state.character:
            self._rebuild_costume_subset(self.state.character)
        print(f"[SelectionTracker] reloaded for lang={lang!r}: "
              f"{len(self._char_templates)} chars, {len(self._costume_templates)} costumes, "
              f"{len(self._kart_templates)} karts, {len(self._course_templates)} courses")
