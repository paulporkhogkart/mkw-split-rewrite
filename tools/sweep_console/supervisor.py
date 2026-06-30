"""Spawns + monitors the 3 children with piped stdout (no console windows).

I/O glue: pure argv come from commands.py; teardown of the in-WSL agent reuses
start_agent.stop_agent. on_line(child_name, text) is invoked from reader threads.
"""
import os
import re
import subprocess
import sys
import threading

import commands

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW
_CHAR_RE = re.compile(r"-- char:\s*(\S+)\s*--")


class ProcessSupervisor:
    def __init__(self, repo_root, on_line, py=sys.executable):
        self.repo_root = repo_root
        self.at_dir = os.path.join(repo_root, "tools", "autotemplate")
        self.clips_dir = os.path.join(repo_root, "captures_sdr", "en_uk", "clips")
        self.on_line = on_line
        self.py = py
        # the matte batch driver needs the GPU venv (rembg/CUDA), not the console's build python
        self.gpu_py = os.path.join(repo_root, "temp", "asset-venv-gpu", "Scripts", "python.exe")
        self.procs = {}            # name -> Popen
        self._resume = os.path.join(self.clips_dir, ".resume_char")

    # ── spawning ──────────────────────────────────────────────────────────────
    def _spawn(self, name, cmd, on_exit=None):
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        p = subprocess.Popen(cmd, cwd=self.repo_root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1,
                              creationflags=_NO_WINDOW, env=env)
        self.procs[name] = p
        threading.Thread(target=self._pump, args=(name, p, on_exit), daemon=True).start()
        return p

    def _pump(self, name, p, on_exit):
        for line in p.stdout:
            line = line.rstrip("\n")
            if name == "sweep":
                m = _CHAR_RE.search(line)
                if m:
                    self.write_resume(m.group(1))
                elif "Sweep complete." in line or "Sample complete" in line:
                    self.clear_resume()
            self.on_line(name, line)
        p.wait()
        if on_exit:
            on_exit()

    def start_agent(self):
        return self._spawn("agent", commands.agent_cmd(self.py, self.at_dir))

    def set_clips_dir(self, clips_dir):
        """Re-point the sweep target — recorder out-dir, clip count, and resume marker — at
        `clips_dir`. Used to switch between the bright set and the DARK-background set. Call
        only while IDLE (the rig bakes the recorder out-dir at launch)."""
        self.clips_dir = clips_dir
        self._resume = os.path.join(self.clips_dir, ".resume_char")

    def start_tracker(self, ws_port=8766, clip_out=None):
        return self._spawn("tracker", commands.tracker_cmd(self.py, ws_port, clip_out))

    def start_sweep(self, start_from, stop_file, on_exit=None, sample_chars=None, sample_karts=None):
        try:
            if os.path.exists(stop_file):
                os.remove(stop_file)                 # clear any stale pause flag
        except OSError:
            pass
        return self._spawn("sweep",
                           commands.sweep_cmd(self.py, self.at_dir, "ws://127.0.0.1:8766",
                                              7878, start_from, stop_file,
                                              sample_chars=sample_chars, sample_karts=sample_karts),
                           on_exit=on_exit)

    def start_processing(self, clips_dir, out_dir, stop_file, on_exit=None):
        """Spawn the headless extract+matte batch driver in the GPU venv. Resumable: it skips
        clips already 'done' in <out_dir>/manifest.json, so RESUME just relaunches this."""
        try:
            if os.path.exists(stop_file):
                os.remove(stop_file)                 # clear any stale pause/stop flag
        except OSError:
            pass
        os.makedirs(out_dir, exist_ok=True)
        return self._spawn("process",
                           commands.process_cmd(self.gpu_py, self.repo_root, clips_dir, out_dir, stop_file),
                           on_exit=on_exit)

    def wait_processing(self, timeout=120.0):
        p = self.procs.get("process")
        if p:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()

    def build_viewer(self, matte_dir, title="asset chips - all segments"):
        """Regenerate the spawn/idle/flourish HTML viewer over a matte dir (synchronous,
        best-effort, build python). Returns the tool's last output line, or None if there's
        nothing to build / it failed -- never raises, so a process exit is never blocked."""
        if not os.path.isdir(matte_dir):
            return None
        try:
            r = subprocess.run(commands.viewer_cmd(self.py, self.repo_root, matte_dir, title),
                               capture_output=True, text=True, timeout=120,
                               creationflags=_NO_WINDOW)
            lines = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines() if ln.strip()]
            return lines[-1] if lines else None
        except Exception as exc:
            return f"viewer build failed: {exc}"

    # ── stop / teardown ─────────────────────────────────────────────────────────
    def request_stop_file(self, stop_file):
        d = os.path.dirname(stop_file)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(stop_file, "w") as f:
            f.write("stop")

    def wait_sweep(self, timeout=60.0):
        p = self.procs.get("sweep")
        if p:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()

    def _kill_tree(self, name):
        p = self.procs.pop(name, None)
        if not p or p.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True)
        else:
            p.terminate()

    def kill_tracker(self):
        self._kill_tree("tracker")

    def kill_agent(self, distro=None):
        # 1) stop the in-WSL agent (no in-band shutdown), 2) kill the Windows launcher tree.
        try:
            import start_agent
            start_agent.stop_agent(distro=distro)
        except Exception as exc:                      # best-effort
            self.on_line("agent", f"[console] stop_agent failed: {exc}")
        self._kill_tree("agent")

    # ── progress / resume marker ────────────────────────────────────────────────
    def clip_count(self):
        try:
            return sum(1 for f in os.listdir(self.clips_dir) if f.endswith(".mkv"))
        except OSError:
            return 0

    def process_done_count(self, manifest_path):
        """How many clips the batch driver has finished matting (from its manifest)."""
        try:
            import json
            with open(manifest_path) as f:
                m = json.load(f)
            return sum(1 for v in m.values() if isinstance(v, dict) and v.get("status") == "done")
        except (OSError, ValueError):
            return 0

    def read_resume(self):
        try:
            with open(self._resume) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def write_resume(self, slug):
        try:
            os.makedirs(self.clips_dir, exist_ok=True)
            with open(self._resume, "w") as f:
                f.write(slug)
        except OSError:
            pass

    def clear_resume(self):
        try:
            os.remove(self._resume)
        except OSError:
            pass
