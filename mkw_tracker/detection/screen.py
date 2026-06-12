"""Screen enum, TRANSITIONS graph, Tell dataclass, and ScreenDetector."""
import copy
import time
import cv2
import numpy as np
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, Optional, Set
from typing import NamedTuple

from ..utils.paths import resource_path


# ---------------------------------------------------------------------------
# Image encoding helpers (used for IPC template comparison)
# ---------------------------------------------------------------------------

def _inject_language(path: str, lang: str) -> str:
    """Convert images/screens/foo.png → images/screens/{lang}/foo.png (no-op if already injected)."""
    prefix = "images/screens/"
    if path and lang and path.startswith(prefix):
        rest = path[len(prefix):]
        if not rest.startswith(lang + "/"):
            return f"{prefix}{lang}/{rest}"
    return path


def _encode_img(img: Optional[np.ndarray]) -> Optional[str]:
    """Encode a numpy image as a base64 PNG string, or None if img is None."""
    if img is None:
        return None
    import base64 as _b64
    _, buf = cv2.imencode(".png", img)
    return _b64.b64encode(buf.tobytes()).decode("ascii")


def _encode_crop_roi(frame: np.ndarray, roi: tuple,
                     binary_thresh: Optional[int],
                     grayscale: bool = False) -> Optional[str]:
    """Crop frame to roi and return as base64 PNG.

    grayscale=True returns the continuous-tone crop (what a grayscale tell
    actually matches); otherwise the crop is binarised with the given threshold
    (or Otsu when binary_thresh is None) to mirror the legacy binary match.
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    if grayscale:
        processed = gray
    elif binary_thresh is not None:
        _, processed = cv2.threshold(gray, binary_thresh, 255, cv2.THRESH_BINARY)
    else:
        _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _encode_img(processed)



# ---------------------------------------------------------------------------
# Region dataclass + score_region (boolean-tree Tell nodes)
# ---------------------------------------------------------------------------

@dataclass
class Region:
    """One detectable region inside a Tell's boolean tree."""
    kind: str = "template"               # "template" | "dark_loading"
    roi: tuple = (0, 0, 0, 0)
    image_path: Optional[str] = None     # template kind
    thresh: int = 170                    # binarisation level (binary path only)
    grayscale: bool = True
    search_pad: int = 6
    icon_roi: Optional[tuple] = None     # dark_loading kind
    template: Optional[np.ndarray] = field(default=None, repr=False)


