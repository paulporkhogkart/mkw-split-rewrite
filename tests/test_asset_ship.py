import os

import ship


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _seed_clip(matte, name):
    """Create the artifacts matte_blankplate writes for one clip's segments."""
    for seg in ("spawn", "idle", "flourish"):
        _touch(os.path.join(matte, f"{name}__{seg}_frames", "000.png"))
        _touch(os.path.join(matte, f"{name}__{seg}_loop.webp"))
        _touch(os.path.join(matte, f"{name}__{seg}_checker.webp"))


def test_ship_moves_only_this_clip(tmp_path):
    out = str(tmp_path / "out" / "matte")
    dst = str(tmp_path / "share" / "matte")
    _seed_clip(out, "mario__base__standard")
    _seed_clip(out, "mario__base__standardx")            # decoy: shares a prefix, must NOT move
    n = ship.ship_clip(out, dst, "mario__base__standard")
    assert n == 9                                         # 3 segs x (frames + loop + checker)
    # target has this clip, source no longer does
    assert os.path.isdir(os.path.join(dst, "mario__base__standard__idle_frames"))
    assert not os.path.exists(os.path.join(out, "mario__base__standard__idle_frames"))
    # decoy untouched in source, absent from target
    assert os.path.isdir(os.path.join(out, "mario__base__standardx__idle_frames"))
    assert not os.path.exists(os.path.join(dst, "mario__base__standardx__idle_frames"))


def test_ship_is_idempotent_overwrites_partial_target(tmp_path):
    out = str(tmp_path / "out" / "matte")
    dst = str(tmp_path / "share" / "matte")
    # a stale/partial target already on the share (e.g. an interrupted previous ship)
    _touch(os.path.join(dst, "clip__idle_frames", "999_partial.png"))
    _seed_clip(out, "clip")
    ship.ship_clip(out, dst, "clip")
    # target now reflects the fresh source only (partial file gone)
    assert os.path.exists(os.path.join(dst, "clip__idle_frames", "000.png"))
    assert not os.path.exists(os.path.join(dst, "clip__idle_frames", "999_partial.png"))
    assert not os.path.exists(os.path.join(out, "clip__idle_frames"))
