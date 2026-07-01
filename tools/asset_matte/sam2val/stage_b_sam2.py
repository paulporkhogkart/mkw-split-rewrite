"""Stage B (sam2-venv, torch+sam2): read each stage-A anchor frame + birefnet mask and write the
SAM2-refined anchor mask. NEVER imports rembg/onnxruntime/matte_blankplate. Run from repo root:

  temp/sam2-venv/Scripts/python.exe tools/asset_matte/sam2val/stage_b_sam2.py
"""
import glob
import os
import sys

import numpy as np
import torch
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "tools", "asset_matte"))     # flat import of sam2_anchor
import sam2_anchor as sa

OUT = ("C:/Users/Paul/AppData/Local/Temp/claude/C--development-mkw-split-rewrite/"
       "209024a0-084e-4c91-8a28-32378aa23e69/scratchpad/sam2val_out")
CKPT = os.path.join(_REPO, "temp", "sam2_ckpt", "sam2.1_hiera_base_plus.pt")
CFG = "configs/sam2.1/sam2.1_hiera_b+.yaml"


def main():
    predictor = SAM2ImagePredictor(build_sam2(CFG, CKPT, device="cuda"))
    work = os.path.join(OUT, "work")
    dirs = sorted(d for d in glob.glob(os.path.join(work, "*"))
                  if os.path.exists(os.path.join(d, "biref_anchor.npy")))
    for d in dirs:
        rgb = np.load(os.path.join(d, "anchor_rgb.npy"))
        biref = np.load(os.path.join(d, "biref_anchor.npy"))
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            mask = sa.sam2_anchor_mask(predictor, rgb, biref)
        np.save(os.path.join(d, "sam2_anchor.npy"), mask)
        name = os.path.basename(d)
        print(f"{name}: sam2_px={int((mask > 0).sum())}  biref_px={int((biref > 0).sum())}", flush=True)
    print("STAGE_B DONE", flush=True)


if __name__ == "__main__":
    main()
