"""Segment one recorded clip into spawn-in / idle-loop / flourish frame spans.

Pure span math here; the extract+write wrapper (segment_file) uses loop_probe
and cv2 to turn one recorded clip + its events.json into sub-clips.
"""
import json
import os

import cv2

from mkw_tracker.tools import loop_probe

# Character-select hero render region, 1080p reference coords (x1, y1, x2, y2).
HERO_ROI = (1075, 30, 1800, 845)
# Placeholder until Task 9 measures the real kart-select crop.
KART_HERO_ROI = HERO_ROI


def _is_kart(item: str) -> bool:
    """Return True when item is a kart (filename has >= 2 double-underscore separators)."""
    return item.count("__") >= 2


def _find_idle_loop(mkv_path, roi, fps, idle_end_t):
    """Probe the idle-animation loop in the clip and return (loop_start, loop_len) in frames.

    loop_start is always 0 within the idle span (seam-search refinement out of scope).
    """
    f_eff, F = loop_probe.load_features(mkv_path, roi_1080=roi, settle=0.0,
                                        max_seconds=idle_end_t)
    lags, scores = loop_probe.autocorr_by_lag(F, max(1, int(0.5 * f_eff)), int(8 * f_eff))
    best, _conf, _top = loop_probe.find_period(lags, scores)
    loop_len = best or int(1.3 * f_eff)
    return 0, loop_len   # loop_start=0 within the idle span (refine if needed)


def _write_span(mkv_path, start, end, out_path):
    """Extract frames [start, end) from mkv_path and write them to out_path (mp4)."""
    cap = cv2.VideoCapture(mkv_path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if start <= i < end:
            vw.write(fr)
        i += 1
    cap.release()
    vw.release()
    return out_path


def segment_file(mkv_path, events_path, out_dir):
    """Turn one recorded clip + its events.json into spawn-in/idle-loop/flourish sub-clips.

    Args:
        mkv_path: path to the recorded clip (any format cv2 can decode).
        events_path: path to the events JSON with keys
                     {fps, swap_t, flourish_t, flourish_end_t, duration_t}.
        out_dir: directory to write output sub-clips into.

    Returns:
        dict mapping segment names ("spawn_in", "idle_loop", "flourish") to
        absolute file paths of the written mp4 sub-clips.
    """
    ev = json.loads(open(events_path, encoding="utf-8").read())
    item = ev.get("item") or os.path.splitext(os.path.basename(mkv_path))[0]
    roi = KART_HERO_ROI if _is_kart(item) else HERO_ROI
    ls, ll = _find_idle_loop(mkv_path, roi, ev["fps"], ev["flourish_t"])
    spans = segment_spans(ev, ls, ll)
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for name, (s, e) in spans.items():
        out[name] = _write_span(mkv_path, s, e,
                                os.path.join(out_dir, f"{item}__{name}.mp4"))
    return out


def segment_spans(events: dict, loop_start_frame: int, loop_len_frames: int) -> dict:
    """Compute frame spans for spawn-in, idle-loop, and flourish segments.

    Args:
        events: dict with keys {fps, swap_t, flourish_t, flourish_end_t, duration_t}.
                swap_t is None for characters (no spawn-in segment).
        loop_start_frame: frame index where the idle loop begins.
        loop_len_frames: duration of the idle loop in frames.

    Returns:
        dict with string keys (among "spawn_in", "idle_loop", "flourish") mapping to
        (start_frame, end_frame) half-open ranges. "spawn_in" is omitted when swap_t is None.
    """
    fps = events["fps"]
    spans = {}
    if events.get("swap_t") is not None:
        spans["spawn_in"] = (round(events["swap_t"] * fps), loop_start_frame)
    spans["idle_loop"] = (loop_start_frame, loop_start_frame + loop_len_frames)
    spans["flourish"] = (round(events["flourish_t"] * fps),
                         round(events["flourish_end_t"] * fps))
    return spans
