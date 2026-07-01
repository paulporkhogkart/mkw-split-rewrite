"""Persistent MatAnyone2 matte worker — runs in its OWN process (pure torch, NEVER imports
rembg/onnxruntime).

Why a separate process: birefnet (onnxruntime) and MatAnyone2 (torch) cannot share one process on
the GPU — onnxruntime's arena grabs the whole card on its first inference and starves torch into a
~50x thrash (measured: after birefnet, torch had 0 GB free; a 5-frame matte took 45 s). In SEPARATE
processes the allocations are independent, so if this worker reserves its ~4 GB BEFORE the main
process's birefnet grabs the rest, both run at full speed (validated cross-process: ~10.5 fps bidir
while birefnet holds ~15 GB next door).

Lifecycle: load model -> WARM a tiny full-res matte to reserve GPU memory -> print `READY` -> serve
one JSON job per stdin line:
    {"frames_dir":.., "first":.., "last":.., "out_dir":.., "warmup":10, "erode":10, "dilate":10}
matte -> write alpha `NNN.png` (grayscale) into out_dir -> reply one JSON line
    {"status":"ok","n":N}   or   {"status":"error","error":".."}
`quit` or EOF exits. Spawned/driven by matte_matanyone._worker().
"""
import glob
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matte_matanyone as mm                                   # torch only; no rembg/onnxruntime


def _load_frames(d):
    return [cv2.imread(p) for p in sorted(glob.glob(os.path.join(d, "*.png")))]


def _cuda_cleanup():
    """Sync + free cached CUDA memory between jobs. MatAnyone2 leaves per-video memory around and
    running several segments back-to-back in one process can corrupt CUDA state (illegal memory
    access on a later job); an explicit sync+empty_cache between jobs prevents the accumulation."""
    try:
        import torch
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
    except Exception:
        pass


def main():
    # WARM-RESERVE: run one tiny full-res (988x1080) matte so torch claims its GPU block before any
    # birefnet process can monopolise the card. A 2-frame pass reserves the same per-step peak as a
    # long one (peak is per-step, not per-sequence), which is what protects us cross-process.
    try:
        dummy = np.zeros((1080, 988, 3), np.uint8)
        full = np.full((1080, 988), 255, np.uint8)
        mm.matte_segment([dummy, dummy], full, full, warmup=2)
    except Exception as exc:                                   # report but still serve
        print(json.dumps({"status": "warmup_error", "error": str(exc)}), flush=True)
    print("READY", flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line or line == "quit":
            break
        try:
            job = json.loads(line)
            frames = _load_frames(job["frames_dir"])
            first = cv2.imread(job["first"], cv2.IMREAD_GRAYSCALE)
            last = cv2.imread(job["last"], cv2.IMREAD_GRAYSCALE)
            alphas = mm.matte_segment(frames, first, last,
                                      warmup=job.get("warmup", 10),
                                      erode=job.get("erode", 10),
                                      dilate=job.get("dilate", 10),
                                      bidir=job.get("bidir", True))
            out_dir = job["out_dir"]
            os.makedirs(out_dir, exist_ok=True)
            for i, a in enumerate(alphas):
                cv2.imwrite(os.path.join(out_dir, f"{i:03d}.png"),
                            (np.clip(a, 0, 1) * 255).astype(np.uint8))
            _cuda_cleanup()                                    # reset CUDA state between jobs
            print(json.dumps({"status": "ok", "n": len(alphas)}), flush=True)
        except Exception as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({"status": "error", "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
