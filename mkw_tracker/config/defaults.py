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

    def __post_init__(self):
        if self.lap_current_roi is None:
            self.lap_current_roi = [282, 979, 282 + 38, 979 + 49]
        if self.lap_total_roi is None:
            self.lap_total_roi = [341, 990, 341 + 27, 990 + 38]
        if self.coin_left_roi is None:
            self.coin_left_roi = [118, 984, 118 + 36, 984 + 44]
        if self.coin_right_roi is None:
            self.coin_right_roi = [154, 984, 154 + 36, 984 + 44]
        if self.finish_roi is None:
            self.finish_roi = [1290, 410, 1290 + 90, 410 + 90]
        if self.mushroom_roi is None:
            self.mushroom_roi = [50, 50, 50 + 190, 50 + 190]
        if self.minimap_roi is None:
            self.minimap_roi = [1442, 251, 466, 796]

    def as_dict(self) -> dict:
        return asdict(self)
