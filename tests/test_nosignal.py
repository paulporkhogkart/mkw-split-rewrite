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


def _nosignal_tell():
    """A detector instance loads every tell's template; return the NO_SIGNAL tell
    with its (Elgato-default) template populated."""
    d = ScreenDetector()
    return d._tells_by_screen[Screen.NO_SIGNAL]


def test_nosignal_tell_matches_elgato_frame():
    detected, score = detect_tell(_img("nosignal_elgato_frame.png"), _nosignal_tell())
    assert detected, f"Elgato no-signal should detect (score={score})"


def test_nosignal_tell_rejects_reset_and_racing():
    tell = _nosignal_tell()
    for name in ("reset_dev.png", "racing_dark_section.png"):
        detected, score = detect_tell(_img(name), tell)
        assert not detected, f"{name} must NOT detect as NO_SIGNAL (score={score})"


def test_set_nosignal_region_swaps_to_ugreen_and_matches():
    d = ScreenDetector()
    res = d.set_nosignal_region("ugreen")
    assert res is not None
    region = d._tells_by_screen[Screen.NO_SIGNAL].groups[0][0]
    assert region.roi == NO_SIGNAL_PRESETS["ugreen"]["roi"]
    assert region.image_path.endswith("nosignal_ugreen.png")
    detected, score = detect_tell(_img("nosignal_ugreen_frame.png"),
                                  d._tells_by_screen[Screen.NO_SIGNAL])
    assert detected, f"UGREEN no-signal should detect after swap (score={score})"


def test_set_nosignal_region_unknown_preset_returns_none():
    assert ScreenDetector().set_nosignal_region("bogus") is None


def test_nosignal_is_universal_candidate_without_mutating_transitions():
    d = ScreenDetector()
    d.current_screen = Screen.RACING
    assert Screen.NO_SIGNAL in d._candidate_screens()
    # The shared TRANSITIONS table must not be polluted by the augmentation.
    assert Screen.NO_SIGNAL not in TRANSITIONS[Screen.RACING]


def test_from_nosignal_rescans_unknown_set():
    d = ScreenDetector()
    d.current_screen = Screen.NO_SIGNAL
    assert d._candidate_screens() == set(TRANSITIONS[Screen.UNKNOWN])
