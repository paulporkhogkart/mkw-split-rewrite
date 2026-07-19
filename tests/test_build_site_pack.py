"""Batch CLI over a synthetic mini master tree (no D:\\ dependency)."""
import json
import os

import numpy as np
from PIL import Image

from tools.asset_matte import build_site_pack as bsp


def _write_frames(d, n, w=64, h=68):
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        a = np.zeros((h, w, 4), np.uint8)
        a[10:40, 10 + i % 8:40 + i % 8] = (180, 60, 60, 255)
        Image.fromarray(a, "RGBA").save(os.path.join(d, f"{i:03d}.png"))


def _mini_masters(root):
    m = {
        "a__base__k1": {"status": "done", "kart": True, "idle_resume": 9,
                        "secs": 1.0, "segments": {"spawn": 6, "idle": 12, "flourish": 8}},
        "b__base": {"status": "done", "kart": False, "idle_resume": 0,
                    "secs": 1.0, "segments": {"idle": 10, "flourish": 6}},
        "c__base": {"status": "error", "kart": False, "idle_resume": 0,
                    "secs": 1.0, "segments": {"idle": 10}},
    }
    matte = os.path.join(root, "matte")
    for name, e in m.items():
        if e["status"] != "done":
            continue
        for anim, n in e["segments"].items():
            _write_frames(os.path.join(matte, f"{name}__{anim}_frames"), n)
    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(m, f)
    return root


def test_plan_combos_skips_non_done_and_missing_dirs(tmp_path):
    src = _mini_masters(str(tmp_path))
    import shutil
    shutil.rmtree(os.path.join(src, "matte", "b__base__flourish_frames"))
    plan = bsp.plan_combos(src)
    assert set(plan) == {"a__base__k1", "b__base"}
    assert plan["a__base__k1"] == ["spawn", "idle", "flourish"]
    assert plan["b__base"] == ["idle"]  # missing flourish dir skipped with a warning


def test_full_build_outputs_and_manifest(tmp_path):
    src = _mini_masters(str(tmp_path / "masters"))
    out = str(tmp_path / "pack")
    rc = bsp.main(["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
                   "--quality", "60", "--alpha-bits", "5", "--workers", "1"])
    assert rc == 0
    chips = os.path.join(out, "chips")
    for f in ["a__base__k1__idle.webp", "a__base__k1__spawn.webp",
              "a__base__k1__flourish.webp", "a__base__k1__idle__sil_k0.png",
              "b__base__idle.webp", "b__base__flourish__sil_k3.png"]:
        assert os.path.exists(os.path.join(chips, f)), f
    man = json.load(open(os.path.join(chips, "manifest.json")))
    assert man["fps"] == 30 and man["fw"] == 32 and man["fh"] == 34
    a = man["combos"]["a__base__k1"]
    assert a["kart"] is True
    assert a["anims"]["idle"]["frames"] == 6          # 12 masters @30fps
    assert a["idle_resume"] == 4                      # 9 // 2
    assert man["combos"]["b__base"]["kart"] is False


def test_resume_skips_done_combos(tmp_path, capsys):
    src = _mini_masters(str(tmp_path / "masters"))
    out = str(tmp_path / "pack")
    args = ["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
            "--quality", "60", "--alpha-bits", "5", "--workers", "1"]
    assert bsp.main(args) == 0
    stamp = os.path.getmtime(os.path.join(out, "chips", "a__base__k1__idle.webp"))
    assert bsp.main(args) == 0                        # second run: all skipped
    assert os.path.getmtime(os.path.join(out, "chips", "a__base__k1__idle.webp")) == stamp
    book = json.load(open(os.path.join(out, "book.json")))
    assert book["a__base__k1"]["done"] is True


def test_stop_file_stops_between_combos_and_keeps_book(tmp_path, capsys):
    src = _mini_masters(str(tmp_path / "masters"))
    out = str(tmp_path / "pack")
    stop = str(tmp_path / ".stop")
    with open(stop, "w") as f:
        f.write("stop")
    rc = bsp.main(["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
                   "--quality", "60", "--alpha-bits", "5", "--workers", "1",
                   "--stop-file", stop])
    assert rc == 0
    assert "stopped by stop-file" in capsys.readouterr().out
    book = json.load(open(os.path.join(out, "book.json")))
    done = sum(1 for v in book.values() if v.get("done"))
    assert done == 1          # stop is checked BETWEEN combos: exactly one completed
    # resume without the stop file finishes the rest
    os.remove(stop)
    assert bsp.main(["--src", src, "--out", out, "--scale", "0.5", "--fps", "30",
                     "--quality", "60", "--alpha-bits", "5", "--workers", "1",
                     "--stop-file", stop]) == 0
    book = json.load(open(os.path.join(out, "book.json")))
    assert sum(1 for v in book.values() if v.get("done")) == 2


def test_progress_line_per_combo(tmp_path, capsys):
    src = _mini_masters(str(tmp_path / "masters"))
    rc = bsp.main(["--src", src, "--out", str(tmp_path / "pack"), "--scale", "0.5",
                   "--fps", "30", "--quality", "60", "--alpha-bits", "5", "--workers", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROGRESS 1/2 " in out and "PROGRESS 2/2 " in out
