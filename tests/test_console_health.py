from health import HealthModel


def test_heartbeat_sets_fps_and_screen():
    h = HealthModel()
    h.apply({"type": "heartbeat", "fps": 60.0, "screen": "KART_SELECT", "tracking": True})
    snap = h.snapshot(now=5.0)
    assert snap["fps"] == 60.0 and snap["screen"] == "KART_SELECT"


def test_selection_update_sets_names():
    h = HealthModel()
    h.apply({"type": "selection_update", "character": "Mario", "costume": "Touring", "kart": "Pipe Frame"})
    snap = h.snapshot(now=0.0)
    assert (snap["character"], snap["costume"], snap["kart"]) == ("Mario", "Touring", "Pipe Frame")


def test_screen_change_overrides_screen():
    h = HealthModel()
    h.apply({"type": "screen_change", "from": "KART_SELECT", "to": "COURSE_SELECT"})
    assert h.snapshot(now=0.0)["screen"] == "COURSE_SELECT"


def test_clip_done_drives_last_clip_age():
    h = HealthModel()
    h.apply({"type": "clip_done", "item": "mario__base"}, now=100.0)
    assert h.snapshot(now=107.0)["last_clip_age"] == 7.0


def test_no_clip_yet_age_none():
    assert HealthModel().snapshot(now=10.0)["last_clip_age"] is None


def test_set_controller():
    h = HealthModel(); h.set_controller(True, "E0:EF:BF:03:74:19")
    snap = h.snapshot(now=0.0)
    assert snap["controller"] is True and snap["mac"].startswith("E0:")
