"""Tests for the template-source capture tool's pure logic.

Covers the two units that hold all the behaviour (no camera/cv2 window needed):
- NameResolver: detected display name -> on-disk template filename.
- CaptureGate:  auto-capture gating (confidence + hold), global dedup, skip.
- Resume-from-disk priming so a prior session's captures are not re-grabbed.
"""
import os


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"")


def _make_images_root(tmp_path, lang="en_uk", files=None):
    """Create empty images/<cat>/<lang>/<base>.png files. files: {cat: [base, ...]}."""
    root = tmp_path / "images"
    for cat, bases in (files or {}).items():
        for base in bases:
            _touch(str(root / cat / lang / f"{base}.png"))
    return str(root)


def _resolver(tmp_path, files, lang="en_uk"):
    from mkw_tracker.tools.capture_sources import NameResolver
    return NameResolver(lang, _make_images_root(tmp_path, lang=lang, files=files))


def _state(**kw):
    from mkw_tracker.detection.selection import SelectionState
    return SelectionState(**kw)


def _gate(tmp_path, files, **kw):
    from mkw_tracker.tools.capture_sources import CaptureGate
    return CaptureGate(_resolver(tmp_path, files), **kw)


# ---------------------------------------------------------------------------
# NameResolver
# ---------------------------------------------------------------------------

def test_resolver_maps_display_names_to_template_filenames(tmp_path):
    r = _resolver(tmp_path, {
        "costumes":   ["all_terrain", "pro_racer"],
        "characters": ["baby_daisy", "chargin_chuck"],
    })
    assert r.resolve("costumes", "All-Terrain") == "all_terrain"
    assert r.resolve("costumes", "Pro Racer") == "pro_racer"
    assert r.resolve("characters", "Baby Daisy") == "baby_daisy"
    assert r.resolve("characters", "Chargin' Chuck") == "chargin_chuck"


def test_resolver_known_returns_base_filenames(tmp_path):
    r = _resolver(tmp_path, {"costumes": ["all_terrain", "pro_racer"]})
    assert r.known("costumes") == {"all_terrain", "pro_racer"}
    assert r.known("karts") == set()


def test_resolver_excludes_tight_caches(tmp_path):
    root = _make_images_root(tmp_path, files={"karts": ["pipe_frame"]})
    _touch(os.path.join(root, "karts", "en_uk", "pipe_frame_tight.png"))
    from mkw_tracker.tools.capture_sources import NameResolver
    assert NameResolver("en_uk", root).known("karts") == {"pipe_frame"}


def test_resolver_slug_fallback_for_unknown_name(tmp_path):
    r = _resolver(tmp_path, {"karts": [], "courses": []})
    assert r.resolve("karts", "Mystery Kart") == "mystery_kart"
    assert r.resolve("courses", "Mario Bros. Circuit") == "mario_bros_circuit"


# ---------------------------------------------------------------------------
# CaptureGate
# ---------------------------------------------------------------------------

def test_gate_fires_after_hold_at_high_conf(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=3)
    st = _state(character="Mario", character_conf=0.9)
    assert gate.observe(Screen.CHARACTER_SELECT, st) == []
    assert gate.observe(Screen.CHARACTER_SELECT, st) == []
    assert gate.observe(Screen.CHARACTER_SELECT, st) == [("characters", "mario")]
    assert gate.observe(Screen.CHARACTER_SELECT, st) == []   # already captured
    assert "mario" in gate.captured["characters"]


def test_gate_does_not_fire_below_min_conf(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=3)
    st = _state(character="Mario", character_conf=0.7)
    for _ in range(5):
        assert gate.observe(Screen.CHARACTER_SELECT, st) == []
    assert gate.captured["characters"] == set()


