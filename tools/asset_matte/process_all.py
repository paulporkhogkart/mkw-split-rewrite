"""Headless batch driver: extract one seamless idle loop from every capture and matte it
into a transparent RGBA chip. GPU venv (rembg + CUDA).

Per clip: extract_loop.extract (baked 2.0s-kart / detected-char loop rule) -> matte_blankplate
(blank-plate kart pipeline / char pre_darken + birefnet). Manifest-tracked, resumable, and
stop-file aware: the stop-file is checked BETWEEN clips, so a stop never leaves a half-matted
clip and a resume just skips everything already marked done.

  temp/asset-venv-gpu/Scripts/python.exe tools/asset_matte/process_all.py \
      --clips captures_sdr/en_uk/clips --out temp/asset_chips
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

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _REPO):                              # extract_loop needs mkw_tracker on path
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_loop as el
import matte_blankplate as mb


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


def main():
    ap = argparse.ArgumentParser(description="Batch extract+matte transparent idle-loop chips.")
    ap.add_argument("--clips", default=os.path.join(_REPO, "captures_sdr", "en_uk", "clips"))
    ap.add_argument("--out", default=os.path.join(_REPO, "temp", "asset_chips"))
    ap.add_argument("--stop-file", default=None, help="default <out>/.process_stop")
    ap.add_argument("--manifest", default=None, help="default <out>/manifest.json")
    ap.add_argument("--limit", type=int, default=0, help="process at most N pending clips (0 = all)")
    ap.add_argument("--keep-loopframes", action="store_true", help="keep intermediate raw frames")
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    loopdir = os.path.join(out, "loopframes")
    mattedir = os.path.join(out, "matte")
    os.makedirs(mattedir, exist_ok=True)
    stop_file = a.stop_file or os.path.join(out, ".process_stop")
    manifest_path = a.manifest or os.path.join(out, "manifest.json")

    try:                                               # never die on console encoding
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    manifest = load_manifest(manifest_path)
    names = clip_names(a.clips)
    total = len(names)
    base_done = done_count(manifest, names)
    print(f"START total={total} already_done={base_done} clips={a.clips} out={out}", flush=True)

    processed = 0
    for name in names:
        if manifest.get(name, {}).get("status") == "done":
            continue
        if os.path.exists(stop_file):                  # clean stop BETWEEN clips
            print(f"STOPPED stop-file present ({base_done + processed}/{total} done)", flush=True)
            break
        if a.limit and processed >= a.limit:
            print(f"LIMIT {a.limit} reached", flush=True)
            break
        clip = os.path.join(a.clips, name + ".mkv")
        seg_base = os.path.join(loopdir, name)
        t0 = time.time()
        print(f"--- {name} ({base_done + processed + 1}/{total}) segmenting...", flush=True)
        try:
            counts = el.extract_segments(clip, seg_base, name)      # spawn / idle / flourish spans
            kart = el.is_kart_combo(name)
            matted = {}
            for seg in ("spawn", "idle", "flourish"):
                if seg not in counts:
                    continue
                segname = f"{name}__{seg}"
                fd = os.path.join(seg_base, segname)
                print(f"    matting {segname} ({counts[seg]}f)...", flush=True)
                matted[seg] = int(mb.matte_loopframes(   # only the KART flourish drops the plate -> no predark;
                    fd, segname, mattedir, clip=clip,    # the char keeps its nameplate through its flourish
                    apply_predark=not (kart and seg == "flourish"), is_kart=kart))
            if not a.keep_loopframes:
                shutil.rmtree(seg_base, ignore_errors=True)
            manifest[name] = {"status": "done", "kart": kart,
                              "segments": matted, "secs": round(time.time() - t0, 1)}
            save_manifest(manifest_path, manifest)
            processed += 1
            print(f"PROCESSED {name} ({base_done + processed}/{total}) {matted} "
                  f"{time.time() - t0:.0f}s", flush=True)
        except Exception as exc:
            import traceback
            manifest[name] = {"status": "error", "error": str(exc)}
            save_manifest(manifest_path, manifest)
            print(f"ERROR {name}: {exc}", flush=True)
            traceback.print_exc()

    print(f"DONE processed={processed} done_total={done_count(manifest, names)}/{total}", flush=True)


if __name__ == "__main__":
    main()
