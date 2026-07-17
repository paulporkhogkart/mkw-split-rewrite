import os
import sys
import types

import pytest


def _install_stubs(monkeypatch):
    """Replace the GPU-dependent modules process_all imports, so the loop runs CPU-only."""
    el = types.ModuleType("extract_loop")

    def extract_segments(clip, seg_base, name):
        for seg in ("idle",):
            d = os.path.join(seg_base, f"{name}__{seg}")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "000.png"), "w").close()
        return {"idle": 1}

    el.extract_segments = extract_segments
    el.is_kart_combo = lambda name: len(name.split("__")) >= 3
    el.char_flourish_raw_tail = lambda kart, seg, counts: 0

    mb = types.ModuleType("matte_blankplate")

    def matte_loopframes(framedir, name, out_base, **kw):
        d = os.path.join(out_base, f"{name}_frames")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "000.png"), "w").close()
        open(os.path.join(out_base, f"{name}_loop.webp"), "w").close()
        return 1

    mb.matte_loopframes = matte_loopframes

    mm = types.ModuleType("matte_matanyone")
    mm.segment_direction = lambda kart, seg: "fwd"

    for modname, mod in (("extract_loop", el), ("matte_blankplate", mb), ("matte_matanyone", mm)):
        monkeypatch.setitem(sys.modules, modname, mod)
    monkeypatch.delitem(sys.modules, "process_all", raising=False)
    import process_all
    return process_all


def _make_clips(clips_dir, names):
    os.makedirs(clips_dir, exist_ok=True)
    for n in names:
        open(os.path.join(clips_dir, n + ".mkv"), "w").close()


def test_optout_processes_all_in_order(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    clips = str(tmp_path / "clips")
    out = str(tmp_path / "out")
    _make_clips(clips, ["a__b", "a__c", "a__d"])
    pa.main(["--clips", clips, "--out", out, "--prefetch", "0"])
    m = pa.load_manifest(os.path.join(out, "manifest.json"))
    assert list(m) == ["a__b", "a__c", "a__d"]   # sorted processing order (crux of opt-out equivalence)
    assert all(v["status"] == "done" for v in m.values())
    assert os.path.exists(os.path.join(out, "matte", "a__b__idle_loop.webp"))
    assert not os.path.isdir(os.path.join(out, "claims"))   # no claims artifacts in opt-out


def test_claims_skips_clip_owned_by_other(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    import claims
    clips = str(tmp_path / "clips")
    out = str(tmp_path / "out")
    share = str(tmp_path / "share")
    claims_dir = os.path.join(share, "claims")
    _make_clips(clips, ["a__b", "a__c", "a__d"])
    claims.try_claim(claims_dir, "a__c", "OTHER")           # the other box owns a__c
    pa.main(["--clips", clips, "--out", out, "--prefetch", "0",
             "--claims-dir", claims_dir, "--ship-dir", share, "--machine-id", "ME"])
    # a__b and a__d done by us: shipped to the share, marked done, gone from local scratch
    assert claims.is_done(claims_dir, "a__b") and claims.is_done(claims_dir, "a__d")
    assert os.path.exists(os.path.join(share, "matte", "a__b__idle_loop.webp"))
    assert not os.path.exists(os.path.join(out, "matte", "a__b__idle_loop.webp"))
    # a__c was skipped: never done by us, no output shipped
    assert not claims.is_done(claims_dir, "a__c")
    assert not os.path.exists(os.path.join(share, "matte", "a__c__idle_loop.webp"))
    # our manifest is published to the share under our own machine-id (so idle_resume reaches the
    # viewer) — carrying exactly the clips we did, not the one the other box owns
    import json
    sm = json.load(open(os.path.join(share, "manifest.ME.json")))
    assert set(sm) == {"a__b", "a__d"}
    assert all("idle_resume" in v for v in sm.values())


def test_reclaim_orphans_mode(tmp_path, monkeypatch):
    pa = _install_stubs(monkeypatch)
    import claims
    claims_dir = str(tmp_path / "claims")
    claims.try_claim(claims_dir, "old", "DEAD")
    pa.main(["--reclaim-orphans", "--claims-dir", claims_dir, "--stale-secs", "0"])
    assert "old" not in claims.claimed_names(claims_dir)
