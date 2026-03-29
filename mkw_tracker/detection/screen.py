"""Screen enum, TRANSITIONS graph, Tell dataclass, and ScreenDetector."""
import time
import cv2
import numpy as np
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Optional, Set
from typing import NamedTuple

from ..utils.paths import resource_path


# ---------------------------------------------------------------------------
# Screen identifiers
# ---------------------------------------------------------------------------

class Screen(Enum):
    UNKNOWN              = auto()
    TITLE                = auto()
    MAIN_MENU            = auto()
    HOME                 = auto()
    CHARACTER_SELECT     = auto()
    KART_SELECT          = auto()
    COURSE_SELECT        = auto()
    START_TIME_TRIAL     = auto()
    START_REPLAY         = auto()
    RACING               = auto()
    GHOST                = auto()
    UNKNOWN_RACE_ACTIVE  = auto()
    RACE_MENU            = auto()
    REPLAY_MENU          = auto()
    RESET                = auto()
    GHOST_RESET          = auto()
    POST_TIME_TRIAL      = auto()
    REPLAY_RACE_AGAINST  = auto()
    GALLERY              = auto()
    SINGLEPLAYER_MENU    = auto()
    TIME_TRIALS          = auto()


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------

TRANSITIONS: Dict[Screen, Set[Screen]] = {
    Screen.UNKNOWN: {
        Screen.TITLE, Screen.MAIN_MENU, Screen.HOME, Screen.CHARACTER_SELECT,
        Screen.KART_SELECT, Screen.COURSE_SELECT, Screen.START_TIME_TRIAL,
        Screen.RESET, Screen.POST_TIME_TRIAL, Screen.UNKNOWN_RACE_ACTIVE,
        Screen.RACE_MENU, Screen.REPLAY_MENU, Screen.REPLAY_RACE_AGAINST,
        Screen.GALLERY, Screen.SINGLEPLAYER_MENU, Screen.TIME_TRIALS,
    },
    Screen.HOME: {Screen.TITLE, Screen.GALLERY},
    Screen.TITLE: {Screen.MAIN_MENU, Screen.HOME},
    Screen.MAIN_MENU: {Screen.SINGLEPLAYER_MENU, Screen.TIME_TRIALS, Screen.TITLE, Screen.HOME},
    Screen.SINGLEPLAYER_MENU: {Screen.TIME_TRIALS, Screen.MAIN_MENU, Screen.HOME},
    Screen.TIME_TRIALS: {Screen.CHARACTER_SELECT, Screen.SINGLEPLAYER_MENU, Screen.MAIN_MENU, Screen.HOME},
    Screen.CHARACTER_SELECT: {Screen.KART_SELECT, Screen.TIME_TRIALS, Screen.HOME},
    Screen.KART_SELECT: {Screen.COURSE_SELECT, Screen.CHARACTER_SELECT, Screen.HOME},
    Screen.COURSE_SELECT: {Screen.START_TIME_TRIAL, Screen.START_REPLAY, Screen.KART_SELECT, Screen.HOME},
    Screen.START_TIME_TRIAL: {Screen.RACING, Screen.RACE_MENU, Screen.COURSE_SELECT, Screen.HOME},
    Screen.START_REPLAY: {Screen.GHOST, Screen.REPLAY_MENU, Screen.COURSE_SELECT, Screen.HOME},
    Screen.RACING: {Screen.POST_TIME_TRIAL, Screen.RACE_MENU, Screen.HOME},
    Screen.GHOST: {Screen.REPLAY_MENU, Screen.HOME},
    Screen.UNKNOWN_RACE_ACTIVE: {Screen.RACE_MENU, Screen.REPLAY_MENU, Screen.POST_TIME_TRIAL, Screen.RESET, Screen.HOME},
    Screen.RESET: {Screen.RACING, Screen.CHARACTER_SELECT, Screen.COURSE_SELECT, Screen.MAIN_MENU, Screen.TITLE, Screen.HOME},
    Screen.GHOST_RESET: {Screen.GHOST, Screen.MAIN_MENU, Screen.COURSE_SELECT, Screen.HOME},
    Screen.POST_TIME_TRIAL: {Screen.HOME, Screen.COURSE_SELECT, Screen.RESET},
    Screen.RACE_MENU: {Screen.RACING, Screen.RESET, Screen.HOME},
    Screen.REPLAY_MENU: {Screen.GHOST, Screen.REPLAY_RACE_AGAINST, Screen.HOME, Screen.GHOST_RESET},
    Screen.REPLAY_RACE_AGAINST: {Screen.RESET, Screen.REPLAY_MENU, Screen.HOME},
    Screen.GALLERY: {Screen.HOME},
}


