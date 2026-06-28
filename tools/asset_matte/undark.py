"""Stage C: post-matte nametag removal + un-darkening, productionized at the
combo crop / OUT_H. drop_nameplate + undark_rgba are the VERBATIM validated kart
method (see memory nametag-mask-undark) — char uses the same method, char template.
Run in the matte venv (cv2/numpy/PIL):  temp/asset-venv-gpu/Scripts/python.exe ...
"""
import glob, os, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # flat `import nametag_core` as script or test
import nametag_core as nc

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
ALPHA_GAIN, STRENGTH, CSUB, TFLOOR = 5.0, 1.02, 0.69, 0.05
T_OPAQUE, PRESENT_LUMA, NAMEPLATE_OUT_FRAC = 0.20, 125.0, 0.30
# plate band near the bottom of the OUT_H crop (validated y[578:589] of 590 -> scaled)
_STRIP = (int(0.979 * nc.OUT_H), int(0.999 * nc.OUT_H))


def _mask_prod(path):
    m = cv2.imread(path, cv2.IMREAD_GRAYSCALE)            # full 4K (3840x2160)
    return nc.prod_crop(m.astype(np.float64) / 255.0)


def load_template(is_char):
    roi = nc.CHAR_ROI if is_char else nc.PLATE_ROI
    pre = "char" if is_char else "kart"
    P = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_P.png"), roi)).astype(np.float64)
    A = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_A.png"), roi)).astype(np.float64)
    t, C = nc.solve_tc(P, A)
    mask = _mask_prod(f"{ASSETS}/nametag_{pre}_mask4k.png")
    return t, C, mask


def plate_present(loopframe):
    strip = loopframe[_STRIP[0]:_STRIP[1], :, :]
    return float(np.median(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY))) < PRESENT_LUMA


def drop_nameplate(rgba, mask):
    a = rgba[..., 3]
    fg = (a > 30).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    inside = mask > 0.05
    out = rgba.copy(); dropped = 0
    for i in range(1, n):
        comp = lab == i
        area = stats[i, cv2.CC_STAT_AREA]
        outside_frac = float((comp & ~inside).sum()) / max(area, 1)
        if outside_frac < NAMEPLATE_OUT_FRAC:
            out[..., 3][comp] = 0; dropped += 1
    return out, dropped


def undark_rgba(rgba, t, C, mask):
    rgb = rgba[..., :3].astype(np.float64)
    a = rgba[..., 3].astype(np.float64) / 255.0
    tt = np.clip(t, TFLOOR, 1.6)
    corrected = np.clip((rgb - CSUB * C[..., None]) / tt[..., None], 0, 255)
    cov = np.clip(mask * ALPHA_GAIN, 0, 1)[..., None]
    rgb2 = np.clip(rgb + STRENGTH * cov * (corrected - rgb), 0, 255)
    opaque = (mask > 0.05) & (t < T_OPAQUE)
    a2 = a * (~opaque)
    return np.dstack([rgb2, a2 * 255.0]).astype(np.uint8)


def _reencode(frames_dir, outbase):
    from PIL import Image
    fr = [Image.open(p).convert("RGBA") for p in sorted(glob.glob(f"{frames_dir}/*.png"))]
    dur = int(round(1000 / 60))
    fr[0].save(f"{outbase}_loop.webp", save_all=True, append_images=fr[1:], duration=dur,
               loop=0, lossless=True, disposal=2)
    fr[0].save(f"{outbase}_apng.png", save_all=True, append_images=fr[1:], duration=dur, loop=0)
    yy, xx = np.mgrid[0:fr[0].height, 0:fr[0].width]
    chk = Image.fromarray(np.where(((xx // 22 + yy // 22) % 2 == 0), 205, 150)
                          .astype(np.uint8)[..., None].repeat(3, 2), "RGB").convert("RGBA")
    comp = [Image.alpha_composite(chk, f) for f in fr]
    comp[0].save(f"{outbase}_checker.webp", save_all=True, append_images=comp[1:], duration=dur, loop=0)


def process(base, names, is_char):
    t, C, mask = load_template(is_char)
    for name in names:
        fdir, ldir = f"{base}/matte/{name}_frames", f"{base}/loopframes/{name}"
        odir = f"{base}/matte/{name}_undark"; os.makedirs(odir, exist_ok=True)
        files = sorted(os.listdir(fdir))
        nap = ndrop = 0
        for n in files:
            rgba = cv2.imread(f"{fdir}/{n}", cv2.IMREAD_UNCHANGED)
            lf = cv2.imread(f"{ldir}/{n}")
            if rgba is None or lf is None:
                continue
            rgba, dr = drop_nameplate(rgba, mask); ndrop += dr
            if is_char or plate_present(lf):
                rgba = undark_rgba(rgba, t, C, mask); nap += 1
            cv2.imwrite(f"{odir}/{n}", rgba)
        _reencode(odir, f"{base}/matte/{name}_undark")
        print(f"{name}: {len(files)}f, {nap} un-darkened, {ndrop} blobs dropped "
              f"({'char' if is_char else 'kart'}) -> {odir}", flush=True)


if __name__ == "__main__":
    a = sys.argv[1:]
    is_char = "--kart" not in a
    a = [x for x in a if x != "--kart"]
    process(a[0], a[1:], is_char)
