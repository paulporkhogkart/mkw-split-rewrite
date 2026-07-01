"""Stage B: matte one extracted idle loop (from extract_loop.py) into transparent RGBA.
GPU venv (rembg + CUDA). Promotes the VALIDATED matte pipelines into importable form so
the headless batch driver can call them without the browser tuner.

- KART COMBOS  -> the LOCKED blank-plate pipeline (== tune_blankplate.py / the 2026-06-29
  kart-chip-matte spec): blank-transform un-darken + per-clip text mask + interior TELEA
  inpaint, then birefnet. Locked params KEY_THR=120, CSUB=0.5, TFLOOR=0.01, FILL_K=51;
  flourish bumper fill is SHELVED (off).
- STANDALONE CHARS -> plain pre_darken(CHAR template) + birefnet. NO hole-repair: a bare char
  is a clean silhouette birefnet mattes well, and the fade-tail backdrop over-grows it (an ~8.8k
  px haze blob beside baby_daisy's head in validation) -- so repair is KART-ONLY.

KART combos then run a COMBINED hole-repair against a per-clip faded clean-backdrop
(`clean_backdrop`): birefnet's wrong cuts are restored where the measured diff-from-backdrop
says the pixels are subject material. Two passes -- enclosed-pocket whole-fill (DK's seat gap)
+ open-notch per-pixel restore (baby_daisy's bib). Strictly additive; validated over-fill-free
on open-frame / dark / white-wispy karts; skipped if the backdrop can't be computed.

`matte_loopframes(framedir, name, out_base, clip=None, backdrop=None)` is the importable entry
point (give `clip` to enable kart hole-repair; the backdrop is derived from its fade tail). A CLI
mattes one or more already-extracted loops. Emits <name>_loop.webp (alpha),
<name>_checker.webp, and the RGBA frames in <name>_frames/.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pre_darken as pd
import nametag_core as nc
from extract_loop import is_kart_combo


def _setup_cuda():
    try:
        import nvidia
        for d in glob.glob(os.path.join(os.path.dirname(nvidia.__file__), "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
    try:
        import onnxruntime
        onnxruntime.preload_dlls()
    except Exception:
        pass


_setup_cuda()
from PIL import Image                                            # noqa: E402
from rembg import remove, new_session                           # noqa: E402
try:
    from pymatting import estimate_foreground_ml
    _HAVE_PM = True
except Exception:
    _HAVE_PM = False

# Locked kart matte params (kart-chip-matte spec). Flourish fill stays OFF.
KEY_THR, CSUB, TFLOOR, FILL_K = 120, 0.5, 0.01, 51
DECONTAM = False         # pymatting foreground decontam: a no-op here (binary alpha) + 13% slower -> off
DUR_MS = int(round(1000 / 60))
# Blank plate = baby_daisy 40-kart masked median (memory kart-chip-matte-pipeline). TODO(step 2):
# promote to a committed artifact + a permanent generator script; for now read the validated npy.
BLANK_NPY = os.path.join(os.path.dirname(__file__), "assets", "blank_plate_masked.npy")
if not os.path.exists(BLANK_NPY):
    BLANK_NPY = r"C:\development\mkw-split-rewrite\temp\notch_poc\blank_plate_masked.npy"

_SESSION = None
# birefnet model: FULL ("birefnet-general") by default — much better at low-contrast edges (dark kart
# parts) than the lite model; set MATTE_BIREFNET_MODEL=birefnet-general-lite to go back to fast/lite.
BIREFNET_MODEL = os.environ.get("MATTE_BIREFNET_MODEL", "birefnet-general")
# Matte engine: MatAnyone2 video matting (default, kills per-frame flicker) vs the legacy per-frame
# birefnet path. Set MATTE_ENGINE=birefnet to A/B or roll back. matte_matanyone is imported lazily
# (only under the matanyone branch) so the birefnet path still runs in a torch-less venv.
MATTE_ENGINE = os.environ.get("MATTE_ENGINE", "matanyone")


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = new_session(BIREFNET_MODEL,
                               providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    return _SESSION


# ── kart blank-plate setup (once) — faithfully from tune_blankplate.py ─────────
_t_kart, _C_kart, _A_kart, _MASK_kart = pd.load_template(False)
_IN_PLATE = _MASK_kart > 0.05
_Hh, _Ww = _MASK_kart.shape[:2]
_, _X = np.indices((_Hh, _Ww))
_BLANK = np.load(BLANK_NPY).astype(np.float32)
_T_B, _C_B = nc.solve_tc(_BLANK, _A_kart)
_BADGE = (_T_B < pd.T_OPAQUE) & _IN_PLATE
_dop = (_t_kart < pd.T_OPAQUE) & _IN_PLATE
_pxs = np.where(_IN_PLATE.any(0))[0]
_PX0, _PX1 = _pxs.min(), _pxs.max()
_BX0 = int(_PX0 + 0.80 * (_PX1 - _PX0))
_tys = np.where((_dop & (_X < _BX0)).any(1))[0]
_ty0, _ty1 = max(0, _tys.min() - 8), _tys.max() + 8
_TEXT_BAND = np.zeros_like(_IN_PLATE)
_TEXT_BAND[_ty0:_ty1 + 1, _PX0:_BX0] = True
_TEXT_BAND &= _IN_PLATE


def _kart_text_mask(P_clip):
    t, _ = nc.solve_tc(P_clip, _A_kart)
    return cv2.dilate(((t < pd.T_OPAQUE) & _TEXT_BAND).astype(np.uint8),
                      np.ones((5, 5), np.uint8)).astype(bool)


def _kart_predark(raw, text):
    """Blank-transform un-darken + interior TELEA inpaint (tune_blankplate `_predark`, flourish off)."""
    O = raw.astype(np.float64)
    S = np.clip((O - CSUB * _C_B[..., None]) / np.clip(_T_B, TFLOOR, 1.6)[..., None], 0, 255)
    opaque = (_BADGE | text) & _IN_PLATE
    subject = _IN_PLATE & (np.abs(S - _A_kart).max(2) >= KEY_THR) & ~opaque
    out = O.copy(); out[_IN_PLATE] = _A_kart[_IN_PLATE]; out[subject] = S[subject]
    out = np.clip(out, 0, 255).astype(np.uint8)
    K = int(FILL_K) | 1
    closed = cv2.morphologyEx(subject.astype(np.uint8) * 255, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (K, K))) > 0
    holes = _IN_PLATE & closed & ~subject
    n, lab, st, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), 8)
    keep = np.zeros_like(holes)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] <= 2000:
            keep |= (lab == i)
    return cv2.inpaint(out, keep.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)


# ── char setup (committed CHAR template) ───────────────────────────────────────
_t_char, _C_char, _A_char, _MASK_char = pd.load_template(True)


# ── birefnet + decontam, hybrid hole-repair (char) ─────────────────────────────
def _birefnet(bgr):
    rgb = cv2.cvtColor(bgr.astype(np.uint8), cv2.COLOR_BGR2RGB)
    out = np.array(remove(Image.fromarray(rgb), session=_session(), post_process_mask=True))
    a = out[..., 3].astype(np.float64) / 255.0
    rgbf = rgb.astype(np.float64) / 255.0
    if DECONTAM and _HAVE_PM and a.max() > 0.02:
        try:
            rgbf = np.clip(estimate_foreground_ml(rgbf, a), 0, 1)
        except Exception:
            pass
    return a.astype(np.float32), cv2.cvtColor((rgbf * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)


def _fill_holes(b):
    ff = b.copy().astype(np.uint8); h, w = b.shape
    cv2.floodFill(ff, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 1)
    out = b.copy().astype(np.uint8); out[ff == 0] = 1
    return out


def _soft_diff(P, A):
    d = np.sqrt(((P - A) ** 2).sum(2))
    s = np.clip(d / max(np.percentile(d, 98), 1e-3), 0, 1)
    return np.clip((s - 0.10) / 0.90, 0, 1).astype(np.float32)


def _repair_holes(alpha, bgr, soft, P, mat_thr=0.15, min_area=40):
    """Fill birefnet's wrong cuts where the measured diff-from-clean-backdrop says the pixels are
    subject material, not real background. Strictly additive over birefnet, in TWO passes:

      (1) ENCLOSED pockets -- background fully surrounded by subject (DK's seat gap). Fill the
          WHOLE pocket when its mean diff is material; per-pixel diff inside an enclosed pocket
          is noisy, so the mean decides and the fill is solid.
      (2) OPEN notches -- background-labelled, high-diff pixels CONNECTED to the matted subject
          (baby_daisy's bib, which opens to the frame edge so pass 1 never sees it). Restored
          per-pixel; isolated background specks aren't connected to the subject, so they stay
          background and can't be over-grown into."""
    a, b = alpha.copy(), bgr.copy()
    filled = _fill_holes((alpha > 0.5).astype(np.uint8))
    holes = ((filled == 1) & (alpha <= 0.5)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(holes, 8)
    for c in range(1, n):
        if st[c, cv2.CC_STAT_AREA] < min_area:
            continue
        m = (lab == c)
        if float(soft[m].mean()) > mat_thr:
            a[m] = 1.0; b[m] = P[m]
    cand = (a < 0.5) & (soft > mat_thr)
    seed = a >= 0.5
    _, labc = cv2.connectedComponents((cand | seed).astype(np.uint8), 8)
    restore = cand & np.isin(labc, np.unique(labc[seed]))
    a[restore] = 1.0; b[restore] = P[restore]
    return a, b


def clean_backdrop(clip, tmpdir=None, seconds=2.2):
    """Per-clip faded clean-background plate (988x1080 BGR float) for the hole-repair soft-diff.

    Decode the last `seconds` of the clip with the PROD crop (so it aligns pixel-exactly with the
    matted loop frames), pick the most-blurred contiguous band by Laplacian variance -- the
    select-screen fade where the subject has dissolved to background -- and median it. Returns
    None if ffmpeg/decoding yields nothing, so the caller cleanly skips repair."""
    own = tmpdir is None
    tmpdir = tmpdir or tempfile.mkdtemp(prefix="bd_")
    fdir = os.path.join(tmpdir, "_bd")
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir, exist_ok=True)
    x1, y1, x2, y2 = nc.PROD_CROP_4K
    vf = f"crop={x2 - x1}:{y2 - y1}:{x1}:{y1},scale={nc.OUT_W}:{nc.OUT_H}:flags=area"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-sseof", f"-{seconds}", "-i", clip,
                        "-vf", vf, "-start_number", "0", os.path.join(fdir, "f%04d.png")], check=True)
        imgs = [im for im in (cv2.imread(p) for p in sorted(glob.glob(os.path.join(fdir, "f*.png")))) if im is not None]
        if not imgs:
            return None
        lv = np.array([cv2.Laplacian(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() for im in imgs])
        z = int(np.argmin(lv)); thr = lv[z] * 1.25; lo = hi = z
        while lo - 1 >= 0 and lv[lo - 1] <= thr:
            lo -= 1
        while hi + 1 < len(lv) and lv[hi + 1] <= thr:
            hi += 1
        return np.median(np.stack([imgs[i].astype(np.float32) for i in range(lo, hi + 1)]), axis=0)
    except Exception:
        return None
    finally:
        shutil.rmtree(fdir, ignore_errors=True)
        if own:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _checker_rgba(w, h, s=22):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)
    return Image.fromarray(np.where(m[..., None], 205, 150).astype(np.uint8).repeat(3, 2), "RGB").convert("RGBA")


