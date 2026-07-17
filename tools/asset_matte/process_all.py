"""Headless batch driver: extract one seamless idle loop from every capture and matte it
into a transparent RGBA chip. GPU venv (rembg + CUDA).

Per clip: extract_loop.extract (baked 2.0s-kart / detected-char loop rule) -> matte_blankplate
(blank-plate kart pipeline / char pre_darken + birefnet). Manifest-tracked, resumable, and
stop-file aware: the stop-file is checked BETWEEN clips, so a stop never leaves a half-matted
clip and a resume just skips everything already marked done.

  temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/process_all.py \
      --clips captures_sdr/en_uk/clips --out temp/asset_chips
  # NB: run under asset-venv-matte (onnxruntime birefnet + torch cu128). The matanyone
  # worker spawns via sys.executable, so asset-venv-gpu (no torch) would fail the default engine.
  ... --limit 1            # process at most N pending clips then exit (dry-run)
  ... --keep-loopframes    # don't delete the intermediate raw frames after matting
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _REPO):                              # extract_loop needs mkw_tracker on path
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_loop as el
import matte_blankplate as mb
import matte_matanyone as mm
import claims
import cuda_recovery
import ship


def _seg_task(clip, seg_base, name):
    """Prefetch worker: CPU-only segmentation of an upcoming clip. Runs in a child
    process (Windows spawn re-imports this module, so sys.path is bootstrapped) while
    the parent's GPU mattes the current clip — the GPU never waits on a decode."""
    return el.extract_segments(clip, seg_base, name)


def clip_names(clips_dir):
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(clips_dir, "*.mkv")))


def load_manifest(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_manifest(path, m):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=1)
    os.replace(tmp, path)                              # atomic — a kill mid-write can't corrupt it


def done_count(manifest, names):
    return sum(1 for n in names if manifest.get(n, {}).get("status") == "done")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Batch extract+matte transparent idle-loop chips.")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--out", default=os.path.join(_REPO, "temp", "asset_chips"))
    ap.add_argument("--stop-file", default=None, help="default <out>/.process_stop")
    ap.add_argument("--manifest", default=None, help="default <out>/manifest.json")
    ap.add_argument("--limit", type=int, default=0, help="process at most N pending clips (0 = all)")
    ap.add_argument("--keep-loopframes", action="store_true", help="keep intermediate raw frames")
    ap.add_argument("--prefetch", type=int, default=2,
                    help="segment upcoming clips in N worker processes while the GPU mattes "
                         "the current one (0 = old serial behaviour; worker span-lines may "
                         "interleave with matte logs)")
    ap.add_argument("--claims-dir", default=None,
                    help="shared dir of atomic per-clip claim files (enables multi-machine mode)")
    ap.add_argument("--ship-dir", default=None,
                    help="move each finished clip's matte/<name>__* here then delete local")
    ap.add_argument("--machine-id", default=None, help="claim owner id (default: hostname)")
    ap.add_argument("--reclaim-orphans", action="store_true",
                    help="one-shot: clear stale in-progress claims (a crashed box) then exit")
    ap.add_argument("--stale-secs", type=float, default=1800,
                    help="orphan-claim age threshold for --reclaim-orphans")
    a = ap.parse_args(argv)

    out = os.path.abspath(a.out)
    loopdir = os.path.join(out, "loopframes")
    mattedir = os.path.join(out, "matte")
    os.makedirs(mattedir, exist_ok=True)
    stop_file = a.stop_file or os.path.join(out, ".process_stop")
    manifest_path = a.manifest or os.path.join(out, "manifest.json")

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

    try:                                               # never die on console encoding
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    manifest = load_manifest(manifest_path)
    names = clip_names(a.clips)
    total = len(names)
    base_done = done_count(manifest, names)
    print(f"START total={total} already_done={base_done} clips={a.clips} out={out}", flush=True)

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
    exit_code = 0
    while True:
        if os.path.exists(stop_file):             # clean stop BETWEEN clips
            print(f"STOPPED stop-file present ({base_done + processed}/{total} done)", flush=True)
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
            idle_resume = counts.get("idle_resume", 0)   # kart post-flourish idle handoff phase
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
                    predark_raw_tail=el.char_flourish_raw_tail(kart, seg, counts),
                    direction=mm.segment_direction(kart, seg)))
            if not a.keep_loopframes:
                shutil.rmtree(seg_base, ignore_errors=True)
            manifest[name] = {"status": "done", "kart": kart,
                              "segments": matted, "idle_resume": idle_resume,
                              "flourish_fallback": bool(counts.get("flourish_fallback", False)),
                              "secs": round(time.time() - t0, 1)}
            save_manifest(manifest_path, manifest)
            if a.ship_dir:                        # ship BEFORE marking done: bytes on share first
                ship.ship_clip(mattedir, os.path.join(a.ship_dir, "matte"), name)
            if a.claims_dir:
                claims.mark_done(a.claims_dir, name)
            processed += 1
            print(f"PROCESSED {name} ({base_done + processed}/{total}) {matted} "
                  f"{time.time() - t0:.0f}s", flush=True)
        except cuda_recovery.CudaContextLost as exc:
            # NOT this clip's fault: the birefnet CUDA context died (a TDR / GPU driver reset) and an
            # in-process rebuild+retry didn't recover it. Leave the clip PENDING (don't mark error),
            # put it back on the queue so the post-loop cleanup releases its claim + loopframes, and
            # stop cleanly so a FRESH process (guaranteed-fresh context) resumes from the manifest.
            # run_matte.bat auto-restarts on exit 75 (progress made) / 76 (no progress -> maybe wedged).
            queue.insert(0, name)
            print(f"CUDA_CONTEXT_LOST on {name}: {exc}", flush=True)
            print(f"STOPPING for a fresh process ({base_done + processed}/{total} done); "
                  f"{name} stays pending and retries on restart.", flush=True)
            exit_code = (cuda_recovery.EXIT_CUDA_LOST_PROGRESS if processed > 0
                         else cuda_recovery.EXIT_CUDA_LOST_NOPROG)
            break
        except Exception as exc:
            import traceback
            manifest[name] = {"status": "error", "error": str(exc)}
            save_manifest(manifest_path, manifest)
            print(f"ERROR {name}: {exc}", flush=True)
            traceback.print_exc()

    if ex is not None:
        ex.shutdown(cancel_futures=True)          # cancel queued prefetches; wait for running ones
    if a.claims_dir:                              # release clips we claimed but didn't matte (stop/
        for n in queue:                           # limit break); AFTER shutdown so no worker re-creates
            claims.release(a.claims_dir, n)        # a loopframe dir we're deleting. queue is [] on a
            shutil.rmtree(os.path.join(loopdir, n), ignore_errors=True)  # natural finish -> no-op.
    if a.ship_dir:                                # publish OUR manifest to the share so per-clip data
        try:                                      # (idle_resume) reaches box 1's viewer. Own file per
            save_manifest(os.path.join(             # machine (disjoint clips, no clobber); make_viewer
                a.ship_dir, f"manifest.{machine_id}.json"), manifest)  # unions every manifest*.json.
        except OSError:                           # a share hiccup here must never fail the run
            pass
    done_total = claims.count_done(a.claims_dir) if a.claims_dir else done_count(manifest, names)
    print(f"DONE processed={processed} done_total={done_total}/{total}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main() or 0)
