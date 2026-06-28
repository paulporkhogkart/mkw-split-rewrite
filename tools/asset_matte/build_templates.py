"""ONE-TIME: derive the char + kart nametag templates from the surviving
temp/asset_eyetest/nametag caches and commit them under tools/asset_matte/assets/.
The source clip mario__base__rally_bike.mkv is gone; the _work ROI caches are the
only remaining source, so this rescues the validated templates into the repo.

Run from repo root with build python:  python tools/asset_matte/build_templates.py
"""
import json, os, glob, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # for flat `import nametag_core` when run as a script
import nametag_core as nc

WORK = "temp/asset_eyetest/nametag/_work"
MASKS = "temp/asset_eyetest/nametag_mask"
OUT = "tools/asset_matte/assets"

# Caches that survived (the rally_bike clip itself is gone). The CHAR_ROI rally extraction
# was never cached (empty dir), but CHAR_ROI is a sub-window of PLATE_ROI of the SAME rally
# frames, so the char ABSENT background is recovered as that sub-window of the kart absent
# median (median commutes with spatial cropping). Matches the validated char_template's A.
CHAR_PRESENT = f"{WORK}/mario__base__roi2378_1604_1178_226"   # Mario idle at CHAR_ROI (plate present)
KART_BOTH    = f"{WORK}/mario__base__rally_bike"               # Rally at PLATE_ROI (present + absent)


def _char_subwindow(plate_img):
    """CHAR_ROI crop out of a PLATE_ROI image (same scene, smaller window). CHAR extends 2px
    below PLATE, so the bottom is edge-padded — those rows are plate-free background anyway."""
    dx = nc.CHAR_ROI[0] - nc.PLATE_ROI[0]      # 18
    dy = nc.CHAR_ROI[1] - nc.PLATE_ROI[1]      # 2
    w, h = nc.CHAR_ROI[2], nc.CHAR_ROI[3]      # 1178, 226
    sub = plate_img[dy:dy + h, dx:dx + w]
    if sub.shape[0] < h:
        sub = np.vstack([sub, np.repeat(sub[-1:], h - sub.shape[0], axis=0)])
    return sub


def _paths(d):
    p = sorted(glob.glob(os.path.join(d, "*.png")))
    if not p:
        raise SystemExit(f"no cached frames in {d} (temp cache missing — cannot rebuild templates)")
    return p


def _present_absent(paths):
    luma = np.array([float(np.median(cv2.imread(p, cv2.IMREAD_GRAYSCALE))) for p in paths])
    pres = nc.classify_presence(luma, smooth=5)
    return ([p for p, f in zip(paths, pres) if f], [p for p, f in zip(paths, pres) if not f])


def _even(paths, k=150):
    if len(paths) <= k:
        return paths
    idx = np.linspace(0, len(paths) - 1, k).round().astype(int)
    return [paths[i] for i in sorted(set(idx.tolist()))]


def _median_png(paths):
    return np.clip(nc.median_reduce([cv2.imread(p) for p in _even(paths)]), 0, 255).astype(np.uint8)


def main():
    os.makedirs(OUT, exist_ok=True)
    # KART: present + absent both from Rally at PLATE_ROI.
    k_pres, k_abs = _present_absent(_paths(KART_BOTH))
    kart_P = _median_png(k_pres); kart_A = _median_png(k_abs)
    # CHAR: present from Mario idle (plate present) at CHAR_ROI; absent (background) recovered as
    # the CHAR_ROI sub-window of the kart absent median (the empty CHAR-ROI rally cache).
    char_P = _median_png(_present_absent(_paths(CHAR_PRESENT))[0])
    char_A = _char_subwindow(kart_A)
    cv2.imwrite(f"{OUT}/char_P.png", char_P); cv2.imwrite(f"{OUT}/char_A.png", char_A)
    cv2.imwrite(f"{OUT}/kart_P.png", kart_P); cv2.imwrite(f"{OUT}/kart_A.png", kart_A)
    # validated hand-checked 4K masks (copied verbatim)
    cv2.imwrite(f"{OUT}/nametag_char_mask4k.png",
                cv2.imread(f"{MASKS}/char/nametag_char_4k.png", cv2.IMREAD_GRAYSCALE))
    cv2.imwrite(f"{OUT}/nametag_kart_mask4k.png",
                cv2.imread(f"{MASKS}/nametag_mask_4k.png", cv2.IMREAD_GRAYSCALE))
    json.dump({"char_roi": list(nc.CHAR_ROI), "kart_roi": list(nc.PLATE_ROI),
               "prod_crop_4k": list(nc.PROD_CROP_4K), "out_wh": [nc.OUT_W, nc.OUT_H],
               "params": {"ALPHA_GAIN": 5.0, "STRENGTH": 1.02, "CSUB": 0.69,
                          "TFLOOR": 0.05, "T_OPAQUE": 0.20}},
              open(f"{OUT}/templates_meta.json", "w"), indent=2)
    print("char_P/A", char_P.shape, char_A.shape, "kart_P/A", kart_P.shape, kart_A.shape, "-> ", OUT)


if __name__ == "__main__":
    main()