def _build_predark_frames(paths, kart, apply_predark):
    """Predark (or raw, for a plate-dropped flourish) BGR uint8 frame per path. Kart text mask is
    computed once from the segment median (== the old inline path)."""
    text = None
    if kart and apply_predark:
        sample = [cv2.imread(p).astype(np.float32) for p in paths[::3]]
        text = _kart_text_mask(np.median(np.stack(sample), axis=0))
    out = []
    for p in paths:
        raw = cv2.imread(p)
        if not apply_predark:
            out.append(raw)                                  # flourish: plate dropped -> raw
        elif kart:
            out.append(_kart_predark(raw, text))
        else:
            out.append(pd.pre_darken(raw, _t_char, _C_char, _A_char, _MASK_char))
    return out


def _write_chip(pairs, name, out_base):
    """Write RGBA frames + _loop.webp + _checker.webp from (bgr_uint8, alpha_float01) pairs. Shared
    by both engines. Returns the frame count."""
    fdir = os.path.join(out_base, f"{name}_frames")
    os.makedirs(fdir, exist_ok=True)
    rgba_frames = []
    for i, (bgr, alpha) in enumerate(pairs):
        rgb = cv2.cvtColor(np.asarray(bgr).astype(np.uint8), cv2.COLOR_BGR2RGB)
        rgba = np.dstack([rgb, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])
        cv2.imwrite(os.path.join(fdir, f"{i:03d}.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        rgba_frames.append(Image.fromarray(rgba, "RGBA"))
        if (i + 1) % 15 == 0 or i + 1 == len(pairs):
            print(f"  matte {name} {i + 1}/{len(pairs)}", flush=True)
    W, H = rgba_frames[0].size
    rgba_frames[0].save(os.path.join(out_base, f"{name}_loop.webp"), save_all=True,
                        append_images=rgba_frames[1:], duration=DUR_MS, loop=0, lossless=True, disposal=2)
    chk = _checker_rgba(W, H)
    comp = [Image.alpha_composite(chk, f) for f in rgba_frames]
    comp[0].save(os.path.join(out_base, f"{name}_checker.webp"), save_all=True,
                 append_images=comp[1:], duration=DUR_MS, loop=0)
    return len(pairs)


def matte_loopframes(framedir, name, out_base, clip=None, backdrop=None,
                     apply_predark=True, is_kart=None):
    """Matte every NNN.png frame in `framedir` -> transparent RGBA. Returns the frame count.

    `apply_predark`: un-darken the nameplate before birefnet (TRUE for spawn/idle, where the plate
    is present; pass FALSE for the FLOURISH segment, where the plate is dropped and predark would
    paint a fake plate). `is_kart`: override kart detection (a segment-suffixed name would otherwise
    miscount the `__` separators)."""
    paths = sorted(glob.glob(os.path.join(framedir, "*.png")))
    if not paths:
        raise RuntimeError(f"no loop frames in {framedir!r}")
    kart = is_kart_combo(name) if is_kart is None else is_kart
    pres = _build_predark_frames(paths, kart, apply_predark)   # predark input frames (shared)

    if MATTE_ENGINE == "matanyone":
        import matte_matanyone as mm                           # lazy: torch only loads on this path
        # birefnet(onnxruntime) and matanyone(torch) can't share one process on the GPU — onnxruntime
        # grabs the whole card and starves torch (~50x). So matanyone runs in a persistent WORKER
        # process; warm it HERE, before the first birefnet call, so torch reserves its GPU block
        # first (validated: they then coexist at full speed). MATTE_MATANYONE_INPROC=1 forces the old
        # single-process path (only usable when birefnet won't also run — e.g. isolated tests).
        inproc = os.environ.get("MATTE_MATANYONE_INPROC") == "1"
        if not inproc:
            mm.ensure_worker()
        first = (_birefnet(pres[0])[0] > 0.5).astype(np.uint8) * 255
        last = (_birefnet(pres[-1])[0] > 0.5).astype(np.uint8) * 255
        matte = mm.matte_segment if inproc else mm.matte_segment_worker
        alphas = matte(pres, first, last)                      # bidirectional, memory-propagated
        pairs = list(zip(pres, alphas))                        # RGB = predark input (no decontam)
    else:                                                      # legacy per-frame birefnet
        pairs = []
        for pre in pres:
            alpha, bgr = _birefnet(pre)
            pairs.append((bgr, alpha))
    return _write_chip(pairs, name, out_base)


if __name__ == "__main__":
    base = sys.argv[1]                                          # holds loopframes/<name>/
    out = os.path.join(base, "matte")
    os.makedirs(out, exist_ok=True)
    for name in sys.argv[2:]:
        fd = os.path.join(base, "loopframes", name)
        n = matte_loopframes(fd, name, out)
        print(f"{name}: {'kart' if is_kart_combo(name) else 'char'} matted {n} frames -> {out}/{name}_*.webp", flush=True)
    print("DONE", flush=True)
