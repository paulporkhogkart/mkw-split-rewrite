"""Generate NO_SIGNAL preset templates, the edit-graph screenshot, and test
fixtures from the two reference captures in temp/.

Outputs (all 1080p space):
  images/nosignal/nosignal_elgato.png       grayscale crop at the Elgato preset ROI
  images/nosignal/nosignal_ugreen.png       grayscale crop at the UGREEN preset ROI
  images/nosignal/nosignal_obs.png          grayscale crop at the OBS preset ROI
  screenshots/en_uk/nosignal.png            full Elgato frame (edit-graph node)
  tests/fixtures/nosignal_elgato_frame.png  full Elgato frame
  tests/fixtures/nosignal_ugreen_frame.png  full UGREEN frame (downscaled 1440->1080)
  tests/fixtures/nosignal_obs_frame.png     full OBS frame

The crop ROIs are read from NO_SIGNAL_PRESETS (single source of truth).  Each
crop is asserted to contain bright text; if the gate fails, adjust the ROI in
detection/screen.py and re-run.
"""
import os
import sys
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mkw_tracker.detection.screen import NO_SIGNAL_PRESETS   # noqa: E402

REFS = {
    "elgato": os.path.join(ROOT, "temp", "nosignal.png"),
    "ugreen": os.path.join(ROOT, "temp", "nosignal2.png"),
    "obs": os.path.join(ROOT, "temp", "obs_no_sig.png"),
}


def _load_1080p(path):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"missing reference: {path}")
    h, w = img.shape[:2]
    if (w, h) != (1920, 1080):
        img = cv2.resize(img, (1920, 1080), interpolation=cv2.INTER_AREA)
    return img


def main():
    os.makedirs(os.path.join(ROOT, "images", "nosignal"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "screenshots", "en_uk"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "tests", "fixtures"), exist_ok=True)

    for preset, ref in REFS.items():
        frame = _load_1080p(ref)
        cv2.imwrite(os.path.join(ROOT, "tests", "fixtures",
                                 f"nosignal_{preset}_frame.png"), frame)
        x1, y1, x2, y2 = NO_SIGNAL_PRESETS[preset]["roi"]
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        bright = int((gray >= 180).sum())
        if bright < 200:
            raise SystemExit(
                f"{preset}: ROI {(x1, y1, x2, y2)} has only {bright} bright px - "
                f"adjust NO_SIGNAL_PRESETS['{preset}']['roi'] in detection/screen.py")
        out = os.path.join(ROOT, NO_SIGNAL_PRESETS[preset]["image_path"])
        cv2.imwrite(out, gray)
        print(f"{preset}: {out}  {gray.shape[1]}x{gray.shape[0]}  ({bright} bright px)")

    cv2.imwrite(os.path.join(ROOT, "screenshots", "en_uk", "nosignal.png"),
                _load_1080p(REFS["elgato"]))
    print("done")


if __name__ == "__main__":
    main()
