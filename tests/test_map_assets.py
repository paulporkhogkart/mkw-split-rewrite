import importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "web" / "public" / "map"


def _canon():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def _manifest():
    return json.loads((MAP / "manifest.json").read_text())


def test_manifest_is_30_unique_canonical_courses():
    canon = _canon()
    slugs = [c["slug"] for c in _manifest()["courses"]]
    assert len(slugs) == len(canon)
    assert len(set(slugs)) == len(canon)
    assert set(slugs) == set(canon)


def test_every_course_has_a_sprite_and_sane_rects():
    for c in _manifest()["courses"]:
        assert (MAP / "sprites" / f"{c['slug']}.png").exists(), c["slug"]
        for r in (c["hit"], c["spr"]):
            assert r["w"] > 0 and r["h"] > 0
            assert r["x"] + r["w"] <= 1.02 and r["y"] + r["h"] <= 1.02
            assert r["x"] >= -0.02 and r["y"] >= -0.02


def test_names_match_canonical_display_names():
    canon = _canon()
    for c in _manifest()["courses"]:
        assert c["name"] == canon[c["slug"]]
