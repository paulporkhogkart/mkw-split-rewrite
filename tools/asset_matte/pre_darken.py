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
    """Per-screen (t, C, A, mask) at the production crop, from the committed templates.
    A is the clean select-screen background behind the plate (needed to paint the footprint out)."""
    roi = nc.CHAR_ROI if is_char else nc.PLATE_ROI
    pre = "char" if is_char else "kart"
    P = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_P.png"), roi)).astype(np.float64)
    A = nc.prod_crop(nc.place_in_canvas(cv2.imread(f"{ASSETS}/{pre}_A.png"), roi)).astype(np.float64)
    t, C = nc.solve_tc(P, A)
    mask = nc.prod_crop(cv2.imread(f"{ASSETS}/nametag_{pre}_mask4k.png", cv2.IMREAD_GRAYSCALE)
                        .astype(np.float64) / 255.0)
    return t, C, A, mask


def pre_darken(raw_bgr, t, C, A, mask, KEY_THR=60, CSUB=0.75, TFLOOR=0.01, YELLOW_S=250, BRIGHT_V=255):
    """Paint the WHOLE plate footprint to the clean background A, then stamp back only the genuine
    overlapping-subject pixels (recovered from behind the semi-transparent serration). birefnet then
    sees continuous background across the plate (empty serration, yellow text, and bright badge all
    become A) and drops it; only the stamped subject pixels stay connected to the kart/character.

    This fixes the collision failure: when a kart/character overlaps the plate, the OLD strip-only
    recovery left the text as opaque yellow and the empty serration as a background-coloured band,
    both of which birefnet KEPT because they were now connected to the salient subject ("brings some
    letters in / chunks of the background in"). Painting them to A severs that connection.

    A pixel inside the footprint is treated as subject when its full-recovery colour S differs from
    the clean background A by >= KEY_THR in any channel AND it is not the yellow name text
    (HSV S > YELLOW_S) nor the bright/opaque badge (HSV V > BRIGHT_V, or t < T_OPAQUE). Classification
    is on the raw (darkened) appearance, so a subject behind the serration — darkened, desaturated —
    is kept while the saturated text / bright badge are dropped."""
    O = raw_bgr.astype(np.float64)
    hsv = cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2HSV)
    S = np.clip((O - CSUB * C[..., None]) / np.clip(t, TFLOOR, 1.6)[..., None], 0, 255)
    in_plate = mask > 0.05
    is_text = hsv[..., 1] > YELLOW_S
    is_badge = (hsv[..., 2] > BRIGHT_V) | (t < T_OPAQUE)
    subject = in_plate & (np.abs(S - A).max(2) >= KEY_THR) & ~is_text & ~is_badge
    out = O.copy()
    out[in_plate] = A[in_plate]      # erase the whole plate to clean background
    out[subject] = S[subject]        # restore the genuine overlapping subject, un-darkened
    return np.clip(out, 0, 255).astype(np.uint8)


def process(base, names, is_char):
    """Pre-darken every raw loopframe of each name -> <base>/loopframes/<name>_pre/."""
    t, C, A, mask = load_template(is_char)
    for name in names:
        src = f"{base}/loopframes/{name}"
        dst = f"{base}/loopframes/{name}_pre"; os.makedirs(dst, exist_ok=True)
        files = sorted(glob.glob(f"{src}/*.png"))
        for f in files:
            raw = cv2.imread(f)
            if raw is None:
                continue
            cv2.imwrite(f"{dst}/{os.path.basename(f)}", pre_darken(raw, t, C, A, mask))
        print(f"{name}: {len(files)} frames pre-darkened ({'char' if is_char else 'kart'}) -> {dst}", flush=True)


def main():
    a = sys.argv[1:]
    is_char = "--kart" not in a
    a = [x for x in a if x != "--kart"]
    process(a[0], a[1:], is_char)


if __name__ == "__main__":
    main()
