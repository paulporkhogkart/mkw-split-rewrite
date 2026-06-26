"""High-quality clip recorder for the capture-card feed (built for 4K60).

ffmpeg captures the DirectShow device straight to a file using hardware encoding
(NVENC / QSV / AMF) when available, so 4K60 keeps up in real time at near-lossless
quality. Recording is deliberately high-quality; downscale/compress for the site
later. This runs in the normal Python env (ffmpeg only) - it is NOT the rembg
processing step, so nothing extra lands in the build Python.

Preview: a single ffmpeg owns the capture card and *tees* its output - it encodes
the 4K60 file AND emits a small downscaled raw stream that Python shows live. One
consumer of the card means no DirectShow sharing conflict (it works even with
Windows camera-sharing off), and those same piped frames are what the tracker will
later read to ground controller nav *while* a sweep records. Between clips a
preview-only ffmpeg streams the same downscaled feed so you can line up a shot.

Run:
    python -m mkw_tracker.tools.record_clips                       # saved device -> temp/clips/, 8s
    python -m mkw_tracker.tools.record_clips --out temp/clips --duration 8
    python -m mkw_tracker.tools.record_clips --no-preview          # headless / automated: file only
    python -m mkw_tracker.tools.record_clips --list-modes          # print the device's capture modes
    python -m mkw_tracker.tools.record_clips --encoder libx264 --quality 12

Per clip: type a name (optionally "name 12" for a 12s clip) + Enter -> 3-2-1 ->
records --duration seconds -> temp/clips/<name>.mkv. Type q to quit.

Capture with HDR OFF on the Switch for accurate colour (same as the asset session).
"""
import argparse
import collections
import os
import queue
import subprocess
import threading
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..utils.camera import _bundled_bin, list_dshow_video_devices

# Best hardware encoder first, software (real-time-capable preset) last.
# "{q}" is replaced by the quality value (qp/cq/crf depending on the encoder).
_ENC_PREF: List[Tuple[str, List[str]]] = [
    # p5 not p7: p6/p7 can't sustain 4K60 NVENC in real time (~0.8x -> dropped frames);
    # p5 runs ~2.3x real-time on modern NVENC with near-identical quality for source footage.
    ("hevc_nvenc", ["-preset", "p5", "-tune", "hq", "-rc", "constqp", "-qp", "{q}", "-pix_fmt", "yuv420p"]),
    ("h264_nvenc", ["-preset", "p5", "-tune", "hq", "-rc", "constqp", "-qp", "{q}", "-pix_fmt", "yuv420p"]),
    ("hevc_qsv",   ["-global_quality", "{q}", "-pix_fmt", "nv12"]),
    ("h264_qsv",   ["-global_quality", "{q}", "-pix_fmt", "nv12"]),
    ("hevc_amf",   ["-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-quality", "quality", "-pix_fmt", "yuv420p"]),
    ("h264_amf",   ["-rc", "cqp", "-qp_i", "{q}", "-qp_p", "{q}", "-quality", "quality", "-pix_fmt", "yuv420p"]),
    ("libx264",    ["-preset", "superfast", "-crf", "{q}", "-pix_fmt", "yuv420p"]),  # real-time-capable fallback
]

# Downscaled preview stream size (16:9). ffmpeg scales whatever the card delivers
# to exactly this, so the pipe frame size is constant regardless of capture res.
PREVIEW_W, PREVIEW_H = 960, 540
PREVIEW_FPS = 20


def encoder_names(encoders_text: str) -> set:
    """Parse `ffmpeg -encoders` output -> the set of available encoder names."""
    names = set()
    for line in encoders_text.splitlines():
        parts = line.split()
        # capability lines look like:  " V....D hevc_nvenc   NVIDIA NVENC hevc encoder"
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def pick_encoder(encoders_text: str, prefer: Optional[str] = None, quality: int = 14) -> Tuple[str, List[str]]:
    """Choose the best available encoder; substitute the quality value into its args."""
    have = encoder_names(encoders_text)
    order = _ENC_PREF
    if prefer:
        order = ([e for e in _ENC_PREF if e[0] == prefer] +
                 [e for e in _ENC_PREF if e[0] != prefer])
    for name, tmpl in order:
        if name in have:
            return name, [a.replace("{q}", str(quality)) for a in tmpl]
    return "libx264", ["-preset", "superfast", "-crf", str(quality), "-pix_fmt", "yuv420p"]


