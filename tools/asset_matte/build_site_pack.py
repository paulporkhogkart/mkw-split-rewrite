"""Build the site chip pack: matte masters -> sprite-sheet webps + sil masks + manifest.

Usage (production values pending the A/B recipe lock):
  python tools/asset_matte/build_site_pack.py --src D:\\kartoff\\asset_chips \\
      --out D:\\kartoff\\asset_chips\\site_pack --scale 0.2 --fps 30 \\
      --quality 60 --alpha-bits 5 --workers 12

Masters are READ-ONLY. Resume: combos recorded done in <out>/book.json are skipped;
delete book.json (or --force) to re-encode. Safe to interrupt (book written per combo).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time

from PIL import Image

# Runnable both as a module and as a script (repo-root imports).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tools.asset_matte import site_pack as sp                     # noqa: E402
from tools.asset_matte.sil_masks import write_sil_masks           # noqa: E402

ANIM_ORDER = ["spawn", "idle", "flourish"]


def plan_combos(src: str) -> dict[str, list[str]]:
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        masters = json.load(f)
    plan: dict[str, list[str]] = {}
    for name, e in sorted(masters.items()):
        if e.get("status") != "done":
            continue
        anims = []
        for anim in ANIM_ORDER:
            if anim not in e.get("segments", {}):
                continue
            d = os.path.join(src, "matte", f"{name}__{anim}_frames")
            if not os.path.isdir(d):
                print(f"warn: {name} missing {anim} frames dir, skipping that anim", file=sys.stderr)
                continue
            anims.append(anim)
        if anims:
            plan[name] = anims
    return plan


def _load_frames(d: str, step: int) -> list[Image.Image]:
    files = sorted(f for f in os.listdir(d) if f.endswith(".png"))
    return [Image.open(os.path.join(d, f)).convert("RGBA") for f in files[::step]]


def process_combo(src: str, out_chips: str, name: str, anims: list[str],
                  scale: float, fps: int, quality: int, alpha_bits: int) -> dict:
    """Worker: encode one combo's sheets + sil masks; return its manifest entry."""
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        master = json.load(f)[name]
    step = sp.subsample_step(fps)
    entry: dict = {"kart": bool(master.get("kart")), "anims": {}}
    total = 0
    fw = fh = None
    for anim in anims:
        frames = _load_frames(os.path.join(src, "matte", f"{name}__{anim}_frames"), step)
        if fw is None:
            fw, fh = sp.encode_size(*frames[0].size, scale)
        frames = [sp.quant_alpha(sp.premul_resize(f, (fw, fh)), alpha_bits) for f in frames]
        total += sp.encode_anim(frames, os.path.join(out_chips, f"{name}__{anim}.webp"), quality)
        write_sil_masks(frames, name, anim, out_chips)
        entry["anims"][anim] = sp.manifest_anim_entry(len(frames), fw, fh)
    if "idle" in entry["anims"]:
        entry["idle_resume"] = sp.encode_idle_resume(
            int(master.get("idle_resume", 0)), step, entry["anims"]["idle"]["frames"])
    return {"name": name, "entry": entry, "bytes": total, "fw": fw, "fh": fh}


def _worker(job):
    try:
        return process_combo(*job)
    except Exception as e:  # keep the batch alive; the combo stays pending in the book
        return {"name": job[2], "error": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="asset_chips root (matte/ + manifest.json)")
    ap.add_argument("--out", required=True, help="site_pack output root")
    ap.add_argument("--scale", type=float, required=True)
    ap.add_argument("--fps", type=int, required=True)
    ap.add_argument("--quality", type=int, required=True)
    ap.add_argument("--alpha-bits", type=int, required=True)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--force", action="store_true", help="ignore book.json, re-encode everything")
    ap.add_argument("--only", nargs="*", help="limit to these combo names (A/B sampling)")
    args = ap.parse_args(argv)

    out_chips = os.path.join(args.out, "chips")
    os.makedirs(out_chips, exist_ok=True)
    book_path = os.path.join(args.out, "book.json")
    book = {}
    if os.path.exists(book_path) and not args.force:
        with open(book_path, encoding="utf-8") as f:
            book = json.load(f)

    plan = plan_combos(args.src)
    if args.only:
        plan = {k: v for k, v in plan.items() if k in set(args.only)}
    pending = {k: v for k, v in plan.items() if not book.get(k, {}).get("done")}
    print(f"{len(plan)} combos planned, {len(pending)} pending, workers={args.workers}")

    jobs = [(args.src, out_chips, name, anims, args.scale, args.fps,
             args.quality, args.alpha_bits) for name, anims in pending.items()]
    combos_manifest = {k: book[k]["entry"] for k in plan if book.get(k, {}).get("done")}
    fw = fh = None
    t0, done, failed = time.time(), 0, 0

    def _record(res):
        nonlocal fw, fh, done, failed
        if "error" in res:
            failed += 1
            print(f"FAIL {res['name']}: {res['error']}", file=sys.stderr)
            return
        done += 1
        fw, fh = fw or res["fw"], fh or res["fh"]
        combos_manifest[res["name"]] = res["entry"]
        book[res["name"]] = {"done": True, "entry": res["entry"], "bytes": res["bytes"]}
        tmp = book_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(book, f)
        os.replace(tmp, book_path)
        if done % 25 == 0:
            rate = done / (time.time() - t0)
            print(f"{done}/{len(jobs)} ({rate:.1f}/s, eta {int((len(jobs)-done)/max(rate,1e-6)/60)}m)")

    if args.workers <= 1:
        for j in jobs:
            _record(_worker(j))
    else:
        with mp.Pool(args.workers) as pool:
            for res in pool.imap_unordered(_worker, jobs):
                _record(res)

    if fw is None and combos_manifest:  # resume run with nothing new: recover fw/fh from a sheet
        any_name = next(iter(combos_manifest))
        anim = next(iter(combos_manifest[any_name]["anims"]))
        e = combos_manifest[any_name]["anims"][anim]
        with Image.open(os.path.join(out_chips, f"{any_name}__{anim}.webp")) as im:
            fw, fh = im.size[0] // e["cols"], im.size[1] // e["rows"]

    manifest = {"version": 1, "scale": args.scale, "fps": args.fps,
                "fw": fw, "fh": fh, "combos": combos_manifest}
    with open(os.path.join(out_chips, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, separators=(",", ":"))
    print(f"done: {done} encoded, {failed} failed, {len(combos_manifest)} in manifest")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
