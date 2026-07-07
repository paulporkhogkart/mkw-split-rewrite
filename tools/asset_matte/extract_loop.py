"""Stage A: extract the most-seamless single idle loop from a hero clip, cropped to
the production region (988x1080), as a PNG sequence. Build python (needs cv2).

Loop-length rule (see memory `asset-kart-loop-period`):
- STANDALONE characters (`char__costume`): DETECT the per-character idle period
  (autocorr of the temporal residual; it varies — DK 1.67s, cow 1.17s, ...).
- KART COMBOS (`char__costume__kart`): FORCE a 2.0s loop. A combo can't be period-
  detected (the spinning wheel hijacks the grayscale autocorr; the static kart swamps
  the silhouette), and the in-kart *rider* idle is a different, longer animation than
  the standing idle — so we loop to the rider at a fixed 2.0s and let the wheel take
  its best-available phase. (Wheel ~1.8s, rider ~2.0s, independent -> a fully-seamless
  combined loop would be their LCM ~18s; not worth it. Bright wheels may pop slightly.)

Both paths locate the idle band by self-similarity (recurrence), then seam-search the
most-seamless start at the chosen period (position + velocity match) -- the loop closes
cleanly without ever localizing the rider (riders shift per character and per kart).
"""
import os
import sys

import cv2
import numpy as np

from mkw_tracker.tools.loop_probe import (
    autocorr_by_lag, find_period, frame_feature, scale_roi, HERO_ROI_1080,
)
from nametag_core import NAMEPLATE_HERO_ROI, OUT_H

CROP_H = OUT_H                          # 1080 — production crop height
KART_PERIOD_S = 2.0                     # forced rider-loop length for kart combos
CHAR_FEAT_ROI = HERO_ROI_1080           # (1075,30,1800,845) — standalone hero box
KART_FEAT_ROI = (1180, 175, 1800, 760)  # whole hero, name label/icon excluded
MAX_DECODE_S = 16.0                     # cap features to spawn+idle+flourish-start
_GAP = 10                               # close idle-band dips shorter than this (blinks)
_FADE_THR = 0.5                         # backdrop-corner luma deviation = the scene fade


def is_kart_combo(name: str) -> bool:
    """`char__costume__kart` (>=3 parts) is a kart combo; `char__costume` (2) is standalone."""
    return len(name.split("__")) >= 3


# ── idle band via self-similarity (period-free; from recurrence_detect.py) ─────
def _smooth(x, w=5):
    return np.convolve(x, np.ones(w) / w, mode="same")


def _runs(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j)); i = j + 1
        else:
            i += 1
    return out


def _close_gaps(mask, g):
    m = mask.copy(); n = len(m)
    for a, b in _runs(~m):
        if a > 0 and b < n - 1 and (b - a + 1) < g:
            m[a:b + 1] = True
    return m


def recurrence(F, gap=24):
    """Per-frame best self-similarity to a non-adjacent frame (the idle plateau ~1)."""
    R = F - F.mean(0, keepdims=True)
    n = np.linalg.norm(R, axis=1, keepdims=True); n[n == 0] = 1.0
    R /= n
    S = R @ R.T
    ii = np.arange(len(F))
    S[np.abs(ii[:, None] - ii[None, :]) <= gap] = -1.0
    return S.max(axis=1)


def idle_band(F):
    """Longest high-self-similarity run = the settled idle (spawn-in/flourish excluded)."""
    r = _smooth(recurrence(F))
    hi, lo = np.percentile(r, 80), np.percentile(r, 20)
    return max(_runs(_close_gaps(r > lo + 0.45 * (hi - lo), _GAP)), key=lambda a: a[1] - a[0])


