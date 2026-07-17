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
CHAR_TEXT_DILATE = 7   # covers the AA ring + dark drop shadow around the yellow glyphs


def char_text_band(t_template, mask):
    """Rows the template glyphs occupy (+-8), full footprint x-span, clipped to the footprint.
    Geometry only — char_P's stale LEVELS are never used (spec: live-derived clean_bg_char)."""
    in_plate = mask > 0.05
    glyph = (t_template < T_OPAQUE) & in_plate
    ys = np.where(glyph.any(1))[0]
    xs = np.where(in_plate.any(0))[0]
    band = np.zeros_like(in_plate)
    band[max(0, ys.min() - 8):ys.max() + 9, xs.min():xs.max() + 1] = True
    return band & in_plate


def char_text_mask(median_bgr, text_band):
    """Per-clip live-text mask: HSV-yellow on the segment median, in-band, dilated.
    NOT the kart t<T_OPAQUE gate — that only works against a TINTED reference (kart A
    anti-correlates with yellow); vs the neutral live char bg it lands on solve_tc's
    ratio path and misses the text entirely (prototype-verified 2026-07-17)."""
    med = np.clip(np.asarray(median_bgr), 0, 255).astype(np.uint8)
    yellow = nc.yellow_text_mask(med) & text_band
    k = np.ones((CHAR_TEXT_DILATE, CHAR_TEXT_DILATE), np.uint8)
    return cv2.dilate(yellow.astype(np.uint8), k).astype(bool)


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


def predark_frame_count(n_frames, raw_tail):
    """How many frames from the segment start get predark; the trailing raw_tail pass raw
    (the departing/absent plate is the matte's job, like the kart flourish)."""
    return max(0, n_frames - max(0, raw_tail))


def main():
    a = sys.argv[1:]
    is_char = "--kart" not in a
    a = [x for x in a if x != "--kart"]
    process(a[0], a[1:], is_char)


if __name__ == "__main__":
    main()