def test_gate_resets_streak_when_name_changes(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario", "luigi"]}, min_conf=0.8, hold=3)
    mario = _state(character="Mario", character_conf=0.9)
    luigi = _state(character="Luigi", character_conf=0.9)
    gate.observe(Screen.CHARACTER_SELECT, mario)   # mario streak 1
    gate.observe(Screen.CHARACTER_SELECT, mario)   # mario streak 2
    gate.observe(Screen.CHARACTER_SELECT, luigi)   # reset -> luigi streak 1
    assert gate.captured["characters"] == set()
    gate.observe(Screen.CHARACTER_SELECT, luigi)   # luigi streak 2
    assert gate.observe(Screen.CHARACTER_SELECT, luigi) == [("characters", "luigi")]


def test_gate_captures_character_and_costume_together(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path,
                 {"characters": ["mario"], "costumes": ["pro_racer"]},
                 min_conf=0.8, hold=2)
    st = _state(character="Mario", character_conf=0.95,
                costume="Pro Racer", costume_conf=0.9)
    gate.observe(Screen.CHARACTER_SELECT, st)
    fired = gate.observe(Screen.CHARACTER_SELECT, st)
    assert set(fired) == {("characters", "mario"), ("costumes", "pro_racer")}


def test_gate_costume_deduped_across_characters(tmp_path):
    """Core requirement: one screenshot per costume, regardless of character."""
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path,
                 {"characters": ["mario", "luigi"], "costumes": ["pro_racer"]},
                 min_conf=0.8, hold=2)
    mario = _state(character="Mario", character_conf=0.95,
                   costume="Pro Racer", costume_conf=0.9)
    gate.observe(Screen.CHARACTER_SELECT, mario)
    first = gate.observe(Screen.CHARACTER_SELECT, mario)
    assert set(first) == {("characters", "mario"), ("costumes", "pro_racer")}

    luigi = _state(character="Luigi", character_conf=0.95,
                   costume="Pro Racer", costume_conf=0.9)
    gate.observe(Screen.CHARACTER_SELECT, luigi)
    second = gate.observe(Screen.CHARACTER_SELECT, luigi)
    assert second == [("characters", "luigi")]   # pro_racer NOT re-captured


def test_gate_skip_blocks_capture_and_remaining(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"courses": ["rainbow_road", "crown_city"]},
                 min_conf=0.8, hold=2)
    gate.skip("courses", "rainbow_road")
    st = _state(course="Rainbow Road", course_conf=0.99)
    for _ in range(5):
        assert gate.observe(Screen.COURSE_SELECT, st) == []
    assert gate.remaining("courses") == {"crown_city"}


def test_gate_remaining_reflects_captured_and_skipped(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario", "luigi"]}, min_conf=0.8, hold=1)
    assert gate.remaining("characters") == {"mario", "luigi"}
    gate.observe(Screen.CHARACTER_SELECT, _state(character="Mario", character_conf=0.9))
    gate.skip("characters", "luigi")
    assert gate.remaining("characters") == set()


def test_gate_mark_captured_prevents_fire(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=2)
    gate.mark_captured("characters", "mario")
    st = _state(character="Mario", character_conf=0.95)
    for _ in range(4):
        assert gate.observe(Screen.CHARACTER_SELECT, st) == []


def test_gate_ignores_non_selection_screens(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    st = _state(character="Mario", character_conf=0.99)
    assert gate.observe(Screen.RACING, st) == []
    assert gate.captured["characters"] == set()


def test_gate_current_targets_reports_status(tmp_path):
    from mkw_tracker.detection.screen import Screen
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=1)
    st = _state(character="Mario", character_conf=0.9)
    assert gate.current_targets(Screen.CHARACTER_SELECT, st) == [
        ("characters", "mario", 0.9, "NEW")]
    gate.mark_captured("characters", "mario")
    assert gate.current_targets(Screen.CHARACTER_SELECT, st) == [
        ("characters", "mario", 0.9, "CAPTURED")]


# ---------------------------------------------------------------------------
# Resume from disk
# ---------------------------------------------------------------------------

def test_scan_existing_captures_lists_bases(tmp_path):
    from mkw_tracker.tools.capture_sources import scan_existing_captures
    out = tmp_path / "captures"
    _touch(str(out / "en_uk" / "characters" / "mario.png"))
    _touch(str(out / "en_uk" / "courses" / "rainbow_road.png"))
    found = scan_existing_captures(str(out), "en_uk")
    assert found["characters"] == {"mario"}
    assert found["courses"] == {"rainbow_road"}
    assert found["karts"] == set()


def test_prime_gate_from_disk_prevents_recapture(tmp_path):
    from mkw_tracker.detection.screen import Screen
    from mkw_tracker.tools.capture_sources import prime_gate_from_disk
    gate = _gate(tmp_path, {"characters": ["mario"]}, min_conf=0.8, hold=2)
    out = tmp_path / "captures"
    _touch(str(out / "en_uk" / "characters" / "mario.png"))
    prime_gate_from_disk(gate, str(out), "en_uk")
    st = _state(character="Mario", character_conf=0.95)
    for _ in range(4):
        assert gate.observe(Screen.CHARACTER_SELECT, st) == []
    assert "mario" in gate.captured["characters"]