# ---------------------------------------------------------------------------
# Performance stats
# ---------------------------------------------------------------------------

class PerfStats(NamedTuple):
    update_ms: float
    tells_evaluated: int
    current_score: float
    candidate_scores: Dict[Screen, float]


# ---------------------------------------------------------------------------
# Tell definition
# ---------------------------------------------------------------------------

@dataclass
class Tell:
    """Describes how to detect a single screen."""
    screen: Screen
    image_path: str
    roi: tuple
    match_threshold: float = 0.9
    binary_thresh: Optional[int] = 170

    alt_image_path: Optional[str] = None
    alt_roi: Optional[tuple] = None

    required_also: list = field(default_factory=list)  # [(path, roi), ...]

    template: Optional[np.ndarray] = field(default=None, repr=False)
    alt_template: Optional[np.ndarray] = field(default=None, repr=False)
    required_also_templates: list = field(default_factory=list, repr=False)

    def load(self):
        self.template = cv2.imread(resource_path(self.image_path), cv2.IMREAD_GRAYSCALE)
        if self.template is None:
            print(f"[WARN] Could not load template: {self.image_path}")
        if self.alt_image_path:
            self.alt_template = cv2.imread(resource_path(self.alt_image_path), cv2.IMREAD_GRAYSCALE)
            if self.alt_template is None:
                print(f"[WARN] Could not load alt template: {self.alt_image_path}")
        self.required_also_templates = []
        for path, _ in self.required_also:
            tmpl = cv2.imread(resource_path(path), cv2.IMREAD_GRAYSCALE)
            if tmpl is None:
                print(f"[WARN] Could not load required_also template: {path}")
            self.required_also_templates.append(tmpl)

    def all_rois(self) -> list:
        rois = [self.roi]
        if self.alt_roi is not None:
            rois.append(self.alt_roi)
        for _, roi in self.required_also:
            rois.append(roi)
        return rois


# ---------------------------------------------------------------------------
# Tell registry
# ---------------------------------------------------------------------------