def _decode_features(clip, roi):
    """One sequential decode -> ALL per-frame signals: grayscale features F (loop/burst),
    backdrop-corner luma bg (fade), and 24x24 swap-ROI colour C (spawn swap / char hard
    cut). Decode dominates segmentation cost (4K60 HEVC ~100fps software), so everything
    is collected in a single pass — the old separate _colour_feats pass doubled it.
    (GPU/NVDEC decode was measured SLOWER here: 46fps vs 102fps software on the 9800X3D —
    the 4K NV12 PCIe download + CPU colour conversion outweighs the decode offload.)"""
    cap = cv2.VideoCapture(clip)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {clip!r}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1, x2, y2, _ = _prod_crop_params(w, h)
    # Two backdrop corner patches at the top of the prod crop: static through the whole
    # spawn/idle/flourish, they only move when the scene fades out. Two corners so a
    # subject crossing ONE of them (flourish jump peak) can't read as a fade.
    ch, cw = int(0.12 * (y2 - y1)), int(0.22 * (x2 - x1))
    corners = ((y1, y1 + ch, x1, x1 + cw), (y1, y1 + ch, x2 - cw, x2))
    rx, ry = w / 1280.0, h / 720.0
    sx1, sy1, sx2, sy2 = (int(_SWAP_ROI_720[0] * rx), int(_SWAP_ROI_720[1] * ry),
                          int(_SWAP_ROI_720[2] * rx), int(_SWAP_ROI_720[3] * ry))
    cap_frames = int(MAX_DECODE_S * fps)
    feats, bg, col, idx = [], [], [], 0
    while idx < cap_frames:
        ok, fr = cap.read()
        if not ok:
            break
        feats.append(frame_feature(fr, roi, 48))
        bg.append([float(cv2.cvtColor(fr[cy1:cy2, cx1:cx2], cv2.COLOR_BGR2GRAY).mean())
                   for cy1, cy2, cx1, cx2 in corners])
        col.append(cv2.resize(fr[sy1:sy2, sx1:sx2], (24, 24), interpolation=cv2.INTER_AREA)
                   .astype(np.float32).reshape(-1))
        idx += 1
    cap.release()
    if not feats:
        raise RuntimeError(f"no frames decoded from {clip!r}")
    return fps, np.stack(feats), (w, h), np.array(bg), np.stack(col)


def find_loop(clip):
    """(start_frame, period_frames, fps, (w,h), is_kart) for the seamless idle loop.

    Importable by the batch driver so it can stamp the period/start without re-decoding.
    """
    best_s, P, fps, wh, kart, _a, _b, _F, _bg, _C = _loop_impl(clip)
    return best_s, P, fps, wh, kart


def seam_start(F, a, b, P):
    """Most-seamless loop start within the FIRST idle cycle: frame s ~ s+P in position AND
    velocity. The idle is periodic, so every seam phase already exists in [a, a+P]; keeping
    the start in the first cycle lets the spawn segment run straight into it (a start deep
    in the band would make spawn->idle playback jump frames mid-bob)."""
    best_s, best_d = a, 1e18
    for s in range(a, max(a + 1, min(a + P + 1, b - P))):
        d = float(np.sum((F[s] - F[s + P]) ** 2) + np.sum((F[s + 1] - F[s + P + 1]) ** 2))
        if d < best_d:
            best_d, best_s = d, s
    return best_s


def first_sustained_dip(r, a, b, drop=0.05, min_run=16):
    """(start, end) of the first at/after-band run where the recurrence r stays below the
    idle plateau for >= min_run frames = the character's one-shot flourish. Recurrence is
    magnitude-free (unit-vector cosine), so a tiny character's jump registers exactly like
    a big one — unlike a motion-burst threshold, which baby-size characters never clear
    (their scan then ran on to the SWAP to the next captured item). A blink is a <10f dip
    by the band's own gap-close rule, so min_run=16 skips it. The run covers windup ->
    jump -> landing (it ends where recurring idle resumes); half-open end. None when no
    dip is found in the window."""
    plateau = float(np.median(r[a:b]))
    lo = r < (plateau - drop)
    k = b
    n = len(r)
    while k < n:
        if lo[k]:
            j = k
            while j + 1 < n and lo[j + 1]:
                j += 1
            if (j - k + 1) >= min_run:
                return k, j + 1
            k = j + 1
        else:
            k += 1
    return None


