"""Tests for minimap_update_payload — Task 4.1 (TDD)."""
import json
import pytest

from mkw_tracker.ipc.protocol import minimap_update_payload
from mkw_tracker.minimap.tracker import MinimapState, MINIMAP_ROI


# MINIMAP_ROI = (x, y, w, h) = (1442, 251, 466, 796)
# state.cx / state.cy are already full-frame (the tracker adds the ROI origin
# inside _publish() before storing), so no offset arithmetic is needed here.


def test_minimap_payload_tracking_emits_full_frame_coords():
    """A 'tracking' state with a known cx/cy/radius produces the right payload."""
    st = MinimapState(
        cx=1500, cy=400,
        cx_smooth=1500.0, cy_smooth=400.0,
        radius=20,
        tracking=True,
        track_state="tracking",
    )
    p = minimap_update_payload(st, MINIMAP_ROI)
    assert p is not None
    assert p["type"] == "minimap_update"
    assert p["track_state"] == "tracking"
    assert p["cx"] == 1500
    assert p["cy"] == 400
    assert p["radius"] == 20
    # The per-map ROI is echoed back so the UI draws the correct box.
    assert p["roi"] == [MINIMAP_ROI[0], MINIMAP_ROI[1], MINIMAP_ROI[2], MINIMAP_ROI[3]]


def test_minimap_payload_ring_only_is_emitted():
    """ring_only is a usable lock — payload must not be None."""
    st = MinimapState(
        cx=1600, cy=500,
        cx_smooth=1600.0, cy_smooth=500.0,
        radius=18,
        tracking=True,
        track_state="ring_only",
    )
    p = minimap_update_payload(st, MINIMAP_ROI)
    assert p is not None
    assert p["track_state"] == "ring_only"
    assert p["cx"] == 1600
    assert p["cy"] == 500


def test_minimap_payload_idle_returns_none():
    """No lock in 'idle' state — payload must be None."""
    st = MinimapState(track_state="idle", tracking=False)
    assert minimap_update_payload(st, MINIMAP_ROI) is None


def test_minimap_payload_lost_returns_none():
    """Lost state (tracking=False) — payload must be None."""
    st = MinimapState(
        cx=1500, cy=400,
        tracking=False,
        track_state="lost",
    )
    assert minimap_update_payload(st, MINIMAP_ROI) is None


def test_minimap_payload_none_coords_returns_none():
    """tracking=True but cx/cy still None (edge case at seed) — return None."""
    st = MinimapState(
        cx=None, cy=None,
        tracking=True,
        track_state="tracking",
    )
    assert minimap_update_payload(st, MINIMAP_ROI) is None


def test_minimap_payload_serialises_to_json():
    """The payload must be JSON-serialisable (all fields are plain Python types)."""
    st = MinimapState(
        cx=1550, cy=350,
        cx_smooth=1550.0, cy_smooth=350.0,
        radius=22,
        tracking=True,
        track_state="tracking",
    )
    p = minimap_update_payload(st, MINIMAP_ROI)
    assert p is not None
    # Should not raise
    serialised = json.dumps(p)
    decoded = json.loads(serialised)
    assert decoded["type"] == "minimap_update"
    assert isinstance(decoded["cx"], int)
    assert isinstance(decoded["cy"], int)
    assert isinstance(decoded["radius"], int)
