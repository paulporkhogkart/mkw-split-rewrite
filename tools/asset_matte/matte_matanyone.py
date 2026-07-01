"""MatAnyone2 mask-guided VIDEO matte engine (bidirectional). Runs in the unified GPU venv
(temp/asset-venv-matte: onnxruntime birefnet + torch cu128 MatAnyone2 in ONE process).

Port of the validated f267585a prototypes (prep_matanyone_seg.py / bidir_prep.py / bidir_merge.py
/ MatAnyone2's inference_matanyone2.py) into an importable, model-cached engine. Given a segment's
predark input frames plus birefnet first- and last-frame binary masks, it propagates the mask
FORWARD and (over reversed frames) BACKWARD, then merges position-weighted -> a temporally stable
alpha per frame that kills the per-frame-birefnet flicker.

Only numpy/os/sys/glob are imported at module top so build python can unit-test merge_bidir; all
heavy imports (cv2, torch, matanyone2) are lazy inside the GPU functions.
"""
import glob
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_MA2 = os.path.abspath(os.path.join(_HERE, "..", "..", "temp", "MatAnyone2"))
_MURL = "https://github.com/pq-yang/MatAnyone2/releases/download/v1.0.0/matanyone2.pth"

_MODEL = None
_DEVICE = None


def merge_bidir(fwd, bwd):
    """Position-weighted forward/backward alpha merge (== bidir_merge.py). fwd/bwd are equal-length
    lists of HxW float01 arrays in the SAME frame order (backward already un-reversed). Forward
    strong early, backward strong late: w = 1 - t/max(1, N-1). Returns a list of HxW float32."""
    n = len(fwd)
    out = []
    for t in range(n):
        w = 1.0 - t / max(1, n - 1)
        out.append(np.clip(w * fwd[t] + (1.0 - w) * bwd[t], 0.0, 1.0).astype(np.float32))
    return out


def _model():
    """Load MatAnyone2 once (module singleton). Returns (model, device)."""
    global _MODEL, _DEVICE
    if _MODEL is None:
        if _MA2 not in sys.path:
            sys.path.insert(0, _MA2)
        from matanyone2.utils.download_util import load_file_from_url
        from matanyone2.utils.get_default_model import get_matanyone2_model
        from matanyone2.utils.device import get_default_device
        _DEVICE = get_default_device()
        ckpt = load_file_from_url(_MURL, os.path.join(_MA2, "pretrained_models"))
        _MODEL = get_matanyone2_model(ckpt, _DEVICE)
    return _MODEL, _DEVICE


def _propagate(frames_bgr, mask_u8, n_warmup=10, r_erode=10, r_dilate=10):
    """One MatAnyone2 propagation over frames_bgr (list of HxWx3 BGR uint8), anchored by mask_u8
    (HxW uint8 0/255 for the FIRST frame). Returns a list of HxW float01 alpha, one per input
    frame. Faithful port of inference_matanyone2.main: warmup repeats frame 0; mask dilate+erode;
    the ti==0 / ti<=warmup / else step schedule; warmup frames dropped from the output."""
    if _MA2 not in sys.path:
        sys.path.insert(0, _MA2)
    import cv2
    import torch
    from matanyone2.utils.inference_utils import gen_dilate, gen_erosion
    from matanyone2.utils.device import safe_autocast_decorator
    from matanyone2.inference.inference_core import InferenceCore

    model, device = _model()

    @torch.inference_mode()
    @safe_autocast_decorator()
    def _run():
        processor = InferenceCore(model, cfg=model.cfg)          # fresh memory per call
        rgb = [cv2.cvtColor(f, cv2.COLOR_BGR2RGB) for f in frames_bgr]
        vt = torch.from_numpy(np.stack(rgb)).permute(0, 3, 1, 2).float()   # N,C,H,W
        warm = vt[0:1].repeat(int(n_warmup), 1, 1, 1)            # repeat first frame for warmup
        vt = torch.cat([warm, vt], dim=0)
        length = vt.shape[0]

        m = mask_u8.copy()
        if r_dilate > 0:
            m = gen_dilate(m, r_dilate, r_dilate)
        if r_erode > 0:
            m = gen_erosion(m, r_erode, r_erode)
        m = torch.from_numpy(m).float().to(device)

        out = []
        for ti in range(length):
            image = (vt[ti] / 255.0).float().to(device)
            if ti == 0:
                processor.step(image, m, objects=[1])            # encode given mask
                op = processor.step(image, first_frame_pred=True)
            elif ti <= n_warmup:
                op = processor.step(image, first_frame_pred=True)
            else:
                op = processor.step(image)
            alpha = processor.output_prob_to_mask(op)            # HxW float01 tensor
            if ti > n_warmup - 1:                                # drop warmup frames
                out.append(alpha.detach().cpu().numpy().astype(np.float32))
        return out

    return _run()


