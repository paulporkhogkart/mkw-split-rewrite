"""Per-segment matte direction. KART SPAWN runs backward (its settled last frame is
pose-continuous with idle f0 — anchor disagreement 639-1558px vs 12k-22k for the mid-drop first
frame — so the drift end moves to the fast-motion drop-in and the spawn->idle handoff gets the
accurate end). KART FLOURISH runs split (fwd first part, bwd second part, seam-searched switch):
the pure-bwd tail holds see-through holes fully open (wario ring 0.00 vs the bidir crossfade's
0.10 ghost) and the switch pop (46-3728px, 3px at the seam-searched frame) sits an order of
magnitude below the spin's natural per-frame alpha motion (median 12.5k px). Chars + idle stay
forward-only."""
import numpy as np
import matte_matanyone as mm


# ── segment_direction rules ─────────────────────────────────────────────────────

def test_kart_spawn_is_backward(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_direction(True, "spawn") == "bwd"


def test_kart_flourish_is_split(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_direction(True, "flourish") == "split"


def test_kart_idle_is_forward(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_direction(True, "idle") == "fwd"


def test_char_segments_are_forward(monkeypatch):
    monkeypatch.delenv("MATTE_MATANYONE_BIDIR", raising=False)
    assert mm.segment_direction(False, "spawn") == "fwd"
    assert mm.segment_direction(False, "idle") == "fwd"
    assert mm.segment_direction(False, "flourish") == "fwd"


def test_env_1_forces_bidir_everywhere(monkeypatch):
    monkeypatch.setenv("MATTE_MATANYONE_BIDIR", "1")
    assert mm.segment_direction(False, "idle") == "bidir"
    assert mm.segment_direction(True, "flourish") == "bidir"


def test_env_0_forces_forward_everywhere(monkeypatch):
    monkeypatch.setenv("MATTE_MATANYONE_BIDIR", "0")
    assert mm.segment_direction(True, "flourish") == "fwd"
    assert mm.segment_direction(True, "spawn") == "fwd"


# ── split seam search + merge ───────────────────────────────────────────────────

def _seq(n, hole_from=None):
    """n frames of a 4x4 alpha; optionally punch a 2x2 hole from index `hole_from` on."""
    out = []
    for t in range(n):
        a = np.ones((4, 4), np.float32)
        if hole_from is not None and t >= hole_from:
            a[:2, :2] = 0.0
        out.append(a)
    return out


def test_split_seam_picks_min_disagreement_frame():
    # fwd and bwd agree everywhere except bwd has a hole from f6 on -> disagreement is 0
    # for t<6 and 4px after; the middle-third search (lo=4) must pick the earliest zero.
    fwd, bwd = _seq(12), _seq(12, hole_from=6)
    assert mm.split_seam(fwd, bwd) == 4


def test_split_seam_is_bounded_to_the_middle_third():
    # disagreement strictly decreasing over time -> unbounded argmin would be the LAST frame;
    # the seam must stay inside [n//3, n-n//3).
    n = 12
    fwd = _seq(n)
    bwd = []
    for t in range(n):
        a = np.ones((4, 4), np.float32)
        a.flat[: n - t] = 0.0                     # disagreement shrinks every frame
        bwd.append(a)
    assert mm.split_seam(fwd, bwd) < n - n // 3


def test_split_seam_tiny_segment_falls_back_to_midpoint():
    fwd, bwd = _seq(4), _seq(4)
    assert mm.split_seam(fwd, bwd) == 2


def test_merge_split_takes_fwd_then_bwd():
    fwd = [np.full((2, 2), 1.0, np.float32) for _ in range(6)]
    bwd = [np.full((2, 2), 0.0, np.float32) for _ in range(6)]
    out = mm.merge_split(fwd, bwd, 4)
    assert len(out) == 6
    assert np.allclose(out[3], 1.0) and np.allclose(out[4], 0.0)
