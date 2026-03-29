# Config Reference

All constants are stored in the `config` SQLite table (JSON-encoded values). They are populated from `mkw_tracker/config/defaults.py` on first run and can be updated at runtime via the IPC `update_config` command or directly in the DB.

## Screen Detection

| Key | Default | Description |
|---|---|---|
| `confirm_loss_frames` | `3` | Consecutive misses before Phase 2 candidate scan |
| `unknown_recheck_interval` | `0.5` | Seconds between scans in UNKNOWN state |

## Selection Tracking

| Key | Default | Description |
|---|---|---|
| `selection_match_threshold` | `0.7` | Minimum NCC score for character/kart/course match |
| `char_confirm_frames` | `5` | Consecutive wins to confirm character (avoids fade-frame mismatches) |
| `costume_loss_frames` | `8` | Consecutive misses before clearing costume |
| `selection_scan_interval` | `0.1` | Minimum seconds between selection scans |
| `char_dir` | `"images/characters"` | Character template directory |
| `costume_dir` | `"images/costumes"` | Costume template directory |
| `kart_dir` | `"images/karts"` | Kart template directory |
| `course_dir` | `"images/courses"` | Course template directory |

## Lap Tracking

| Key | Default | Description |
|---|---|---|
| `lap_digit_threshold` | `0.70` | Minimum NCC score for digit match |
| `lap_scan_interval` | `0.1` | Minimum seconds between lap scans |
| `lap_current_height` | `40` | Target height (px) for current-lap digit templates |
| `lap_total_height` | `28` | Target height (px) for total-laps digit templates |

## Coin Tracking

| Key | Default | Description |
|---|---|---|
| `coin_digit_threshold` | `0.60` | Minimum NCC score for coin digit |
| `coin_scan_interval` | `0.1` | Minimum seconds between coin scans |

## Timestamp Tracking

| Key | Default | Description |
|---|---|---|
| `timestamp_digit_threshold` | `0.50` | Minimum NCC score for timestamp digit |
| `timestamp_digit_height` | `42` | Target height (px) for timestamp digit templates |

## Finish Detection

| Key | Default | Description |
|---|---|---|
| `finish_match_threshold` | `0.60` | Minimum NCC score for finish overlay |
| `finish_confirm_frames` | `3` | Consecutive hits required to confirm finish |

## Mushroom Tracking

| Key | Default | Description |
|---|---|---|
| `mushroom_match_threshold` | `0.55` | Minimum NCC score for mushroom count template |
| `mushroom_loss_frames` | `2` | Consecutive misses before decrementing count |
| `mushroom_gain_frames` | `2` | Consecutive hits before incrementing count |

## Minimap Tracking (`_MM_*` knobs)

| Key | Default | Description |
|---|---|---|
| `mm_char_h_px` | `28` | Template height in pixels |
| `mm_char_w_px` | `28` | Template width in pixels |
| `mm_max_jump_px` | `40` | Max position jump allowed per frame (jump gate) |
| `mm_reacquire_frames` | `5` | Frames needed to re-acquire after jump rejection |
| `mm_confident_score` | `0.65` | NCC score below which low-conf streak starts |
| `mm_visual_freeze_frames` | `4` | Low-conf frames before visual freeze |
| `mm_lost_streak_max` | `36` | Low-conf frames before tracking fully suspended |
| `mm_ema_alpha` | `0.35` | EMA smoothing factor for cx_smooth/cy_smooth |
| `mm_hough_dp` | `1.5` | Hough circle detection dp parameter |
| `mm_hough_min_dist` | `20` | Hough minimum distance between circles |
| `mm_hough_param1` | `50` | Hough Canny edge threshold |
| `mm_hough_param2` | `18` | Hough accumulator threshold |
| `mm_hough_min_radius` | `8` | Hough minimum circle radius |
| `mm_hough_max_radius` | `25` | Hough maximum circle radius |

## Replay

| Key | Default | Description |
|---|---|---|
| `replay_history_limit` | `100` | Max history runs per course before rollover |

## Camera

| Key | Default | Description |
|---|---|---|
| `camera_width` | `1920` | Capture width |
| `camera_height` | `1080` | Capture height |
| `camera_fps` | `60` | Capture FPS |
