"""Segmentation invariants for extract_loop: the spawn->idle handoff must be
frame-contiguous, and the flourish must end before the scene fade-out."""
import numpy as np
import extract_loop as el        # FLAT import — conftest adds tools/asset_matte to sys.path


def _periodic_features(n=500, P=50, dims=8, seed=7):
    """Synthetic feature track: pure idle cycle with a little noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)[:, None]
    phase = 2 * np.pi * t / P + np.linspace(0, np.pi, dims)[None, :]
    return (np.sin(phase) + 0.01 * rng.standard_normal((n, dims))).astype(np.float32)


def test_seam_start_stays_in_first_idle_cycle():
    # The loop must start where the spawn segment can hand off to it: within the
    # FIRST cycle after the idle-band start (any seam phase already exists there).
    F = _periodic_features()
    a, b, P = 40, 460, 50
    s = el.seam_start(F, a, b, P)
    assert a <= s <= a + P


def test_seam_start_degenerate_band_returns_band_start():
    F = _periodic_features(n=100)
    a, b, P = 40, 52, 10          # barely one cycle in the band
    s = el.seam_start(F, a, b, P)
    assert a <= s <= b - P


def test_fade_start_detects_backdrop_change_after_band():
    # Both backdrop corners static through the band, then the scene fades.
    n, a, b, fade = 300, 30, 220, 262
    bg = np.full((n, 2), 100.0)
    ramp = np.arange(n - fade)
    bg[fade:, 0] -= 0.4 * ramp    # gradual fade in both corners
    bg[fade:, 1] -= 0.4 * ramp
    assert el.fade_start(bg, a, b) == fade + 2   # 0.4/frame crosses the 0.5 gate on frame 2


def test_fade_start_ignores_single_corner_occlusion():
    # The flourish jump can put the subject over ONE corner; that is not a fade.
    n, a, b = 300, 30, 220
    bg = np.full((n, 2), 100.0)
    bg[230:250, 0] += 30.0        # subject crosses the left corner only
    assert el.fade_start(bg, a, b) == n


def test_fade_start_no_fade_in_window_returns_length():
    bg = np.full((200, 2), 100.0)
    assert el.fade_start(bg, 30, 150) == 200


def test_first_sustained_dip_finds_flourish_run():
    # Char flourish = a LONG non-recurring run after the band; magnitude-free so a tiny
    # character (baby_mario) triggers it just like a big one. Returns the full half-open
    # run (windup -> jump -> landing).
    r = np.ones(400)
    r[250:320] = 0.6              # the jump: 70 low-recurrence frames
    assert el.first_sustained_dip(r, 20, 240) == (250, 320)


def test_first_sustained_dip_skips_blinks():
    # A blink is a SHORT dip (<10f) — must not read as the flourish start.
    r = np.ones(400)
    r[250:257] = 0.6              # blink
    r[300:370] = 0.55             # the real jump
    assert el.first_sustained_dip(r, 20, 240) == (300, 370)


def test_first_sustained_dip_none_returns_none():
    r = np.ones(300)
    assert el.first_sustained_dip(r, 20, 240) is None


def test_burst_start_finds_first_clearing_frame():
    # Normal kart: near-static idle, the flourish spin's ||dF|| clears 4x the idle
    # median within a few frames of the band end.
    jump = np.full(300, 50.0)
    jump[203:260] = 1600.0            # the spin burst
    assert el.burst_flourish_start(jump, b=200, thr=4.0 * 50.0, limit=280) == 203


def test_burst_start_spinning_idle_falls_back_to_band_edge():
    # bowser_bruiser regime: jagged wheels spin AT IDLE, inflating the idle median
    # (measured 455 vs b_dasher's 54) so 4x it (1820) sits ABOVE the real flourish
    # peak (1590). The scan must not run past the flourish onto the fade/map screen
    # (that produced a 1-frame flourish export); the kart idle band already ends AT
    # the flourish, so the band edge is the correct start.
    jump = np.full(400, 455.0)
    jump[203:260] = 1590.0            # real flourish: under thr=1820
    jump[310:] = 2500.0               # map screen after the fade: over thr
    assert el.burst_flourish_start(jump, b=200, thr=4.0 * 455.0, limit=300) == 201


def test_burst_start_never_scans_past_the_limit():
    # No burst inside [b, limit) at all -> band edge, never a frame at/after limit.
    jump = np.full(400, 100.0)
    jump[350] = 9000.0                # only spike lives beyond the limit
    assert el.burst_flourish_start(jump, b=200, thr=400.0, limit=300) == 201


def test_burst_threshold_is_relative_for_calm_idle():
    # Normal kart: 4x the idle median, well under the cap.
    assert el.burst_threshold(54.4) == 4.0 * 54.4


def test_burst_threshold_capped_for_spinning_idle():
    # Spinning-idle karts (bowser_bruiser: idle median ~455) push 4x to ~1820, INSIDE the
    # flourish's own motion range — the scan then fires mid-spin (sailor variant started
    # 13f into the rotation, missing the whole windup) or not at all. The spin ONSET is a
    # step (idle <=506 -> first spin frame >=1007 across all 44 surveyed clips), so the
    # cap sits in the empty band between them and the scan fires on the onset frame.
    assert el.burst_threshold(455.2) == el.KART_BURST_CAP
    assert el.KART_BURST_CAP == 750.0


def test_fade_anchored_start_is_fixed_gap_before_fade():
    # The recorder is deterministic: across the 164-clip survey (3 characters x 40 karts
    # + the baby_daisy roster) the fade starts exactly 27f after the flourish ends, so
    # the flourish START is fade - 27 - 62. Threshold-free — immune to spinning-idle
    # karts (bowser_bruiser) and to big-rider idle blips (bowser on calm karts cleared
    # the low 4x-median gate mid-idle and exported pure idle).
    assert el.fade_anchored_start(fade=830, n=960, band_b=738) == 830 - el.KART_FADE_GAP - el.KART_FLOURISH


def test_fade_anchored_start_none_without_fade_in_window():
    # fade == n means fade_start found nothing inside the decoded window (baby_daisy
    # big_horn: flourish at f911, fade beyond the 16s cap) -> caller falls back to the
    # burst scan.
    assert el.fade_anchored_start(fade=960, n=960, band_b=738) is None


def test_fade_anchored_start_rejects_start_before_band_end():
    # Sanity: an anchored start landing before the idle-band end means the fade fired
    # spuriously early -> fall back to the scan rather than export idle.
    assert el.fade_anchored_start(fade=500, n=960, band_b=738) is None


def test_kart_burst_floor_rejects_big_rider_idle_blips():
    # Bowser's in-kart idle blips reach ~250 ||dF|| — above 4x a calm kart's median
    # (~220 on cute_scoot/fin_twin/pipe_frame, which misfired in trailing idle) but far
    # below the weakest real spin onset (994 across 164 clips). The kart threshold
    # floors at 600.
    assert el.kart_burst_threshold(56.0) == el.KART_BURST_FLOOR
    assert el.KART_BURST_FLOOR == 600.0
    assert el.kart_burst_threshold(455.2) == el.KART_BURST_CAP


def test_kart_flourish_excludes_recorder_fade_frames():
    # The in-game kart flourish is 64f but the sweep recording's fade-out owns the last 2
    # (measured f62/f63 dimming on all 4 dirval karts) -> the fixed window stops at 62.
    assert el.KART_FLOURISH == 62


def test_char_flourish_fallback_length():
    # Fallback span when no hard cut is found (and the burst-branch char length): the
    # smallest real motion-start->cut window on the roster (mario: 56) minus the 2f
    # guard — safe for any character.
    assert el.CHAR_FLOURISH_LEN == 54


def _cut_track(n=200, idle_med=50.0, cut_at=None, cut_size=2000.0):
    """Synthetic colour-diff track: idle noise, motion burst at 60-90, hold, optional cut."""
    d = np.full(n, idle_med)
    d[60:90] = 900.0                  # the flourish motion: big diffs, moving neighbours
    d[90:] = 40.0                     # held pose: near-still
    if cut_at is not None:
        d[cut_at] = cut_size
    return d


def test_char_cut_finds_hard_cut_after_hold():
    # The end of a char flourish is a single-frame HARD CUT to the char-in-kart: a huge
    # colour change PRECEDED BY STILLNESS (the held pose). Measured on 13 probe clips:
    # cut diff 984-3153, pre-cut 4-frame max 10-91, exactly one real hit per clip.
    d = _cut_track(cut_at=140)
    assert el.char_cut(d, ds=60) == 140


def test_char_cut_rejects_the_characters_own_motion():
    # mario's jump (+8f), conkdor's head-slam (+16f) and swoop's flip (+24f) fooled the
    # old argmax swap detector. Motion frames have MOVING neighbours, so the stillness
    # gate rejects them even when they are the biggest diff in the clip.
    d = _cut_track(cut_at=140)
    d[65] = 3000.0                    # dramatic motion spike, bigger than the cut
    assert el.char_cut(d, ds=60) == 140


def test_char_cut_gates_are_absolute_not_relative():
    # Roster survey caught 4 clips where RELATIVE (x idle-median) gates failed: high-
    # motion characters (cataquack idle_med 326) inflated the stillness allowance until
    # semi-moving frames passed as "still" (hammer_bro pre4=260 squeaked under 3x261 ->
    # false 20f flourish; king_boo__pro_racer pre4=530 under 550 -> false 36f) and lifted
    # the spike bar above the real cut (cataquack's 1939 < 6x327). Real cuts separate on
    # ABSOLUTE numbers alone: pre4 <= 91 vs false candidates >= 260.
    d = _cut_track(cut_at=140)
    d[112:116] = 260.0                # in-window semi-motion: "still" under 3x-median, not ours
    d[116] = 1300.0                   # the hammer_bro/coin_coffer-style false cut
    assert el.char_cut(d, ds=60) == 140
    assert el.CHAR_CUT_STILL == 150.0 and el.CHAR_CUT_SPIKE == 500.0


def test_char_cut_scans_only_the_roster_window():
    # king_boo's pause-then-tongue-lunge (+37) is gate-indistinguishable from a real cut
    # on some costumes — but real cuts land +56..+95 after the onset on all 153 roster
    # clips, so the scan starts at +50. It also ends at +120: the clip tail's kart-select
    # screen transition must never read as the flourish end.
    d = _cut_track(cut_at=140)        # +80: in window
    d[95] = 2000.0                    # perfect-looking "cut" at +35 (the lunge) — ignored
    assert el.char_cut(d, ds=60) == 140
    tail_only = _cut_track(n=260, cut_at=190)   # +130: past the window -> no cut
    assert el.char_cut(tail_only, ds=60) is None


def test_char_cut_rejects_small_blips_after_stillness():
    # A blink / nameplate shimmer during the hold is preceded by stillness but far
    # below the absolute cut floor (weakest real cut: 984).
    d = _cut_track(cut_at=140)
    d[120] = 300.0                    # blip during the hold
    assert el.char_cut(d, ds=60) == 140


def test_char_cut_none_when_no_cut():
    d = _cut_track(cut_at=None)
    assert el.char_cut(d, ds=60) is None


def test_char_cut_guard_is_two_frames():
    # The incoming item's nameplate text blends in ~2f before the subject cut.
    assert el.CHAR_CUT_GUARD == 2


def test_clamp_flourish_end_backs_off_detected_fade():
    # fade_start's 0.5-luma corner gate fires ~2f after the brighter subject visibly dims,
    # so the clamp must back the detected fade off by the guard.
    assert el.clamp_flourish_end(fe=64, fade=60, n=200, fs=0) == 58


def test_clamp_flourish_end_without_fade_leaves_end():
    # fade == n means "no fade inside the decoded window": never trim against the window edge.
    assert el.clamp_flourish_end(fe=64, fade=200, n=200, fs=0) == 64


def test_clamp_flourish_end_keeps_at_least_one_frame():
    assert el.clamp_flourish_end(fe=10, fade=1, n=200, fs=5) == 6


def test_fresh_dir_clears_stale_frames(tmp_path):
    # Re-extracting a SHORTER segment into a dir that already holds a longer run must not
    # leave the old tail behind (a stale 062/063 put the recorder fade back into a 62f
    # flourish); _fresh_dir gives extract_segments an empty dir every time.
    d = tmp_path / "seg"
    d.mkdir()
    (d / "063.png").write_bytes(b"stale")
    el._fresh_dir(str(d))
    import os
    assert os.path.isdir(str(d)) and os.listdir(str(d)) == []
