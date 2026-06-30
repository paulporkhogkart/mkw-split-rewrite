"""Sweep Console — one Tk window supervising agent + tracker + sweep.

Thin wiring: pure logic lives in controlstate/progress/health; I/O in
supervisor/wsconsumer/manual. Cross-thread updates are marshalled onto the Tk
main thread via a queue drained by after().
"""
import os
import queue
import shutil
import sys
import time
import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "..", "autotemplate")):
    _p = os.path.abspath(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import controlstate as cs
import procstate as ps
from controller_bridge import ControllerBridge
from health import HealthModel
from manual import ManualController
from progress import ProgressModel
from supervisor import ProcessSupervisor
from wsconsumer import WsConsumer

REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Clips + matte output live on the DATA drive (large SSD), NOT in the repo on C:. The app, venv,
# birefnet model + templates stay on C: (read-only at startup, no perf impact). Override the root
# with the KARTOFF_DATA_ROOT env var (e.g. to fall back to C: on a machine without D:).
DATA_ROOT = os.environ.get("KARTOFF_DATA_ROOT", r"D:\kartoff")
CLIPS_DIR = os.path.join(DATA_ROOT, "captures_sdr", "en_uk", "clips")   # rig records here + processing reads here
TOTAL = 6273
WS_URL = "ws://127.0.0.1:8766"
# Asset processing (extract+matte) — independent of the rig; runs the GPU-venv batch driver.
PROCESS_OUT = os.path.join(DATA_ROOT, "asset_chips")
PROCESS_STOP = os.path.join(PROCESS_OUT, ".process_stop")
PROCESS_MANIFEST = os.path.join(PROCESS_OUT, "manifest.json")


def _fmt_eta(s):
    if s is None:
        return "—"
    s = int(s)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class ConsoleApp:
    def __init__(self, root):
        self.root = root
        root.title("MKW Asset Sweep")
        root.geometry("1080x720")                    # fixed initial window size (still resizable)
        self.q = queue.Queue()                       # (callable) marshalled to the Tk thread
        self.state = cs.ControlState()
        self.pstate = ps.ProcessState()              # independent asset-processing lifecycle
        self.health = HealthModel()
        self.progress = ProgressModel(TOTAL)
        self.pprogress = ProgressModel(0)            # total set per-tick from the clip count
        self.sup = ProcessSupervisor(REPO_ROOT, self._on_line)
        self.sup.set_clips_dir(CLIPS_DIR)            # rig records + sweep control files live on the data drive
        for _d in (CLIPS_DIR, PROCESS_OUT):          # ensure the data-drive dirs exist up front
            try:
                os.makedirs(_d, exist_ok=True)
            except OSError:
                pass
        self.ws = None
        self.manual = None
        self._photo = None
        self._proc_last = ""                         # latest batch-driver log line (shown in headline)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain)
        root.after(1000, self._tick)

    # ── layout ──────────────────────────────────────────────────────────────────
    def _build(self):
        bar = ttk.Frame(self.root); bar.pack(fill="x", padx=6, pady=4)
        self.btn = {}
        for key, label in [("start", "Start Rig"), ("begin", "Begin Sweep"),
                           ("pause", "Pause"), ("stop", "Stop")]:
            b = ttk.Button(bar, text=label, command=lambda k=key: self._click(k))
            b.pack(side="left", padx=2); self.btn[key] = b
        self.status = ttk.Label(bar, text="● idle"); self.status.pack(side="right")

        bar2 = ttk.Frame(self.root); bar2.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Label(bar2, text="Asset processing:").pack(side="left", padx=(0, 6))
        self.pbtn = {}
        for key, label in [("pstart", "Process"), ("ppause", "Pause"), ("pstop", "Stop")]:
            b = ttk.Button(bar2, text=label, command=lambda k=key: self._click_process(k))
            b.pack(side="left", padx=2); self.pbtn[key] = b
        self.pstatus = ttk.Label(bar2, text="● idle"); self.pstatus.pack(side="right")

        body = ttk.Frame(self.root); body.pack(fill="both", expand=True)
        left = ttk.Frame(body); left.pack(side="left", fill="y", padx=6, pady=6)
        # Fix the preview to 320x180 PIXELS. A bare tk.Label sizes width/height in TEXT units until
        # an image arrives, which ballooned the left column and pushed the log panes off-screen on a
        # processing-only run (no Start Rig -> no camera image to shrink it). pack_propagate(False)
        # locks the frame's pixel size so the panes are visible from startup.
        thumb_box = tk.Frame(left, width=320, height=180, background="#111")
        thumb_box.pack(); thumb_box.pack_propagate(False)
        self.thumb = tk.Label(thumb_box, background="#111")
        self.thumb.pack(fill="both", expand=True)
        man = ttk.LabelFrame(left, text="Manual control"); man.pack(pady=8, fill="x")
        grid = ttk.Frame(man); grid.pack(padx=4, pady=4)
        for (r, c, key, txt) in [(0, 1, "up", "▲"), (1, 0, "left", "◀"),
                                 (1, 2, "right", "▶"), (2, 1, "down", "▼")]:
            ttk.Button(grid, width=3, text=txt,
                       command=lambda k=key: self._manual(k)).grid(row=r, column=c, padx=1, pady=1)
        extra = ttk.Frame(man); extra.pack(padx=4, pady=4)
        for key, txt in [("a", "A"), ("b", "B"), ("plus", "+"), ("home", "HOME")]:
            ttk.Button(extra, width=4, text=txt,
                       command=lambda k=key: self._manual(k)).pack(side="left", padx=1)
        self.man_frame = man

        right = ttk.Frame(body); right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self.hl = ttk.Label(right, justify="left", font=("Consolas", 10))
        self.hl.pack(anchor="w")
        self.phl = ttk.Label(right, justify="left", font=("Consolas", 10), foreground="#2a7")
        self.phl.pack(anchor="w")
        self.logs = {}
        for name in ("agent", "tracker", "sweep", "process"):
            lf = ttk.LabelFrame(right, text=name); lf.pack(fill="both", expand=True, pady=2)
            txt = tk.Text(lf, height=7, wrap="none", font=("Consolas", 8))
            txt.pack(fill="both", expand=True); txt.configure(state="disabled")
            self.logs[name] = txt
        self._refresh_buttons()

    # ── thread-safe plumbing ─────────────────────────────────────────────────────
    def _on_line(self, name, text):
        self.q.put(lambda: self._append(name, text))

    def _on_preview(self, msg):
        self.q.put(lambda: self._set_thumb(msg))

    def _on_state(self, msg):
        self.q.put(lambda: self._apply_state(msg))

    def _drain(self):
        try:
            while True:
                cb = self.q.get_nowait()
                try:
                    cb()
                except Exception:
                    pass   # a callback error must not stop draining
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _append(self, name, text):
        t = self.logs.get(name)
        if not t:
            return
        t.configure(state="normal")
        t.insert("end", text + "\n")
        if int(t.index("end-1c").split(".")[0]) > 400:
            t.delete("1.0", "100.0")
        t.see("end"); t.configure(state="disabled")
        if name == "process" and text.strip():
            self._proc_last = text.strip()

    def _set_thumb(self, msg):
        try:
            self._photo = tk.PhotoImage(data=msg["data"])   # base64 PNG (Tk 8.6)
            self.thumb.configure(image=self._photo)
        except Exception:
            pass

    def _apply_state(self, msg):
        self.health.apply(msg, now=time.monotonic())
        if msg.get("type") == "clip_done":
            self.progress.update(self.sup.clip_count(), time.monotonic())

    # ── controls ─────────────────────────────────────────────────────────────────
    def _click(self, key):
        event = {"start": cs.START_RIG, "begin": cs.BEGIN_SWEEP,
                 "stop": cs.STOP}.get(key)
        if key == "pause":
            event = cs.RESUME if self.state.state == cs.PAUSED else cs.PAUSE
        for act in self.state.on_event(event):
            self._do(act)
        self._refresh_buttons()

    def _stop_file(self):
        return os.path.join(os.path.dirname(self.sup.clips_dir), ".sweep_stop")

    def _do(self, action):
        if action == "start_agent":
            self.sup.start_agent()
        elif action == "start_tracker":
            self.sup.start_tracker(clip_out=CLIPS_DIR)   # rig records straight to the data drive
        elif action == "connect_ws":
            self.ws = WsConsumer(WS_URL, self._on_preview, self._on_state); self.ws.start()
        elif action == "connect_manual":
            br = ControllerBridge(); br.connect(); br.start_reconnect_loop()
            self.manual = ManualController(br)
        elif action in ("enable_manual", "disable_manual"):
            pass                                       # handled by _refresh_buttons
        elif action == "start_sweep":
            start_from = self.sup.read_resume()
            self.sup.start_sweep(start_from, self._stop_file(), on_exit=self._sweep_exited)
        elif action == "request_sweep_stop":
            self.sup.request_stop_file(self._stop_file())
        elif action == "stop_rig":
            self.sup.kill_tracker(); self.sup.kill_agent()
        elif action == "disconnect":
            if self.ws: self.ws.close(); self.ws = None
            if self.manual: self.manual.close(); self.manual = None

    def _sweep_exited(self):
        self.q.put(lambda: self._after_sweep_exit())

    def _after_sweep_exit(self):
        for act in self.state.on_event(cs.SWEEP_EXITED):
            self._do(act)
        self._refresh_buttons()

    # ── asset processing (independent of the rig) ────────────────────────────────
    def _click_process(self, key):
        if key == "pstart" and self.pstate.state == ps.IDLE:
            self.pprogress = ProgressModel(self.sup.clip_count())   # fresh run: reset ETA samples
        event = {"pstart": ps.START, "pstop": ps.STOP}.get(key)
        if key == "ppause":
            event = ps.RESUME if self.pstate.state == ps.PAUSED else ps.PAUSE
        for act in self.pstate.on_event(event):
            self._do_process(act)
        self._refresh_buttons()

    def _do_process(self, action):
        if action == "start_processing":
            self.sup.start_processing(CLIPS_DIR, PROCESS_OUT, PROCESS_STOP,
                                      on_exit=self._process_exited)
        elif action == "request_process_stop":
            self.sup.request_stop_file(PROCESS_STOP)
        elif action == "completed":
            self._on_line("process", "[console] all clips processed")

    def _process_exited(self):
        self.q.put(self._after_process_exit)

    def _after_process_exit(self):
        for act in self.pstate.on_event(ps.EXITED):
            self._do_process(act)
        msg = self.sup.build_viewer(os.path.join(PROCESS_OUT, "matte"))   # regenerate the chip viewer
        if msg:
            self._on_line("process", f"[console] {msg}")
        self._refresh_buttons()

    def _manual(self, key):
        if self.manual and self.state.state in (cs.RIG_WARM, cs.PAUSED):
            self.manual.press(key)

    def _refresh_buttons(self):
        s = self.state.state
        self.btn["start"].configure(state=("normal" if s == cs.IDLE else "disabled"))
        self.btn["begin"].configure(state=("normal" if s == cs.RIG_WARM else "disabled"))
        self.btn["pause"].configure(
            text=("Resume" if s == cs.PAUSED else "Pause"),
            state=("normal" if s in (cs.SWEEPING, cs.PAUSED) else "disabled"))
        self.btn["stop"].configure(state=("disabled" if s == cs.IDLE else "normal"))
        manual_on = s in (cs.RIG_WARM, cs.PAUSED)
        for child in self.man_frame.winfo_children():
            for b in child.winfo_children():
                try: b.configure(state=("normal" if manual_on else "disabled"))
                except tk.TclError: pass
        self.status.configure(text=f"● {s.lower().replace('_', ' ')}")
        pp = self.pstate.state
        self.pbtn["pstart"].configure(state=("normal" if pp == ps.IDLE else "disabled"))
        self.pbtn["ppause"].configure(
            text=("Resume" if pp == ps.PAUSED else "Pause"),
            state=("normal" if pp in (ps.RUNNING, ps.PAUSED) else "disabled"))
        self.pbtn["pstop"].configure(state=("disabled" if pp == ps.IDLE else "normal"))
        self.pstatus.configure(text=f"● {pp.lower().replace('_', ' ')}")

    def _free_gb(self):
        try:
            return shutil.disk_usage(self.sup.clips_dir).free / 1e9
        except OSError:
            return None

    # ── 1 Hz refresh ─────────────────────────────────────────────────────────────
    def _tick(self):
        if self.manual:
            try:
                st = self.manual.status(); self.health.set_controller(st["connected"], st["mac"])
            except Exception:
                self.health.set_controller(False)
        self.progress.update(self.sup.clip_count(), time.monotonic())
        h = self.health.snapshot(time.monotonic()); pr = self.progress.snapshot()
        age = h["last_clip_age"]
        gb = self._free_gb()
        self.hl.configure(text=(
            f"controller {'OK' if h['controller'] else 'X'}    screen {h['screen'] or '-'}\n"
            f"{h['character'] or '-'} / {h['costume'] or '-'} / {h['kart'] or '-'}\n"
            f"clips {pr['done']}/{pr['total']}  {pr['pct']*100:4.1f}%   ETA {_fmt_eta(pr['eta_seconds'])}\n"
            f"last clip {('%.0fs' % age) if age is not None else '-'}   fps {h['fps'] or '-'}   disk {('%.0f GB' % gb) if gb is not None else '-'}"))
        self.pprogress.total = self.sup.clip_count()
        self.pprogress.update(self.sup.process_done_count(PROCESS_MANIFEST), time.monotonic())
        pp = self.pprogress.snapshot()
        self.phl.configure(text=(
            f"processed {pp['done']}/{pp['total']}  {pp['pct'] * 100:4.1f}%   "
            f"ETA {_fmt_eta(pp['eta_seconds'])}   [{self.pstate.state.lower().replace('_', ' ')}]\n"
            f"{self._proc_last[:72]}"))
        self.root.after(1000, self._tick)

    def _on_close(self):
        if self.state.state != cs.IDLE:
            for act in self.state.on_event(cs.STOP):
                self._do(act)
            if self.state.state == cs.STOP_REQUESTED:
                self.sup.wait_sweep(timeout=60)
                for act in self.state.on_event(cs.SWEEP_EXITED):
                    self._do(act)
        if self.pstate.state in (ps.RUNNING, ps.PAUSE_REQUESTED, ps.STOP_REQUESTED):
            self.sup.request_stop_file(PROCESS_STOP)   # clean-stop the batch between clips
            self.sup.wait_processing(timeout=120)
        self.root.destroy()


def main():
    root = tk.Tk()
    ConsoleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
