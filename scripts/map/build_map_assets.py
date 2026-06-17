#!/usr/bin/env python3
"""Build the World Map web assets.

Base  : the hi-res map with the baked course icons removed (seamless-clone of the warped
        stage-less `inner` terrain) and a sharp drop-shadow baked back under each course.
Sprites: the official transparent icon PNGs (scripts/map/icons/<slug>.png), placed on their
        spots. On hover the frontend lifts each sprite off its baked shadow.

Why this shape: the icons are CLEAN transparent PNGs (no crushed edges), and the shadow is baked
INTO the base (not a CSS layer), so nothing composites a dark ring around the soft icon edges.
Requires libavif (pillow-avif-plugin) to decode the AVIF source layers; build-time only.

  pip install pillow-avif-plugin
  python scripts/map/build_map_assets.py
"""
import importlib.util, json
from pathlib import Path
import cv2, numpy as np
from PIL import Image
import pillow_avif  # noqa: F401  - registers the AVIF decoder on PIL

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SRC = HERE / "sources"
ICONS = HERE / "icons"
LABELS = HERE / "labels.json"
OUT = ROOT / "web" / "public" / "map"

DISPLAY_W = 1100
BASE_W = DISPLAY_W * 2          # 2x export for retina crispness
RR_CENTER = (0.500, 0.734)
RR_WIDTH = 0.092               # Rainbow Road sprite width (normalized) - not on the base art
CROP_PAD = 10
MATCH_SLACK = 70
SNAP_GATE = 30                # px: reject a local match that drifts this far from the labeled centre
SHADOW_DARK = 0.5             # how much the baked shadow darkens the terrain (0..1)
SHADOW_DY = 0.06             # baked shadow offset straight down, as a fraction of icon height


def load_canonical():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def load_avif(path):
    return cv2.cvtColor(np.array(Image.open(path).convert("RGBA")), cv2.COLOR_RGBA2BGRA)


def alpha_bbox(bgra):
    ys, xs = (bgra[:, :, 3] > 16).nonzero()
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def detect_boxes(alpha):
    H, W = alpha.shape
    m = (alpha > 40).astype(np.uint8)
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
    if M is None:
        raise RuntimeError("ORB alignment failed - not enough matches between inner and hires")
    return M