def burst_flourish_start(jump, b, thr, limit):
    """First frame in [b, limit) whose ||dF|| clears thr = the flourish motion burst;
    band edge (b + 1) when none does. `limit` (the scene fade / feature-window end)
    bounds the scan: a kart whose wheels spin AT IDLE (bowser_bruiser's jagged tyres)
    inflates the idle median so the real spin never reaches 4x it, and an unbounded
    scan then ran past the whole flourish onto the fade/map screen (the flourish span
    collapsed to 1 frame). The kart idle band ends AT the flourish (recurrence breaks
    when the spin starts), so the band edge is the correct start when no burst clears."""
    fs = b
    while fs < limit and jump[fs] <= thr:
        fs += 1
    return fs if fs < limit else b + 1


# The spin ONSET is a step: across the 164-clip survey (baby_daisy/bowser/waluigi/
# fish_bone x the kart roster) the idle ||dF|| never exceeded 506 (bowser_bruiser's
# spinning tyres) and big-rider idle blips reach ~250 (bowser head-turns), while the
# weakest real spin onset is 994. The floor/cap bracket sits in that empty band.
KART_BURST_CAP = 750.0
KART_BURST_FLOOR = 600.0
# The sweep recorder is deterministic: the scene fade starts exactly this many frames
# after the 62f kart flourish ends (27 on 117/120 survey clips; one 26 = fade-gate
# jitter of a single frame).
KART_FADE_GAP = 27


def burst_threshold(idle_med):
    """Burst gate for the flourish scan: 4x the idle median, capped at KART_BURST_CAP.
    Uncapped, a spinning-idle kart's 4x (~1820) lands INSIDE the flourish's motion range,
    so the scan fires mid-spin (sailor bowser_bruiser started 13f into the rotation,
    missing the windup) or never (base variant collapsed to the fallback)."""
    return min(4.0 * idle_med, KART_BURST_CAP)


def kart_burst_threshold(idle_med):
    """Kart burst gate: burst_threshold with a floor. On a calm kart (idle median ~55)
    4x sits near 220, which bowser's in-kart idle blips (~250) cleared — three clips
    exported trailing idle as the flourish. The floor rejects rider blips; real spin
    onsets (>= 994 surveyed) clear it everywhere."""
    return max(burst_threshold(idle_med), KART_BURST_FLOOR)


def fade_anchored_start(fade, n, band_b):
    """Kart flourish start anchored BACKWARDS from the scene fade: the recorder timing
    is deterministic, so the flourish occupies the fixed [fade - KART_FADE_GAP -
    KART_FLOURISH, fade - KART_FADE_GAP) window. Threshold-free, so it is immune to
    spinning-idle karts and big-rider idle blips. Returns None when the fade is not
    inside the decoded window (fade == n; e.g. an unusually late flourish pushes it
    past the decode cap) or when the anchored start lands at/before the idle-band end
    (a spuriously early fade) — the caller then falls back to the burst scan."""
    if fade >= n:
        return None
    fs = fade - KART_FADE_GAP - KART_FLOURISH
    return fs if fs > band_b else None


def fade_start(bg, a, b, thr=_FADE_THR):
    """First frame at/after the idle-band end where BOTH backdrop corners deviate from
    their idle baseline = the scene fade-out (a subject crossing one corner is not a fade).
    Returns len(bg) when no fade is inside the decoded window."""
    base = np.median(bg[a:b], axis=0)
    dev = np.abs(bg - base[None, :]).min(axis=1)       # min over corners = both must move
    for k in range(b, len(bg)):
        if dev[k] > thr:
            return k
    return len(bg)


def clamp_flourish_end(fe, fade, n, fs):
    """Clamp the flourish end before the scene fade-out. `fade` = fade_start's detection, whose
    0.5-luma corner gate fires ~_FADE_GUARD frames after the (brighter) subject visibly dims, so
    a detected fade is backed off by the guard. fade == n means no fade inside the decoded
    window — never trim against the window edge. Always keeps at least one frame past fs."""
    if fade >= n:
        return fe
    return min(fe, max(fade - _FADE_GUARD, fs + 1))


