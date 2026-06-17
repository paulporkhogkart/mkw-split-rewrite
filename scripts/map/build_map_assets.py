#!/usr/bin/env python3
"""Build the World Map web assets from the committed source art (deterministic).

Outputs web/public/map/{base.jpg, sprites/<slug>.png, manifest.json}.
Requires ffmpeg on PATH to decode the AVIF source layers. Only needed to *regenerate*
assets; building the website itself does not need ffmpeg.

  python scripts/map/build_map_assets.py
"""
import importlib.util, json, subprocess, tempfile
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "sources"
LABELS = Path(__file__).resolve().parent / "labels.json"
OUT = ROOT / "web" / "public" / "map"

DISPLAY_W = 1100          # CSS width the map renders at
BASE_W = DISPLAY_W * 2    # 2x export for retina crispness
RR_CENTER = (0.500, 0.734)
RR_WIDTH = 0.089          # normalized width of the Rainbow Road sprite


def load_canonical():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def decode_avif(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), str(dst)], check=True)


def smoothstep_alpha(crop, lo=16.0, hi=64.0):
    b = crop.max(axis=2).astype(np.float32)
    a = np.clip((b - lo) / (hi - lo), 0, 1)
    return (a * a * (3 - 2 * a) * 255).astype(np.uint8)


def detect_boxes(stages):
    H, W = stages.shape[:2]
    m = (stages.max(axis=2) > 50).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    boxes = [(int(x), int(y), int(w), int(h)) for i in range(1, n)
             for x, y, w, h, a in [st[i]] if a >= 3000 and w < W * 0.55 and h < H * 0.55]
    boxes.sort(key=lambda b: (round((b[1] + b[3] / 2) / 120), b[0]))
    return boxes


def orb_transform(a, b):
    o = cv2.ORB_create(6000)
    ka, da = o.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), None)
    kb, db = o.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), None)
    mm = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db),
                key=lambda z: z.distance)[:500]
    src = np.float32([ka[z.queryIdx].pt for z in mm]).reshape(-1, 1, 2)
    dst = np.float32([kb[z.trainIdx].pt for z in mm]).reshape(-1, 1, 2)
    M, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=5.0)
    return M


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sprites").mkdir(exist_ok=True)
    canon = load_canonical()
    labels = json.loads(LABELS.read_text())

    with tempfile.TemporaryDirectory() as td:
        inner_p, stages_p = Path(td) / "inner.png", Path(td) / "stages.png"
        decode_avif(SRC / "MarioKartWorld_World_Map_Inner.webp", inner_p)
        decode_avif(SRC / "MarioKartWorld_World_Map_Stages.webp", stages_p)
        inner, stages = cv2.imread(str(inner_p)), cv2.imread(str(stages_p))
    hires = cv2.imread(str(SRC / "highresmap.jpeg"))
    rr = cv2.imread(str(SRC / "MKWorld_Icon_Rainbow_Road.webp"), cv2.IMREAD_UNCHANGED)
    Hs, Ws = stages.shape[:2]; Hh, Wh = hires.shape[:2]

    M = orb_transform(inner, hires)
    scale = float(np.hypot(M[0, 0], M[0, 1]))

    def to_hi(x, y):
        p = M @ np.array([x, y, 1.0]); return float(p[0]), float(p[1])

    courses = []  # {sprite(BGRA), hit[x,y,w,h], spr[x,y,w,h]} in normalized base coords
    for (x, y, w, h) in detect_boxes(stages):
        PAD = 10
        px0, py0 = max(0, x - PAD), max(0, y - PAD)
        px1, py1 = min(Ws, x + w + PAD), min(Hs, y + h + PAD)
        crop = stages[py0:py1, px0:px1]
        mask = smoothstep_alpha(crop)
        tw, th = round((px1 - px0) * scale), round((py1 - py0) * scale)
        tmpl = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
        tmask = cv2.resize(mask, (tw, th), interpolation=cv2.INTER_AREA)
        pcx, pcy = to_hi((px0 + px1) / 2, (py0 + py1) / 2)
        S = 70
        rx0, ry0 = max(0, int(pcx - tw / 2 - S)), max(0, int(pcy - th / 2 - S))
        rx1, ry1 = min(Wh, int(pcx + tw / 2 + S)), min(Hh, int(pcy + th / 2 + S))
        res = cv2.matchTemplate(hires[ry0:ry1, rx0:rx1], tmpl, cv2.TM_CCOEFF_NORMED, mask=tmask)
        res[~np.isfinite(res)] = 0
        _, _, _, loc = cv2.minMaxLoc(res)
        tlx, tly = rx0 + loc[0], ry0 + loc[1]
        sprite = cv2.cvtColor(hires[tly:tly + th, tlx:tlx + tw], cv2.COLOR_BGR2BGRA)
        sprite[:, :, 3] = tmask[:sprite.shape[0], :sprite.shape[1]]
        bcx, bcy = to_hi(x + w / 2, y + h / 2)
        hcx, hcy = bcx + (tlx + tw / 2 - pcx), bcy + (tly + th / 2 - pcy)
        cw, ch = w * scale, h * scale
        courses.append({"sprite": sprite,
                        "hit": [(hcx - cw / 2) / Wh, (hcy - ch / 2) / Hh, cw / Wh, ch / Hh],
                        "spr": [tlx / Wh, tly / Hh, tw / Wh, th / Hh]})

    # Rainbow Road: not on the base art; placed from the supplied icon at the locked rect.
    rrw = round(RR_WIDTH * Wh); rrh = round(rrw * rr.shape[0] / rr.shape[1])
    cx, cy, wn = *RR_CENTER, RR_WIDTH
    hn = wn * (Wh / Hh) * (rr.shape[0] / rr.shape[1])
    courses.append({"sprite": cv2.resize(rr, (rrw, rrh), interpolation=cv2.INTER_AREA),
                    "hit": [cx - wn * 0.21, cy - hn * 0.24, wn * 0.42, hn * 0.42],
                    "spr": [cx - wn / 2, cy - hn / 2, wn, hn]})

    # Assign each course its slug by the nearest labeled center.
    manifest = {"base": {"w": BASE_W, "h": round(Hh * BASE_W / Wh)}, "courses": []}
    for c in courses:
        ccx, ccy = c["hit"][0] + c["hit"][2] / 2, c["hit"][1] + c["hit"][3] / 2
        slug = min(labels, key=lambda L: (L["cx"] - ccx) ** 2 + (L["cy"] - ccy) ** 2)["slug"]
        cv2.imwrite(str(OUT / "sprites" / f"{slug}.png"), c["sprite"])
        manifest["courses"].append({
            "slug": slug, "name": canon[slug],
            "hit": {k: round(v, 5) for k, v in zip("xywh", c["hit"])},
            "spr": {k: round(v, 5) for k, v in zip("xywh", c["spr"])}})

    base = cv2.resize(hires, (BASE_W, manifest["base"]["h"]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "base.jpg"), base, [cv2.IMWRITE_JPEG_QUALITY, 88])
    manifest["courses"].sort(key=lambda c: c["slug"])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))

    slugs = [c["slug"] for c in manifest["courses"]]
    assert len(slugs) == 30 and len(set(slugs)) == 30 and set(slugs) == set(canon), \
        f"slug mismatch: {set(slugs) ^ set(canon)}"
    print(f"built {len(slugs)} courses -> {OUT}")


if __name__ == "__main__":
    main()
