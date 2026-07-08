"""Unit tests for cuda_recovery — the birefnet CUDA-context-loss recovery. Pure stdlib, no GPU/venv:
run with any python (`python tools/asset_matte/test_cuda_recovery.py`)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cuda_recovery import (CudaContextLost, EXIT_CUDA_LOST_NOPROG, EXIT_CUDA_LOST_PROGRESS,
                           GIVE_UP, NORMAL, RESTART, call_with_cuda_recovery,
                           classify_process_exit, is_cuda_failure)

# the exact onnxruntime error observed on the rig (PAUL-AM5-DT) when a TDR reset the driver mid-sweep
REAL_TDR_ERR = ("[ONNXRuntimeError] : 1 : FAIL : CUDA failure 999: unknown error ; GPU=0 ; "
                "hostname=PAUL-AM5-DT ; file=...gpu_data_transfer.cc ; line=65 ; "
                "expr=cudaMemcpyAsync(dst_data, src_data, bytes, cudaMemcpyHostToDevice, ...);")


class _CudaErr(RuntimeError):
    """Stand-in for onnxruntime's Fail exception (which needs the GPU venv to import)."""


class TestIsCudaFailure(unittest.TestCase):
    def test_real_tdr_message_classified(self):
        self.assertTrue(is_cuda_failure(_CudaErr(REAL_TDR_ERR)))

    def test_plain_errors_not_classified(self):
        self.assertFalse(is_cuda_failure(ValueError("no loop frames in 'foo'")))
        self.assertFalse(is_cuda_failure(RuntimeError("ffmpeg failed")))
        self.assertFalse(is_cuda_failure(OSError("disk full")))


class TestRecovery(unittest.TestCase):
    def test_recovers_after_one_cuda_failure(self):
        """Transient context loss the driver recovers from: rebuild once, retry succeeds."""
        calls, rebuilds = {"n": 0}, {"n": 0}

        def call():
            calls["n"] += 1
            if calls["n"] == 1:
                raise _CudaErr(REAL_TDR_ERR)     # context just died
            return "ALPHA"                       # fresh session works

        def rebuild():
            rebuilds["n"] += 1

        self.assertEqual(call_with_cuda_recovery(call, rebuild), "ALPHA")
        self.assertEqual(calls["n"], 2)          # original + one retry
        self.assertEqual(rebuilds["n"], 1)       # session rebuilt once

    def test_persistent_cuda_failure_raises_context_lost(self):
        """Driver still wedged after rebuild -> CudaContextLost (batch driver stops cleanly)."""
        rebuilds = {"n": 0}

        def call():
            raise _CudaErr(REAL_TDR_ERR)

        def rebuild():
            rebuilds["n"] += 1

        with self.assertRaises(CudaContextLost):
            call_with_cuda_recovery(call, rebuild)
        self.assertEqual(rebuilds["n"], 2)       # rebuilt before AND after the failed retry

    def test_non_cuda_error_propagates_unwrapped(self):
        """A normal per-clip error must NOT trigger a rebuild and must NOT become CudaContextLost."""
        def call():
            raise ValueError("no loop frames")

        def rebuild():
            raise AssertionError("must not rebuild on a non-CUDA error")

        with self.assertRaises(ValueError):
            call_with_cuda_recovery(call, rebuild)

    def test_first_call_success_no_rebuild(self):
        def call():
            return "ALPHA"

        def rebuild():
            raise AssertionError("must not rebuild when the first call succeeds")

        self.assertEqual(call_with_cuda_recovery(call, rebuild), "ALPHA")


class TestClassifyProcessExit(unittest.TestCase):
    def test_progress_loss_restarts_and_resets_guard(self):
        # a loss after real progress is transient: restart, and clear the no-progress guard
        self.assertEqual(classify_process_exit(EXIT_CUDA_LOST_PROGRESS, running=True, wedged=3),
                         (RESTART, 0))

    def test_noprogress_loss_restarts_and_counts(self):
        self.assertEqual(classify_process_exit(EXIT_CUDA_LOST_NOPROG, running=True, wedged=0),
                         (RESTART, 1))
        self.assertEqual(classify_process_exit(EXIT_CUDA_LOST_NOPROG, running=True, wedged=3),
                         (RESTART, 4))

    def test_noprogress_hits_cap_gives_up(self):
        self.assertEqual(classify_process_exit(EXIT_CUDA_LOST_NOPROG, running=True, wedged=4),
                         (GIVE_UP, 5))

    def test_user_stop_never_restarts(self):
        # context-loss code, but the user had requested stop/pause (running=False) -> honour it
        self.assertEqual(classify_process_exit(EXIT_CUDA_LOST_NOPROG, running=False, wedged=2),
                         (NORMAL, 0))

    def test_clean_and_crash_exits_are_normal(self):
        self.assertEqual(classify_process_exit(0, running=True, wedged=2), (NORMAL, 0))
        self.assertEqual(classify_process_exit(1, running=True, wedged=0), (NORMAL, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