def main():
    (OUT / "sprites").mkdir(parents=True, exist_ok=True)
    canon = load_canonical()
    labels = json.loads(LABELS.read_text())
    sprites = {p.stem: load_avif(p) if p.suffix == ".webp" else cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
               for p in ICONS.glob("*.png")}

    stages = load_avif(SRC / "MarioKartWorld_World_Map_Stages.webp"); sa = stages[:, :, 3]; sbgr = stages[:, :, :3]
    inner = load_avif(SRC / "MarioKartWorld_World_Map_Inner.webp")[:, :, :3]
    hires = cv2.imread(str(SRC / "highresmap.jpeg"))
    Hs, Ws = sa.shape; Hh, Wh = hires.shape[:2]
    M = orb_transform(inner, hires); scale = float(np.hypot(M[0, 0], M[0, 1]))
    def to_hi(x, y):
        p = M @ np.array([x, y, 1.0]); return float(p[0]), float(p[1])

    # 1) locate each baked course: snapped centre (cx,cy) + on-map size (ow,oh), in hi-res px.
    placed = []      # (slug, cx, cy, ow, oh)
    icon_mask = np.zeros((Hh, Wh), np.uint8)   # baked-icon footprints to erase from the base
    for (x, y, w, h) in detect_boxes(sa):
        px0, py0 = max(0, x - CROP_PAD), max(0, y - CROP_PAD)
        px1, py1 = min(Ws, x + w + CROP_PAD), min(Hs, y + h + CROP_PAD)
        tw, th = round((px1 - px0) * scale), round((py1 - py0) * scale)
        tmask = cv2.resize(sa[py0:py1, px0:px1], (tw, th))
        pcx, pcy = to_hi((px0 + px1) / 2, (py0 + py1) / 2)
        rx0, ry0 = max(0, int(pcx - tw / 2 - MATCH_SLACK)), max(0, int(pcy - th / 2 - MATCH_SLACK))
        rx1, ry1 = min(Wh, int(pcx + tw / 2 + MATCH_SLACK)), min(Hh, int(pcy + th / 2 + MATCH_SLACK))
        res = cv2.matchTemplate(hires[ry0:ry1, rx0:rx1], cv2.resize(sbgr[py0:py1, px0:px1], (tw, th)),
                                cv2.TM_CCOEFF_NORMED, mask=tmask)
        res[~np.isfinite(res)] = 0
        _, _, _, loc = cv2.minMaxLoc(res)
        mcx, mcy = rx0 + loc[0] + tw / 2, ry0 + loc[1] + th / 2
        lab = min(labels, key=lambda L: (L["cx"] - pcx / Wh) ** 2 + (L["cy"] - pcy / Hh) ** 2)
        lcx, lcy = lab["cx"] * Wh, lab["cy"] * Hh
        if (mcx - lcx) ** 2 + (mcy - lcy) ** 2 > SNAP_GATE ** 2:   # match drifted -> trust the label
            mcx, mcy = lcx, lcy
        # erase the baked icon (the stages footprint at this spot) from the base
        bx0, by0 = int(mcx - tw / 2), int(mcy - th / 2)
        sub = icon_mask[max(0, by0):by0 + th, max(0, bx0):bx0 + tw]
        sub[:] = np.maximum(sub, (tmask[:sub.shape[0], :sub.shape[1]] > 24).astype(np.uint8) * 255)
        # on-map icon size = the baked core box (w,h scaled), fitted to the clean PNG's aspect
        wiki = sprites[lab["slug"]]; ax0, ay0, ax1, ay1 = alpha_bbox(wiki); bw, bh = ax1 - ax0, ay1 - ay0
        cw, ch = w * scale, h * scale
        s = ch / bh
        placed.append((lab["slug"], mcx, mcy, bw * s, bh * s))

    # Rainbow Road: not baked into the map; placed at its locked rect (no footprint to clean).
    wiki = sprites["rainbow_road"]; ax0, ay0, ax1, ay1 = alpha_bbox(wiki); bw, bh = ax1 - ax0, ay1 - ay0
    rw = RR_WIDTH * Wh
    placed.append(("rainbow_road", RR_CENTER[0] * Wh, RR_CENTER[1] * Hh, rw, rw * bh / bw))

    # 2) clean the baked icons out of the base (seamless-clone the warped inner terrain).
    icon_mask = cv2.dilate(icon_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    warped = cv2.warpAffine(inner, M, (Wh, Hh))
    base = hires.copy()
    n, lbl = cv2.connectedComponents(icon_mask)
    for i in range(1, n):
        comp = (lbl == i).astype(np.uint8) * 255
        ys, xs = comp.nonzero()
        if len(xs) < 30:
            continue
        base = cv2.seamlessClone(warped, base, comp, (int(round(xs.mean())), int(round(ys.mean()))), cv2.NORMAL_CLONE)

    # 3) bake a sharp drop-shadow under each course (the clean icon silhouette, darkened, dropped).
    basef = base.astype(np.float32)
    for slug, cx, cy, ow, oh in placed:
        wiki = sprites[slug]; ax0, ay0, ax1, ay1 = alpha_bbox(wiki)
        ow_i, oh_i = max(1, int(round(ow))), max(1, int(round(oh)))
        sil = cv2.resize(wiki[ay0:ay1, ax0:ax1, 3], (ow_i, oh_i)).astype(np.float32) / 255.0 * SHADOW_DARK
        ox, oy = int(round(cx - ow_i / 2)), int(round(cy - oh_i / 2 + SHADOW_DY * oh_i))
        x0, y0 = max(0, ox), max(0, oy); x1, y1 = min(Wh, ox + ow_i), min(Hh, oy + oh_i)
        if x1 <= x0 or y1 <= y0:
            continue
        a = sil[y0 - oy:y1 - oy, x0 - ox:x1 - ox][:, :, None]
        basef[y0:y1, x0:x1] *= (1 - a)
    base = basef.astype(np.uint8)

    # 4) emit base + sprites + manifest
    bh_out = round(Hh * BASE_W / Wh)
    cv2.imwrite(str(OUT / "base.jpg"), cv2.resize(base, (BASE_W, bh_out), interpolation=cv2.INTER_AREA),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    manifest = {"base": {"w": BASE_W, "h": bh_out}, "courses": []}
    for slug, cx, cy, ow, oh in placed:
        wiki = sprites[slug]; ax0, ay0, ax1, ay1 = alpha_bbox(wiki)
        cv2.imwrite(str(OUT / "sprites" / f"{slug}.png"), wiki[ay0:ay1, ax0:ax1])
        spr = {"x": (cx - ow / 2) / Wh, "y": (cy - oh / 2) / Hh, "w": ow / Wh, "h": oh / Hh}
        hit = {"x": (cx - ow * 0.30) / Wh, "y": (cy - oh * 0.22) / Hh, "w": ow * 0.60 / Wh, "h": oh * 0.60 / Hh}
        manifest["courses"].append({"slug": slug, "name": canon[slug],
                                    "hit": {k: round(v, 5) for k, v in hit.items()},
                                    "spr": {k: round(v, 5) for k, v in spr.items()}})
    manifest["courses"].sort(key=lambda c: c["slug"])
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    slugs = [c["slug"] for c in manifest["courses"]]
    assert len(slugs) == len(canon) == len(set(slugs)) and set(slugs) == set(canon), "slug mismatch"
    print(f"built {len(slugs)} courses -> {OUT}")


if __name__ == "__main__":
    main()
