"""NO_SIGNAL screen detection: presets, device-name auto-selection, tell match,
universal-candidate wiring, and region swapping."""
import os
import cv2
from mkw_tracker.detection.screen import (
    Screen, TELLS, TRANSITIONS, GRAPH_NODE_SHOTS, ScreenDetector, detect_tell,
    NO_SIGNAL_PRESETS, NO_SIGNAL_DEVICE_HINTS, auto_nosignal_preset,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _img(name):
    img = cv2.imread(os.path.join(FIXTURES, name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    assert img.shape[:2] == (1080, 1920), f"{name} not 1080p"
    return img


def test_no_signal_enum_and_graph_node_exist():
    assert Screen.NO_SIGNAL.name == "NO_SIGNAL"
    assert GRAPH_NODE_SHOTS[Screen.NO_SIGNAL] == "nosignal.png"


def test_presets_are_well_formed():
    for key in ("elgato", "ugreen"):
        p = NO_SIGNAL_PRESETS[key]
        assert p["image_path"].endswith(f"nosignal_{key}.png")
        assert len(p["roi"]) == 4 and p["roi"][2] > p["roi"][0] and p["roi"][3] > p["roi"][1]


def test_auto_nosignal_preset_matches_brand_substring():
    assert auto_nosignal_preset("Elgato 4K X") == "elgato"
    assert auto_nosignal_preset("UGREEN 25773") == "ugreen"
    assert auto_nosignal_preset("elgato 4k x") == "elgato"     # case-insensitive
    assert auto_nosignal_preset("Some USB Capture") is None
    assert auto_nosignal_preset("") is None
