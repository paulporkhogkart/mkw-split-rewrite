import numpy as np
import carve_gaps as cg   # FLAT import — conftest adds tools/asset_matte to sys.path


H, W = 60, 80
BG_VAL = 60           # dark-grey backdrop level
KART_VAL = 160        # subject level, far from backdrop


def _backdrop():
    return np.full((H, W, 3), BG_VAL, np.float32)


def _stack(n=6, jitter=True, gap=True, bg_patch=False):
    """Synthetic segment: a 'kart' square (rows 10..50, cols 10..60) over the backdrop,
    with a see-through gap (rows 25..35, cols 30..44) showing frozen backdrop. The kart
    jitters +-1px per frame (the idle putter); the gap pixels never move. Alpha wrongly
    covers the whole square INCLUDING the gap (the defect under test). `bg_patch` adds a
    subject patch NEAR-backdrop-coloured whose shading alternates 58/62 with the wobble
    (within CARVE_THR *and* CARVE_MED_DIFF of the 60 backdrop) — only the temporal gate
    can save it."""
    raws, alphas = [], []
    for i in range(n):
        dx = (i % 2) if jitter else 0
        raw = np.full((H, W, 3), BG_VAL, np.uint8)
        raw[10 + dx:50 + dx, 10 + dx:60 + dx] = KART_VAL
        if gap:
            raw[25:35, 30:44] = BG_VAL                      # frozen backdrop through the gap
        if bg_patch:
            raw[40:48, 12:26] = 58 if i % 2 == 0 else 62    # bobbing shading, ~bg-coloured
        a = np.zeros((H, W), np.float32)
        a[10 + dx:50 + dx, 10 + dx:60 + dx] = 1.0           # matte keeps the gap filled
        raws.append(raw)
        alphas.append(a)
    return alphas, raws


def test_gap_carved_on_idle():
    alphas, raws = _stack()
    out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    for a in out:
        assert a[30, 37] == 0.0                             # gap centre now transparent
    assert all(c >= 100 for c in carved)                    # ~14x10 gap
    # ONE global mask: the exact same carve every frame (flicker impossible)
    assert len({c for c in carved}) == 1


def test_idle_mask_stable_even_when_engine_alpha_flickers():
    # The engine alpha popping 0<->1 in the gap (the observed defect) must still yield a
    # constant carve: any-frame alpha support feeds the global mask.
    alphas, raws = _stack()
    for i, a in enumerate(alphas):
        if i % 2 == 0:
            a[25:35, 30:44] = 0.0                           # engine already carved these frames
    out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    assert len({c for c in carved}) == 1
    for a in out:
        assert a[30, 37] == 0.0                             # transparent in EVERY frame


def test_oscillating_borderline_match_never_carved_per_frame():
    # spawn/flourish: a subject patch that matches the backdrop only on alternating frames
    # (threshold noise) must never carve — per-frame decisions used to flicker here.
    alphas, raws = _stack(jitter=False, gap=False)
    for i, r in enumerate(raws):
        r[30:40, 20:40] = BG_VAL if i % 2 == 0 else KART_VAL    # match, miss, match, miss...
    out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=False)
    assert all(c == 0 for c in carved)
    assert out[0][35, 30] == 1.0


def test_subject_kept():
    alphas, raws = _stack()
    out, _ = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    assert out[0][15, 20] == 1.0                            # solid kart body untouched
    assert out[0][45, 55] == 1.0


def test_wobbling_bg_coloured_subject_protected_by_temporal_gate():
    # A subject patch nearly backdrop-coloured (within thr) whose shading wobbles with the
    # idle putter: the colour match alone WOULD carve it; the temporal gate must protect it.
    alphas, raws = _stack(bg_patch=True)
    out, _ = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    assert out[0][44, 18] == 1.0
    out_nogate, _ = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=False)
    assert out_nogate[0][44, 18] == 0.0                     # proves the gate did the saving


def test_static_dark_part_protected_by_median_gate():
    # A STATIC subject patch coincidentally within CARVE_THR of the backdrop (a dark engine
    # slot against a dark backdrop) but not AT it: median-diff gate must refuse it in both
    # modes — a true gap shows the plate's own pixels, this one is 8.7 levels off.
    alphas, raws = _stack(gap=False)
    for r in raws:
        r[30:40, 20:40] = BG_VAL + 5                        # diff 8.66: cand, above med gate
    for gate in (True, False):
        out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=gate)
        assert all(c == 0 for c in carved)
        assert out[0][35, 30] == 1.0


def test_speckle_below_min_area_not_carved():
    alphas, raws = _stack(gap=False)
    for r in raws:
        r[20:23, 20:23] = BG_VAL                            # 9px hole << CARVE_MIN_AREA
    out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    assert all(c == 0 for c in carved)
    assert out[0][21, 21] == 1.0


def test_no_backdrop_is_noop():
    alphas, raws = _stack()
    out, carved = cg.carve_gaps(alphas, raws, None, temporal_gate=True)
    assert all(c == 0 for c in carved)
    assert np.array_equal(out[0], alphas[0])


def test_per_frame_mode_carves_without_static_gate():
    # flourish/spawn: no temporal gate — the colour match alone finds the gap
    alphas, raws = _stack(jitter=False)
    out, carved = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=False)
    assert out[0][30, 37] == 0.0
    assert all(c >= 100 for c in carved)


def test_outside_alpha_never_carved():
    alphas, raws = _stack()
    out, _ = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    assert out[0][5, 5] == 0.0 and alphas[0][5, 5] == 0.0   # bg was and stays 0 (not counted)


def test_feather_soft_edge():
    alphas, raws = _stack()
    out, _ = cg.carve_gaps(alphas, raws, _backdrop(), temporal_gate=True)
    ring = out[0][24, 30:44]                                # one row above the carved gap
    assert ring.min() < 1.0 and ring.max() > 0.0            # softened, not a hard step
