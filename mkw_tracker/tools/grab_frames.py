"""Quick full-frame screenshot grabber for the capture-card feed.

Opens the live OBS/capture feed in a window; press SPACE (or ENTER) to save the
current 1920x1080 frame as a PNG. Built to grab the SAME selection-grid page over
a few *different* backgrounds for the transparent-icon matting prototype:

    park on a grid page, then save 3-4 frames spread over several seconds so the
    animated menu background behind the semi-transparent panels differs between
    shots while the icons stay pixel-identical. The bigger the background
    difference between shots, the cleaner the triangulation matte.

For accurate icon colours, capture with **HDR off on the Switch** (same as the
asset session) — the matting maths works regardless, but the output colours are
whatever the feed delivers.

Run:
    python -m mkw_tracker.tools.grab_frames                 # saved device, -> temp/matte/
    python -m mkw_tracker.tools.grab_frames --out temp/matte/peach --prefix peach_
    python -m mkw_tracker.tools.grab_frames --device "Elgato 4K" --burst 4

Keys:  SPACE / ENTER  save one frame
       b              burst: save --burst frames spaced --burst-gap seconds apart
       Tab            toggle the HUD
       q / ESC        quit
"""
import argparse
import os
import time

import cv2

from ..utils.camera import build_camera_source
from .capture_sources import _resize_1080p, _encode_png


def next_index(out_dir: str, prefix: str) -> int:
    """Return the next free <prefix>NNN index so re-runs never overwrite earlier grabs."""
    highest = 0
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            low = fn.lower()
            if low.startswith(prefix.lower()) and low.endswith(".png"):
                stem = fn[len(prefix):-4]
                if stem.isdigit():
                    highest = max(highest, int(stem))
    return highest + 1


def _save(out_dir: str, prefix: str, idx: int, frame) -> str:
    """Write a copy of *frame* to out_dir/<prefix>NNN.png; return the path.

    The frame is copied before encoding because the camera source hands back a
    shared ring buffer that its reader thread will overwrite.
    """
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{prefix}{idx:03d}.png")
    data = _encode_png(frame.copy())
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _put(img, text, org, color=(60, 220, 60), scale=0.6, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def run(args):
    from ..config.settings import get_settings
    from ..database.migrations import apply_migrations

    apply_migrations()
    settings = get_settings()
    device = args.device if args.device is not None else (settings.get("camera_device", "") or None)
    out_dir = args.out
    prefix = args.prefix
    idx = next_index(out_dir, prefix)
    start_idx = idx

    print(f"[grab] device={device!r}  out={out_dir!r}  prefix={prefix!r}  next={prefix}{idx:03d}.png")
    print("[grab] SPACE/ENTER save  .  b burst  .  Tab HUD  .  q quit")

    try:
        cap = build_camera_source(device_name=device)
    except Exception as e:
        print(f"[grab] camera open failed: {e}")
        return

    win = "MKW Grab"
    show_hud = True
    flash, flash_until = "", 0.0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                if (cap.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                continue
            frame = _resize_1080p(frame)

            display = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
            t = time.perf_counter()
            if show_hud:
                if t < flash_until:
                    _put(display, flash, (16, 36), (60, 220, 60), 0.7, 2)
                _put(display, f"next: {prefix}{idx:03d}.png   .   saved {idx - start_idx}",
                     (16, 700), (235, 235, 235), 0.6)
                _put(display, "SPACE save  .  b burst  .  Tab HUD  .  q quit",
                     (16, 676), (170, 170, 170), 0.5)
            cv2.imshow(win, display)

            key = (cap.waitKey(1) if cap is not None else cv2.waitKey(1)) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key in (ord(" "), 13):           # SPACE / ENTER
                try:
                    path = _save(out_dir, prefix, idx, frame)
                    print(f"[grab] saved {path}")
                    flash, flash_until = f"SAVED {os.path.basename(path)}", t + 0.9
                    idx += 1
                except Exception as e:
                    print(f"[grab] SAVE FAILED: {type(e).__name__}: {e}")
                    flash, flash_until = "SAVE FAILED (see console)", t + 1.6
            elif key == ord("b"):                 # burst over time for background variation
                print(f"[grab] burst: {args.burst} frames, {args.burst_gap}s apart")
                for _ in range(args.burst):
                    ret2, f2 = cap.read()
                    if ret2 and f2 is not None:
                        try:
                            path = _save(out_dir, prefix, idx, _resize_1080p(f2))
                            print(f"[grab]   saved {path}")
                            idx += 1
                        except Exception as e:
                            print(f"[grab]   SAVE FAILED: {type(e).__name__}: {e}")
                    end = time.perf_counter() + args.burst_gap
                    while time.perf_counter() < end:    # keep draining newest frames during the gap
                        cap.read()
                flash, flash_until = f"BURST x{args.burst}", time.perf_counter() + 1.0
            elif key == 9:                        # Tab
                show_hud = not show_hud
    finally:
        try:
            cap.release()
        except Exception:
            pass
        cv2.destroyAllWindows()


def main():
    p = argparse.ArgumentParser(
        description="Grab full 1920x1080 screenshots from the capture feed (for the "
                    "transparent-icon matting prototype).")
    p.add_argument("--device", default=None,
                   help="Capture device name (default: saved camera_device, else auto-probe).")
    p.add_argument("--out", default=os.path.join("temp", "matte"),
                   help="Output directory (default: temp/matte).")
    p.add_argument("--prefix", default="page_",
                   help="Filename prefix; files are <prefix>NNN.png (default: page_).")
    p.add_argument("--burst", type=int, default=3,
                   help="Frames saved by the 'b' burst key (default 3).")
    p.add_argument("--burst-gap", type=float, default=1.0, dest="burst_gap",
                   help="Seconds between burst frames (default 1.0).")
    run(p.parse_args())


if __name__ == "__main__":
    main()
