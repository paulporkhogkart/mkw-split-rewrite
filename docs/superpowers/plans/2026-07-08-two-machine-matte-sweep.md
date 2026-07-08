# Two-Machine Matte Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `tools/asset_matte/process_all.py` concurrently on the rig (RTX 5080) and a second box (RTX 2080 Ti) over SMB, coordinated by atomic per-clip claim files, so the two boxes matte disjoint clips with no duplication, independent Start/Stop, and ~1–2 GB local footprint on the second box.

**Architecture:** A GPU-free coordination module (`claims.py`) provides atomic `O_CREAT|O_EXCL` claim files in a shared directory on the rig; a GPU-free `ship.py` moves each finished clip's output to the share and deletes the local copy. `process_all.py` gains two opt-in flags (`--claims-dir`, `--ship-dir`) that wrap its per-clip loop with claim-gate + ship + done-marker; with both absent the driver is byte-identical to today. The Tk console (`app.py`) reads new `KARTOFF_*` env vars (resolved by a pure `procconfig.py`) so the second box runs the same GUI pointed at the share.

**Tech Stack:** Python 3 (stdlib `os`/`glob`/`shutil`/`socket`/`time`), pytest + `tmp_path`, existing `ProcessPoolExecutor` prefetch, Tkinter console, Windows SMB2/3 to an NTFS share.

## Global Constraints

