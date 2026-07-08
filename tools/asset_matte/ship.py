"""Move one clip's matte artifacts from a local scratch dir to the shared output dir.

The second box mattes to a local SSD, then ships each finished clip's <name>__* set
(the <seg>_frames/ dirs + _loop/_checker.webp) to the share and deletes the local copy,
so its disk never accumulates. Idempotent: a same-named target (e.g. from an interrupted
copy) is removed first, so a re-ship after a crash cleanly overwrites. GPU-free.
"""
import glob
import os
import shutil


def ship_clip(out_matte_dir, ship_matte_dir, name):
    """Move every <name>__* artifact from out_matte_dir into ship_matte_dir, overwriting
    same-named targets first. Returns the number of artifacts moved."""
    os.makedirs(ship_matte_dir, exist_ok=True)
    moved = 0
    for src in glob.glob(os.path.join(out_matte_dir, name + "__*")):
        dst = os.path.join(ship_matte_dir, os.path.basename(src))
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        elif os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        shutil.move(src, dst)
        moved += 1
    return moved
