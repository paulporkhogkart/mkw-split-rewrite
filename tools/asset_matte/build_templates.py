"""Derive the char + kart nametag templates from the recorded clips and commit them
under tools/asset_matte/assets/. Difference method (see memory nametag-mask-undark):
the plate over a KNOWN background (P, idle/present) vs that same background bare
(A, Rally-Bike flourish where the plate drops + the bike floats clear).

  char: present = mario__base idle @CHAR_ROI ; absent = mario__base__rally_bike flourish @CHAR_ROI
  kart: present + absent both = mario__base__rally_bike @PLATE_ROI

Run from repo root with build python:  python tools/asset_matte/build_templates.py
"""
import json, os, glob, sys, subprocess
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # flat `import nametag_core` when run as a script
import nametag_core as nc

CLIPS = "captures_sdr/en_uk/clips"
WORK = "temp/asset_matte/_tpl_work"          # gitignored extraction scratch
OUT = "tools/asset_matte/assets"


def extract_roi_frames(clip_path, roi, tmp):
    """ffmpeg -vsync 0 crop the ROI to PNGs under tmp; return sorted paths (clears tmp first)."""
    if not os.path.exists(clip_path):
        sys.exit(f"clip not found: {clip_path} — record it first")
    x, y, w, h = roi
    os.makedirs(tmp, exist_ok=True)
    for f in glob.glob(os.path.join(tmp, "*.png")):
        os.remove(f)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", clip_path,
                    "-vf", f"crop={w}:{h}:{x}:{y}", "-vsync", "0",
                    os.path.join(tmp, "f%05d.png")], check=True)
    return sorted(glob.glob(os.path.join(tmp, "*.png")))


def _even(paths, k=150):
    if len(paths) <= k:
        return paths
    idx = np.linspace(0, len(paths) - 1, k).round().astype(int)
    return [paths[i] for i in sorted(set(idx.tolist()))]


def _present_absent(paths):
    luma = np.array([float(np.median(cv2.imread(p, cv2.IMREAD_GRAYSCALE))) for p in paths])
    pres = nc.classify_presence(luma, smooth=5)
    return ([p for p, f in zip(paths, pres) if f], [p for p, f in zip(paths, pres) if not f])


def _median_png(paths):
    return np.clip(nc.median_reduce([cv2.imread(p) for p in _even(paths)]), 0, 255).astype(np.uint8)


def _mask4k(P, A, roi):
    """Difference-method soft-alpha mask (ROI), placed into a full-4K uint8 canvas."""
    alpha = nc.diff_to_alpha(P.astype(np.float64), A.astype(np.float64))   # ROI HxW [0,1]
    return np.clip(nc.place_in_canvas(alpha, roi) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def main():
    os.makedirs(OUT, exist_ok=True)
    rally = f"{CLIPS}/mario__base__rally_bike.mkv"
    # CHAR: present = Mario idle plate @CHAR_ROI ; absent = Rally flourish background @CHAR_ROI.
    cP_pres, _ = _present_absent(extract_roi_frames(f"{CLIPS}/mario__base.mkv", nc.CHAR_ROI, f"{WORK}/char_present"))
    _, cA_abs = _present_absent(extract_roi_frames(rally, nc.CHAR_ROI, f"{WORK}/char_absent"))
    char_P, char_A = _median_png(cP_pres), _median_png(cA_abs)
    # KART: present + absent both from Rally @PLATE_ROI.
    k_pres, k_abs = _present_absent(extract_roi_frames(rally, nc.PLATE_ROI, f"{WORK}/kart"))
    kart_P, kart_A = _median_png(k_pres), _median_png(k_abs)
    print(f"char: {len(cP_pres)} present / {len(cA_abs)} absent ; kart: {len(k_pres)} present / {len(k_abs)} absent")

    cv2.imwrite(f"{OUT}/char_P.png", char_P); cv2.imwrite(f"{OUT}/char_A.png", char_A)
    cv2.imwrite(f"{OUT}/kart_P.png", kart_P); cv2.imwrite(f"{OUT}/kart_A.png", kart_A)
    cv2.imwrite(f"{OUT}/nametag_char_mask4k.png", _mask4k(char_P, char_A, nc.CHAR_ROI))
    cv2.imwrite(f"{OUT}/nametag_kart_mask4k.png", _mask4k(kart_P, kart_A, nc.PLATE_ROI))
    json.dump({"char_roi": list(nc.CHAR_ROI), "kart_roi": list(nc.PLATE_ROI),
               "prod_crop_4k": list(nc.PROD_CROP_4K), "out_wh": [nc.OUT_W, nc.OUT_H],
               "source": {"char_present": "mario__base", "absent": "mario__base__rally_bike"},
               "params": {"ALPHA_GAIN": 5.0, "STRENGTH": 1.02, "CSUB": 0.69,
                          "TFLOOR": 0.05, "T_OPAQUE": 0.20}},
              open(f"{OUT}/templates_meta.json", "w"), indent=2)
    print("wrote", OUT, "char_P/A", char_P.shape, "kart_P/A", kart_P.shape)


if __name__ == "__main__":
    main()