def _loop_impl(clip):
    """find_loop internals, also returning the idle band (a, b), the decoded features F
    (needed by find_segments to place spawn relative to where idling actually starts),
    the backdrop-corner luma track bg (fade clamp) and the swap-ROI colour feats C
    (spawn swap / char hard cut) — all from the single decode."""
    name = os.path.splitext(os.path.basename(clip))[0]
    kart = is_kart_combo(name)
    fps, F, wh, bg, C = _decode_features(clip, KART_FEAT_ROI if kart else CHAR_FEAT_ROI)
    a, b = idle_band(F)
    if kart:
        P = int(round(KART_PERIOD_S * fps))            # baked rule: forced 2.0s
    else:
        lo, hi = int(0.5 * fps), min(int(15 * fps), (b - a) - 2)
        lags, scores = autocorr_by_lag(F[a:b + 1], lo, hi)
        P, _conf, _ = find_period(lags, scores)
    P = int(max(2, min(P, (b - a) - 2)))               # keep one full cycle inside the band
    return seam_start(F, a, b, P), P, fps, wh, kart, a, b, F, bg, C


def extract(clip, outdir):
    """Write the P seamless prod-crop (988x1080) loop frames to `outdir`/NNN.png."""
    start, P, fps, (w, h), kart = find_loop(clip)
    os.makedirs(outdir, exist_ok=True)
    x1, y1, x2, y2 = scale_roi(NAMEPLATE_HERO_ROI, w, h)     # production crop region
    scale = CROP_H / (y2 - y1)
    out_w = int(round((x2 - x1) * scale))
    cap = cv2.VideoCapture(clip)
    idx = saved = 0
    while saved < P:
        ok, frame = cap.read()
        if not ok:
            break
        if idx >= start:
            crop = cv2.resize(frame[y1:y2, x1:x2], (out_w, CROP_H), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(outdir, f"{saved:03d}.png"), crop)
            saved += 1
        idx += 1
    cap.release()
    kind = "kart 2.0s(forced)" if kart else "char(detected)"
    print(f"{os.path.basename(clip)}: {kind} period={P}f({P / fps:.2f}s) start={start} "
          f"saved={saved} crop={out_w}x{CROP_H} @ {w}x{h}")
    return P, start


# ── full spawn / idle / flourish segmentation ───────────────────────────────────
# The recorded events.json flourish timing is UNRELIABLE on dark captures (it lands after
# the flourish, on empty frames / the map screen), so derive spans from prod-crop ACTIVITY:
# idle = the seamless loop (find_loop); flourish_start = the first motion spike after it (the
# rider rears up); flourish_end = the next variance drop (subject leaves frame, before the map);
# spawn = the subject-present window back from idle (capped). Robust without events.json.

def _prod_crop_params(w, h):
    x1, y1, x2, y2 = scale_roi(NAMEPLATE_HERO_ROI, w, h)
    scale = CROP_H / (y2 - y1)
    return x1, y1, x2, y2, int(round((x2 - x1) * scale))


# Validated segmentation (temp/asset_eyetest/detection_scripts/recurrence_detect.py + memory
# `asset-clip-segmentation`, checked against the user's hand-marks): idle band by grayscale
# recurrence (already in _loop_impl); the SPAWN swap is a COLOUR spike -- grayscale can't tell two
# karts apart -- and the flourish is the fixed in-game anim length after the band (bg-independent).
_SWAP_ROI_720 = (760, 90, 1230, 560)     # generous hero box @720p; scaled to the clip resolution
# The in-game kart flourish is a 64f anim, but the sweep recording's fade-out owns its last 2
# frames (measured: f62/f63 subject dimming on all 4 dirval karts, recorder timing deterministic).
KART_FLOURISH = 62
# Char flourish FALLBACK length (also the burst-branch char window): the smallest real
# motion-start->cut window on the 153-clip roster survey (mario: 56) minus the 2f guard —
# safe for any character when the hard cut can't be found.
CHAR_FLOURISH_LEN = 54
# The char flourish ends with a single-frame HARD CUT to the char-in-kart (the next
# captured item). The incoming item's nameplate text blends in ~2f before the subject
# cut, so the export stops CHAR_CUT_GUARD frames short of it.
CHAR_CUT_GUARD = 2
# Cut-detector gates (9-clip probe: real cuts 984-3153 preceded by 4f stillness <= 63;
# character motion spikes always have moving neighbours; hold blips/blinks stay small).
_CUT_SPIKE_FLOOR = 500.0    # absolute: half the weakest real cut
_CUT_STILL_CEIL = 100.0     # absolute stillness allowance during the held pose
# fade_start's 0.5-luma gate sits on the DARK backdrop corners, which dim less per frame than the
# bright subject — it fires ~2 frames after the fade is visible on the kart. Back the clamp off.
_FADE_GUARD = 2


