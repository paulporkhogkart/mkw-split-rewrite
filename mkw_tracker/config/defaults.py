"""All hardcoded constants as typed dataclass defaults, grouped by subsystem."""
from dataclasses import dataclass, asdict


@dataclass
class Defaults:
    # ── Screen detection ──────────────────────────────────────────────────────
    confirm_loss_frames: int = 3
    unknown_recheck_interval: float = 0.5

    # ── Selection tracking ────────────────────────────────────────────────────
    selection_match_threshold: float = 0.7
    char_confirm_frames: int = 5
    costume_loss_frames: int = 8
    selection_scan_interval: float = 0.1

    # Template directories
    char_dir: str = 'images/characters'
    costume_dir: str = 'images/costumes'
    kart_dir: str = 'images/karts'
    course_dir: str = 'images/courses'

    # ── Lap counter ───────────────────────────────────────────────────────────
    lap_scan_interval: float = 0.1
    lap_digit_threshold: float = 0.70
    lap_current_digit_h: int = 40
    lap_total_digit_h: int = 28

    # ROIs (full 1080p)
    lap_current_roi: list = None   # set in __post_init__
    lap_total_roi: list = None

    # ── Coin counter ──────────────────────────────────────────────────────────
    coin_scan_interval: float = 0.1
    coin_digit_threshold: float = 0.60
    coin_digit_h: int = 35
    coin_left_roi: list = None
    coin_right_roi: list = None

    # ── Timestamp tracker ─────────────────────────────────────────────────────
    timestamp_scan_interval: float = 0.1
    timestamp_digit_threshold: float = 0.50
    timestamp_digit_h: int = 42
    timestamp_digit_dir: str = 'images/timestamps/cropped'

    # ── Finish detector ───────────────────────────────────────────────────────
    finish_match_threshold: float = 0.60
    finish_confirm_frames: int = 3
    finish_scan_interval: float = 0.0
    finish_roi: list = None

    # ── Mushroom tracker ──────────────────────────────────────────────────────
    mushroom_match_threshold: float = 0.55
    mushroom_loss_frames: int = 2
    mushroom_gain_frames: int = 2
    mushroom_scan_interval: float = 0.1
    mushroom_roi: list = None

    # ── Selection screen ROIs ──────────────────────────────────────────────────
    char_name_roi:   list = None
    costume_roi:     list = None
    kart_name_roi:   list = None
    course_name_roi: list = None

    # ── Minimap tracker ───────────────────────────────────────────────────────
    minimap_roi: list = None          # [x, y, w, h]
    mm_radius_min: int = 12
    mm_radius_max: int = 42
    mm_search_tight: int = 30
    mm_search_loose: int = 80
    mm_miss_expand: int = 4
    mm_ema_alpha: float = 0.4
    mm_accept_score: float = 0.18
    mm_confident_score: float = 0.90
    mm_lost_frames: int = 36
    mm_max_jump_px: int = 40
    mm_reacquire_frames: int = 4
    mm_calib_margin_base: float = 0.50
    mm_calib_min: float = 0.75
    mm_calib_max: float = 0.98
    mm_char_w_f: float = 0.60
    mm_char_h_f: float = 0.90
    mm_char_w_px: int = 24
    mm_char_h_px: int = 36
    mm_hough_r_min: int = 17
    mm_hough_r_max: int = 25
    mm_hough_param1: int = 50
    mm_hough_param2: int = 18

    # ── Replay ────────────────────────────────────────────────────────────────
    replay_history_limit: int = 100

    # ── Camera ────────────────────────────────────────────────────────────────
    camera_width: int = 1920
    camera_height: int = 1080
    camera_fps: int = 60
    camera_device: str = ""       # empty = auto-detect
    audio_device_label: str = ""  # empty = auto (group-paired); "none" = video-only

    # ── Capture normalization ────────────────────────────────────────────────
    # Per-channel linear+gamma transform applied to every captured frame before
    # any detector sees it.  Identity by default (gain=1, offset=0, gamma=1).
    # Auto-fitted by the calibration wizard against a shipped Switch HDR test-
    # pattern reference, or manually tuned via sliders.  See utils/normalize.py.
    calib_enabled:  int   = 1
    calib_gain_r:   float = 1.0
    calib_gain_g:   float = 1.0
    calib_gain_b:   float = 1.0
    calib_offset_r: int   = 0
    calib_offset_g: int   = 0
    calib_offset_b: int   = 0
    calib_gamma:    float = 1.0

    # ── Language ──────────────────────────────────────────────────────────────
    app_language:    str = 'en_uk'   # UI display language
    switch2_language: str = 'en_uk'  # Nintendo Switch 2 system language (determines template dirs)

    # ── Setup ─────────────────────────────────────────────────────────────────
    setup_complete: int = 0   # 0 = first-time setup required, 1 = done

    def __post_init__(self):
        # All ROI defaults are stored as fractions of 1920×1080.
        # Settings.get() multiplies by the fixed 1920×1080 reference dims
        # (frames are always normalised to that resolution before detection).
        W, H = 1920.0, 1080.0
        if self.lap_current_roi is None:
            self.lap_current_roi = [282/W, 979/H, 320/W, 1028/H]
        if self.lap_total_roi is None:
            self.lap_total_roi = [341/W, 990/H, 368/W, 1028/H]
        if self.coin_left_roi is None:
            self.coin_left_roi = [118/W, 984/H, 154/W, 1028/H]
        if self.coin_right_roi is None:
            self.coin_right_roi = [154/W, 984/H, 190/W, 1028/H]
        if self.finish_roi is None:
            self.finish_roi = [1290/W, 410/H, 1380/W, 500/H]
        if self.mushroom_roi is None:
            self.mushroom_roi = [50, 134, 240, 226]
        if self.minimap_roi is None:
            self.minimap_roi = [1442/W, 251/H, 466/W, 796/H]
        if self.char_name_roi is None:
            self.char_name_roi = [1210, 816, 1770, 900]
        if self.costume_roi is None:
            self.costume_roi = [1210, 916, 1770, 961]
        if self.kart_name_roi is None:
            self.kart_name_roi = [1240, 822, 1729, 891]
        if self.course_name_roi is None:
            self.course_name_roi = [175, 390, 639, 450]

    def as_dict(self) -> dict:
        return asdict(self)