def matte_segment(frames_bgr, first_mask_u8, last_mask_u8, warmup=10, erode=10, dilate=10, bidir=True):
    """Matte one segment. frames_bgr: predark input frames (list of HxWx3 BGR uint8, in order).
    first_mask_u8 / last_mask_u8: birefnet BINARY masks (0/255) of the first / last frame. Returns a
    list of HxW float01 alpha. bidir=True: forward + reversed-backward, position-weighted merge
    (bidir_prep.py + bidir_merge.py). bidir=False: forward-only (the originally-accepted recipe — no
    fwd/bwd blend, so no per-frame merge shimmer on fine edges; last_mask unused)."""
    fwd = _propagate(frames_bgr, first_mask_u8, warmup, erode, dilate)
    if not bidir:
        return fwd
    bwd_rev = _propagate(list(reversed(frames_bgr)), last_mask_u8, warmup, erode, dilate)
    bwd = list(reversed(bwd_rev))                                # un-reverse to frame order
    return merge_bidir(fwd, bwd)


# ── persistent worker client (runs matte_segment in a SEPARATE torch process) ──────────────────
# birefnet(onnxruntime) monopolises the GPU and starves an in-process torch matte. So the main
# pipeline spawns this worker (pure torch) and — crucially — warms it BEFORE its first birefnet
# call, so torch reserves its GPU block first. See matte_matanyone_worker.py and the ledger.
_WORKER = None


def _worker():
    """Spawn (once) the persistent matte worker subprocess and block until it prints READY. Uses the
    SAME interpreter running us (the unified asset-venv-matte python), so no venv path is needed."""
    global _WORKER
    if _WORKER is None:
        import subprocess
        worker_py = os.path.join(_HERE, "matte_matanyone_worker.py")
        _WORKER = subprocess.Popen([sys.executable, worker_py],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   text=True, bufsize=1)
        for line in _WORKER.stdout:                              # wait for warm-up to finish
            if line.strip() == "READY":
                break
            if _WORKER.poll() is not None:
                raise RuntimeError("matte worker died before READY")
    return _WORKER


def ensure_worker():
    """Start + warm the worker NOW (call before the first birefnet inference so torch reserves the
    GPU before onnxruntime grabs it). Idempotent."""
    _worker()


def _reset_worker():
    """Tear down a dead/broken worker so the next _worker() spawns a fresh one (new CUDA context)."""
    global _WORKER
    if _WORKER is not None:
        try:
            _WORKER.kill()
        except Exception:
            pass
        _WORKER = None


def matte_segment_worker(frames_bgr, first_mask_u8, last_mask_u8, warmup=10, erode=10, dilate=10,
                         bidir=True):
    """Same contract as matte_segment, but the propagation runs in the worker process. Hands frames
    + masks over via a temp dir and reads the alpha PNGs back."""
    import json
    import shutil
    import tempfile
    import cv2
    w = _worker()
    work = tempfile.mkdtemp(prefix="mawork_")
    try:
        fdir = os.path.join(work, "frames")
        os.makedirs(fdir)
        for i, f in enumerate(frames_bgr):
            cv2.imwrite(os.path.join(fdir, f"{i:04d}.png"), f)
        fp, lp = os.path.join(work, "first.png"), os.path.join(work, "last.png")
        cv2.imwrite(fp, first_mask_u8)
        cv2.imwrite(lp, last_mask_u8)
        out_dir = os.path.join(work, "out")
        job = {"frames_dir": fdir, "first": fp, "last": lp, "out_dir": out_dir,
               "warmup": warmup, "erode": erode, "dilate": dilate, "bidir": bidir}
        r = _run_job_with_retry(job)                            # respawns the worker on death/CUDA error
        return [cv2.imread(os.path.join(out_dir, f"{i:03d}.png"),
                           cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
                for i in range(r["n"])]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_job_with_retry(job, attempts=2):
    """Send a job to the worker; if it dies or returns a CUDA error (sequential-job state corruption
    can trigger an illegal-memory-access that kills the process), respawn a FRESH worker and retry.
    A fresh process = fresh CUDA context, and single jobs are reliable."""
    import json
    last = None
    for k in range(attempts):
        w = _worker()
        try:
            w.stdin.write(json.dumps(job) + "\n")
            w.stdin.flush()
            reply = w.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            last = f"pipe error: {exc}"
            _reset_worker()
            continue
        if not reply:                                          # worker crashed mid-job
            last = "worker closed the pipe (no reply)"
            _reset_worker()
            continue
        r = json.loads(reply)
        if r.get("status") == "ok":
            return r
        last = r                                               # error reply (e.g. CUDA) -> fresh worker
        _reset_worker()
    raise RuntimeError(f"matte worker failed after {attempts} attempts: {last}")