def score_region(frame: np.ndarray, region: "Region", match_threshold: float) -> float:
    """Return a region's match score in [0, 1]."""
    if region.kind == "dark_loading":
        detected, _ = _detect_dark_loading(frame, region.roi, region.icon_roi)
        return 1.0 if detected else 0.0
    return _match_tell(frame, region.roi, region.template,
                       region.thresh, region.grayscale, region.search_pad)


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
    UNKNOWN_RESET        = auto()
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
        # From an unknown context only the *ambiguous* reset is reachable. The
        # three reset tells are byte-identical (dark_loading), so listing the
        # confident RESET/GHOST_RESET here made all three match at once on any
        # dark frame - and let a "cold" HOME (which folds in TRANSITIONS[UNKNOWN])
        # jump straight to a confident reset. UNKNOWN_RESET self-resolves to the
        # correct subtype via _RESET_TYPE_RESOLUTION once the next screen is known.
        Screen.UNKNOWN_RESET,
        Screen.POST_TIME_TRIAL, Screen.UNKNOWN_RACE_ACTIVE,
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
    Screen.UNKNOWN_RESET: {Screen.UNKNOWN_RACE_ACTIVE, Screen.RACING, Screen.GHOST, Screen.CHARACTER_SELECT, Screen.COURSE_SELECT, Screen.MAIN_MENU, Screen.TITLE, Screen.HOME},
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
    """Boolean-tree description of how to detect a screen.

    groups is an AND of groups; each group is an OR of Regions.  A tell matches
    when every group matches, and a group matches when any region in it matches.
    """
    screen: Screen
    groups: list = field(default_factory=list)   # list[list[Region]]
    match_threshold: float = 0.9

    def load(self, switch2_language: str = None):
        from ..utils.paths import data_dir, resource_path
        import os

        def _load_one(rel_path: str) -> Optional[np.ndarray]:
            if not rel_path:
                return None
            if switch2_language:
                lang_path = _inject_language(rel_path, switch2_language)
                user_lang = str(data_dir() / lang_path)
                if os.path.exists(user_lang):
                    img = cv2.imread(user_lang, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        return img
                return cv2.imread(resource_path(lang_path), cv2.IMREAD_GRAYSCALE)
            user = str(data_dir() / rel_path)
            if os.path.exists(user):
                img = cv2.imread(user, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    return img
            return cv2.imread(resource_path(rel_path), cv2.IMREAD_GRAYSCALE)

        for group in self.groups:
            for region in group:
                if region.kind != "template":
                    continue
                region.template = _load_one(region.image_path)
                if region.template is None:
                    print(f"[WARN] Could not load template: {region.image_path}")

    def all_rois(self) -> list:
        rois = []
        for group in self.groups:
            for region in group:
                rois.append(region.roi)
                if region.icon_roi is not None:
                    rois.append(region.icon_roi)
        return rois


# ---------------------------------------------------------------------------
# Tell registry
# ---------------------------------------------------------------------------

def _tmpl(image_path, roi, thresh=170, grayscale=True):
    return Region(kind="template", image_path=image_path, roi=roi,
                  thresh=thresh, grayscale=grayscale)


TELLS: list = [
    Tell(screen=Screen.TITLE, groups=[[
        _tmpl("images/screens/title.png", (833, 156, 1082, 360), thresh=75)]]),
    Tell(screen=Screen.HOME, groups=[[
        _tmpl("images/screens/home.png",  (1110, 805, 1312, 877), thresh=55),
        _tmpl("images/screens/home2.png", (1361, 803, 1548, 875), thresh=55)]]),
    Tell(screen=Screen.START_TIME_TRIAL, groups=[[
        _tmpl("images/screens/starttimetrial.png", (671, 312, 1267, 359), thresh=199)]]),
    Tell(screen=Screen.START_REPLAY, groups=[[
        _tmpl("images/screens/startreplay.png", (726, 317, 1209, 356), thresh=222)]]),
    # RESET family: the icon group plus two icon-less dark groups.  A real
    # loading screen is near-uniformly dark across the WHOLE frame; dark Switch
    # system screens carry bright content up top (game thumbnails, the Nintendo
    # boot logo) that the top-left/top-right groups veto.
    Tell(screen=Screen.RESET, groups=[
        [Region(kind="dark_loading", roi=(0, 589, 527, 1080),
                icon_roi=(1700, 920, 1870, 1030))],
        [Region(kind="dark_loading", roi=(0, 0, 527, 491))],
        [Region(kind="dark_loading", roi=(1393, 0, 1920, 491))]]),
    Tell(screen=Screen.GHOST_RESET, groups=[
        [Region(kind="dark_loading", roi=(0, 589, 527, 1080),
                icon_roi=(1700, 920, 1870, 1030))],
        [Region(kind="dark_loading", roi=(0, 0, 527, 491))],
        [Region(kind="dark_loading", roi=(1393, 0, 1920, 491))]]),
    Tell(screen=Screen.UNKNOWN_RESET, groups=[
        [Region(kind="dark_loading", roi=(0, 589, 527, 1080),
                icon_roi=(1700, 920, 1870, 1030))],
        [Region(kind="dark_loading", roi=(0, 0, 527, 491))],
        [Region(kind="dark_loading", roi=(1393, 0, 1920, 491))]]),
    Tell(screen=Screen.POST_TIME_TRIAL, groups=[[
        _tmpl("images/screens/posttimetrial.png",  (1364, 798, 1458, 825), thresh=190),
        _tmpl("images/screens/posttimetrial2.png", (1209, 664, 1618, 691), thresh=190)]]),
    Tell(screen=Screen.MAIN_MENU, groups=[[
        _tmpl("images/screens/mainmenu.png",      (554, 784, 612, 838), thresh=168),
        _tmpl("images/screens/main_menu-alt.png", (392, 800, 447, 854), thresh=117)]]),
    Tell(screen=Screen.CHARACTER_SELECT, groups=[[
        _tmpl("images/screens/character_screen.png", (1768, 1027, 1887, 1055), thresh=208)]]),
    Tell(screen=Screen.KART_SELECT, groups=[[
        _tmpl("images/screens/kart_screen.png", (1288, 1032, 1462, 1055), thresh=195)]]),
    Tell(screen=Screen.COURSE_SELECT, groups=[[
        _tmpl("images/screens/course_select.png",  (267, 916, 553, 945), thresh=226),
        _tmpl("images/screens/track-sel-alt.png",  (279, 814, 540, 858), thresh=214)]]),
    Tell(screen=Screen.RACING, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.GHOST, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.UNKNOWN_RACE_ACTIVE, groups=[
        [_tmpl("images/screens/racing-coin.png", (78, 987, 96, 1015), thresh=173)],
        [_tmpl("images/screens/racing-flag.png", (245, 991, 269, 1011), thresh=170)]]),
    Tell(screen=Screen.RACE_MENU, groups=[[
        _tmpl("images/screens/racemenu.png",     (781, 649, 1142, 674), thresh=180),
        _tmpl("images/screens/racemenu-alt.png", (810, 530, 1111, 552), thresh=190)]]),
    Tell(screen=Screen.REPLAY_MENU, groups=[[
        _tmpl("images/screens/ghostmenu.png", (770, 590, 1160, 615), thresh=190)]]),
    Tell(screen=Screen.REPLAY_RACE_AGAINST, groups=[[
        _tmpl("images/screens/ghostmenu-red.png", (762, 590, 1160, 615), thresh=190)]]),
    Tell(screen=Screen.GALLERY, groups=[[
        _tmpl("images/screens/gallery.png", (106, 191, 181, 484), thresh=151)]]),
    Tell(screen=Screen.SINGLEPLAYER_MENU, groups=[[
        _tmpl("images/screens/singleplayer.png", (110, 562, 183, 628), thresh=187)]]),
    Tell(screen=Screen.TIME_TRIALS, groups=[[
        _tmpl("images/screens/timetrials.png", (110, 562, 183, 628), thresh=204)]]),
]


# ---------------------------------------------------------------------------
# Provenance: the clean reference screenshot each grayscale tell's template(s)
# are cut from.  Filenames are relative to screenshots/<lang>/.  All of a tell's
# ROIs (primary + alt + required_also) are cut from this one screenshot.  Used by
# scripts/gen_grayscale_templates.py; validated to reproduce every shipped
# template at >=99.8% pixel agreement.  POST_TIME_TRIAL uses an en_uk PLACEHOLDER
# (old_assets/posttimetrial.png) seeded into every language's screenshot dir for
# now - replace per-language and regen later.
# ---------------------------------------------------------------------------

SCREENSHOT_FILES: Dict[Screen, str] = {
    Screen.TITLE:               "title.png",
    Screen.HOME:                "home.png",
    Screen.START_TIME_TRIAL:    "starttimetrial.png",
    Screen.START_REPLAY:        "startreplay.png",
    Screen.MAIN_MENU:           "mainmenu.png",
    Screen.CHARACTER_SELECT:    "character_screen.png",
    Screen.KART_SELECT:         "kart_screen.png",
    Screen.COURSE_SELECT:       "course_select.png",
    Screen.RACING:              "racing_coin.png",
    Screen.GHOST:               "racing_coin.png",
    Screen.UNKNOWN_RACE_ACTIVE: "racing_coin.png",
    Screen.RACE_MENU:           "racemenu.png",
    Screen.REPLAY_MENU:         "ghostmenu.png",
    Screen.REPLAY_RACE_AGAINST: "ghostmenu_red.png",
    Screen.GALLERY:             "gallery.png",
    Screen.SINGLEPLAYER_MENU:   "singleplayer.png",
    Screen.TIME_TRIALS:         "timetrials.png",
    Screen.POST_TIME_TRIAL:     "posttimetrial.png",
    # RESET family is NOT here: it uses dark_loading detection, not a template.
}


# Reference screenshots for the edit-mode graph nodes. Superset of
# SCREENSHOT_FILES: the RESET family has no template provenance (dark_loading
# detection) but does have a representative reset screenshot, so the navigator
# still shows an image card for it. Used by the get_screen_thumbs IPC only -
# NOT by the template generator, which must keep using SCREENSHOT_FILES.
GRAPH_NODE_SHOTS: Dict[Screen, str] = {
    **SCREENSHOT_FILES,
    Screen.RESET:         "reset.png",
    Screen.GHOST_RESET:   "reset.png",
    Screen.UNKNOWN_RESET: "reset.png",
}


# ---------------------------------------------------------------------------
# Alias groups: canonical screen → screens that share the same tell.
# Editing the canonical screen's ROI/thresh propagates to all aliases.
# ---------------------------------------------------------------------------

TELL_ALIAS_GROUPS: Dict[Screen, list] = {
    Screen.RACING: [Screen.GHOST, Screen.UNKNOWN_RACE_ACTIVE],
    Screen.RESET:  [Screen.GHOST_RESET, Screen.UNKNOWN_RESET],
}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

# Dark-loading (RESET) detection thresholds - see _detect_dark_loading.
_DARK_MAX_MEAN: float = 40.0    # ROI brightness ceiling (dev loading ~20, aiden ~1)
_DARK_MAX_STD:  float = 15.0    # ROI flatness ceiling (dev ~2.3, aiden ~0.3; menus >> this)
_DARK_ICON_MIN_MAX: int = 150   # a bright mascot must be present (guards vs fade-to-black)
_DARK_ICON_SAT_MIN: int = 100       # "colourful" icon pixel: HSV saturation floor
_DARK_ICON_VAL_MIN: int = 100       # "colourful" icon pixel: HSV value floor
_DARK_ICON_MIN_COLOR_PX: int = 40   # colourful-pixel count floor (real items pulse 88-1600
                                    # across cards; gray Switch UI / minimap lines are ~0)


def _detect_dark_loading(frame: np.ndarray, roi: tuple,
                         icon_roi: Optional[tuple]) -> tuple:
    """Detect a dark loading/reset screen by a crush-invariant signature instead
    of template matching: a near-uniformly dark `roi` (low mean AND low std) plus
    a bright COLOURFUL mascot in `icon_roi`.

    Some capture cards clip the loading screen's faint shadow pattern (values
    ~19-26) to pure black, leaving zero variance for a template to correlate
    against - but "dark and flat" stays dark and flat regardless of black levels,
    and the bright mascot survives too.  The icon check guards against plain
    fade-to-black (dark, but no icon).  The mascot is an item from a varying,
    animating set (mushroom, fire flower, ...), so it can't be template-matched -
    but every item is saturated colour, while the dark Switch system screens that
    used to false-positive here (boot logo, user-select glyphs) are pure
    grayscale in that corner, as are minimap lines on a dark race section.  The
    chroma gate is skipped for single-channel frames (chroma unmeasurable);
    production frames are always BGR.  Returns (detected, score).
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    if float(gray.mean()) >= _DARK_MAX_MEAN or float(gray.std()) >= _DARK_MAX_STD:
        return False, 0.0
    if icon_roi is not None:
        ix1, iy1, ix2, iy2 = icon_roi
        icon = frame[iy1:iy2, ix1:ix2]
        if icon.size == 0:
            return False, 0.0
        is_bgr = len(icon.shape) == 3
        ig = cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY) if is_bgr else icon
        if int(ig.max()) <= _DARK_ICON_MIN_MAX:
            return False, 0.0
        if is_bgr:
            hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV)
            colorful = int(((hsv[..., 1] >= _DARK_ICON_SAT_MIN) &
                            (hsv[..., 2] >= _DARK_ICON_VAL_MIN)).sum())
            if colorful < _DARK_ICON_MIN_COLOR_PX:
                return False, 0.0
    return True, 1.0


def _match_tell(frame: np.ndarray, roi: tuple, template: np.ndarray,
                binary_thresh: Optional[int],
                grayscale: bool = False, search_pad: int = 0) -> float:
    if template is None:
        return 0.0
    x1, y1, x2, y2 = roi

    if grayscale:
        # Continuous-tone match.  Pad the crop (clamped to frame bounds) so
        # matchTemplate can slide the roi-sized template +/- search_pad px and
        # take the best correlation - this absorbs the small positional offset
        # between capture setups.  TM_CCOEFF_NORMED is already mean/contrast
        # normalised, so no binarisation (and no calibration LUT) is needed to
        # tolerate exposure differences.  The original roi is always within the
        # frame, so the padded crop is never smaller than the template.
        h, w = frame.shape[:2]
        px1, py1 = max(0, x1 - search_pad), max(0, y1 - search_pad)
        px2, py2 = min(w, x2 + search_pad), min(h, y2 + search_pad)
        crop = frame[py1:py2, px1:px2]
        if crop.size == 0:
            return 0.0
        processed = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    else:
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
    """Return (detected: bool, best_score: float) for a boolean-tree tell."""
    if not tell.groups:
        return False, 0.0
    group_scores = []
    for group in tell.groups:
        if not group:
            return False, 0.0
        group_scores.append(max(score_region(frame, r, tell.match_threshold) for r in group))
    overall = min(group_scores)
    return overall >= tell.match_threshold, overall


# ---------------------------------------------------------------------------
# ScreenDetector
# ---------------------------------------------------------------------------

class ScreenDetector:
    """
    Two-phase screen detector.

    Phase 1 (every frame): re-confirm current screen with single tell match.
    Phase 2 (after CONFIRM_LOSS_FRAMES misses): scan all candidate next-screens.
    """

    # Consecutive frames the current screen must fail to re-confirm before Phase 2
    # re-scans candidates.  1 = scan on the very first lost frame (no delay), so a
    # brief intermediary screen - e.g. the time-trials menu hover that bridges
    # SINGLEPLAYER_MENU and CHARACTER_SELECT - is caught instead of being skipped
    # while we wait.  Trade-off: a single below-threshold frame (loading flash,
    # animation) now triggers an immediate candidate scan.
    CONFIRM_LOSS_FRAMES: int = 1

    _RACE_TYPE_RESOLUTION: Dict[Screen, Screen] = {
        Screen.RACE_MENU:       Screen.RACING,
        Screen.REPLAY_MENU:     Screen.GHOST,
        Screen.POST_TIME_TRIAL: Screen.RACING,
    }

    # Resolves UNKNOWN_RESET → specific reset type when the next screen is known.
    # If the next state is UNKNOWN_RACE_ACTIVE the race type is still ambiguous and
    # UNKNOWN_RACE_ACTIVE will resolve it via _RACE_TYPE_RESOLUTION later.
    _RESET_TYPE_RESOLUTION: Dict[Screen, Screen] = {
        Screen.RACING:             Screen.RESET,
        Screen.GHOST:              Screen.GHOST_RESET,
        Screen.RACE_MENU:          Screen.RESET,
        Screen.REPLAY_MENU:        Screen.GHOST_RESET,
    }

    def __init__(
        self,
        tells: list = TELLS,
        transitions: Dict[Screen, Set[Screen]] = TRANSITIONS,
        on_screen_change: Optional[Callable[[Screen, Screen], None]] = None,
        unknown_recheck_interval: float = 0.5,
        switch2_language: str = None,
    ):
        self.transitions = transitions
        self.on_screen_change = on_screen_change
        self.unknown_recheck_interval = unknown_recheck_interval
        self._switch2_language = switch2_language
        # Keep the original tell list so reset_to_defaults() can rebuild from
        # pristine copies.  Never mutate _tells_spec items directly.
        self._tells_spec = tells
        # Work on deep copies so module-level TELLS objects stay unmodified.
        _copies = [copy.deepcopy(t) for t in tells]
        self._tells_by_screen: Dict[Screen, Tell] = {t.screen: t for t in _copies}
        for tell in _copies:
            tell.load(switch2_language)

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
                # Add the pre-home screen itself AND all its neighbours - we may
                # have been mid-transition when HOME was pressed, so any screen
                # reachable from the last known state is a valid landing point.
                base.add(self._pre_home_screen)
                base |= self.transitions.get(self._pre_home_screen, set())
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
            tells_evaluated += sum(len(g) for g in current_tell.groups)
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
            tells_evaluated += sum(len(g) for g in tell.groups)
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

        if old == Screen.UNKNOWN_RESET:
            resolved = self._RESET_TYPE_RESOLUTION.get(new)
            if resolved is not None:
                self.current_screen = resolved
                if self.on_screen_change:
                    self.on_screen_change(Screen.UNKNOWN_RESET, resolved)
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
    def reload_language(self, switch2_language: str):
        """Hot-reload all tell templates for a new Switch 2 language."""
        self._switch2_language = switch2_language
        for tell in self._tells_by_screen.values():
            tell.load(switch2_language)
        print(f"[ScreenDetector] reloaded templates for lang={switch2_language!r}")

    # ------------------------------------------------------------------
    def reset_to_defaults(self):
        """Discard all user ROI/threshold edits and rebuild tells from the
        hardcoded TELLS defaults, reloading templates for the current language."""
        _copies = [copy.deepcopy(t) for t in self._tells_spec]
        self._tells_by_screen = {t.screen: t for t in _copies}
        for tell in _copies:
            tell.load(self._switch2_language)
        print(f"[ScreenDetector] reset tells to defaults (lang={self._switch2_language!r})")

    # ------------------------------------------------------------------
    def force_screen(self, screen: Screen):
        """Manually override the current screen."""
        if screen != self.current_screen:
            self._on_transition(self.current_screen, screen)
            self._loss_streak = 0

    # ── tree access helpers ───────────────────────────────────────────────
    def _region_at(self, tell, group: int, region: int):
        if tell is None or group >= len(tell.groups):
            return None
        grp = tell.groups[group]
        if region >= len(grp):
            return None
        return grp[region]

    def _region_to_dict(self, r) -> dict:
        return {
            "kind":       r.kind,
            "roi":        list(r.roi),
            "image_path": r.image_path,
            "thresh":     r.thresh,
            "grayscale":  r.grayscale,
            "search_pad": r.search_pad,
            "icon_roi":   list(r.icon_roi) if r.icon_roi else None,
        }

    def _tell_to_dict(self, screen: Screen, tell) -> dict:
        entry = {
            "screen":          screen.name,
            "match_threshold": tell.match_threshold,
            "groups":          [[self._region_to_dict(r) for r in g] for g in tell.groups],
        }
        if screen in TELL_ALIAS_GROUPS:
            entry["aliases"] = [s.name for s in TELL_ALIAS_GROUPS[screen]]
        return entry

    def get_tells_config(self) -> list:
        return [self._tell_to_dict(s, t) for s, t in self._tells_by_screen.items()]

    def reset_tell(self, screen_name: str) -> Optional[dict]:
        """Restore one screen's tell to its hardcoded default tree (and aliases)."""
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        spec = next((t for t in self._tells_spec if t.screen == screen), None)
        if spec is None:
            return None
        fresh = copy.deepcopy(spec)
        fresh.load(self._switch2_language)
        self._tells_by_screen[screen] = fresh
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, fresh)

    def _propagate_tree(self, screen: Screen) -> None:
        """Deep-copy the canonical screen's groups onto its alias screens."""
        tell = self._tells_by_screen.get(screen)
        if tell is None:
            return
        for alias_screen in TELL_ALIAS_GROUPS.get(screen, []):
            alias = self._tells_by_screen.get(alias_screen)
            if alias is None:
                continue
            alias.groups = copy.deepcopy(tell.groups)
            alias.load(self._switch2_language)

    def update_region(self, screen_name: str, group: int, region: int,
                      roi=None, thresh=None, grayscale=None,
                      kind=None, icon_roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None:
            return None
        if roi is not None and len(roi) >= 4:
            r.roi = tuple(int(v) for v in roi)
        if thresh is not None:
            r.thresh = int(thresh)
        if grayscale is not None:
            r.grayscale = bool(grayscale)
        if kind is not None:
            r.kind = str(kind)
        if icon_roi is not None and len(icon_roi) >= 4:
            r.icon_roi = tuple(int(v) for v in icon_roi)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def add_region(self, screen_name: str, group: int, roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None or group >= len(tell.groups):
            return None
        sn_lower = screen_name.lower()
        lang = self._switch2_language or ""
        pfx = f"images/screens/{lang}/" if lang else "images/screens/"
        new_roi = tuple(int(v) for v in roi) if roi and len(roi) >= 4 else tell.groups[group][0].roi
        n = sum(len(g) for g in tell.groups)
        tell.groups[group].append(Region(
            kind="template", image_path=f"{pfx}{sn_lower}-r{group}-{n}.png",
            roi=new_roi))
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def remove_region(self, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if self._region_at(tell, group, region) is None:
            return None
        tell.groups[group].pop(region)
        if not tell.groups[group]:                 # dropped last region → drop group
            tell.groups.pop(group)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def add_group(self, screen_name: str, roi=None) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None:
            return None
        sn_lower = screen_name.lower()
        lang = self._switch2_language or ""
        pfx = f"images/screens/{lang}/" if lang else "images/screens/"
        g = len(tell.groups)
        new_roi = tuple(int(v) for v in roi) if roi and len(roi) >= 4 else (935, 515, 985, 565)
        tell.groups.append([Region(kind="template",
                                   image_path=f"{pfx}{sn_lower}-g{g}.png", roi=new_roi)])
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def remove_group(self, screen_name: str, group: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        if tell is None or group >= len(tell.groups):
            return None
        tell.groups.pop(group)
        self._propagate_tree(screen)
        return self._tell_to_dict(screen, tell)

    def test_region(self, frame, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None or frame is None:
            return None
        score = score_region(frame, r, tell.match_threshold)
        if r.kind == "dark_loading":
            live = _encode_crop_roi(frame, r.roi, None, grayscale=True)
            tmpl_img = None
        else:
            live = _encode_crop_roi(frame, r.roi, r.thresh, r.grayscale)
            tmpl_img = _encode_img(r.template)
        return {
            "screen": screen_name, "group": group, "region": region,
            "score": round(score, 4), "threshold": tell.match_threshold,
            "matched": score >= tell.match_threshold,
            "roi": list(r.roi), "template_img": tmpl_img, "live_crop": live,
        }

    def get_region_images(self, frame, screen_name: str, group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None:
            return None
        live = None
        if frame is not None:
            live = (_encode_crop_roi(frame, r.roi, None, grayscale=True)
                    if r.kind == "dark_loading"
                    else _encode_crop_roi(frame, r.roi, r.thresh, r.grayscale))
        return {
            "screen": screen_name, "group": group, "region": region,
            "template_img": _encode_img(r.template) if r.kind == "template" else None,
            "live_crop": live,
        }

    def capture_region_template(self, frame, screen_name: str,
                                group: int, region: int) -> Optional[dict]:
        try:
            screen = Screen[screen_name]
        except KeyError:
            return None
        tell = self._tells_by_screen.get(screen)
        r = self._region_at(tell, group, region)
        if r is None or frame is None or r.kind != "template" or not r.image_path:
            return None
        x1, y1, x2, y2 = r.roi
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop.copy()
        if r.grayscale:
            processed = gray
        elif r.thresh is not None:
            _, processed = cv2.threshold(gray, r.thresh, 255, cv2.THRESH_BINARY)
        else:
            _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        from ..utils.paths import data_dir
        import os
        save_rel = _inject_language(r.image_path, self._switch2_language or "")
        save_path = str(data_dir() / save_rel)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        cv2.imwrite(save_path, processed)
        tell.load(self._switch2_language)
        self._propagate_tree(screen)
        score = score_region(frame, r, tell.match_threshold)
        return {
            "screen": screen_name, "group": group, "region": region,
            "score": round(score, 4), "threshold": tell.match_threshold,
            "matched": score >= tell.match_threshold,
        }
