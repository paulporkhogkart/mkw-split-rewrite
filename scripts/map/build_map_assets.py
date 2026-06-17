#!/usr/bin/env python3
"""Build the World Map web assets.

This mirrors exactly what you get by opening the two source layers in a browser and laying the
transparent STAGES webp over the stage-less INNER webp: a perfect combined map. We keep them as the
two layers they are instead of flattening:

Base   : the INNER layer (the map with NO course icons). Nothing is baked on top, so on hover there
         is no icon underneath to ghost through. We bake one sharp straight-down drop shadow per
         course into it (inner has none of its own).
Sprites: each course icon is cut from the STAGES layer - which is icons-only on full transparency.
         So a sprite contains the icon and its own glow and NOTHING of the background, and on hover
         only the icon grows (not a patch of terrain). inner and stages are pixel-aligned (identical
         dimensions), so a sprite drops back onto its exact spot with no alignment math.

Rainbow Road is the lone exception (not present on either layer): a tightened official icon over one
baked shadow. The hi-res map is used only at build time, to carry the hand-placed course labels
(authored against it) into the inner frame for slug assignment.
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
RR_CENTER = (0.500, 0.734)     # hand-placed in the hi-res frame; transformed to the inner frame below
RR_WIDTH = 0.092               # Rainbow Road sprite width, as a fraction of the hi-res map width
GLOW_DILATE = 18               # px (native): grow each icon's body mask to recapture its soft glow
SHADOW_DY = 0.05               # baked shadow offset, straight down, as a fraction of icon height
SHADOW_DARK = 0.45             # how much the baked shadow darkens the terrain (0..1)
SHADOW_LO, SHADOW_HI = 110, 165  # solidify the icon alpha into a sharp (not glow-soft) shadow shape


def load_canonical():
    spec = importlib.util.spec_from_file_location("courses", ROOT / "server" / "courses.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return dict(mod.CANONICAL_COURSES)


def load_avif(path):
    return cv2.cvtColor(np.array(Image.open(path).convert("RGBA")), cv2.COLOR_RGBA2BGRA)


def alpha_bbox(alpha):
    ys, xs = (alpha > 1).nonzero()
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def solidify(a, lo, hi):
    """Smoothstep an alpha channel: <lo -> 0, >hi -> 255, smooth between."""
    t = np.clip((a.astype(np.float32) - lo) / (hi - lo), 0, 1)
    return (t * t * (3 - 2 * t) * 255).astype(np.uint8)


def tighten(bgra):
    """Strip the wide low-alpha glow plate from an official icon (keep the solid body + a few px of
    its real edge). Used only for Rainbow Road, which has no map-layer twin."""
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

    stages = load_avif(SRC / "MarioKartWorld_World_Map_Stages.webp"); sa = stages[:, :, 3]; srgb = stages[:, :, :3]
    inner = load_avif(SRC / "MarioKartWorld_World_Map_Inner.webp")[:, :, :3]
    hires = cv2.imread(str(SRC / "highresmap.jpeg"))
    Hs, Ws = sa.shape; Hh, Wh = hires.shape[:2]

    # The labels were hand-placed against the hi-res map. Carry them into the inner/stages frame
    # (inner and stages share one frame) so we can name each detected icon. ORB only, build-time.
    M = orb_transform(inner, hires); scale = float(np.hypot(M[0, 0], M[0, 1]))
    Minv = cv2.invertAffineTransform(M)
    def hi_to_inner(hx, hy):
        p = Minv @ np.array([hx, hy, 1.0]); return float(p[0]), float(p[1])
    lab_inner = [(L["slug"], *hi_to_inner(L["cx"] * Wh, L["cy"] * Hh)) for L in labels]

    # detect each icon as a connected blob of the stages alpha.
    m = cv2.morphologyEx((sa > 40).astype(np.uint8), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    n, lbl, st, cent = cv2.connectedComponentsWithStats(m, 8)

    base = inner.copy()
    placed = []   # (slug, sprite_bgra, gx0, gy0)  - gx0,gy0 = top-left in inner/stages px
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (area >= 3000 and w < Ws * 0.55 and h < Hs * 0.55):
            continue
        cx, cy = cent[i]
        slug = min(lab_inner, key=lambda L: (L[1] - cx) ** 2 + (L[2] - cy) ** 2)[0]
        # this icon's alpha = its blob grown to recapture its soft glow, masking off neighbours.
        maski = cv2.dilate((lbl == i).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (GLOW_DILATE, GLOW_DILATE)))
        spa = sa * maski
        ax0, ay0, ax1, ay1 = alpha_bbox(spa)
        sprite = np.dstack([srgb, spa])[ay0:ay1, ax0:ax1]
        bake_shadow(base, solidify(sprite[:, :, 3], SHADOW_LO, SHADOW_HI), ax0, ay0, SHADOW_DY, SHADOW_DARK)
        placed.append((slug, sprite, ax0, ay0))

    # Rainbow Road: not on either layer. Tighten the official icon (glow plate stripped) + a shadow.
    rr = tighten(cv2.imread(str(ICONS / "rainbow_road.png"), cv2.IMREAD_UNCHANGED))
    rax0, ray0, rax1, ray1 = alpha_bbox(rr[:, :, 3]); rr = rr[ray0:ray1, rax0:rax1]
    rw = RR_WIDTH * Wh / scale; rh = rw * rr.shape[0] / rr.shape[1]   # hi-res width -> inner px
    rr = cv2.resize(rr, (int(round(rw)), int(round(rh))))
    rcx, rcy = hi_to_inner(RR_CENTER[0] * Wh, RR_CENTER[1] * Hh)
    rgx0, rgy0 = int(round(rcx - rw / 2)), int(round(rcy - rh / 2))
    bake_shadow(base, rr[:, :, 3], rgx0, rgy0, SHADOW_DY, SHADOW_DARK)
    placed.append(("rainbow_road", rr, rgx0, rgy0))

    # emit base + sprites + manifest (everything normalized to the inner/stages frame).
    bh_out = round(Hs * BASE_W / Ws)
    cv2.imwrite(str(OUT / "base.jpg"), cv2.resize(base, (BASE_W, bh_out), interpolation=cv2.INTER_AREA),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    manifest = {"base": {"w": BASE_W, "h": bh_out}, "courses": []}
    for slug, bgra, gx0, gy0 in placed:
        cv2.imwrite(str(OUT / "sprites" / f"{slug}.png"), bgra)
        oh, ow = bgra.shape[:2]; cx, cy = gx0 + ow / 2, gy0 + oh / 2
        spr = {"x": gx0 / Ws, "y": gy0 / Hs, "w": ow / Ws, "h": oh / Hs}
        hit = {"x": (cx - ow * 0.30) / Ws, "y": (cy - oh * 0.22) / Hs, "w": ow * 0.60 / Ws, "h": oh * 0.60 / Hs}
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