def char_cut(d, ds, idle_med, still_n=4):
    """Index of the HARD CUT that ends a character flourish, scanning the colour-diff
    track d from just after the motion onset ds; None when no cut is found.

    The cut is the standing character being replaced by the char-in-kart in ONE frame:
    a huge colour change (relative to idle AND above an absolute floor) whose preceding
    still_n frames are near-still (the held pose). The character's own motion — however
    dramatic (mario's jump, conkdor's head-slam, swoop's flip) — always has moving
    neighbours, so it can't pass the stillness gate; blinks/nameplate shimmer during the
    hold pass the stillness gate but sit far below the spike floor."""
    spike = max(6.0 * (idle_med + 1.0), _CUT_SPIKE_FLOOR)
    still = max(3.0 * (idle_med + 1.0), _CUT_STILL_CEIL)
    for t in range(ds + still_n, len(d)):
        if d[t] > spike and float(np.max(d[t - still_n:t])) < still:
            return t
    return None


def find_segments(clip):
    """{seg: (start, end)} half-open spans for 'spawn'/'idle'/'flourish' (+ fps, kart, fell_back).

    fell_back=True flags a flourish end that used a fallback: a char whose hard cut wasn't
    found (fixed 54f window used), or a kart burst scan that never cleared its threshold and
    took the band edge — surfaced in the sweep log so those items get a spot eyetest.

    idle  = the seam-searched loop [start, start+P], with start inside the band's FIRST cycle.
    spawn = KARTS ONLY. The swap = the biggest consecutive COLOUR change in the ~45f before idle
            settles (grayscale can't separate two karts); spawn_start = the post-spike frame = the
            new kart's first frame. Standalone characters have no spawn-in (just a swap, no drop-in
            animation) -> no spawn segment. The spawn runs through the settle up to the idle-loop
            start, so spawn's last frame is the source frame right before idle's first: the
            spawn->idle handoff plays frame-contiguously.
    flourish = a fixed-length window (kart 62f / char 54f). KART: anchored backwards from the
            deterministic recorder fade (fade - KART_FADE_GAP - KART_FLOURISH); the burst scan
            + band-edge fallback covers fade-outside-window clips, clamped to the fade. CHAR:
            starts at the recurrence-dip motion onset and runs CHAR_FLOURISH_LEN flat (motion +
            held pose; the roster-surveyed window always fits).
    Ports the method validated against hand-marks (memory `asset-clip-segmentation`) +
    the 2026-07-07 164-kart-clip / 153-char-clip surveys."""
    start, P, fps, wh, kart, a, b, F, bg, C = _loop_impl(clip)

    # ── spawn-in (KARTS only): colour-spike swap in the ~45f before idle settles ──────────────
    spawn = None
    if kart:
        w0 = max(1, a - 45)
        if len(C) > a and a - w0 >= 1:
            diffs = np.array([float(np.linalg.norm(C[t] - C[t - 1])) for t in range(w0, a)])
            best = w0 + int(np.argmax(diffs))
        else:
            best = a - 16
        sp = min(max(best + 1, a - 45), a - 4)
        if a - sp >= 4:
            spawn = (sp, start)                        # run through the settle to the loop start

    # ── flourish span after the idle band: kart = fade-anchor, char = dip start -> hard cut ───
    # CHAR: burst magnitude scales with body size (a baby character never clears a burst
    # threshold), so the START is the first SUSTAINED low-recurrence run — size-free, and
    # constant on the roster (motion onset f619-625 on all 153 surveyed clips). The END is
    # the HARD CUT to the char-in-kart (the next captured item), minus CHAR_CUT_GUARD:
    # flourish length is per-character (mario 54f ... king_boo 92f), each anim playing out
    # to its own recorder-deterministic cut. char_cut's stillness gate is what the old
    # argmax swap clamp lacked — the char's own motion (mario's jump at +8f, conkdor's
    # head-slam at +16f, swoop's flip at +24f) false-fired it (baby_daisy__pro_racer was
    # cut to 23f). No fade clamp for chars: a jump can cover BOTH backdrop corners (false
    # fades as early as onset+20) while a real fade always lands past the cut.
    fell_back = False
    dip = None if kart else first_sustained_dip(_smooth(recurrence(F)), a, b)
    if dip is not None:
        fs = dip[0]
        d = np.concatenate([[0.0], np.linalg.norm(np.diff(C, axis=0), axis=1)])
        cut = char_cut(d, fs, float(np.median(d[a:b])) if b > a else 0.0)
        if cut is not None and cut - CHAR_CUT_GUARD - fs >= 16:
            fe = cut - CHAR_CUT_GUARD
        else:
            fe = fs + CHAR_FLOURISH_LEN   # roster-safe fixed fallback
            fell_back = True
    else:
        # KART primary: anchor backwards from the deterministic recorder fade (threshold-
        # free). Burst scan only when the fade is outside the decoded window.
        fade = fade_start(bg, a, b)
        fs = fade_anchored_start(fade, len(bg), b) if kart else None
        if fs is None:
            jump = np.concatenate([[0.0], np.linalg.norm(np.diff(F, axis=0), axis=1)])
            idle_jump = float(np.median(jump[a:b])) if b > a else 0.0
            thr = kart_burst_threshold(idle_jump) if kart else burst_threshold(idle_jump)
            fs = burst_flourish_start(jump, b, thr, min(len(jump) - 1, fade))
            fell_back = bool(jump[fs] <= thr)   # frame never cleared -> band-edge fallback
        fe = fs + (KART_FLOURISH if kart else CHAR_FLOURISH_LEN)
        fe = clamp_flourish_end(fe, fade, len(bg), fs)   # never include fade-out frames

    segs = {"idle": (start, start + P), "flourish": (fs, fe)}
    if spawn:
        segs["spawn"] = spawn
    return segs, fps, kart, fell_back


