import hashlib
import json
import os
import tarfile

from tools.asset_matte import pack_shards as ps


def _mini_pack(root):
    chips = os.path.join(root, "chips")
    os.makedirs(chips)
    names = ["a__base__k1__idle.webp", "a__base__k1__idle__sil_k0.png",
             "a__base__k2__idle.webp", "b__base__idle.webp"]
    for n in names:
        open(os.path.join(chips, n), "wb").write(n.encode())
    json.dump({"version": 1, "combos": {}}, open(os.path.join(chips, "manifest.json"), "w"))
    return chips


def test_char_of():
    assert ps.char_of("baby_daisy__base__b_dasher__idle.webp") == "baby_daisy"
    assert ps.char_of("mario__base__flourish__sil_k2.png") == "mario"


def test_build_shards_layout_and_lock(tmp_path):
    chips = _mini_pack(str(tmp_path))
    rel = os.path.join(str(tmp_path), "release")
    files = ps.build_shards(chips, rel, "chips-v9", "https://example.test/dl/chips-v9")
    names = [n for n, _ in files]
    assert names[0] == "chips-manifest.json"
    assert set(names) == {"chips-manifest.json", "chips-a.tar", "chips-b.tar"}
    with tarfile.open(os.path.join(rel, "chips-a.tar")) as t:
        members = t.getnames()
    assert sorted(members) == ["a__base__k1__idle.webp", "a__base__k1__idle__sil_k0.png",
                               "a__base__k2__idle.webp"]
    lock = open(os.path.join(rel, "chips.lock")).read().splitlines()
    assert lock[0] == "tag chips-v9"
    assert lock[1] == "base https://example.test/dl/chips-v9"
    for line, (name, sha) in zip(lock[2:], files):
        assert line == f"{sha}  {name}"
    # shas are real
    h = hashlib.sha256(open(os.path.join(rel, "chips-b.tar"), "rb").read()).hexdigest()
    assert (f"{h}  chips-b.tar") in lock
