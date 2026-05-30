import numpy as np
from mkw_tracker.detection.screen import Region, score_region


def _solid(w, h, val):
    return np.full((h, w), val, np.uint8)


def test_template_region_scores_high_on_match():
    frame = _solid(40, 30, 200)
    tmpl = _solid(20, 14, 200)
    r = Region(kind="template", roi=(5, 5, 25, 19), grayscale=True, search_pad=2)
    r.template = tmpl
    assert score_region(frame, r, 0.9) >= 0.9


def test_dark_loading_region_scores_one_when_dark_and_icon_bright():
    frame = np.zeros((1080, 1920), np.uint8)        # dark everywhere
    frame[930:1020, 1720:1850] = 220                # bright mascot in icon_roi
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 1.0


def test_dark_loading_region_scores_zero_without_icon():
    frame = np.zeros((1080, 1920), np.uint8)        # dark, no bright icon
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 0.0


from mkw_tracker.detection.screen import Tell, Screen, detect_tell


def _match_region(score_val):
    """Return a region that matches (score≥0.9) or doesn't match (<0.9) a 10×10 solid-200 frame.

    Matching: solid-200 template on solid-200 frame → TM_CCOEFF_NORMED ≈ 1.
    Non-matching: alternating-row template (0/255) has a mean very different from
    the 200-flat frame, so TM_CCOEFF_NORMED scores well below 0.9.
    """
    r = Region(kind="template", roi=(0, 0, 10, 10), grayscale=True, search_pad=0)
    if score_val:
        r.template = _solid(10, 10, 200)
    else:
        # Alternating rows 0/255: non-uniform → low CCOEFF vs a flat-200 frame
        rows = [np.full((1, 10), 0 if i % 2 == 0 else 255, np.uint8) for i in range(10)]
        r.template = np.vstack(rows)
    return r


def test_single_group_single_region_and():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.TITLE, groups=[[_match_region(True)]])
    matched, score = detect_tell(frame, t)
    assert matched and score >= 0.9


def test_two_groups_and_fails_when_one_group_fails():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.RACING,
             groups=[[_match_region(True)], [_match_region(False)]])
    matched, score = detect_tell(frame, t)
    assert not matched
    assert score < 0.9


def test_or_within_group_passes_when_any_region_matches():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.HOME,
             groups=[[_match_region(False), _match_region(True)]])
    matched, score = detect_tell(frame, t)
    assert matched and score >= 0.9


def test_empty_groups_never_matches():
    frame = _solid(10, 10, 200)
    t = Tell(screen=Screen.TITLE, groups=[])
    assert detect_tell(frame, t) == (False, 0.0)


from mkw_tracker.detection.screen import ScreenDetector


def test_serialize_get_tells_config_round_trip():
    d = ScreenDetector()
    cfg = {e["screen"]: e for e in d.get_tells_config()}
    racing = cfg["RACING"]
    assert len(racing["groups"]) == 2                 # coin AND flag
    assert len(racing["groups"][0]) == 1
    assert racing["groups"][0][0]["kind"] == "template"
    assert "aliases" in racing                        # GHOST, UNKNOWN_RACE_ACTIVE
    home = cfg["HOME"]
    assert len(home["groups"]) == 1 and len(home["groups"][0]) == 2  # OR


def test_add_and_remove_group_propagates_to_aliases():
    d = ScreenDetector()
    d.add_group("RACING", roi=[10, 10, 50, 50])
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    ghost  = next(e for e in d.get_tells_config() if e["screen"] == "GHOST")
    assert len(racing["groups"]) == 3
    assert len(ghost["groups"]) == 3                  # alias kept in sync
    d.remove_group("RACING", 2)
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    assert len(racing["groups"]) == 2


def test_add_region_adds_or_alternative():
    d = ScreenDetector()
    d.add_region("HOME", group=0, roi=[1, 2, 3, 4])
    home = next(e for e in d.get_tells_config() if e["screen"] == "HOME")
    assert len(home["groups"][0]) == 3


def test_update_region_sets_roi_and_thresh():
    d = ScreenDetector()
    d.update_region("TITLE", group=0, region=0, roi=[1, 2, 30, 40], thresh=88)
    title = next(e for e in d.get_tells_config() if e["screen"] == "TITLE")
    assert title["groups"][0][0]["roi"] == [1, 2, 30, 40]
    assert title["groups"][0][0]["thresh"] == 88


def test_reset_tell_restores_default_and_aliases():
    d = ScreenDetector()
    d.update_region("RACING", group=0, region=0, roi=[1, 2, 3, 4])
    d.add_group("RACING", roi=[5, 6, 7, 8])
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    assert len(racing["groups"]) == 3 and racing["groups"][0][0]["roi"] == [1, 2, 3, 4]
    d.reset_tell("RACING")
    racing = next(e for e in d.get_tells_config() if e["screen"] == "RACING")
    ghost  = next(e for e in d.get_tells_config() if e["screen"] == "GHOST")
    assert len(racing["groups"]) == 2
    assert racing["groups"][0][0]["roi"] == [78, 987, 96, 1015]
    assert len(ghost["groups"]) == 2 and ghost["groups"][0][0]["roi"] == [78, 987, 96, 1015]


def test_serialize_groups_round_trips_through_blob():
    from mkw_tracker.database.tell_repo import serialize_groups, groups_from_blob
    d = ScreenDetector()
    tell = d._tells_by_screen[Screen.RACING]
    blob = serialize_groups(tell)
    rebuilt = groups_from_blob(blob)
    assert len(rebuilt) == len(tell.groups) == 2
    assert rebuilt[0][0].roi == tell.groups[0][0].roi
    assert rebuilt[0][0].image_path == tell.groups[0][0].image_path
    # dark_loading icon_roi survives the round-trip
    reset = groups_from_blob(serialize_groups(d._tells_by_screen[Screen.RESET]))
    assert reset[0][0].kind == "dark_loading"
    assert reset[0][0].icon_roi == d._tells_by_screen[Screen.RESET].groups[0][0].icon_roi