- **Opt-out is byte-identical:** with neither `--claims-dir` nor `--ship-dir` set, `process_all.py` produces the exact same files, manifest, and matte order as today. Every new behaviour is gated on a flag being present.
- **machine-id** defaults to `os.environ.get("COMPUTERNAME") or socket.gethostname()`; it only has to be distinct between the two boxes. Overridable via `--machine-id`.
- **Atomic claim primitive:** `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` — exactly one racer wins, the loser gets `FileExistsError`. Reliable Windows→Windows SMB2/3 to NTFS (both boxes are Windows; share is NTFS on the rig).
- **Ship glob is `<name>__*`** under `<out>/matte/` — every artifact is prefixed `"<name>__<seg>"` and clip names never nest, so it is exact and collision-free. Ship is **idempotent** (delete same-named target first) and **precedes** the `.done` marker (a clip is complete to the cluster only once its bytes are on the share).
- **STALE_SECS = 1800** for orphan reclaim (≫ any clip's ~90–180 s).
- **Paths:** share = `\\PAUL-AM5-DT\kartoff`; machine-1 out = `D:\kartoff\asset_chips`; machine-1 claims = `D:\kartoff\asset_chips\claims`; machine-2 scratch out = `C:\kartoff_scratch\asset_chips` (auto-created); machine-2 clips = `\\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips`; machine-2 ship = `\\PAUL-AM5-DT\kartoff\asset_chips`.
- **Tests:** live in root `tests/`, run with `python -m pytest tests/<file> -v`; `tests/conftest.py` already adds `tools/asset_matte` + `tools/sweep_console` to `sys.path`; use the `tmp_path` fixture. GPU modules are stubbed via `sys.modules` so tests need no CUDA/rembg.
- **Spec:** `docs/superpowers/specs/2026-07-08-two-machine-matte-sweep-design.md`.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `tools/asset_matte/claims.py` | Create | Atomic claim-file coordination: claim/done/pending/count/reclaim. GPU-free. |
| `tools/asset_matte/ship.py` | Create | Move one clip's `<name>__*` artifacts to the share, idempotently. GPU-free. |
| `tools/asset_matte/process_all.py` | Modify | Add `--claims-dir`/`--ship-dir`/`--machine-id`/`--reclaim-orphans`; claimed-queue loop wrapping claim-gate + ship + done. |
| `tools/sweep_console/commands.py` | Modify | `process_cmd` appends `--claims-dir`/`--ship-dir` when set. |
| `tools/sweep_console/supervisor.py` | Modify | `start_processing` passes the flags; `process_done_count` counts `.done` when in claims mode. |
| `tools/sweep_console/procconfig.py` | Create | Pure `env → {clips,out,claims,ship,stop,manifest}` resolver. |
| `tools/sweep_console/app.py` | Modify | Use `procconfig`; pass claims/ship to `start_processing`; bootstrap dirs; global progress; viewer target. |
| `run_console_m1.bat`, `run_console_m2.bat` | Create | Per-machine env wrappers. |
| `docs/two-machine-sweep.md` | Create | Runbook: SMB share setup, venv, operations, reclaim, cleanup. |
| `tests/test_asset_claims.py`, `tests/test_asset_ship.py`, `tests/test_process_all_dist.py`, `tests/test_sweep_commands.py`, `tests/test_sweep_supervisor_done.py`, `tests/test_sweep_procconfig.py` | Create | Unit + stubbed-integration tests. |

**Dependency order:** T1 (claims) and T2 (ship) and T5 (procconfig) are independent. T3 needs T1+T2. T4 needs T1. T6 needs T4+T5. T7 last.

---

### Task 1: `claims.py` — atomic claim coordination

**Files:**
- Create: `tools/asset_matte/claims.py`
- Test: `tests/test_asset_claims.py`

**Interfaces:**
- Produces:
  - `default_machine_id() -> str`
  - `try_claim(claims_dir: str, name: str, machine_id: str) -> bool` — atomic; True = won
  - `mark_done(claims_dir: str, name: str) -> None`
  - `is_done(claims_dir: str, name: str) -> bool`
  - `claimed_names(claims_dir: str) -> set[str]`
  - `count_done(claims_dir: str) -> int`
  - `pending_names(all_names: list[str], claims_dir: str, own_done: set[str]) -> list[str]`
  - `reclaim_own(claims_dir: str, machine_id: str) -> int`
  - `reclaim_orphans(claims_dir: str, stale_secs: float = 1800) -> int`
  - `release(claims_dir: str, name: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_asset_claims.py`:

```python
import os
import time
from concurrent.futures import ThreadPoolExecutor

import claims


def test_try_claim_is_exclusive(tmp_path):
    d = str(tmp_path / "claims")
    assert claims.try_claim(d, "clipA", "m1") is True
    assert claims.try_claim(d, "clipA", "m2") is False   # already taken
    assert claims.try_claim(d, "clipB", "m2") is True


def _claim(d_name_who):
    d, name, who = d_name_who
    return claims.try_claim(d, name, who)


def test_try_claim_race_exactly_one_winner(tmp_path):
    # 8 threads race for the same name. os.open(O_CREAT|O_EXCL) is atomic in the
    # kernel, so exactly one wins regardless of the GIL — this exercises our
    # FileExistsError->False path. (True cross-machine atomicity is validated live.)
    d = str(tmp_path / "claims")
    os.makedirs(d, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_claim, [(d, "x", f"m{i}") for i in range(8)]))
    assert sum(results) == 1                              # exactly one thread won


def test_done_and_counts(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "a", "m1")
    claims.try_claim(d, "b", "m1")
    claims.mark_done(d, "a")
    assert claims.is_done(d, "a") is True
    assert claims.is_done(d, "b") is False
    assert claims.count_done(d) == 1
    assert claims.claimed_names(d) == {"a", "b"}


def test_pending_excludes_claimed_and_own_done(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "b", "other")                    # someone else owns b
    pend = claims.pending_names(["a", "b", "c"], d, own_done={"c"})
    assert pend == ["a"]                                 # b claimed, c own-done


def test_reclaim_own_only_mine_and_not_done(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "mine_ip", "m1")                 # mine, in progress -> reclaimed
    claims.try_claim(d, "mine_done", "m1"); claims.mark_done(d, "mine_done")  # mine, done -> kept
    claims.try_claim(d, "theirs", "m2")                  # not mine -> kept
    n = claims.reclaim_own(d, "m1")
    assert n == 1
    assert claims.try_claim(d, "mine_ip", "m1") is True  # freed -> reclaimable
    assert claims.is_claimed_dir_has(d, "theirs") if hasattr(claims, "is_claimed_dir_has") else True
    assert "theirs" in claims.claimed_names(d)
    assert "mine_done" in claims.claimed_names(d)


def test_reclaim_orphans_by_age(tmp_path):
    d = str(tmp_path / "claims")
    claims.try_claim(d, "fresh", "m1")
    claims.try_claim(d, "stale", "m2")
    old = time.time() - 5000
    os.utime(os.path.join(d, "stale.claim"), (old, old))
    n = claims.reclaim_orphans(d, stale_secs=1800)
    assert n == 1
    assert "stale" not in claims.claimed_names(d)
    assert "fresh" in claims.claimed_names(d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_asset_claims.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claims'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/asset_matte/claims.py`:

```python
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
```

- [ ] **Step 4: Remove the stray helper reference in the test**

The test's `test_reclaim_own_only_mine_and_not_done` contains a defensive `hasattr` line referencing a non-existent `is_claimed_dir_has`; simplify it. Edit `tests/test_asset_claims.py`, replace:

```python
    assert claims.is_claimed_dir_has(d, "theirs") if hasattr(claims, "is_claimed_dir_has") else True
    assert "theirs" in claims.claimed_names(d)
```

with:

```python
    assert "theirs" in claims.claimed_names(d)          # not mine -> kept
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_asset_claims.py -v`
Expected: PASS — 6 passed. (The race test uses 8 threads sharing the already-imported `claims`; the OS makes `os.open(O_EXCL)` atomic so exactly one wins.)

- [ ] **Step 6: Commit**

```bash
git add tools/asset_matte/claims.py tests/test_asset_claims.py
git commit -m "feat(matte): atomic claim-file coordination for multi-machine sweep"
```

---

### Task 2: `ship.py` — ship a clip's output to the share

**Files:**
- Create: `tools/asset_matte/ship.py`
- Test: `tests/test_asset_ship.py`

**Interfaces:**
- Produces: `ship_clip(out_matte_dir: str, ship_matte_dir: str, name: str) -> int` — moves every `<name>__*` artifact (files + `_frames/` dirs) from `out_matte_dir` to `ship_matte_dir`, overwriting same-named targets first; returns the count moved.

- [ ] **Step 1: Write the failing test**

Create `tests/test_asset_ship.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_asset_ship.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ship'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/asset_matte/ship.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_asset_ship.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/asset_matte/ship.py tests/test_asset_ship.py
git commit -m "feat(matte): ship-and-delete a clip's output to the share (idempotent)"
```

---

### Task 3: `process_all.py` — distributed claimed-queue loop

**Files:**
- Modify: `tools/asset_matte/process_all.py` (args block ~67–79; loop ~99–154; `main()` signature)
- Test: `tests/test_process_all_dist.py`

**Interfaces:**
- Consumes: `claims.*` (Task 1), `ship.ship_clip` (Task 2).
- Produces: `main(argv: list[str] | None = None)` — argparse over `argv` (None → `sys.argv`); new flags `--claims-dir`, `--ship-dir`, `--machine-id`, `--reclaim-orphans`, `--stale-secs`.

**Loop semantics (unchanged for the opt-out path):** the existing prefetch overlap is preserved — a small queue of *owned* clips has its segmentation submitted to the `ProcessPoolExecutor` ahead of matting, so the GPU never idles during segmentation. In claims mode only clips this machine wins the claim for are ever segmented (no orphaned loop-frames on the local scratch). On a graceful stop the in-flight clip finishes + ships; any pre-claimed, not-yet-matted queued clips are **released** (claim removed, loop-frames cleaned) so the other machine can take them.

- [ ] **Step 1: Write the failing test**

Create `tests/test_process_all_dist.py`. It stubs the GPU modules via `sys.modules` **before** importing `process_all`, so it runs in the plain venv (no CUDA/rembg) at `--prefetch 0`:

```python
import os
import sys
import types

import pytest


def _install_stubs(monkeypatch):
    """Replace the GPU-dependent modules process_all imports, so the loop runs CPU-only."""
    el = types.ModuleType("extract_loop")

    def extract_segments(clip, seg_base, name):
        for seg in ("idle",):
            d = os.path.join(seg_base, f"{name}__{seg}")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "000.png"), "w").close()
        return {"idle": 1}

    el.extract_segments = extract_segments
    el.is_kart_combo = lambda name: len(name.split("__")) >= 3

    mb = types.ModuleType("matte_blankplate")

    def matte_loopframes(framedir, name, out_base, **kw):
        d = os.path.join(out_base, f"{name}_frames")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "000.png"), "w").close()
        open(os.path.join(out_base, f"{name}_loop.webp"), "w").close()
        return 1

    mb.matte_loopframes = matte_loopframes

    mm = types.ModuleType("matte_matanyone")
    mm.segment_direction = lambda kart, seg: "fwd"

    for modname, mod in (("extract_loop", el), ("matte_blankplate", mb), ("matte_matanyone", mm)):
        monkeypatch.setitem(sys.modules, modname, mod)
    monkeypatch.delitem(sys.modules, "process_all", raising=False)
    import process_all
    return process_all


def _make_clips(clips_dir, names):
    os.makedirs(clips_dir, exist_ok=True)
    for n in names:
        open(os.path.join(clips_dir, n + ".mkv"), "w").close()


def test_optout_processes_all_in_order(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    clips = str(tmp_path / "clips")
    out = str(tmp_path / "out")
    _make_clips(clips, ["a__b", "a__c", "a__d"])
    pa.main(["--clips", clips, "--out", out, "--prefetch", "0"])
    m = pa.load_manifest(os.path.join(out, "manifest.json"))
    assert set(m) == {"a__b", "a__c", "a__d"}
    assert all(v["status"] == "done" for v in m.values())
    assert os.path.exists(os.path.join(out, "matte", "a__b__idle_loop.webp"))
    assert not os.path.isdir(os.path.join(out, "claims"))   # no claims artifacts in opt-out


def test_claims_skips_clip_owned_by_other(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    import claims
    clips = str(tmp_path / "clips")
    out = str(tmp_path / "out")
    share = str(tmp_path / "share")
    claims_dir = os.path.join(share, "claims")
    _make_clips(clips, ["a__b", "a__c", "a__d"])
    claims.try_claim(claims_dir, "a__c", "OTHER")           # the other box owns a__c
    pa.main(["--clips", clips, "--out", out, "--prefetch", "0",
             "--claims-dir", claims_dir, "--ship-dir", share, "--machine-id", "ME"])
    # a__b and a__d done by us: shipped to the share, marked done, gone from local scratch
    assert claims.is_done(claims_dir, "a__b") and claims.is_done(claims_dir, "a__d")
    assert os.path.exists(os.path.join(share, "matte", "a__b__idle_loop.webp"))
    assert not os.path.exists(os.path.join(out, "matte", "a__b__idle_loop.webp"))
    # a__c was skipped: never done by us, no output shipped
    assert not claims.is_done(claims_dir, "a__c")
    assert not os.path.exists(os.path.join(share, "matte", "a__c__idle_loop.webp"))


def test_reclaim_orphans_mode(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    import claims
    claims_dir = str(tmp_path / "claims")
    claims.try_claim(claims_dir, "old", "DEAD")
    pa.main(["--reclaim-orphans", "--claims-dir", claims_dir, "--stale-secs", "0"])
    assert "old" not in claims.claimed_names(claims_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_process_all_dist.py -v`
Expected: FAIL — `TypeError: main() takes 0 positional arguments` (and the new flags are unknown).

- [ ] **Step 3: Add the new args and `main(argv)` signature**

In `tools/asset_matte/process_all.py`, add the two imports near the existing ones (after line 33, `import matte_matanyone as mm`):

```python
import claims
import ship
```

Change `def main():` (line 67) to `def main(argv=None):` and change `a = ap.parse_args()` (line 79) to `a = ap.parse_args(argv)`. Then add these arguments inside `main`, right after the existing `--prefetch` argument (after line 78):

```python
    ap.add_argument("--claims-dir", default=None,
                    help="shared dir of atomic per-clip claim files (enables multi-machine mode)")
    ap.add_argument("--ship-dir", default=None,
                    help="move each finished clip's matte/<name>__* here then delete local")
    ap.add_argument("--machine-id", default=None, help="claim owner id (default: hostname)")
    ap.add_argument("--reclaim-orphans", action="store_true",
                    help="one-shot: clear stale in-progress claims (a crashed box) then exit")
    ap.add_argument("--stale-secs", type=float, default=1800,
                    help="orphan-claim age threshold for --reclaim-orphans")
```

- [ ] **Step 4: Add the reclaim-mode + machine-id + dir bootstrap**

In `main`, right after `manifest_path = a.manifest or os.path.join(out, "manifest.json")` (line 86), insert:

```python
    machine_id = a.machine_id or claims.default_machine_id()
    if a.reclaim_orphans:
        if not a.claims_dir:
            print("ERROR --reclaim-orphans needs --claims-dir", flush=True)
            return
        n = claims.reclaim_orphans(a.claims_dir, a.stale_secs)
        print(f"RECLAIMED {n} orphan claim(s)", flush=True)
        return
    os.makedirs(loopdir, exist_ok=True)
    if a.claims_dir:
        os.makedirs(a.claims_dir, exist_ok=True)
        freed = claims.reclaim_own(a.claims_dir, machine_id)
        if freed:
            print(f"RECLAIMED {freed} own in-progress claim(s) from a prior run", flush=True)
    if a.ship_dir:
        os.makedirs(os.path.join(a.ship_dir, "matte"), exist_ok=True)
```

- [ ] **Step 5: Replace the loop with the claimed-queue loop**

Replace the block from `pending = [n for n in names if manifest.get(n, {}).get("status") != "done"]` (line 99) through the end of the `for i, name in enumerate(pending):` loop and its final `if ex is not None: ex.shutdown(...)` (line 153), i.e. lines 99–153, with:

```python
    own_done = {n for n in names if manifest.get(n, {}).get("status") == "done"}
    pending = (claims.pending_names(names, a.claims_dir, own_done) if a.claims_dir
               else [n for n in names if n not in own_done])
    ex = ProcessPoolExecutor(max_workers=a.prefetch) if a.prefetch > 0 else None
    futures = {}

    def _submit(n):
        if ex is not None and n not in futures:
            futures[n] = ex.submit(_seg_task, os.path.join(a.clips, n + ".mkv"),
                                   os.path.join(loopdir, n), n)

    pend = iter(pending)
    queue = []                                    # clips we OWN, segmentation submitted, awaiting matte
    depth = 1 + (a.prefetch or 0)

    def _refill():
        while len(queue) < depth:
            n = next(pend, None)
            if n is None:
                break
            if a.claims_dir and not claims.try_claim(a.claims_dir, n, machine_id):
                continue                          # another machine owns it
            queue.append(n)
            _submit(n)

    processed = 0
    while True:
        if os.path.exists(stop_file):             # clean stop BETWEEN clips
            print(f"STOPPED stop-file present ({base_done + processed}/{total} done)", flush=True)
            if a.claims_dir:                      # release our pre-claimed, un-matted clips
                for n in queue:
                    claims.release(a.claims_dir, n)
                    shutil.rmtree(os.path.join(loopdir, n), ignore_errors=True)
            break
        if a.limit and processed >= a.limit:
            print(f"LIMIT {a.limit} reached", flush=True)
            break
        _refill()
        if not queue:
            break
        name = queue.pop(0)
        clip = os.path.join(a.clips, name + ".mkv")
        seg_base = os.path.join(loopdir, name)
        t0 = time.time()
        print(f"--- {name} ({base_done + processed + 1}/{total}) segmenting...", flush=True)
        try:
            counts = (futures.pop(name).result() if ex is not None
                      else el.extract_segments(clip, seg_base, name))   # spawn/idle/flourish spans
            kart = el.is_kart_combo(name)
            matted = {}
            for seg in ("spawn", "idle", "flourish"):
                if seg not in counts:
                    continue
                segname = f"{name}__{seg}"
                fd = os.path.join(seg_base, segname)
                print(f"    matting {segname} ({counts[seg]}f)...", flush=True)
                matted[seg] = int(mb.matte_loopframes(
                    fd, segname, mattedir, clip=clip,
                    apply_predark=not (kart and seg == "flourish"), is_kart=kart,
                    direction=mm.segment_direction(kart, seg)))
            if not a.keep_loopframes:
                shutil.rmtree(seg_base, ignore_errors=True)
            manifest[name] = {"status": "done", "kart": kart,
                              "segments": matted, "secs": round(time.time() - t0, 1)}
            save_manifest(manifest_path, manifest)
            if a.ship_dir:                        # ship BEFORE marking done: bytes on share first
                ship.ship_clip(mattedir, os.path.join(a.ship_dir, "matte"), name)
            if a.claims_dir:
                claims.mark_done(a.claims_dir, name)
            processed += 1
            print(f"PROCESSED {name} ({base_done + processed}/{total}) {matted} "
                  f"{time.time() - t0:.0f}s", flush=True)
        except Exception as exc:
            import traceback
            manifest[name] = {"status": "error", "error": str(exc)}
            save_manifest(manifest_path, manifest)
            print(f"ERROR {name}: {exc}", flush=True)
            traceback.print_exc()

    if ex is not None:
        ex.shutdown(cancel_futures=True)          # queued prefetches die; running ones finish
```

Then change the final `DONE` line (was line 154) so the total reflects the whole cluster in claims mode:

```python
    done_total = claims.count_done(a.claims_dir) if a.claims_dir else done_count(manifest, names)
    print(f"DONE processed={processed} done_total={done_total}/{total}", flush=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_process_all_dist.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 7: Verify the opt-out path is unbroken with prefetch on**

Run the full asset-matte-adjacent suite in the plain venv to confirm nothing regressed:
Run: `python -m pytest tests/test_process_all_dist.py tests/test_extract_loop.py -v`
Expected: PASS (the dist tests pass; `test_extract_loop` is unaffected). If `test_extract_loop` errors on import for unrelated reasons in this venv, note it and move on — it is not touched by this task.

- [ ] **Step 8: Commit**

```bash
git add tools/asset_matte/process_all.py tests/test_process_all_dist.py
git commit -m "feat(matte): claimed-queue distributed loop (--claims-dir/--ship-dir) in process_all"
```

---

### Task 4: console I/O — `commands.py` + `supervisor.py`

**Files:**
- Modify: `tools/sweep_console/commands.py:30-34` (`process_cmd`)
- Modify: `tools/sweep_console/supervisor.py:90-103` (`start_processing`), `:173-181` (`process_done_count`)
- Test: `tests/test_sweep_commands.py`, `tests/test_sweep_supervisor_done.py`

**Interfaces:**
- Consumes: `claims.count_done` (Task 1).
- Produces:
  - `commands.process_cmd(gpu_py, repo_root, clips_dir, out_dir, stop_file, claims_dir=None, ship_dir=None) -> list[str]`
  - `ProcessSupervisor.start_processing(clips_dir, out_dir, stop_file, on_exit=None, claims_dir=None, ship_dir=None)`
  - `ProcessSupervisor.process_done_count(manifest_path, claims_dir=None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sweep_commands.py`:

```python
import commands


def test_process_cmd_plain_has_no_dist_flags():
    cmd = commands.process_cmd("py.exe", "/repo", "/clips", "/out", "/out/.stop")
    assert "--claims-dir" not in cmd
    assert "--ship-dir" not in cmd
    assert cmd[-2:] == ["--stop-file", "/out/.stop"]


def test_process_cmd_appends_dist_flags_when_set():
    cmd = commands.process_cmd("py.exe", "/repo", "/clips", "/out", "/out/.stop",
                               claims_dir="/share/claims", ship_dir="/share")
    assert cmd[cmd.index("--claims-dir") + 1] == "/share/claims"
    assert cmd[cmd.index("--ship-dir") + 1] == "/share"
```

Create `tests/test_sweep_supervisor_done.py`:

```python
import os

import claims
from supervisor import ProcessSupervisor


def test_done_count_uses_manifest_without_claims(tmp_path):
    sup = ProcessSupervisor(str(tmp_path), on_line=lambda *a: None)
    man = tmp_path / "manifest.json"
    man.write_text('{"a": {"status": "done"}, "b": {"status": "error"}}')
    assert sup.process_done_count(str(man)) == 1


def test_done_count_uses_claims_when_given(tmp_path):
    sup = ProcessSupervisor(str(tmp_path), on_line=lambda *a: None)
    d = str(tmp_path / "claims")
    claims.try_claim(d, "a", "m"); claims.mark_done(d, "a")
    claims.try_claim(d, "b", "m"); claims.mark_done(d, "b")
    claims.try_claim(d, "c", "m")                         # claimed, not done
    assert sup.process_done_count(str(tmp_path / "manifest.json"), claims_dir=d) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sweep_commands.py tests/test_sweep_supervisor_done.py -v`
Expected: FAIL — `TypeError: process_cmd() got an unexpected keyword argument 'claims_dir'` / `process_done_count() got an unexpected keyword argument 'claims_dir'`.

- [ ] **Step 3: Implement `process_cmd`**

Replace `tools/sweep_console/commands.py:30-34` with:

```python
def process_cmd(gpu_py, repo_root, clips_dir, out_dir, stop_file, claims_dir=None, ship_dir=None):
    """Headless extract+matte batch driver. Runs in the GPU venv (rembg/CUDA), not the
    console's build python; process_all.py sets its own sys.path so no PYTHONPATH is needed.
    claims_dir/ship_dir enable the multi-machine claimed-queue + ship-and-delete mode."""
    cmd = [gpu_py, os.path.join(repo_root, "tools", "asset_matte", "process_all.py"),
           "--clips", clips_dir, "--out", out_dir, "--stop-file", stop_file]
    if claims_dir:
        cmd += ["--claims-dir", claims_dir]
    if ship_dir:
        cmd += ["--ship-dir", ship_dir]
    return cmd
```

- [ ] **Step 4: Implement `start_processing` + `process_done_count`**

Add `import claims` near the top of `tools/sweep_console/supervisor.py` (after `import commands`, line 12). This resolves because `app.py` adds `tools/asset_matte` to `sys.path` before importing the supervisor (Task 6), and `tests/conftest.py` already does so for tests.

Replace the `start_processing` signature + body (supervisor.py:90-103) so it threads the flags through:

```python
    def start_processing(self, clips_dir, out_dir, stop_file, on_exit=None,
                         claims_dir=None, ship_dir=None):
        """Spawn the headless extract+matte batch driver in the GPU venv. Resumable: it skips
        clips already 'done' in <out_dir>/manifest.json, so RESUME just relaunches this.
        claims_dir/ship_dir enable multi-machine mode (shared claims + ship-and-delete)."""
        try:
            if os.path.exists(stop_file):
                os.remove(stop_file)                 # clear any stale pause/stop flag
        except OSError:
            pass
        os.makedirs(out_dir, exist_ok=True)
        # forward-only matte (engine default) — the user-validated configuration (spawn-dip fix
        # eyetest 2026-07-02); the earlier console bidir opt-in is dropped with it.
        return self._spawn("process",
                           commands.process_cmd(self.gpu_py, self.repo_root, clips_dir, out_dir,
                                                 stop_file, claims_dir=claims_dir, ship_dir=ship_dir),
                           on_exit=on_exit)
```

Replace `process_done_count` (supervisor.py:173-181) with:

```python
    def process_done_count(self, manifest_path, claims_dir=None):
        """How many clips are finished. In multi-machine mode (claims_dir set) this is the GLOBAL
        count of .done markers on the share so both consoles show the same batch progress;
        otherwise it's this machine's manifest 'done' count."""
        if claims_dir:
            return claims.count_done(claims_dir)
        try:
            import json
            with open(manifest_path) as f:
                m = json.load(f)
            return sum(1 for v in m.values() if isinstance(v, dict) and v.get("status") == "done")
        except (OSError, ValueError):
            return 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_sweep_commands.py tests/test_sweep_supervisor_done.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 6: Commit**

```bash
git add tools/sweep_console/commands.py tools/sweep_console/supervisor.py \
        tests/test_sweep_commands.py tests/test_sweep_supervisor_done.py
git commit -m "feat(console): thread claims/ship flags + global done-count through supervisor"
```

---

### Task 5: `procconfig.py` — pure env → paths resolver

**Files:**
- Create: `tools/sweep_console/procconfig.py`
- Test: `tests/test_sweep_procconfig.py`

**Interfaces:**
- Produces: `resolve_process_config(env: dict, data_root_default: str = r"D:\kartoff") -> ProcessConfig`, a `namedtuple("ProcessConfig", "clips out claims ship stop manifest")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sweep_procconfig.py`:

```python
import os

import procconfig


def test_defaults_match_single_machine_layout():
    cfg = procconfig.resolve_process_config({}, data_root_default=r"D:\kartoff")
    assert cfg.clips == os.path.join(r"D:\kartoff", "captures_sdr", "en_uk", "clips")
    assert cfg.out == os.path.join(r"D:\kartoff", "asset_chips")
    assert cfg.claims is None                              # single-machine: no claims
    assert cfg.ship is None
    assert cfg.stop == os.path.join(cfg.out, ".process_stop")
    assert cfg.manifest == os.path.join(cfg.out, "manifest.json")


def test_machine2_env_overrides():
    env = {
        "KARTOFF_CLIPS_DIR": r"\\RIG\kartoff\captures_sdr\en_uk\clips",
        "KARTOFF_PROCESS_OUT": r"C:\kartoff_scratch\asset_chips",
        "KARTOFF_CLAIMS_DIR": r"\\RIG\kartoff\asset_chips\claims",
        "KARTOFF_SHIP_DIR": r"\\RIG\kartoff\asset_chips",
    }
    cfg = procconfig.resolve_process_config(env)
    assert cfg.clips == r"\\RIG\kartoff\captures_sdr\en_uk\clips"
    assert cfg.out == r"C:\kartoff_scratch\asset_chips"
    assert cfg.claims == r"\\RIG\kartoff\asset_chips\claims"
    assert cfg.ship == r"\\RIG\kartoff\asset_chips"
    assert cfg.stop == os.path.join(cfg.out, ".process_stop")   # local to machine 2


def test_data_root_env_moves_defaults():
    cfg = procconfig.resolve_process_config({"KARTOFF_DATA_ROOT": r"E:\k"})
    assert cfg.out == os.path.join(r"E:\k", "asset_chips")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sweep_procconfig.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'procconfig'`.

- [ ] **Step 3: Write minimal implementation**

Create `tools/sweep_console/procconfig.py`:

```python
"""Resolve the asset-processing paths from the environment (pure; unit-tested).

Single-machine default (no env) reproduces today's layout under KARTOFF_DATA_ROOT. The second
box sets KARTOFF_CLIPS_DIR (share), KARTOFF_PROCESS_OUT (local scratch), KARTOFF_CLAIMS_DIR
(shared coordinator), KARTOFF_SHIP_DIR (share) to run the same console pointed at the rig.
"""
import os
from collections import namedtuple

ProcessConfig = namedtuple("ProcessConfig", "clips out claims ship stop manifest")


def resolve_process_config(env, data_root_default=r"D:\kartoff"):
    data_root = env.get("KARTOFF_DATA_ROOT", data_root_default)
    clips = env.get("KARTOFF_CLIPS_DIR",
                    os.path.join(data_root, "captures_sdr", "en_uk", "clips"))
    out = env.get("KARTOFF_PROCESS_OUT", os.path.join(data_root, "asset_chips"))
    claims = env.get("KARTOFF_CLAIMS_DIR") or None
    ship = env.get("KARTOFF_SHIP_DIR") or None
    return ProcessConfig(
        clips=clips,
        out=out,
        claims=claims,
        ship=ship,
        stop=os.path.join(out, ".process_stop"),
        manifest=os.path.join(out, "manifest.json"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sweep_procconfig.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/sweep_console/procconfig.py tests/test_sweep_procconfig.py
git commit -m "feat(console): pure procconfig env->paths resolver for multi-machine"
```

---

### Task 6: `app.py` — wire config, claims/ship, dirs, progress, viewer

**Files:**
- Modify: `tools/sweep_console/app.py` — path setup (~16-20), constants block (35-42), dir bootstrap (68-72), `start_processing` call (251-253), `process_done_count` call (340), viewer target (265).

**Interfaces:**
- Consumes: `procconfig.resolve_process_config` (Task 5); `ProcessSupervisor.start_processing(..., claims_dir, ship_dir)` + `process_done_count(..., claims_dir)` (Task 4).
- Produces (module-level names other code/tests read): `CLIPS_DIR`, `PROCESS_OUT`, `PROCESS_STOP`, `PROCESS_MANIFEST`, `CLAIMS_DIR`, `SHIP_DIR`.

This task is Tk wiring (no unit test — matches the repo, which has no `app.py` test); it ends with an import-smoke check of the env wiring plus a manual live checkpoint.

- [ ] **Step 1: Add `tools/asset_matte` to the console's `sys.path`**

In `tools/sweep_console/app.py`, the loop at lines 16-20 adds `_HERE` and `../autotemplate`. Add `../asset_matte` so the supervisor's `import claims` (Task 4) and this module resolve it. Replace lines 17-20:

```python
for _d in (_HERE, os.path.join(_HERE, "..", "autotemplate"), os.path.join(_HERE, "..", "asset_matte")):
    _p = os.path.abspath(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
```

- [ ] **Step 2: Replace the hardcoded path constants with `procconfig`**

Replace `app.py:31-42` (from the `REPO_ROOT = ...` comment block through `PROCESS_MANIFEST = ...`) with:

```python
REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Clips + matte output live on the DATA drive (large SSD), NOT in the repo on C:. The app, venv,
# birefnet model + templates stay on C: (read-only at startup, no perf impact). Paths resolve from
# the environment (procconfig): default = today's single-machine layout under KARTOFF_DATA_ROOT;
# the 2nd box overrides KARTOFF_CLIPS_DIR/PROCESS_OUT/CLAIMS_DIR/SHIP_DIR to run against the rig.
import procconfig
_CFG = procconfig.resolve_process_config(os.environ)
DATA_ROOT = os.environ.get("KARTOFF_DATA_ROOT", r"D:\kartoff")
CLIPS_DIR = _CFG.clips                          # rig records here + processing reads here
TOTAL = 6273
WS_URL = "ws://127.0.0.1:8766"
# Asset processing (extract+matte) — independent of the rig; runs the GPU-venv batch driver.
PROCESS_OUT = _CFG.out
PROCESS_STOP = _CFG.stop
PROCESS_MANIFEST = _CFG.manifest
CLAIMS_DIR = _CFG.claims                        # None = single-machine; set = multi-machine coordinator
SHIP_DIR = _CFG.ship                            # None = write in place; set = ship-and-delete to share
```

- [ ] **Step 3: Bootstrap the claims + ship dirs at startup**

Replace the dir-creation loop at `app.py:68-72` with one that also creates the coordinator + ship dirs (all `exist_ok`, harmless over SMB):

```python
        _bootstrap = [CLIPS_DIR, PROCESS_OUT]
        if CLAIMS_DIR:
            _bootstrap.append(CLAIMS_DIR)
        if SHIP_DIR:
            _bootstrap.append(os.path.join(SHIP_DIR, "matte"))
        for _d in _bootstrap:                        # ensure the data-drive dirs exist up front
            try:
                os.makedirs(_d, exist_ok=True)
            except OSError:
                pass
```

- [ ] **Step 4: Pass claims/ship into `start_processing`**

Replace the `start_processing` call in `_do_process` (app.py:251-253) with:

```python
        if action == "start_processing":
            self.sup.start_processing(CLIPS_DIR, PROCESS_OUT, PROCESS_STOP,
                                      on_exit=self._process_exited,
                                      claims_dir=CLAIMS_DIR, ship_dir=SHIP_DIR)
```

- [ ] **Step 5: Global progress + viewer target**

Replace the `process_done_count` call in `_tick` (app.py:340) with the claims-aware form:

```python
        self.pprogress.update(self.sup.process_done_count(PROCESS_MANIFEST, claims_dir=CLAIMS_DIR),
                              time.monotonic())
```

Replace the viewer build in `_after_process_exit` (app.py:265) so it targets the shared full set when shipping:

```python
        _viewer_matte = os.path.join(SHIP_DIR or PROCESS_OUT, "matte")
        msg = self.sup.build_viewer(_viewer_matte)   # regenerate the chip viewer over the full set
```

- [ ] **Step 6: Import-smoke the env wiring (automated)**

Run this from the repo root (build venv) to confirm the constants track the env without opening Tk:

```bash
python -c "import os, sys; sys.path[:0]=[os.path.abspath('tools/sweep_console'), os.path.abspath('tools/asset_matte'), os.path.abspath('tools/autotemplate')]; os.environ.update(KARTOFF_CLIPS_DIR=r'\\\\RIG\\k\\clips', KARTOFF_PROCESS_OUT=r'C:\\scratch', KARTOFF_CLAIMS_DIR=r'\\\\RIG\\k\\claims', KARTOFF_SHIP_DIR=r'\\\\RIG\\k'); import app; assert app.CLIPS_DIR==r'\\\\RIG\\k\\clips', app.CLIPS_DIR; assert app.PROCESS_OUT==r'C:\\scratch'; assert app.CLAIMS_DIR==r'\\\\RIG\\k\\claims'; assert app.SHIP_DIR==r'\\\\RIG\\k'; assert app.PROCESS_STOP==os.path.join(r'C:\\scratch','.process_stop'); print('env wiring OK')"
```

Expected: prints `env wiring OK`. (If `import app` fails on a missing GUI/websockets dep in this venv, run the same assertions against `procconfig.resolve_process_config(os.environ)` instead and note that `app` import needs the console venv.)

- [ ] **Step 7: Commit**

```bash
git add tools/sweep_console/app.py
git commit -m "feat(console): resolve processing paths from env; wire claims/ship + global progress"
```

- [ ] **Step 8: Manual live checkpoint (deferred to rollout)**

Not run now — recorded here for the rollout in Task 7's runbook: on the rig, `run_console_m1.bat` → Process a couple of clips with `--claims-dir` set (edit to `--limit` for the smoke) and confirm `.claim`/`.done` appear under `asset_chips\claims\` and chips land in `asset_chips\matte\`. Then on machine 2, `run_console_m2.bat` and confirm it claims *different* clips, ships them to the share, and its `C:\kartoff_scratch` stays ~empty.

---

### Task 7: launchers + runbook

**Files:**
- Create: `run_console_m1.bat`, `run_console_m2.bat`
- Create: `docs/two-machine-sweep.md`
- Modify: `tools/asset_matte/README.md` (add a pointer)

- [ ] **Step 1: Create the machine-1 launcher**

Create `run_console_m1.bat`:

```bat
@echo off
cd /d "%~dp0"
rem Rig (RTX 5080). Joins the shared claim queue; output stays local on D: (which is the share).
set KARTOFF_CLAIMS_DIR=D:\kartoff\asset_chips\claims
python tools\sweep_console\app.py
```

- [ ] **Step 2: Create the machine-2 launcher**

Create `run_console_m2.bat` (edit the `\\PAUL-AM5-DT` host + share name if different):

```bat
@echo off
cd /d "%~dp0"
rem Second box (RTX 2080 Ti). Reads clips over SMB, mattes to a LOCAL scratch, ships each
rem finished clip to the rig, deletes local. C:\kartoff_scratch is created automatically.
set KARTOFF_CLIPS_DIR=\\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips
set KARTOFF_PROCESS_OUT=C:\kartoff_scratch\asset_chips
set KARTOFF_CLAIMS_DIR=\\PAUL-AM5-DT\kartoff\asset_chips\claims
set KARTOFF_SHIP_DIR=\\PAUL-AM5-DT\kartoff\asset_chips
python tools\sweep_console\app.py
```

- [ ] **Step 3: Write the runbook**

Create `docs/two-machine-sweep.md`:

````markdown
# Two-machine matte sweep — runbook

Run the `process_all.py` matte batch on the rig (RTX 5080) and a second box (RTX 2080 Ti)
at once. They coordinate through atomic per-clip **claim files** in a shared folder on the
rig, so each clip is matted exactly once, either box can Start/Stop independently, and the
second box keeps only ~1–2 GB locally (it ships each finished clip to the rig and deletes it).
Design: `docs/superpowers/specs/2026-07-08-two-machine-matte-sweep-design.md`.

## One-time setup

### 1. Share `D:\kartoff` from the rig (SMB) — MANUAL

This is not automatic. On the **rig**, in an elevated PowerShell:

```powershell
# Grant the second box's account read/write to the share.
New-SmbShare -Name kartoff -Path D:\kartoff -FullAccess "PAUL-AM5-DT\<user>"
Get-SmbShare kartoff                         # verify it exists
```

- If the two boxes use different accounts, either add that account with `-FullAccess`, or use a
  dedicated account with `-ChangeAccess`. `-FullAccess "Everyone"` works only on a trusted LAN.
- Set the rig's network profile to **Private** (not Public) and allow **File and Printer
  Sharing** through the firewall for the Private profile (Settings → Network → Properties →
  Private; Windows Defender Firewall → Allow an app → File and Printer Sharing / Private).
- Both **share** permissions (above) and **NTFS** permissions on `D:\kartoff\asset_chips` and
  `…\claims` must allow the second box's account to **write**. GUI fallback: right-click
  `D:\kartoff` → Properties → Sharing → Advanced Sharing → Permissions.

On the **second box**, confirm access (and store credentials if the logins differ):

```powershell
Test-Path \\PAUL-AM5-DT\kartoff\captures_sdr\en_uk\clips     # -> True
cmdkey /add:PAUL-AM5-DT /user:<rig-user> /pass               # only if logins differ
```

### 2. Stand up the GPU venv on the second box

Clone/copy this repo to the second box, then build `temp/asset-venv-matte` exactly as on the
rig (py3.12 + onnxruntime-gpu 1.22/CUDA 12 + torch cu128 + MatAnyone2). See
`tools/asset_matte/README.md` and the chip-asset-matting memory for the venv recipe. The
2080 Ti (Turing) runs the CUDA 12 wheels fine. `C:\kartoff_scratch` is created automatically —
no manual mkdir.

## Running

- **Rig:** double-click `run_console_m1.bat`, then use the **Process** button as usual. (Do not
  run this while the *recording* sweep is using the GPU — same rule as before.)
- **Second box:** double-click `run_console_m2.bat`, then **Process**. It reads clips over SMB,
  mattes locally, ships each clip to the rig.
- Either box can run **solo** (the other's Process off) and will chew through everything pending.
  Both consoles show the same **global** progress (X / 6273) from the shared `.done` markers.

## Stopping / powering off

- Click **Stop** (or close the window) on a box: it finishes and ships the in-flight clip, then
  exits — after which that box is **safe to power off**. Stop is per-box; it never stops the other.
- If a box is **hard powered off** mid-clip, nothing is corrupted or half-published: on its next
  launch it clears its own interrupted claim and redoes that one clip.

## If a box crashed and won't come back

Its claims for un-finished clips would otherwise stay held. Clear stale ones (older than 30 min,
not done) from the **rig** so they get redone:

```bash
temp\asset-venv-matte\Scripts\python.exe tools\asset_matte\process_all.py ^
    --reclaim-orphans --claims-dir D:\kartoff\asset_chips\claims
```

## When the batch is done

- The full chip set + `index.html` viewer are in `D:\kartoff\asset_chips\matte\` on the rig.
- Delete the second box's scratch: `Remove-Item -Recurse -Force C:\kartoff_scratch`.
- Optionally delete `D:\kartoff\asset_chips\claims\` (only after you're sure the batch finished).
````

- [ ] **Step 4: Add a pointer from the asset-matte README**

In `tools/asset_matte/README.md`, under the top intro (after the status blockquote), add a line:

```markdown
> **Running the matte batch on two machines:** see `docs/two-machine-sweep.md` (shared claim
> queue + ship-and-delete; either box Start/Stops independently).
```

- [ ] **Step 5: Verify the launchers reference real paths**

Run: `python -m pytest tests/test_sweep_procconfig.py -v`
Expected: PASS (confirms the env-var names the `.bat` files set are exactly what `procconfig` reads).

- [ ] **Step 6: Commit**

```bash
git add run_console_m1.bat run_console_m2.bat docs/two-machine-sweep.md tools/asset_matte/README.md
git commit -m "docs(matte): two-machine launchers + runbook (SMB share, ops, reclaim)"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| Atomic claim files (claim/done/skip/pending) | T1 |
| Crash recovery (own-reclaim + `--reclaim-orphans`) | T1 (funcs), T3 (startup own-reclaim + reclaim mode), T7 (runbook) |
| Ship-and-delete, `<name>__*` glob, idempotent re-ship | T2, T3 (wired) |
| `--claims-dir`/`--ship-dir` opt-in, byte-identical opt-out | T3 |
| Directory bootstrap (scratch/ship/claims auto-created) | T3 (driver), T6 (console) |
| Claimed-queue loop preserves prefetch; graceful-stop release | T3 |
| Console env plumbing + same GUI on box 2 | T5 (resolver), T6 (app), T7 (launchers) |
| Global progress via `.done` count | T4 (supervisor), T6 (call site) |
| Viewer over the shared union | T6 (viewer target); relies on `make_viewer` globbing the matte dir (verified in spec) |
| Independent per-machine stop-file | T5/T6 (stop derives from local `PROCESS_OUT`), unchanged supervisor stop path |
| Safe stop / power-off semantics | T3 (loop), T7 (runbook) |
| SMB share setup guide | T7 |
| Disruption-free on machine 1 | inherent (no processing when Process off; SMB serving only) — documented, no code |

No gaps.

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every step has full code or an exact command. The one defensive stray (`is_claimed_dir_has`) is explicitly removed in T1 Step 4.

**3. Type consistency:** `try_claim(claims_dir, name, machine_id)`, `ship_clip(out_matte_dir, ship_matte_dir, name)`, `process_cmd(..., claims_dir=None, ship_dir=None)`, `start_processing(..., claims_dir=None, ship_dir=None)`, `process_done_count(manifest_path, claims_dir=None)`, `resolve_process_config(env, data_root_default)` → `ProcessConfig(clips,out,claims,ship,stop,manifest)`, and `app` module constants `CLIPS_DIR/PROCESS_OUT/PROCESS_STOP/PROCESS_MANIFEST/CLAIMS_DIR/SHIP_DIR` — all consistent across the tasks that produce and consume them.
