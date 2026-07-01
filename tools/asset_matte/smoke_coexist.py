"""Coexistence gate: prove birefnet (onnxruntime-gpu) and MatAnyone2 (torch cu128) both run on
CUDA in ONE process (the unified temp/asset-venv-matte). Mirrors the real pipeline's import order:
birefnet first (rembg + its nvidia-cudnn), then torch/MatAnyone2. Exit 0 = gate passed.

Run: temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/smoke_coexist.py
"""
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _HERE)                                   # matte_blankplate (for _setup_cuda + _birefnet)
sys.path.insert(0, _ROOT)                                   # mkw_tracker package (needed by extract_loop)
_MA2 = os.path.abspath(os.path.join(_HERE, "..", "..", "temp", "MatAnyone2"))
sys.path.insert(0, _MA2)


def main():
    # 1) birefnet on CUDA (this also runs matte_blankplate._setup_cuda at import)
    import matte_blankplate as mb
    img = (np.random.rand(256, 256, 3) * 255).astype(np.uint8)
    alpha, _ = mb._birefnet(img)
    assert alpha.shape == (256, 256), alpha.shape
    # rembg wraps the ORT session; CUDA provider is on the inner ORT session
    prov = mb._session().inner_session.get_providers()
    assert "CUDAExecutionProvider" in prov, prov
    assert prov[0] == "CUDAExecutionProvider", f"CUDA is not the primary provider (CPU fallback?): {prov}"
    print(f"birefnet OK on {prov[0]} alpha[min,max]={alpha.min():.3f},{alpha.max():.3f}", flush=True)

    # 2) torch + MatAnyone2 on CUDA, same process
    import torch
    assert torch.cuda.is_available(), "torch reports no CUDA"
    x = torch.ones(4, 4, device="cuda") * 2
    assert float(x.sum().item()) == 32.0
    from matanyone2.utils.download_util import load_file_from_url
    from matanyone2.utils.get_default_model import get_matanyone2_model
    from matanyone2.utils.device import get_default_device
    dev = get_default_device()
    url = "https://github.com/pq-yang/MatAnyone2/releases/download/v1.0.0/matanyone2.pth"
    ckpt = load_file_from_url(url, os.path.join(_MA2, "pretrained_models"))
    model = get_matanyone2_model(ckpt, dev)
    from matanyone2.inference.inference_core import InferenceCore
    proc = InferenceCore(model, cfg=model.cfg)
    frame = torch.rand(3, 64, 64, device=dev)
    mask = torch.zeros(64, 64, device=dev); mask[20:44, 20:44] = 1.0
    with torch.inference_mode():
        proc.step(frame, mask, objects=[1])
        op = proc.step(frame, first_frame_pred=True)
        out = proc.output_prob_to_mask(op)
    assert tuple(out.shape) == (64, 64), out.shape
    print(f"MatAnyone2 OK on {dev} step-out {tuple(out.shape)}", flush=True)
    print("COEXIST OK", flush=True)


if __name__ == "__main__":
    main()