TELLS: list = [
    Tell(screen=Screen.TITLE, image_path="images/screens/title.png",
         roi=(833, 98, 833+271, 98+323), binary_thresh=170),
    Tell(screen=Screen.HOME, image_path="images/screens/home.png",
         roi=(1110, 805, 1312, 877), binary_thresh=120,
         alt_image_path="images/screens/home2.png",
         alt_roi=(1361, 803, 1548, 875)),
    Tell(screen=Screen.START_TIME_TRIAL, image_path="images/screens/starttimetrial.png",
         roi=(643, 295, 1298, 373), binary_thresh=170),
    Tell(screen=Screen.START_REPLAY, image_path="images/screens/startreplay.png",
         roi=(643, 295, 1298, 373), binary_thresh=170),
    Tell(screen=Screen.RESET, image_path="images/screens/reset.png",
         roi=(0, 589, 527, 1080), binary_thresh=None),
    Tell(screen=Screen.GHOST_RESET, image_path="images/screens/reset.png",
         roi=(0, 589, 527, 1080), binary_thresh=None),
    Tell(screen=Screen.POST_TIME_TRIAL, image_path="images/screens/posttimetrial.png",
         roi=(1334, 784, 1334+164, 784+55), binary_thresh=170,
         alt_image_path="images/screens/posttimetrial2.png",
         alt_roi=(1192, 653, 1192+445, 653+53)),
    Tell(screen=Screen.MAIN_MENU, image_path="images/screens/mainmenu.png",
         roi=(546, 773, 546+74, 773+74), binary_thresh=170),
    Tell(screen=Screen.CHARACTER_SELECT, image_path="images/screens/character_screen.png",
         roi=(1360, 1024, 1920, 1080), binary_thresh=170),
    Tell(screen=Screen.KART_SELECT, image_path="images/screens/kart_screen.png",
         roi=(1360, 1024, 1920, 1080), binary_thresh=170),
    Tell(screen=Screen.COURSE_SELECT, image_path="images/screens/course_select.png",
         roi=(170, 891, 642, 973), binary_thresh=170,
         alt_image_path="images/screens/track-sel-alt.png",
         alt_roi=(279, 814, 279+261, 814+44)),
    Tell(screen=Screen.RACING, image_path="images/screens/racing-coin.png",
         roi=(60, 979, 115, 1031), binary_thresh=170,
         required_also=[("images/screens/racing-flag.png", (228, 974, 280, 1030))]),
    Tell(screen=Screen.GHOST, image_path="images/screens/racing-coin.png",
         roi=(60, 979, 115, 1031), binary_thresh=170,
         required_also=[("images/screens/racing-flag.png", (228, 974, 280, 1030))]),
    Tell(screen=Screen.UNKNOWN_RACE_ACTIVE, image_path="images/screens/racing-coin.png",
         roi=(60, 979, 115, 1031), binary_thresh=170,
         required_also=[("images/screens/racing-flag.png", (228, 974, 280, 1030))]),
    Tell(screen=Screen.RACE_MENU, image_path="images/screens/racemenu.png",
         roi=(764, 635, 764+400, 635+53), binary_thresh=170,
         alt_image_path="images/screens/racemenu-alt.png",
         alt_roi=(799, 516, 799+323, 516+50)),
    Tell(screen=Screen.REPLAY_MENU, image_path="images/screens/ghostmenu.png",
         roi=(756, 576, 756+415, 576+47), binary_thresh=170),
    Tell(screen=Screen.REPLAY_RACE_AGAINST, image_path="images/screens/ghostmenu-red.png",
         roi=(756, 576, 756+415, 576+47), binary_thresh=170),
    Tell(screen=Screen.GALLERY, image_path="images/screens/gallery.png",
         roi=(106, 191, 106+75, 191+293), binary_thresh=170),
    Tell(screen=Screen.SINGLEPLAYER_MENU, image_path="images/screens/singleplayer.png",
         roi=(110, 562, 110+73, 562+66), binary_thresh=170),
    Tell(screen=Screen.TIME_TRIALS, image_path="images/screens/timetrials.png",
         roi=(110, 562, 110+73, 562+66), binary_thresh=170),
]


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _match_tell(frame: np.ndarray, roi: tuple, template: np.ndarray,
                binary_thresh: Optional[int]) -> float:
    if template is None:
        return 0.0
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    if binary_thresh is not None:
        _, processed = cv2.threshold(gray, binary_thresh, 255, cv2.THRESH_BINARY)
    else:
        _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if template.shape[0] > processed.shape[0] or template.shape[1] > processed.shape[1]:
        return 0.0
    result = cv2.matchTemplate(processed, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return float(max_val)


def detect_tell(frame: np.ndarray, tell: Tell) -> tuple:
    """Return (detected: bool, best_score: float)."""
    primary_score = _match_tell(frame, tell.roi, tell.template, tell.binary_thresh)

    if tell.required_also:
        scores = [primary_score]
        for (_, roi), tmpl in zip(tell.required_also, tell.required_also_templates):
            scores.append(_match_tell(frame, roi, tmpl, tell.binary_thresh))
        min_score = min(scores)
        return min_score >= tell.match_threshold, min_score

    if primary_score >= tell.match_threshold:
        return True, primary_score

    if tell.alt_template is not None and tell.alt_roi is not None:
        alt_score = _match_tell(frame, tell.alt_roi, tell.alt_template, tell.binary_thresh)
        if alt_score >= tell.match_threshold:
            return True, alt_score

    return False, primary_score


# ---------------------------------------------------------------------------
# ScreenDetector
# ---------------------------------------------------------------------------

class ScreenDetector:
    """
    Two-phase screen detector.

    Phase 1 (every frame): re-confirm current screen with single tell match.
    Phase 2 (after CONFIRM_LOSS_FRAMES misses): scan all candidate next-screens.
    """

    CONFIRM_LOSS_FRAMES: int = 3

    _RACE_TYPE_RESOLUTION: Dict[Screen, Screen] = {
        Screen.RACE_MENU:       Screen.RACING,
        Screen.REPLAY_MENU:     Screen.GHOST,
        Screen.POST_TIME_TRIAL: Screen.RACING,
    }

    def __init__(
        self,
        tells: list = TELLS,
        transitions: Dict[Screen, Set[Screen]] = TRANSITIONS,
        on_screen_change: Optional[Callable[[Screen, Screen], None]] = None,
        unknown_recheck_interval: float = 0.5,
    ):
        self.transitions = transitions
        self.on_screen_change = on_screen_change
        self.unknown_recheck_interval = unknown_recheck_interval

        self._tells_by_screen: Dict[Screen, Tell] = {t.screen: t for t in tells}
        for tell in tells:
            tell.load()

        self.current_screen: Screen = Screen.UNKNOWN
        self._last_unknown_check: float = 0.0
        self._pre_home_screen: Optional[Screen] = None
        self._loss_streak: int = 0
        self._last_candidate_scores: Dict[Screen, float] = {}

    # ------------------------------------------------------------------
    def _candidate_screens(self) -> Set[Screen]:
        if self.current_screen == Screen.HOME:
            base = self.transitions.get(Screen.HOME, set()).copy()
            if self._pre_home_screen is None:
                base |= self.transitions.get(Screen.UNKNOWN, set())
            else:
                base.add(self._pre_home_screen)
            return base
        return self.transitions.get(self.current_screen, set())

    # ------------------------------------------------------------------
    def update(self, frame: np.ndarray) -> tuple:
        t_start = time.perf_counter()
        tells_evaluated: int = 0

        if self.current_screen == Screen.UNKNOWN:
            now = time.perf_counter()
            if (now - self._last_unknown_check) < self.unknown_recheck_interval:
                return Screen.UNKNOWN, PerfStats(0.0, 0, 0.0, self._last_candidate_scores)
            self._last_unknown_check = now
            best_screen, best_score, tells_evaluated, candidate_scores = \
                self._full_candidate_scan(frame)
            self._last_candidate_scores = candidate_scores
            if best_screen is not None:
                self._on_transition(self.current_screen, best_screen)
                self._loss_streak = 0
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return self.current_screen, PerfStats(
                elapsed_ms, tells_evaluated, 0.0, self._last_candidate_scores)

        current_tell = self._tells_by_screen.get(self.current_screen)
        if current_tell is not None:
            confirmed, current_score = detect_tell(frame, current_tell)
            tells_evaluated += 1 + len(current_tell.required_also)
        else:
            confirmed, current_score = False, 0.0

        if confirmed:
            self._loss_streak = 0
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return self.current_screen, PerfStats(
                elapsed_ms, tells_evaluated, current_score, self._last_candidate_scores)

        self._loss_streak += 1
        if self._loss_streak < self.CONFIRM_LOSS_FRAMES:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return self.current_screen, PerfStats(
                elapsed_ms, tells_evaluated, current_score, self._last_candidate_scores)

        best_screen, best_score, cand_tells, candidate_scores = \
            self._full_candidate_scan(frame)
        tells_evaluated += cand_tells
        self._last_candidate_scores = candidate_scores

        if best_screen is not None and best_screen != self.current_screen:
            self._on_transition(self.current_screen, best_screen)
            self._loss_streak = 0
            current_score = best_score

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return self.current_screen, PerfStats(
            elapsed_ms, tells_evaluated, current_score, self._last_candidate_scores)

    # ------------------------------------------------------------------
    def _full_candidate_scan(self, frame: np.ndarray) -> tuple:
        candidates = self._candidate_screens()
        best_screen: Optional[Screen] = None
        best_score: float = 0.0
        tells_evaluated: int = 0
        candidate_scores: Dict[Screen, float] = {}

        for screen in candidates:
            tell = self._tells_by_screen.get(screen)
            if tell is None:
                continue
            tells_evaluated += 1 + len(tell.required_also)
            detected, score = detect_tell(frame, tell)
            candidate_scores[screen] = score
            if detected and score > best_score:
                best_score = score
                best_screen = screen

        return best_screen, best_score, tells_evaluated, candidate_scores

    # ------------------------------------------------------------------
    def _on_transition(self, old: Screen, new: Screen):
        if old == Screen.UNKNOWN_RACE_ACTIVE:
            resolved = self._RACE_TYPE_RESOLUTION.get(new)
            if resolved is not None:
                self.current_screen = resolved
                if self.on_screen_change:
                    self.on_screen_change(Screen.UNKNOWN_RACE_ACTIVE, resolved)
                old = resolved

        if new == Screen.HOME:
            if old not in (Screen.HOME, Screen.UNKNOWN, Screen.UNKNOWN_RACE_ACTIVE, Screen.GALLERY):
                self._pre_home_screen = old
        elif old == Screen.HOME and new != Screen.GALLERY:
            self._pre_home_screen = None

        self.current_screen = new
        if self.on_screen_change:
            self.on_screen_change(old, new)

    # ------------------------------------------------------------------
    def force_screen(self, screen: Screen):
        """Manually override the current screen."""
        if screen != self.current_screen:
            self._on_transition(self.current_screen, screen)
            self._loss_streak = 0
