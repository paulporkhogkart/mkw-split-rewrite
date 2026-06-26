"""Segment one recorded clip into spawn-in / idle-loop / flourish frame spans.

Pure span math here; the extract+write wrapper (Task 10) reuses extract_loop.
"""


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
