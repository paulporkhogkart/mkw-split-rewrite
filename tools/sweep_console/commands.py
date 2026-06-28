"""Pure argv builders for the three child processes."""
import os


def agent_cmd(py, at_dir):
    return [py, os.path.join(at_dir, "start_agent.py")]


def tracker_cmd(py, ws_port=8766):
    return [py, "-m", "mkw_tracker", "--clip-capture", "--ws-port", str(ws_port), "--no-display"]


def sweep_cmd(py, at_dir, capture_ws, agent_port, start_from, stop_file):
    cmd = [py, os.path.join(at_dir, "sweep_runner.py"),
           "--capture-ws", capture_ws, "--agent-port", str(agent_port),
           "--stop-file", stop_file]
    if start_from:
        cmd += ["--start-from", start_from]
    return cmd
