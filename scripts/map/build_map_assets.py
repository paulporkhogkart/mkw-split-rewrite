#!/usr/bin/env python3
"""Build the World Map web assets.

This mirrors what you get by laying the transparent STAGES webp over the stage-less INNER webp -
a perfect combined map - but keeps them as two layers so the icons can react to hover:

Base   : the INNER layer (the map with NO course icons). Nothing is baked on top, so on hover there
         is no icon to ghost through and no shadow frozen into the art. The frontend draws each
         icon's drop shadow as a live layer that moves when the icon lifts.
Sprites: each course icon, cut from the icons-only STAGES layer, then CLEANED: the stages alpha
         carries a wide near-black halo (alpha 1-128 with RGB ~0) that composites as a dark
         island-shaped haze over the map. We drop that halo (keep the alpha 128+ body) and bleed the
         body's edge colour outward so nothing black is left to fringe when the browser scales it.

inner and stages are pixel-aligned (identical dimensions), so a sprite drops onto its exact spot
with no alignment math. Rainbow Road is the lone exception (on neither layer): a cleaned official
icon. The hi-res map is used only at build time to carry the hand-placed labels into the inner
frame for slug assignment.
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
RR_CENTER = (0.501, 0.706)     # Rainbow Road centre, direct in the inner/stages frame (on neither layer)
RR_WIDTH = 0.078               # Rainbow Road sprite width, as a fraction of the inner map width
ISO_DILATE = 6                 # px: grow each icon's blob a touch so cleaning keeps its full edge
ALPHA_LO, ALPHA_HI = 120, 185  # drop the near-black halo (alpha < LO), keep the body (alpha >= HI)


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


def alpha_bleed(bgr, a, iters=90):
    """Flood the opaque body's colour outward into the transparent area, so no black RGB remains to
    fringe when the image is scaled."""
    out = bgr.astype(np.float32).copy()
    known = a > 180
    K = known.astype(np.float32); Csum = out * K[:, :, None]
    for _ in range(iters):
        if known.all():
            break
        Kd = cv2.blur(K, (3, 3)); Cd = cv2.blur(Csum, (3, 3))
        cand = (Kd > 1e-6) & (~known)
        out[cand] = Cd[cand] / Kd[cand][:, None]
        known = known | cand
        K = known.astype(np.float32); Csum = out * K[:, :, None]
    return out.astype(np.uint8)


def clean(bgra):
    """Strip an icon's near-black low-alpha halo and bleed its edge colour, leaving just the body."""
    a = solidify(bgra[:, :, 3], ALPHA_LO, ALPHA_HI)
    rgb = alpha_bleed(bgra[:, :, :3], bgra[:, :, 3])
    out = np.dstack([rgb, a])
    x0, y0, x1, y1 = alpha_bbox(a)
    return out[y0:y1, x0:x1], x0, y0


def grade(src, ref, src_icons, ref_icons):
    """Reinhard colour transfer (Lab) so the flat `src` (inner) takes on `ref`'s (hi-res sample's)
    grade. Icon areas are excluded from the statistics so the icons baked into `ref` don't skew the
    terrain colour match."""
    s = cv2.cvtColor(src, cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    st = ~src_icons.astype(bool); rt = ~ref_icons.astype(bool)
    out = s.copy()
    for c in range(3):
        sm, ss = s[:, :, c][st].mean(), s[:, :, c][st].std() + 1e-6
        rm, rs = r[:, :, c][rt].mean(), r[:, :, c][rt].std() + 1e-6
        out[:, :, c] = (s[:, :, c] - sm) * float(np.clip(rs / ss, 0.5, 1.8)) + rm
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


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

    # The labels were hand-placed against the hi-res map. Carry them into the inner/stages frame so
    # we can name each detected icon (ORB only, build-time).
    M = orb_transform(inner, hires)
    Minv = cv2.invertAffineTransform(M)
    def hi_to_inner(hx, hy):
        p = Minv @ np.array([hx, hy, 1.0]); return float(p[0]), float(p[1])
    lab_inner = [(L["slug"], *hi_to_inner(L["cx"] * Wh, L["cy"] * Hh)) for L in labels]

    # detect each icon as a connected blob of the stages alpha.
    m = cv2.morphologyEx((sa > 40).astype(np.uint8), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17)))
    n, lbl, st, cent = cv2.connectedComponentsWithStats(m, 8)

    placed = []   # (slug, sprite_bgra, gx0, gy0)  - gx0,gy0 = top-left in inner/stages px
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (area >= 3000 and w < Ws * 0.55 and h < Hs * 0.55):
            continue
        cx, cy = cent[i]
        slug = min(lab_inner, key=lambda L: (L[1] - cx) ** 2 + (L[2] - cy) ** 2)[0]
        maski = cv2.dilate((lbl == i).astype(np.uint8),
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ISO_DILATE, ISO_DILATE)))
        ax0, ay0, ax1, ay1 = alpha_bbox(sa * maski)
        sprite, dx, dy = clean(np.dstack([srgb, sa * maski])[ay0:ay1, ax0:ax1])
        placed.append((slug, sprite, ax0 + dx, ay0 + dy))

    # Rainbow Road: on neither layer, placed directly at its inner-frame spot. It's delicate
    # (clouds/glow), so clean it gently - bleed the black halo away but keep the soft cloud detail;
    # the island clean() hardens it into a different-looking blob.
    rr = cv2.imread(str(ICONS / "rainbow_road.png"), cv2.IMREAD_UNCHANGED)
    rr = np.dstack([alpha_bleed(rr[:, :, :3], rr[:, :, 3]), solidify(rr[:, :, 3], 70, 150)])
    rx0, ry0, rx1, ry1 = alpha_bbox(rr[:, :, 3]); rr = rr[ry0:ry1, rx0:rx1]
    rw = RR_WIDTH * Ws; rh = rw * rr.shape[0] / rr.shape[1]
    rr = cv2.resize(rr, (int(round(rw)), int(round(rh))))
    placed.append(("rainbow_road", rr,
                   int(round(RR_CENTER[0] * Ws - rw / 2)), int(round(RR_CENTER[1] * Hs - rh / 2))))

    # grade the inner base to the hi-res sample's look (terrain only; baked icons masked out).
    ek = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
    icon_inner = cv2.dilate((sa > 40).astype(np.uint8), ek)
    icon_hi = cv2.dilate((cv2.warpAffine(sa, M, (Wh, Hh)) > 40).astype(np.uint8), ek)
    graded = grade(inner, hires, icon_inner, icon_hi)

    # the inner ocean is flat; bring in the hi-res sample's ocean texture. Warp hires into the inner
    # frame and composite it over the water only, feathered at the coastline (both already carry the
    # hi-res grade, so the palette matches and the land stays the approved graded inner).
    warped = cv2.warpAffine(hires, Minv, (Ws, Hs))
    b, g_, r = inner[:, :, 0].astype(int), inner[:, :, 1].astype(int), inner[:, :, 2].astype(int)
    water = ((b > r + 12) & (b > g_) & (b > 70)).astype(np.uint8) * 255
    water = cv2.morphologyEx(water, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    water = cv2.morphologyEx(water, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    water = cv2.bitwise_and(water, (warped.sum(2) > 24).astype(np.uint8) * 255)   # only where warp covers
    wf = (cv2.GaussianBlur(water.astype(np.float32), (0, 0), 10) / 255.0)[:, :, None]
    base = (graded * (1 - wf) + warped * wf).astype(np.uint8)

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
