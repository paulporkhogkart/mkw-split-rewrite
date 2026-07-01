# Asset-matte unified venv — build reference

**Venv path:** `temp/asset-venv-matte` (gitignored — rebuild from these steps)
**Python:** 3.12.10
**GPU:** RTX 5080 (Blackwell), CUDA 12.x / cuDNN 9.x
**Purpose:** Unified venv that runs birefnet (onnxruntime-gpu) and MatAnyone2 (torch cu128)
**in one process** — the design requires this; no subprocess/worker fallback.

## Build commands

```bash
cd /c/development/mkw-split-rewrite

# 1. Create venv
py -3.12 -m venv temp/asset-venv-matte
temp/asset-venv-matte/Scripts/python.exe -m pip install --upgrade pip

# 2. torch cu128 (Blackwell 5080 — must be cu128, NOT cu121/cu124)
temp/asset-venv-matte/Scripts/python.exe -m pip install torch==2.11.0 torchvision==0.26.0 \
    --index-url https://download.pytorch.org/whl/cu128

# 3. birefnet stack + matte deps
temp/asset-venv-matte/Scripts/python.exe -m pip install \
    onnxruntime-gpu==1.22.0 rembg==2.0.76 opencv-python numpy pillow

# 4. nvidia CUDA/cuDNN runtime wheels (same version as asset-venv-gpu;
#    onnxruntime-gpu does NOT pull these automatically on Windows)
temp/asset-venv-matte/Scripts/python.exe -m pip install \
    "nvidia-cudnn-cu12==9.23.2.1" "nvidia-cuda-runtime-cu12==12.9.79"

# 5. MatAnyone2 minimal deps (no cchardet/PySide6/netifaces from its pyproject)
temp/asset-venv-matte/Scripts/python.exe -m pip install \
    hydra-core omegaconf einops tqdm imageio imageio-ffmpeg \
    safetensors huggingface_hub easydict requests av

# 6. MatAnyone2 package itself — --no-deps avoids the broken extras in its pyproject
temp/asset-venv-matte/Scripts/python.exe -m pip install --no-deps -e temp/MatAnyone2
```

## Coexistence smoke test

```bash
temp/asset-venv-matte/Scripts/python.exe tools/asset_matte/smoke_coexist.py
```

Expected output:
```
birefnet OK on CUDAExecutionProvider alpha[min,max]=0.000,0.000
MatAnyone2 OK on cuda step-out (64, 64)
COEXIST OK
```

The script (`tools/asset_matte/smoke_coexist.py`) imports birefnet first (which calls
`_setup_cuda()` to register the nvidia DLL directories via `os.add_dll_directory`), then
imports torch and runs a MatAnyone2 `InferenceCore` propagation — all in one process.

## cuDNN coexistence notes

**No DLL conflict was observed.** Both onnxruntime-gpu 1.22.0 and torch cu128 target CUDA 12.x /
cuDNN 9.x. The explicit `nvidia-cudnn-cu12==9.23.2.1` pin (step 4) aligns the cuDNN version
across both backends. `matte_blankplate._setup_cuda()` registers the nvidia wheel `bin/`
directories via `os.add_dll_directory` at import time, which loads them before torch initialises;
that import order (birefnet first) is the one the smoke test mirrors, and no DLL error surfaced.

## Two script-level path fixes made during gate verification

1. `smoke_coexist.py` adds `_ROOT` (repo root) to `sys.path` so `extract_loop` can resolve
   `mkw_tracker.tools.loop_probe` (which is used by `matte_blankplate` → `extract_loop`).
2. The CUDA-provider assertion uses `mb._session().inner_session.get_providers()` — the rembg
   session is a thin wrapper; the raw onnxruntime `InferenceSession` lives at `.inner_session`.
   CUDAExecutionProvider was confirmed active.

## Installed package versions (key)

| Package | Version |
|---------|---------|
| torch | 2.11.0+cu128 |
| torchvision | 0.26.0+cu128 |
| onnxruntime-gpu | 1.22.0 |
| rembg | 2.0.76 |
| nvidia-cudnn-cu12 | 9.23.2.1 |
| nvidia-cublas-cu12 | 12.9.2.10 |
| nvidia-cuda-runtime-cu12 | 12.9.79 |
| numpy | 2.4.4 |
| opencv-python | 4.13.0.92 |
| matanyone2 | 1.0.0 (editable, temp/MatAnyone2) |

## Existing venvs (left untouched)

- `temp/asset-venv-gpu` — original birefnet-only venv (onnxruntime-gpu + rembg, no torch)
- `temp/matanyone-venv` — original MatAnyone2-only venv (torch cu128, no onnxruntime)

These are kept as operator fallbacks and are not used by the production pipeline.
