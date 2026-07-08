"""Sweep Console — one Tk window supervising agent + tracker + sweep.

Thin wiring: pure logic lives in controlstate/progress/health; I/O in
supervisor/wsconsumer/manual. Cross-thread updates are marshalled onto the Tk
main thread via a queue drained by after().
"""
import glob
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (_HERE, os.path.join(_HERE, "..", "autotemplate"), os.path.join(_HERE, "..", "asset_matte")):
    _p = os.path.abspath(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import controlstate as cs
import cuda_recovery                                  # shared exit-code contract with process_all
import procstate as ps
from controller_bridge import ControllerBridge
from health import HealthModel
from manual import ManualController
from progress import ProgressModel
from supervisor import ProcessSupervisor, parse_matte_progress
from wsconsumer import WsConsumer

REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
# Clips + matte output live on the DATA drive (large SSD), NOT in the repo on C:. The app, venv,
# birefnet model + templates stay on C: (read-only at startup, no perf impact). Paths resolve from
# the environment (procconfig): default = today's single-machine layout under KARTOFF_DATA_ROOT;
# the 2nd box overrides KARTOFF_CLIPS_DIR/PROCESS_OUT/CLAIMS_DIR/SHIP_DIR to run against the rig.
import procconfig
_CFG = procconfig.resolve_process_config(os.environ)
DATA_ROOT = os.environ.get("KARTOFF_DATA_ROOT", r"D:\kartoff")
CLIPS_DIR = _CFG.clips                          # rig records here + processing reads here
TOTAL = 6273
WS_URL = "ws://127.0.0.1:8766"
# Asset processing (extract+matte) — independent of the rig; runs the GPU-venv batch driver.
PROCESS_OUT = _CFG.out
PROCESS_STOP = _CFG.stop
PROCESS_MANIFEST = _CFG.manifest
CLAIMS_DIR = _CFG.claims                        # None = single-machine; set = multi-machine coordinator
SHIP_DIR = _CFG.ship                            # None = write in place; set = ship-and-delete to share
PP_W, PP_H = 200, 219                          # processing-preview box (matched to the 988x1080 chip crop)
PP_BG = (0, 177, 64)                           # greenscreen chroma-key green behind the chip preview
PP_BG_HEX = "#%02x%02x%02x" % PP_BG            # same colour for the Tk box, so the whole pane keys green


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
        _bootstrap = [CLIPS_DIR, PROCESS_OUT]
        if CLAIMS_DIR:
            _bootstrap.append(CLAIMS_DIR)
        if SHIP_DIR:
            _bootstrap.append(os.path.join(SHIP_DIR, "matte"))
        for _d in _bootstrap:                        # ensure the data-drive dirs exist up front
            try:
                os.makedirs(_d, exist_ok=True)
            except OSError:
                pass
        self.ws = None
        self.manual = None
        self._photo = None
        self._proc_last = ""                         # latest batch-driver log line (shown in headline)
        self._proc_seg = None                        # current matte segment dir name (for the preview)
        self._proc_frac = ""                         # 'done/total' of the current segment
        self._proc_wedged = 0                        # consecutive no-progress CUDA-context losses (restart guard)
        self._pphoto = None                          # keep a ref so Tk doesn't GC the preview image
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
        # Always-clickable: rebuild the chip viewer/index over the FINAL matte dir, unioning every
        # manifest*.json (box 1 + box 2). Press it on the rig once both boxes have stopped. Runs off
        # the Tk thread so a large (or over-SMB) glob never freezes the UI.
        self.btn_viewer = ttk.Button(bar2, text="Build viewer", command=self._click_build_viewer)
        self.btn_viewer.pack(side="left", padx=(12, 2))
        self.pstatus = ttk.Label(bar2, text="● idle"); self.pstatus.pack(side="right")

        body = ttk.Frame(self.root); body.pack(fill="both", expand=True)
        left = ttk.Frame(body); left.pack(side="left", fill="y", padx=6, pady=6)
        # Fix the preview to 320x180 PIXELS. A bare tk.Label sizes width/height in TEXT units until
        # an image arrives, which ballooned the left column and pushed the log panes off-screen on a
        # processing-only run (no Start Rig -> no camera image to shrink it). pack_propagate(False)
        # locks the frame's pixel size so the panes are visible from startup.
        sw = ttk.LabelFrame(left, text="Switch preview"); sw.pack(fill="x")
        thumb_box = tk.Frame(sw, width=320, height=180, background="#111")
        thumb_box.pack(padx=4, pady=4); thumb_box.pack_propagate(False)
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
        pp = ttk.LabelFrame(left, text="Processing preview"); pp.pack(fill="x")
        ppbox = tk.Frame(pp, width=PP_W, height=PP_H, background=PP_BG_HEX)
        ppbox.pack(padx=4, pady=4); ppbox.pack_propagate(False)
        self.pthumb = tk.Label(ppbox, background=PP_BG_HEX)
        self.pthumb.pack(fill="both", expand=True)
        self.pcap = ttk.Label(pp, text="—", font=("Consolas", 8))
        self.pcap.pack(anchor="w", padx=4, pady=(0, 4))

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
            prog = parse_matte_progress(text)
            if prog:
                self._proc_seg, self._proc_frac = prog

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

    def _sweep_exited(self, code=None):
        self.q.put(lambda: self._after_sweep_exit())

    def _after_sweep_exit(self):
        for act in self.state.on_event(cs.SWEEP_EXITED):
            self._do(act)
        self._refresh_buttons()

    # ── asset processing (independent of the rig) ────────────────────────────────
    def _click_process(self, key):
        if key == "pstart" and self.pstate.state == ps.IDLE:
            self.pprogress = ProgressModel(self.sup.clip_count())   # fresh run: reset ETA samples
            self._proc_wedged = 0                                   # fresh run: reset the restart guard
        event = {"pstart": ps.START, "pstop": ps.STOP}.get(key)
        if key == "ppause":
            event = ps.RESUME if self.pstate.state == ps.PAUSED else ps.PAUSE
        for act in self.pstate.on_event(event):
            self._do_process(act)
        self._refresh_buttons()

    def _do_process(self, action):
        if action == "start_processing":
            self.sup.start_processing(CLIPS_DIR, PROCESS_OUT, PROCESS_STOP,
                                      on_exit=self._process_exited,
                                      claims_dir=CLAIMS_DIR, ship_dir=SHIP_DIR)
        elif action == "request_process_stop":
            self.sup.request_stop_file(PROCESS_STOP)
        elif action == "completed":
            self._on_line("process", "[console] all clips processed")

    def _process_exited(self, code=None):
        self.q.put(lambda: self._after_process_exit(code))

    def _after_process_exit(self, code=None):
        # A birefnet CUDA-context loss the driver couldn't rebuild in-process makes process_all exit
        # 75/76 (see cuda_recovery). A fresh PROCESS = fresh context, so auto-restart instead of
        # reporting completion — unless the user requested stop/pause (then pstate isn't RUNNING and
        # classify returns NORMAL). GIVE_UP after too many no-progress losses (GPU likely wedged).
        decision, self._proc_wedged = cuda_recovery.classify_process_exit(
            code, running=self.pstate.state == ps.RUNNING, wedged=self._proc_wedged)
        if decision == cuda_recovery.RESTART:
            self._on_line("process", f"[console] CUDA context lost (exit {code}) — resuming with a "
                                     "fresh process in 20s (manifest keeps the finished clips)...")
            self.root.after(20000, self._restart_processing)
            return
        if decision == cuda_recovery.GIVE_UP:
            self._on_line("process", "[console] CUDA context lost repeatedly with no progress — the "
                                     "GPU driver is likely wedged. Reboot, then press Process again.")
            self.pstate.on_event(ps.EXITED)          # RUNNING -> IDLE; drop 'completed' (this failed)
            self._refresh_buttons()
            return
        for act in self.pstate.on_event(ps.EXITED):
            self._do_process(act)
        if not (CLAIMS_DIR or SHIP_DIR):             # single-machine: keep the convenient auto-build.
            msg = self.sup.build_viewer(os.path.join(PROCESS_OUT, "matte"))   # multi-machine is
            if msg:                                  # unreliable on exit (the other box may not have
                self._on_line("process", f"[console] {msg}")   # published yet) -> use Build viewer.
        self._refresh_buttons()

    def _restart_processing(self):
        """Relaunch the batch after a CUDA-context-loss exit — a fresh process gets a fresh CUDA
        context. If the user pressed Stop/Pause during the 20s settle, the process already exited, so
        settle the state machine (the EXITED it awaits won't come from a dead process) instead."""
        if self.pstate.state == ps.RUNNING:
            self._do_process("start_processing")     # manifest resumes; the clip that died stays pending
        else:
            for act in self.pstate.on_event(ps.EXITED):
                self._do_process(act)
        self._refresh_buttons()

    def _click_build_viewer(self):
        """Rebuild the chip viewer/index over the final matte dir, off the Tk thread. Always
        available (read-only over the matte); a click while one is running is skipped, not doubled."""
        if getattr(self, "_building_viewer", False):
            self._on_line("process", "[console] viewer build already running")
            return
        self._building_viewer = True
        matte = os.path.join(SHIP_DIR or PROCESS_OUT, "matte")
        self._on_line("process", f"[console] building viewer over {matte} ...")
        threading.Thread(target=self._build_viewer_worker, args=(matte,), daemon=True).start()

    def _build_viewer_worker(self, matte):
        msg = self.sup.build_viewer(matte)           # unions every manifest*.json (make_viewer)

        def done():
            self._building_viewer = False
            self._on_line("process", f"[console] {msg or 'viewer build produced no output'}")
        self.q.put(done)

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

    def _update_proc_preview(self):
        """Show the newest matted frame of the segment currently being processed (1 Hz, running
        only). Best-effort: a missing dir or a partial mid-write PNG just skips this tick and the
        last frame stays up, so the preview never disrupts the UI or stalls processing."""
        if self.pstate.state != ps.RUNNING or not self._proc_seg:
            return
        try:
            from PIL import Image, ImageTk
            pngs = glob.glob(os.path.join(PROCESS_OUT, "matte", f"{self._proc_seg}_frames", "*.png"))
            if not pngs:
                return
            im = Image.open(max(pngs)).convert("RGBA")        # zero-padded names: max == newest frame
            im.thumbnail((PP_W, PP_H))
            bg = Image.new("RGB", (PP_W, PP_H), PP_BG)  # greenscreen bg: alpha holes/halos + edge fringe pop
            bg.paste(im, ((PP_W - im.width) // 2, (PP_H - im.height) // 2), im)
            self._pphoto = ImageTk.PhotoImage(bg)
            self.pthumb.configure(image=self._pphoto)
            self.pcap.configure(text=f"{' · '.join(self._proc_seg.split('__')[-2:])}   {self._proc_frac}")
        except Exception:
            pass

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
        self.pprogress.update(self.sup.process_done_count(PROCESS_MANIFEST, claims_dir=CLAIMS_DIR),
                              time.monotonic())
        pp = self.pprogress.snapshot()
        self.phl.configure(text=(
            f"processed {pp['done']}/{pp['total']}  {pp['pct'] * 100:4.1f}%   "
            f"ETA {_fmt_eta(pp['eta_seconds'])}   [{self.pstate.state.lower().replace('_', ' ')}]\n"
            f"{self._proc_last[:72]}"))
        self._update_proc_preview()
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