def build_record_cmd(ffmpeg: str, device: str, size: str, fps: int, duration: float,
                     out_path: str, enc_name: str, enc_args: List[str],
                     rtbufsize: str = "1024M", pixel_format: Optional[str] = None) -> List[str]:
    """Build the ffmpeg dshow capture-to-file command (records `duration` seconds)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats",
           "-f", "dshow", "-rtbufsize", rtbufsize, "-framerate", str(fps), "-video_size", size]
    if pixel_format:
        cmd += ["-pixel_format", pixel_format]
    cmd += ["-i", f"video={device}", "-t", str(duration), "-c:v", enc_name]
    cmd += enc_args
    cmd += ["-y", out_path]
    return cmd


def _parse_name_dur(raw: str, default_dur: float) -> Tuple[str, float]:
    """'mario_idle' -> (mario_idle, default). 'mario spin 12' -> (mario_spin, 12)."""
    parts = raw.split()
    if len(parts) >= 2 and parts[-1].replace(".", "", 1).isdigit():
        return "_".join(parts[:-1]), float(parts[-1])
    return "_".join(parts), default_dur


def _resolve_device(arg_device: Optional[str]) -> Optional[str]:
    if arg_device:
        return arg_device
    try:
        from ..config.settings import get_settings
        from ..database.migrations import apply_migrations
        apply_migrations()
        saved = get_settings().get("camera_device", "") or ""
    except Exception:
        saved = ""
    if saved:
        return saved
    devs = list_dshow_video_devices()
    return next((d for d in devs if "obs" not in d.lower()), devs[0] if devs else None)


# ── ffmpeg command builders ───────────────────────────────────────────────────

def _dshow_input(device: str, size: str, fps: int, rtbuf: str) -> List[str]:
    return ["-f", "dshow", "-rtbufsize", rtbuf, "-framerate", str(fps),
            "-video_size", size, "-i", f"video={device}"]


def preview_cmd(ffmpeg: str, device: str, size: str, fps: int) -> List[str]:
    """Preview-only: capture the card -> downscaled bgr24 raw frames on stdout."""
    return ([ffmpeg, "-hide_banner", "-loglevel", "error"]
            + _dshow_input(device, size, fps, "512M")
            + ["-an", "-vf", f"scale={PREVIEW_W}:{PREVIEW_H},fps={PREVIEW_FPS}",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])


def tee_cmd(ffmpeg: str, device: str, size: str, fps: int, duration: float,
            out_path: str, enc: str, enc_args: List[str],
            pixel_format: Optional[str] = None, rtbuf: str = "1024M") -> List[str]:
    """Record AND preview from one capture: split the input into the encoded 4K file
    plus a downscaled bgr24 raw stream on stdout (the live preview / future grounding feed)."""
    inp = _dshow_input(device, size, fps, rtbuf)
    if pixel_format:
        inp = ["-pixel_format", pixel_format] + inp
    return ([ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats"]
            + inp
            + ["-filter_complex",
               f"[0:v]split=2[rec][mon];[mon]scale={PREVIEW_W}:{PREVIEW_H},fps={PREVIEW_FPS}[mon2]",
               "-map", "[rec]", "-c:v", enc] + enc_args + ["-t", str(duration), "-y", out_path,
               "-map", "[mon2]", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-t", str(duration), "pipe:1"])


# ── ffmpeg raw-frame pipe reader ──────────────────────────────────────────────

class FramePipe:
    """Run an ffmpeg command that emits PREVIEW_W×PREVIEW_H bgr24 frames on stdout;
    a reader thread keeps only the latest frame. `latest()` returns it (or None)."""

    def __init__(self, cmd: List[str], w: int = PREVIEW_W, h: int = PREVIEW_H, quiet: bool = True):
        self.w, self.h = w, h
        self._n = w * h * 3
        self._buf = np.zeros((h, w, 3), np.uint8)
        self._have = False
        self._lock = threading.Lock()
        self._run = True
        self._errtail: "collections.deque" = collections.deque(maxlen=40)
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE,
            stderr=(subprocess.PIPE if quiet else None),   # capture quietly, or inherit (live stats)
            stdin=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        if quiet and self._proc.stderr is not None:
            threading.Thread(target=self._drain_err, daemon=True).start()

    def _drain_err(self):
        for line in iter(self._proc.stderr.readline, b""):
            self._errtail.append(line.decode(errors="replace").rstrip())

    def error_tail(self, n: int = 8) -> str:
        return "\n".join(s for s in list(self._errtail)[-n:] if s.strip())

    def has_frame(self) -> bool:
        return self._have

    def _reader(self):
        raw = self._proc.stdout
        n = self._n
        tmp = bytearray(n)
        mv = memoryview(tmp)
        while self._run:
            got = 0
            while got < n:
                r = raw.readinto(mv[got:])
                if not r:                 # EOF: ffmpeg exited
                    self._run = False
                    return
                got += r
            frame = np.frombuffer(tmp, np.uint8).reshape(self.h, self.w, 3)
            with self._lock:
                self._buf[:] = frame
                self._have = True

    def latest(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._buf.copy() if self._have else None

    def alive(self) -> bool:
        return self._proc.poll() is None

    def stop(self):
        self._run = False
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:
            pass


# ── Preview HUD ───────────────────────────────────────────────────────────────

def _put(img, text, org, color, scale=0.6, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _blank(w: int = PREVIEW_W, h: int = PREVIEW_H):
    return np.zeros((h, w, 3), np.uint8)


def _draw_hud(img, state: str, name: str, count: int, last_saved: str):
    h, w = img.shape[:2]
    if state == "recording":
        _put(img, f"REC  {name}", (14, 36), (60, 60, 235), 0.8, 2)
        cv2.circle(img, (w - 26, 28), 9, (60, 60, 235), -1)
    elif state == "countdown":
        _put(img, f"{count}...", (14, 42), (60, 200, 235), 1.1, 2)
    else:
        _put(img, "LIVE", (14, 36), (70, 220, 70), 0.8, 2)
    if last_saved:
        _put(img, last_saved, (14, h - 12), (215, 215, 215), 0.5, 1)
    _put(img, "type clip names in the console  .  q to quit", (14, h - 32), (170, 170, 170), 0.45, 1)


# ── Record loops ──────────────────────────────────────────────────────────────

_QUIT = object()


def _stdin_thread(q: "queue.Queue"):
    while True:
        try:
            line = input("clip > ")
        except EOFError:
            q.put(_QUIT)
            return
        q.put(line.strip())


def _preview_record_loop(args, ffmpeg, device, enc, enc_args):
    """Live preview + tee'd recording, single card consumer. Main-thread cv2 GUI;
    a worker thread feeds typed clip commands through a queue."""
    cmd_q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_stdin_thread, args=(cmd_q,), daemon=True).start()

    win = "MKW Record - preview"
    preview = FramePipe(preview_cmd(ffmpeg, device, args.size, args.fps), quiet=True)
    rec: Optional[FramePipe] = None
    state = "idle"            # idle | countdown | recording
    name, dur = "", 0.0
    count_until = rec_until = 0.0
    last_saved = ""
    quit_now = False
    warned = False
    retry_at = 0.0
    try:
        while not quit_now:
            now = time.time()

            if state == "idle":
                # Surface / recover a preview ffmpeg that failed to open the card.
                if preview.has_frame():
                    warned = False
                elif not preview.alive():
                    if not warned:
                        print("  [preview] ffmpeg could not open the card for the preview feed:")
                        for ln in (preview.error_tail() or "(no ffmpeg stderr)").splitlines():
                            print("      " + ln)
                        print("    -> If Windows camera sharing is ON, turn it OFF: its frame server")
                        print("       holds the Elgato so ffmpeg can't get exclusive dshow access.")
                        warned, retry_at = True, now + 3.0
                    elif now >= retry_at:
                        preview = FramePipe(preview_cmd(ffmpeg, device, args.size, args.fps), quiet=True)
                        retry_at = now + 3.0
                try:
                    cmd = cmd_q.get_nowait()
                except queue.Empty:
                    cmd = None
                if cmd is _QUIT:
                    break
                if isinstance(cmd, str):
                    if cmd.lower() in ("q", "quit", ""):
                        break
                    name, dur = _parse_name_dur(cmd, args.duration)
                    count_until = now + args.countdown
                    state = "countdown"
                    print(f"  '{name}' in {args.countdown}s, then {dur:g}s recording...")

            elif state == "countdown":
                if now >= count_until:
                    preview.stop()
                    time.sleep(0.3)                       # let the device free before the recorder opens it
                    out_path = os.path.join(args.out, name + ".mkv")
                    print(f"  > RECORDING {dur:g}s -> {out_path}")
                    rec = FramePipe(tee_cmd(ffmpeg, device, args.size, args.fps, dur, out_path,
                                            enc, enc_args, pixel_format=args.pixel_format,
                                            rtbuf=args.rtbufsize), quiet=False)
                    rec_until = time.time() + dur + 4.0   # backstop if ffmpeg overruns
                    state = "recording"

            elif state == "recording":
                if (rec is None) or (not rec.alive()) or now >= rec_until:
                    if rec:
                        rec.stop()
                    out_path = os.path.join(args.out, name + ".mkv")
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        last_saved = f"saved {name}.mkv ({os.path.getsize(out_path) / 1e6:.0f} MB)"
                        print(f"  {last_saved}")
                    else:
                        last_saved = f"{name}: FAILED (see console)"
                        print(f"  {last_saved}")
                        print("    [hint] If Windows camera sharing is ON, turn it OFF: its frame "
                              "server holds the Elgato so ffmpeg can't open it for capture.")
                    time.sleep(0.4)
                    preview = FramePipe(preview_cmd(ffmpeg, device, args.size, args.fps), quiet=True)
                    state = "idle"

            src = rec if state == "recording" else preview
            frame = src.latest() if src else None
            disp = frame.copy() if frame is not None else _blank()
            count = max(1, int(count_until - now) + 1) if state == "countdown" else 0
            _draw_hud(disp, state, name, count, last_saved)
            cv2.imshow(win, disp)
            if (cv2.waitKey(20) & 0xFF) == ord("q"):
                break
    finally:
        if rec:
            rec.stop()
        if preview:
            preview.stop()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass


def _record_loop(args, ffmpeg, device, enc, enc_args):
    """Headless record loop (no preview): pure ffmpeg dshow-to-file, sole card consumer."""
    while True:
        try:
            raw = input("clip > ").strip()
        except EOFError:
            break
        if raw.lower() in ("q", "quit", ""):
            break
        name, dur = _parse_name_dur(raw, args.duration)
        out_path = os.path.join(args.out, name + ".mkv")
        for i in range(args.countdown, 0, -1):
            print(f"  {i}...")
            time.sleep(1)
        print(f"  > RECORDING {dur:g}s -> {out_path}")
        cmd = build_record_cmd(ffmpeg, device, args.size, args.fps, dur, out_path,
                               enc, enc_args, rtbufsize=args.rtbufsize, pixel_format=args.pixel_format)
        t0 = time.time()
        subprocess.run(cmd)
        if os.path.exists(out_path):
            mb = os.path.getsize(out_path) / 1e6
            print(f"  saved {out_path}  ({mb:.0f} MB in {time.time() - t0:.1f}s)")
        else:
            print("  FAILED - check the ffmpeg output above (try --list-modes / --pixel-format).")


def run(args):
    ffmpeg = _bundled_bin("ffmpeg")
    device = _resolve_device(args.device)
    if not device:
        print("[record] no capture device found (pass --device NAME).")
        return
    if args.list_modes:
        subprocess.run([ffmpeg, "-hide_banner", "-f", "dshow",
                        "-list_options", "true", "-i", f"video={device}"])
        return
    try:
        enc_text = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                  capture_output=True, text=True).stdout
    except Exception as e:
        print(f"[record] could not query ffmpeg encoders: {e}")
        return
    enc, enc_args = pick_encoder(enc_text, args.encoder, args.quality)
    os.makedirs(args.out, exist_ok=True)

    print(f"[record] device={device!r}  {args.size}@{args.fps}  encoder={enc}  q={args.quality}  out={args.out!r}")
    if enc == "libx264":
        print("[record] NOTE: no hardware encoder found - x264 'superfast' may drop frames at 4K60. "
              "Watch the 'fps=' in the stats; if it dips below ~60, lower --size or --fps.")
    print("[record] type a clip name (e.g. 'mario_idle', or 'mario_spin 12' for 12s); q to quit.")

    if args.preview:
        _preview_record_loop(args, ffmpeg, device, enc, enc_args)
    else:
        _record_loop(args, ffmpeg, device, enc, enc_args)


def main():
    p = argparse.ArgumentParser(description="Record high-quality 4K60 clips from the capture card via ffmpeg.")
    p.add_argument("--device", default=None, help="Capture device name (default: saved camera_device / first non-OBS).")
    p.add_argument("--out", default=os.path.join("temp", "clips"), help="Output dir (default temp/clips).")
    p.add_argument("--duration", type=float, default=8.0, help="Default clip length in seconds (default 8).")
    p.add_argument("--size", default="3840x2160", help="Capture resolution (default 3840x2160).")
    p.add_argument("--fps", type=int, default=60, help="Capture framerate (default 60).")
    p.add_argument("--encoder", default=None, help="Force an encoder (e.g. hevc_nvenc, libx264).")
    p.add_argument("--quality", type=int, default=14, help="qp/cq/crf value, lower=better (default 14, ~visually lossless).")
    p.add_argument("--countdown", type=int, default=3, help="Seconds counted down before each record (default 3).")
    p.add_argument("--rtbufsize", default="1024M", help="ffmpeg dshow real-time buffer (default 1024M for 4K60).")
    p.add_argument("--pixel-format", default=None, dest="pixel_format", help="Force dshow input pixel format (see --list-modes).")
    p.add_argument("--list-modes", action="store_true", dest="list_modes", help="Print the device's capture modes and exit.")
    p.add_argument("--preview", dest="preview", action="store_true", default=True,
                   help="Live preview window via a tee'd ffmpeg (default on; single card consumer).")
    p.add_argument("--no-preview", dest="preview", action="store_false",
                   help="Disable the preview window (headless / automated use; file only).")
    run(p.parse_args())


if __name__ == "__main__":
    main()