def _fresh_dir(d):
    """Recreate d empty. Segment dirs are numbered from 000, so re-extracting a SHORTER span
    over a previous run would otherwise leave the old tail frames behind (stale 062/063 put the
    recorder fade back into a 62f flourish) — downstream globs *.png and can't tell."""
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)


def extract_segments(clip, out_base, name):
    """Write prod-crop PNG sequences for each detected segment to <out_base>/<name>__<seg>/NNN.png.
    Returns {seg: frame_count}."""
    segs, fps, kart, fell_back = find_segments(clip)
    want = {}
    for seg, (s, e) in segs.items():
        for f in range(s, e):
            want.setdefault(f, []).append((seg, f - s))
    dirs = {seg: os.path.join(out_base, f"{name}__{seg}") for seg in segs}
    for d in dirs.values():
        _fresh_dir(d)
    cap = cv2.VideoCapture(clip)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x1, y1, x2, y2, out_w = _prod_crop_params(w, h)
    idx, last_wanted = 0, max(want)
    while idx <= last_wanted:                # segments end well before EOF — stop there
        ok, fr = cap.read()
        if not ok:
            break
        if idx in want:
            crop = cv2.resize(fr[y1:y2, x1:x2], (out_w, CROP_H), interpolation=cv2.INTER_AREA)
            for seg, li in want[idx]:
                cv2.imwrite(os.path.join(dirs[seg], f"{li:03d}.png"), crop)
        idx += 1
    cap.release()
    print(f"{os.path.basename(clip)}: " +
          " ".join(f"{nm}={e0 - s0}f" for nm, (s0, e0) in segs.items()) +
          (" flourish=FALLBACK" if fell_back else ""), flush=True)
    return {seg: (e - s) for seg, (s, e) in segs.items()}


if __name__ == "__main__":
    base = sys.argv[1]
    for clip in sys.argv[2:]:
        nm = os.path.splitext(os.path.basename(clip))[0]
        extract(clip, os.path.join(base, "loopframes", nm))
