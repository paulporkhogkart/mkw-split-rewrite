"""Validate re-matted standalone chars against the 2026-07-17 artifact symptoms.

  python tools/asset_matte/validate_char_predark.py --matte-dir <out>/matte \
      --chars peepa__base,penguin__base,rosalina__base,mario__base,luigi__base \
      --out temp/char_predark_validation

Sheets per char/segment: plate-band checker composite + alpha x3 heat, sampled across the
segment. Hard gates (exit 1 on failure):
  G1  peepa__base idle: plate-band alpha == 0 on every sampled frame (was 0 pre-fix; any
      junk the new predark introduced would show here first).
  G2  every char: flourish tail spike gone — mean band alpha over the LAST 6 frames must be
      <= 1.15 x the mean over the preceding 6 (old peepa spiked 93 -> 627, rosalina 19.4k ->
      23.3k at the tail; a settled hold pose is flat).
Everything subtler (text ghosts at low alpha) is for the eyetest sheets, not gated."""
import argparse
import glob
import os
import sys

import cv2
import numpy as np

BAND_Y = 860


def band_alpha(png):
    im = cv2.imread(png, cv2.IMREAD_UNCHANGED)
    return int(im[..., 3][BAND_Y:, :].astype(np.uint32).sum() / 255)


def checker(h, w, s=22):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)[..., None]
    return np.where(m, 205, 150).astype(np.uint8).repeat(3, axis=2)


def label(img, txt):
    im = img.copy()
    cv2.putText(im, txt, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(im, txt, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 1, cv2.LINE_AA)
    return im


def sheet_rows(pngs, idxs, tag):
    cells = []
    for i in idxs:
        im = cv2.imread(pngs[i], cv2.IMREAD_UNCHANGED)
        a = im[..., 3:4].astype(np.float32) / 255.0
        comp = (im[..., :3].astype(np.float32) * a
                + checker(*im.shape[:2]) * (1 - a)).astype(np.uint8)[BAND_Y:, :]
        heat = cv2.applyColorMap(np.clip(im[..., 3].astype(np.float32) * 3, 0, 255)
                                 .astype(np.uint8), cv2.COLORMAP_INFERNO)[BAND_Y:, :]
        cells += [label(comp, f"{tag}[{i}] checker"), label(heat, f"{tag}[{i}] alpha x3")]
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matte-dir", required=True)
    ap.add_argument("--chars", required=True, help="comma-separated item names")
    ap.add_argument("--out", default=os.path.join("temp", "char_predark_validation"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    failures = []
    for name in a.chars.split(","):
        for seg in ("idle", "flourish"):
            d = os.path.join(a.matte_dir, f"{name}__{seg}_frames")
            pngs = sorted(glob.glob(os.path.join(d, "*.png")))
            if not pngs:
                failures.append(f"{name} {seg}: NO FRAMES at {d}")
                continue
            n = len(pngs)
            idxs = sorted(set([0, n // 4, n // 2, 3 * n // 4, n - 1]
                              + (list(range(max(0, n - 12), n)) if seg == "flourish" else [])))
            cells = sheet_rows(pngs, idxs, seg)
            h = max(c.shape[0] for c in cells); w = max(c.shape[1] for c in cells)
            cells = [cv2.copyMakeBorder(c, 0, h - c.shape[0], 0, w - c.shape[1],
                                        cv2.BORDER_CONSTANT, value=(30, 30, 30)) for c in cells]
            rows = [np.hstack(cells[i:i + 2]) for i in range(0, len(cells), 2)]
            out_png = os.path.join(a.out, f"{name}__{seg}.png")
            cv2.imwrite(out_png, np.vstack(rows))
            series = [band_alpha(p) for p in pngs]
            print(f"{name} {seg}: band-alpha min={min(series)} max={max(series)} -> {out_png}")
            if seg == "idle" and name == "peepa__base" and max(series) > 0:
                failures.append(f"G1 {name} idle: band alpha max {max(series)} != 0")
            if seg == "flourish" and n >= 12:
                pre = np.mean(series[-12:-6]); tail = np.mean(series[-6:])
                if tail > max(1.15 * pre, pre + 60):
                    failures.append(f"G2 {name} flourish tail spike: {pre:.0f} -> {tail:.0f}")
    if failures:
        print("FAIL\n" + "\n".join("  " + f for f in failures))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
