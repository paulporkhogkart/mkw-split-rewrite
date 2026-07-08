"""Cross-machine work coordination via one atomic file per clip in a shared dir.

The matte batch is embarrassingly parallel; the only shared mutable state is *who is
processing which clip*. A single JSON manifest can't serve that across machines (whole-file
read/modify/replace clobbers), so each clip is claimed with an atomic exclusive-create:

  <claims>/<name>.claim   created with O_CREAT|O_EXCL -> exactly one racer wins (NTFS/SMB2/3)
  <claims>/<name>.done    created after the clip's bytes are on the share

GPU-free (stdlib only), so it imports + tests without CUDA/rembg.
"""
import os
import socket
import time

_CLAIM = ".claim"
_DONE = ".done"


def default_machine_id():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def _claim_path(claims_dir, name):
    return os.path.join(claims_dir, name + _CLAIM)


def _done_path(claims_dir, name):
    return os.path.join(claims_dir, name + _DONE)


def try_claim(claims_dir, name, machine_id):
    """Atomically claim `name`. True if this caller won it, False if already claimed."""
    os.makedirs(claims_dir, exist_ok=True)
    try:
        fd = os.open(_claim_path(claims_dir, name), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"{machine_id} {time.time():.0f}".encode())
    finally:
        os.close(fd)
    return True


def mark_done(claims_dir, name):
    """Mark a claimed clip finished (bytes are on the share)."""
    open(_done_path(claims_dir, name), "w").close()


def is_done(claims_dir, name):
    return os.path.exists(_done_path(claims_dir, name))


def release(claims_dir, name):
    """Drop an in-progress claim so another machine can take it (graceful stop)."""
    try:
        os.remove(_claim_path(claims_dir, name))
    except OSError:
        pass


def claimed_names(claims_dir):
    """Set of clip names that have a .claim (claimed or done)."""
    try:
        return {f[:-len(_CLAIM)] for f in os.listdir(claims_dir) if f.endswith(_CLAIM)}
    except OSError:
        return set()


def count_done(claims_dir):
    try:
        return sum(1 for f in os.listdir(claims_dir) if f.endswith(_DONE))
    except OSError:
        return 0


def pending_names(all_names, claims_dir, own_done):
    """Names not yet claimed by anyone and not already done in this machine's own manifest."""
    claimed = claimed_names(claims_dir)
    return [n for n in all_names if n not in claimed and n not in own_done]


def _owner(claims_dir, name):
    try:
        with open(_claim_path(claims_dir, name)) as f:
            return f.read().split(" ", 1)[0]
    except OSError:
        return None


def reclaim_own(claims_dir, machine_id):
    """On startup, drop THIS machine's own in-progress (no .done) claims so they get redone.
    Race-free: only this machine writes its own id."""
    n = 0
    for name in claimed_names(claims_dir):
        if is_done(claims_dir, name):
            continue
        if _owner(claims_dir, name) == machine_id:
            try:
                os.remove(_claim_path(claims_dir, name))
                n += 1
            except OSError:
                pass
    return n


def reclaim_orphans(claims_dir, stale_secs=1800):
    """Drop any in-progress claim older than stale_secs (a crashed other machine). Manual sweep."""
    now = time.time()
    n = 0
    for name in claimed_names(claims_dir):
        if is_done(claims_dir, name):
            continue
        p = _claim_path(claims_dir, name)
        try:
            if now - os.path.getmtime(p) >= stale_secs:
                os.remove(p)
                n += 1
        except OSError:
            pass
    return n
