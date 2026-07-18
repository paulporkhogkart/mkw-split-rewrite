import json
import os

from tools.asset_matte import build_ab_lab as ab


def test_variants_change_one_thing():
    v = {x["id"]: x for x in ab.VARIANTS}
    cand = v["candidate"]
    assert cand == {"id": "candidate", "scale": 0.2, "fps": 60, "quality": 60, "alpha_bits": 5}
    for vid, x in v.items():
        if vid == "candidate":
            continue
        diffs = [k for k in ("scale", "fps", "quality", "alpha_bits") if x[k] != cand[k]]
        assert len(diffs) == 1, f"{vid} changes {diffs}"


def test_html_embeds_stepper_and_variants(tmp_path):
    html = ab.render_html(
        combos=["a__base__k1"], variants=ab.VARIANTS,
        manifests={"candidate": {"fps": 30, "fw": 205, "fh": 216, "combos": {"a__base__k1": {
            "kart": True, "idle_resume": 51,
            "anims": {"idle": {"frames": 60, "cols": 8, "rows": 8}}}}}},
        sizes={("candidate", "a__base__k1", "idle"): 190000},
        stepper_js="/* STEPPER */ export function bgPos(){}",
    )
    assert "STEPPER" in html and "export " not in html   # inlined, exports stripped
    assert "candidate" in html and "a__base__k1__idle.webp" in html
    assert "drop-shadow(1px 0 0 #101114)" in html        # ink ring on
