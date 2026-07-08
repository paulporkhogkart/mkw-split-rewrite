"""CUDA-context-loss recovery for the birefnet ONNX session (matte_blankplate).

A TDR / GPU driver reset — common on a Windows workstation whose display GPU also runs the matte
batch, because desktop/browser GPU contention can stall a kernel past `TdrDelay` (default 2s) —
destroys every CUDA context on the card. onnxruntime then fails every later call with
`CUDA failure 999: unknown error` on a trivial memcpy. Because the rembg session is a module
singleton that was never rebuilt, the batch used to CASCADE: every remaining clip hit the same dead
context and got marked 'error', burning the rest of a ~100 GPU-hr run.

This mirrors the MatAnyone worker's respawn-on-CUDA-death recovery (matte_matanyone._reset_worker):
on a CUDA failure, drop + rebuild the session and retry ONCE (a fresh context works once the driver
has recovered); a second CUDA failure raises CudaContextLost so the batch driver stops cleanly and a
fresh PROCESS (guaranteed-fresh context) resumes from the manifest (run_matte.bat auto-restarts).

Pure stdlib (no rembg/torch/GPU) so it unit-tests without the GPU venv — see test_cuda_recovery.py.
"""

# Case-insensitive substrings that mark a CUDA/GPU-context failure (vs a normal per-clip error).
# The observed TDR error is:
#   [ONNXRuntimeError] : 1 : FAIL : CUDA failure 999: unknown error ; GPU=0 ; hostname=... ;
#   ... expr=cudaMemcpyAsync(dst_data, src_data, bytes, cudaMemcpyHostToDevice, ...);
_CUDA_MARKERS = ("cuda failure", "cudaerror", "cuda_error", "cuda error",
                 "cudamemcpy", "cudnn", "cublas", "gpu=")


class CudaContextLost(RuntimeError):
    """The birefnet CUDA context died and an in-process rebuild+retry did NOT recover it (the driver
    is still wedged). The batch driver treats this as 'stop cleanly, let a fresh process resume' —
    NOT a per-clip error — because every remaining clip would hit the same dead context."""


def is_cuda_failure(exc):
    """True if `exc` looks like a CUDA/GPU context failure rather than a normal per-clip error."""
    s = str(exc).lower()
    return any(m in s for m in _CUDA_MARKERS)


def call_with_cuda_recovery(call, rebuild):
    """Run `call()`; on a CUDA failure, `rebuild()` the context and retry ONCE. A second CUDA failure
    raises CudaContextLost. Non-CUDA exceptions propagate unchanged (no rebuild).

    `call`    performs the GPU work and returns its result.
    `rebuild` drops the dead session so the next `call()` constructs a fresh CUDA context.
    """
    try:
        return call()
    except Exception as exc:
        if not is_cuda_failure(exc):
            raise
        print(f"[cuda-recovery] CUDA failure ({exc}); rebuilding context + retrying once...",
              flush=True)
        rebuild()
        try:
            return call()
        except Exception as exc2:
            rebuild()                                    # leave no dead session behind
            if is_cuda_failure(exc2):
                raise CudaContextLost(str(exc2)) from exc2
            raise


# ── batch-driver exit codes: a CUDA context loss the driver couldn't recover in-process ─────────
# A fresh PROCESS gets a fresh CUDA context, so a supervisor (sweep_console app / run_matte.bat)
# should RESTART on these rather than treat them as completion. Kept here — next to CudaContextLost —
# as the single source of truth for the process_all <-> supervisor contract.
EXIT_CUDA_LOST_PROGRESS = 75    # >=1 clip matted this run before the loss (transient) -> reset the guard
EXIT_CUDA_LOST_NOPROG = 76      # 0 clips this run (GPU maybe wedged) -> count consecutive for a give-up cap

# classify_process_exit decisions:
RESTART = "restart"             # relaunch the batch with a fresh process (fresh context)
GIVE_UP = "give_up"             # too many no-progress losses in a row -> stop; user must reboot the GPU
NORMAL = "normal"               # not a context-loss exit (or a user-requested stop) -> ordinary handling


def classify_process_exit(code, running, wedged, cap=5):
    """Decide what a supervisor should do when the batch subprocess exits with `code`.

    running: is the run still logically RUNNING (i.e. the user did NOT request stop/pause)?
    wedged:  count of consecutive no-progress context losses so far.
    Returns (decision, new_wedged). Only a still-RUNNING process auto-restarts; a user stop/pause
    always yields NORMAL so the caller settles the state as the user asked. new_wedged resets to 0 on
    anything but a NOPROG loss, and after `cap` NOPROG losses in a row the decision becomes GIVE_UP."""
    if running and code in (EXIT_CUDA_LOST_PROGRESS, EXIT_CUDA_LOST_NOPROG):
        new_wedged = wedged + 1 if code == EXIT_CUDA_LOST_NOPROG else 0
        return (GIVE_UP if new_wedged >= cap else RESTART), new_wedged
    return NORMAL, 0
