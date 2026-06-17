#!/usr/bin/env python3
"""Build the World Map web assets.

Base   : the ORIGINAL hi-res map, untouched - it already has all 29 course icons baked in with
         their sharp in-game drop-shadows. (Rainbow Road isn't on the art, so we bake one shadow
         for it.)
Sprites: each course icon is CUT STRAIGHT FROM THE HI-RES MAP at its located spot, matted with the
         island silhouette from the stages layer. So a sprite is pixel-identical to its baked twin
         underneath - at rest it's invisible, and on hover the frontend lifts it off the baked
         shadow. Rainbow Road is the one exception: a tightened copy of the official icon.

Why cut from the map (not the official wiki icons): the wiki/stages icons carry a wide low-alpha
glow plate with near-black RGB, which composites as a faint dark SQUARE over the map. A map-cut
sprite's faint matte edge is terrain-coloured instead, so it disappears against its own twin.
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
MATTE_LO, MATTE_HI = 40, 85   # solidify the island matte: drop the faintest glow, keep the full edge
RR_SHADOW_DY = 0.06           # Rainbow Road's baked shadow offset, straight down (frac of icon height)
RR_SHADOW_DARK = 0.5          # how much that shadow darkens the terrain (0..1)


def load_canonical():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def load_avif(path):
    return cv2.cvtColor(np.array(Image.open(path).convert("RGBA")), cv2.COLOR_RGBA2BGRA)


def alpha_bbox(bgra):
    ys, xs = (bgra[:, :, 3] > 16).nonzero()
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def solidify(a, lo, hi):
    """Smoothstep an alpha channel: <lo -> 0, >hi -> 255, smooth between. Trims the faint glow
    tail without hard-clipping the natural anti-aliased edge."""
    t = np.clip((a.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return (t * t * (3 - 2 * t) * 255).astype(np.uint8)


def tighten(bgra):
    """Strip the wide low-alpha glow plate from an official icon (keep the solid body + a few px of
    its real anti-aliased edge). Used only for Rainbow Road, which has no map-cut twin."""
    a = bgra[:, :, 3]
    keep = cv2.dilate((a > 120).astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    out = bgra.copy(); out[:, :, 3] = (a * keep)
    return out


def bake_shadow(base, alpha, gx0, gy0, dy, dark):
    """Darken the base under a sprite's silhouette, offset straight down - a sharp baked shadow."""
    h, w = alpha.shape
    oy = gy0 + int(round(dy * h))
    x0, y0 = max(0, gx0), max(0, oy)
    x1, y1 = min(base.shape[1], gx0 + w), min(base.shape[0], oy + h)
    if x1 <= x0 or y1 <= y0:
        return
    asub = (alpha[y0 - oy:y1 - oy, x0 - gx0:x1 - gx0].astype(np.float32) / 255.0 * dark)[:, :, None]
    base[y0:y1, x0:x1] = (base[y0:y1, x0:x1] * (1 - asub)).astype(np.uint8)


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

    stages = load_avif(SRC / "MarioKartWorld_World_Map_Stages.webp"); sa = stages[:, :, 3]; sbgr = stages[:, :, :3]
    inner = load_avif(SRC / "MarioKartWorld_World_Map_Inner.webp")[:, :, :3]
    hires = cv2.imread(str(SRC / "highresmap.jpeg"))
    Hs, Ws = sa.shape; Hh, Wh = hires.shape[:2]
    M = orb_transform(inner, hires); scale = float(np.hypot(M[0, 0], M[0, 1]))
    def to_hi(x, y):
        p = M @ np.array([x, y, 1.0]); return float(p[0]), float(p[1])

    # 1) locate each baked course and CUT its sprite from the hi-res map at that spot.
    #    placed: (slug, sprite_bgra, gx0, gy0) where (gx0,gy0) is the sprite's top-left in hi-res px.
    placed = []
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
        # cut RGB straight from the hi-res map (so it equals its baked twin) + the solidified matte.
        gx0 = int(round(min(max(mcx - tw / 2, 0), Wh - tw)))
        gy0 = int(round(min(max(mcy - th / 2, 0), Hh - th)))
        rgb = hires[gy0:gy0 + th, gx0:gx0 + tw]
        alpha = solidify(tmask[:rgb.shape[0], :rgb.shape[1]], MATTE_LO, MATTE_HI)
        placed.append((lab["slug"], np.dstack([rgb, alpha]), gx0, gy0))

    # 2) base = the original hi-res map, untouched (its baked icons + sharp shadows are kept).
    base = hires.copy()

    # 3) Rainbow Road: not on the map. Use a tightened official icon (glow plate stripped so it
    #    doesn't square), and bake it a matching straight-down shadow into the base.
    rr = tighten(load_avif(ICONS / "rainbow_road.png") if (ICONS / "rainbow_road.png").suffix == ".webp"
                 else cv2.imread(str(ICONS / "rainbow_road.png"), cv2.IMREAD_UNCHANGED))
    rax0, ray0, rax1, ray1 = alpha_bbox(rr); rr = rr[ray0:ray1, rax0:rax1]
    rw = RR_WIDTH * Wh; rh = rw * rr.shape[0] / rr.shape[1]
    rr = cv2.resize(rr, (int(round(rw)), int(round(rh))))
    rgx0 = int(round(RR_CENTER[0] * Wh - rw / 2)); rgy0 = int(round(RR_CENTER[1] * Hh - rh / 2))
    bake_shadow(base, rr[:, :, 3], rgx0, rgy0, RR_SHADOW_DY, RR_SHADOW_DARK)
    placed.append(("rainbow_road", rr, rgx0, rgy0))

    # 4) emit base + sprites + manifest
    bh_out = round(Hh * BASE_W / Wh)
    cv2.imwrite(str(OUT / "base.jpg"), cv2.resize(base, (BASE_W, bh_out), interpolation=cv2.INTER_AREA),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    manifest = {"base": {"w": BASE_W, "h": bh_out}, "courses": []}
    for slug, bgra, gx0, gy0 in placed:
        ax0, ay0, ax1, ay1 = alpha_bbox(bgra)
        cv2.imwrite(str(OUT / "sprites" / f"{slug}.png"), bgra[ay0:ay1, ax0:ax1])
        cx, cy = gx0 + (ax0 + ax1) / 2, gy0 + (ay0 + ay1) / 2
        ow, oh = ax1 - ax0, ay1 - ay0
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


def detect_boxes(alpha):
    H, W = alpha.shape
    m = (alpha > 40).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    boxes = [(int(x), int(y), int(w), int(h)) for i in range(1, n)
             for x, y, w, h, a in [st[i]] if a >= 3000 and w < W * 0.55 and h < H * 0.55]
    boxes.sort(key=lambda b: (round((b[1] + b[3] / 2) / 120), b[0]))
    return boxes


if __name__ == "__main__":
    main()
