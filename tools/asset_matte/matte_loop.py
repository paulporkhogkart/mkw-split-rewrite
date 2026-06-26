"""Stage B: per-frame birefnet matte + pymatting foreground decontam, assembled into
a seamless transparent loop. Outputs WebM (VP9 alpha), animated WebP, a checkerboard
WebP, and a contact sheet. Run in temp/asset-venv (rembg + pymatting + PIL; ffmpeg on PATH).

Plain per-frame matte only - no temporal smoothing / hole-fill (those caused ghosting)."""
import glob
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image


def _setup_cuda():
    """Make pip-installed nvidia CUDA/cuDNN DLLs loadable + preload them (GPU venv).
    No-op on the CPU venv (no nvidia packages)."""
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

from rembg import remove, new_session

PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"]

try:
    from pymatting import estimate_foreground_ml
    HAVE_PM = True
except Exception as e:
    HAVE_PM = False
    print("pymatting unavailable:", e, flush=True)

DUR = int(round(1000 / 60))   # ~17 ms/frame (60fps)


def checker_bg(w, h, s=22, a=205, b=150):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // s + yy // s) % 2 == 0)
    img = np.where(m[..., None], a, b).astype(np.uint8).repeat(3, axis=2)
    return Image.fromarray(img, "RGB").convert("RGBA")


def make_webm(frames_dir, out_path):
    """Encode a PNG sequence to a transparent VP9 WebM (yuva420p)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-framerate", "60", "-i", os.path.join(frames_dir, "%03d.png"),
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "18",
           "-an", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [webm] ffmpeg failed: {r.stderr[-300:]}", flush=True)
        return False
    return True


def matte_dir(indir, outbase, name, model):
    session = new_session(model, providers=PROVIDERS)
    files = sorted(glob.glob(os.path.join(indir, "*.png")))
    frames_dir = f"{outbase}_frames"
    os.makedirs(frames_dir, exist_ok=True)
    frames = []
    t0 = time.time()
    for i, p in enumerate(files):
        img = Image.open(p).convert("RGB")
        rgba = np.array(remove(img, session=session, post_process_mask=True))   # HxWx4 uint8
        alpha = rgba[..., 3].astype(np.float64) / 255.0
        rgb = np.asarray(img).astype(np.float64) / 255.0
        if HAVE_PM and alpha.max() > 0.02:
            try:
                rgb = np.clip(estimate_foreground_ml(rgb, alpha), 0, 1)   # kill bg colour fringe
            except Exception:
                pass
        out = np.dstack([(rgb * 255).astype(np.uint8), rgba[..., 3]])
        im = Image.fromarray(out, "RGBA")
        im.save(os.path.join(frames_dir, f"{i:03d}.png"))
        frames.append(im)
        if (i + 1) % 10 == 0 or i == len(files) - 1:
            print(f"  {name}: {i+1}/{len(files)}  ({(time.time()-t0)/(i+1):.1f}s/frame)", flush=True)

    W, H = frames[0].size
    frames[0].save(f"{outbase}_loop.webp", save_all=True, append_images=frames[1:],
                   duration=DUR, loop=0, lossless=True, disposal=2)
    chk = checker_bg(W, H)
    comp = [Image.alpha_composite(chk, f) for f in frames]
    comp[0].save(f"{outbase}_checker.webp", save_all=True, append_images=comp[1:],
                 duration=DUR, loop=0)
    idxs = np.linspace(0, len(comp) - 1, 6).astype(int)
    th = 300
    tw = int(W * th / H)
    sheet = Image.new("RGB", (tw * len(idxs), th), (255, 255, 255))
    for k, ii in enumerate(idxs):
        sheet.paste(comp[ii].convert("RGB").resize((tw, th)), (k * tw, 0))
    sheet.save(f"{outbase}_sheet.png")
    # APNG: universal transparent loop (VP8/VP9 alpha WebM is broken in this ffmpeg build).
    frames[0].save(f"{outbase}_apng.png", save_all=True, append_images=frames[1:],
                   duration=DUR, loop=0)
    print(f"{name}: DONE {len(frames)} frames {W}x{H} -> _apng.png / _loop.webp / _checker.webp / _sheet.png", flush=True)


if __name__ == "__main__":
    base = sys.argv[1]
    model = sys.argv[2]            # e.g. birefnet-general (full) or birefnet-general-lite
    suffix = sys.argv[3]           # output name suffix, e.g. _full  (use "-" for none)
    suffix = "" if suffix == "-" else suffix
    names = sys.argv[4:]
    outdir = os.path.join(base, "matte")
    os.makedirs(outdir, exist_ok=True)
    print(f"model={model}  suffix={suffix!r}  names={names}", flush=True)
    for name in names:
        matte_dir(os.path.join(base, "loopframes", name), os.path.join(outdir, name + suffix), name, model)
    print("ALL DONE", flush=True)
