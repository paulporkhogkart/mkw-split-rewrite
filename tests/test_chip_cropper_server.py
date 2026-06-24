def test_list_captures_enumerates_categories(tmp_path):
    from scripts.chip_cropper_server import list_captures
    root = tmp_path / "captures_sdr" / "en_uk"
    (root / "combos").mkdir(parents=True)
    (root / "karts").mkdir(parents=True)
    (root / "combos" / "mario__base.png").write_bytes(b"")
    (root / "karts" / "baby_blooper.png").write_bytes(b"")
    items = list_captures(str(tmp_path / "captures_sdr"), "en_uk")
    names = {(i["category"], i["name"]) for i in items}
    assert ("combos", "mario__base") in names
    assert ("karts", "baby_blooper") in names
    combo = next(i for i in items if i["name"] == "mario__base")
    assert combo["url"] == "/captures/en_uk/combos/mario__base.png"


def test_load_crops_returns_skeleton_when_absent(tmp_path):
    from scripts.chip_cropper_server import load_crops
    spec = load_crops(str(tmp_path / "nope.json"))
    assert spec["combos"] == {} and spec["karts"] == {} and spec["courses"] == {}
    assert "meta" in spec and "defaults" in spec


def test_save_then_load_roundtrips(tmp_path):
    from scripts.chip_cropper_server import save_crops, load_crops
    path = tmp_path / "chips.crops.json"
    data = {"meta": {"chip_px": 96}, "defaults": {"character": {}, "course": None},
            "combos": {"mario__base": {"x": 1, "y": 2, "w": 3, "h": 4}},
            "karts": {}, "courses": {}}
    save_crops(str(path), data)
    assert load_crops(str(path))["combos"]["mario__base"] == {"x": 1, "y": 2, "w": 3, "h": 4}
