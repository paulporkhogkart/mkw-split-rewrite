"""Pre-matte nametag removal: fully un-darken the plate's serration in the RAW frame so birefnet
detaches the plate itself (the serration-over-bg becomes true background -> dropped; the
serration-over-character becomes the character -> kept; the yellow text + bright badge are left as
opaque UI -> dropped). One path for characters and karts. See the 2026-06-28 pre-matte design spec.

Pre-darken is pure cv2/numpy (build python). Run the matte separately (matte_loop.py, GPU venv)."""
import glob, os, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # flat `import nametag_core`
import nametag_core as nc

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
T_OPAQUE = 0.20    # transmission below this = opaque plate content (text/badge) -> left as UI


def load_template(is_char):
    """Per-screen (t, C, mask) at the production crop, from the committed templates."""
    roi = nc.CHAR_ROI if is_char else nc.PLATE_ROI
    pre = "char" if is_char else "kart"
    P = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_P.png"), roi)).astype(np.float64)
    A = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_A.png"), roi)).astype(np.float64)
    t, C = nc.solve_tc(P, A)
    mask = nc.prod_crop(cv2.imread(f"{ASSETS}/nametag_{pre}_mask4k.png", cv2.IMREAD_GRAYSCALE)
                        .astype(np.float64) / 255.0)
    return t, C, mask


def pre_darken(raw_bgr, t, C, mask, CSUB=1.0, TFLOOR=0.05, YELLOW_S=60, BRIGHT_V=200):
    """Un-darken ONLY the semi-transparent serration (full recovery); leave text/badge as UI."""
    O = raw_bgr.astype(np.float64)
    hsv = cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2HSV)
    serr = ((mask > 0.05) & (t >= T_OPAQUE)
            & ~(hsv[..., 1] > YELLOW_S) & ~(hsv[..., 2] > BRIGHT_V)).astype(np.float64)
    S = np.clip((O - CSUB * C[..., None]) / np.clip(t, TFLOOR, 1.6)[..., None], 0, 255)
    out = np.clip(O * (1 - serr[..., None]) + S * serr[..., None], 0, 255)
    return out.astype(np.uint8)
