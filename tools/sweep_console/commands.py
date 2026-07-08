"""Pure argv builders for the three child processes."""
import os


def agent_cmd(py, at_dir):
    return [py, os.path.join(at_dir, "start_agent.py")]


def tracker_cmd(py, ws_port=8766, clip_out=None):
    cmd = [py, "-m", "mkw_tracker", "--clip-capture", "--ws-port", str(ws_port), "--no-display"]
    if clip_out:
        cmd += ["--clip-out", clip_out]          # write clips to a dir other than the default
    return cmd


def sweep_cmd(py, at_dir, capture_ws, agent_port, start_from, stop_file,
              sample_chars=None, sample_karts=None):
    cmd = [py, os.path.join(at_dir, "sweep_runner.py"),
           "--capture-ws", capture_ws, "--agent-port", str(agent_port),
           "--stop-file", stop_file]
    if start_from:
        cmd += ["--start-from", start_from]
    if sample_chars:                             # scope the sweep (e.g. dark BD-base run)
        cmd += ["--sample-chars", sample_chars]
    if sample_karts:
        cmd += ["--sample-karts", sample_karts]
    return cmd


def process_cmd(gpu_py, repo_root, clips_dir, out_dir, stop_file, claims_dir=None, ship_dir=None):
    """Headless extract+matte batch driver. Runs in the GPU venv (rembg/CUDA), not the
    console's build python; process_all.py sets its own sys.path so no PYTHONPATH is needed.
    claims_dir/ship_dir enable the multi-machine claimed-queue + ship-and-delete mode."""
    cmd = [gpu_py, os.path.join(repo_root, "tools", "asset_matte", "process_all.py"),
           "--clips", clips_dir, "--out", out_dir, "--stop-file", stop_file]
    if claims_dir:
        cmd += ["--claims-dir", claims_dir]
    if ship_dir:
        cmd += ["--ship-dir", ship_dir]
    return cmd


def viewer_cmd(py, repo_root, matte_dir, title="asset chips - all segments"):
    """Build the spawn/idle/flourish HTML viewer over a matte dir. Pure stdlib, so it runs in the
    console's build python (no GPU venv needed)."""
    return [py, os.path.join(repo_root, "tools", "asset_matte", "make_viewer.py"),
            "--matte", matte_dir, "--title", title]
