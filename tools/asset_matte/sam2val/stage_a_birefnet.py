"""Stage A (asset-venv-matte, birefnet/onnxruntime): per subject, extract the wanted segments,
predark them, and dump the frame-0 birefnet anchor. Writes work/<subject>__<seg>/{frames,anchor}.
NEVER imports matanyone. Run from repo root:

  temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/sam2val/stage_a_birefnet.py
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (os.path.join(_REPO, "tools", "asset_matte"), _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_loop as el
import matte_blankplate as mb

CLIPS = "D:/kartoff/captures_sdr/en_uk/clips"
OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")

# (subject, [segments]) — idle for all 5; spawn also for one kart (off-center test).
# hot_rod gets ALL three segments to reproduce the user's reported flourish (end) + spawn (start) defects.
JOBS = [
    ("mario__base", ["idle"]),
    ("mario__base__hot_rod", ["spawn", "idle", "flourish"]),
    ("mario__base__plushbuggy", ["idle"]),
    ("mario__base__standard_kart", ["idle", "spawn"]),
    ("mario__base__zoom_buggy", ["idle"]),
]


def run_subject(subject, wanted):
    clip = os.path.join(CLIPS, subject + ".mkv")
    kart = el.is_kart_combo(subject)
    seg_raw = os.path.join(OUT, "raw", subject)        # kept out of work/ so stage B/C glob(work/*) is clean
    os.makedirs(seg_raw, exist_ok=True)
    counts = el.extract_segments(clip, seg_raw, subject)          # writes <seg_raw>/<subject>__<seg>/NNN.png
    print(f"{subject}: segments={counts} kart={kart}", flush=True)
    for seg in wanted:
        if seg not in counts:
            print(f"  !! {seg} not detected for {subject}; skipping", flush=True)
            continue
        raw_dir = os.path.join(seg_raw, f"{subject}__{seg}")
        paths = sorted(glob.glob(os.path.join(raw_dir, "*.png")))
        apply_predark = not (kart and seg == "flourish")   # kart flourish drops the plate -> no predark
        pres = mb._build_predark_frames(paths, kart, apply_predark=apply_predark)   # BGR uint8 list
        work = os.path.join(OUT, "work", f"{subject}__{seg}")
        fdir = os.path.join(work, "frames")
        os.makedirs(fdir, exist_ok=True)
        for i, bgr in enumerate(pres):
            cv2.imwrite(os.path.join(fdir, f"{i:03d}.png"), bgr)
        anchor_bgr = pres[0]
        biref = (mb._birefnet(anchor_bgr)[0] > 0.5).astype(np.uint8) * 255
        np.save(os.path.join(work, "anchor_rgb.npy"),
                cv2.cvtColor(anchor_bgr, cv2.COLOR_BGR2RGB))
        np.save(os.path.join(work, "biref_anchor.npy"), biref)
        with open(os.path.join(work, "meta.json"), "w") as f:
            json.dump({"kart": bool(kart), "seg": seg, "n": len(pres)}, f)
        print(f"  {seg}: {len(pres)}f  biref_px={int((biref > 0).sum())}", flush=True)


def main():
    for subject, wanted in JOBS:
        run_subject(subject, wanted)
    print("STAGE_A DONE", flush=True)


if __name__ == "__main__":
    main()
