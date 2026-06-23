# Config Reference

All constants are stored in the `config` SQLite table (JSON-encoded values). They are populated from `mkw_tracker/config/defaults.py` on first run and can be updated at runtime via the IPC `update_config` command or directly in the DB.

## Screen Detection

| Key | Default | Description |
|---|---|---|
| `confirm_loss_frames` | `3` | Consecutive misses before Phase 2 candidate scan |
| `unknown_recheck_interval` | `0.5` | Seconds between scans in UNKNOWN state |

Per-screen tell edits (the boolean tree of regions) persist as one JSON blob per
screen, `tell_tree_<SCREEN>` (e.g. `tell_tree_RACING`), written by the region-edit
IPC. Schema v3 (`database/tell_repo.py:migrate_tells_to_tree`) one-time-migrates the
legacy `tell_roi_*` / `tell_thresh_*` / `tell_alt_*` / `tell_req_also_*` /
`tell_and_thresh_*` / `tell_alt_thresh_*` keys into these blobs and deletes them.
`calib_*` keys remain but are unused (image normalization is a no-op).

### NO_SIGNAL template (auto-selected, no stored key)
The NO_SIGNAL screen's template is chosen automatically from the `camera_device`
name (`elgato`/`ugreen`/`obs virtual` substring; no match -> Elgato default). There is no stored
preset key: "manual" mode is simply the presence of a `tell_tree_NO_SIGNAL`
override (written by editing the NO_SIGNAL node in Edit Screens). "Revert to auto"
deletes that override.

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

Final-lap finish is detected by `FinishStillDetector` (`race/finish.py`): on the
final lap the timer freezes on the total time with no gold/white flash, so a masked
frame-diff of the bright digit pixels stays still. Tunables are class constants
(not config keys): `STILL_SECONDS=2.5`, `DIFF_THRESHOLD=8.0`, `BRIGHT_THRESHOLD=175`.
The old position-ROI scan (`finish_match_threshold` / `finish_confirm_frames`) is
disabled but kept in code.

## Mushroom Tracking

Templates are grayscale crops of `old_assets/*mush.png` (matched with `search_pad`,
not binary). The ROI is read from the `mushroom_roi` config key.

| Key | Default | Description |
|---|---|---|
| `mushroom_match_threshold` | `0.55` | Min NCC score for mushroom count (grayscale; may need re-tuning) |
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

## Capture normalization

Per-channel `gain` + `offset` + shared `gamma` LUT applied to every frame
after resize, before any detector sees it.  Identity transform by default;
auto-fitted by the calibration wizard against up to 7 shipped reference frames
(`images/calibration/switch_hdr_test_1.png` … `..._7.png`, one per Switch HDR
test pattern) or manually tuned via sliders.

Patches are sampled from a fixed sub-region of every frame —
`(482, 162)` size `956×532` — covering just the test pattern itself and
ignoring the surrounding system UI chrome, which varies across capture cards
and locales.  See `PATTERN_ROI` and `DEFAULT_PATCHES` in
`mkw_tracker/utils/calibrate.py`.

Capture fresh references from the current capture-card setup with the dev
script — it accepts any number of slots and captures them sequentially in one
preview window:

```
python scripts/capture_calibration_ref.py 1 2 3 4 5 6 7
```

See `mkw_tracker/utils/normalize.py` and `mkw_tracker/utils/calibrate.py`.

| Key | Default | Description |
|---|---|---|
| `calib_enabled`  | `1`   | `1` apply the LUT to every frame, `0` pass-through (identity) |
| `calib_gain_r`   | `1.0` | Red-channel multiplier (typical range 0.5–2.0) |
| `calib_gain_g`   | `1.0` | Green-channel multiplier |
| `calib_gain_b`   | `1.0` | Blue-channel multiplier |
| `calib_offset_r` | `0`   | Red-channel offset in pixel units (range -100..+100) |
| `calib_offset_g` | `0`   | Green-channel offset |
| `calib_offset_b` | `0`   | Blue-channel offset |
| `calib_gamma`    | `1.0` | Shared gamma exponent applied before gain/offset (range 0.5–2.0) |
