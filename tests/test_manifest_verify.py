"""manifest_verify: share-bookkeeping audit + one-manifest merge (spec
2026-07-17-console-verify-manifest-design)."""
import json
import os

import pytest
import manifest_verify as mv     # FLAT import — conftest adds tools/asset_matte to sys.path


def _mk(tmp_path, clips, primary=None, foreign=None, frames=()):
    """Synthetic world: <clips_dir> with .mkv stubs, <out>/manifest.json (+ foreign),
    <out>/matte/<name>__{idle,flourish}_frames dirs for names in `frames`."""
    clips_dir = tmp_path / "clips"; clips_dir.mkdir()
    for n in clips:
        (clips_dir / f"{n}.mkv").write_bytes(b"x")
    out = tmp_path / "out"; (out / "matte").mkdir(parents=True)
    pp = out / "manifest.json"
    pp.write_text(json.dumps(primary or {}))
    if foreign is not None:
        (out / "manifest.BOX2.json").write_text(json.dumps(foreign))
    for n in frames:
        for seg in ("idle", "flourish"):
            (out / "matte" / f"{n}__{seg}_frames").mkdir()
    return str(clips_dir), str(out), str(pp)


DONE = {"status": "done", "kart": True, "segments": {}, "idle_resume": 3}
DONE_STD = {"status": "done", "kart": False, "segments": {}, "idle_resume": 0}


def test_audit_classifies_unrecorded_by_frame_presence(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["a__b__c", "d__e__f"], primary={},
                             frames=["a__b__c"])
    a = mv.audit(clips_dir, out, pp)
    assert a["unrecorded_with_frames"] == ["a__b__c"]
    assert a["unrecorded_no_frames"] == ["d__e__f"]
    assert a["pending"] == ["a__b__c", "d__e__f"]


def test_audit_foreign_only_and_union_health_checks(tmp_path):
    clips_dir, out, pp = _mk(
        tmp_path, ["m__b__k1", "m__b__k2", "m__b__k3", "m__b"],
        primary={"m__b__k1": dict(DONE)},
        foreign={"m__b__k2": dict(DONE),                       # healthy, foreign-only
                 "m__b__k3": {"status": "done", "kart": True}},  # done but NO idle_resume
        frames=["m__b__k1", "m__b__k2"])                        # k3 done but frames missing
    a = mv.audit(clips_dir, out, pp)
    assert a["foreign_only"] == ["m__b__k2", "m__b__k3"]
    assert a["missing_idle_resume"] == ["m__b__k3"]             # kart-only check
    assert a["missing_frames"] == ["m__b__k3"]
    assert "m__b" in a["unrecorded_no_frames"]                  # standalone never exempt from audit
    assert a["pending"] == ["m__b", "m__b__k2", "m__b__k3"]     # primary-only view


def test_audit_standalone_done_without_idle_resume_is_not_flagged(tmp_path):
    e = {"status": "done", "kart": False, "segments": {}}       # no idle_resume: fine for chars
    clips_dir, out, pp = _mk(tmp_path, ["m__b"], primary={"m__b": e}, frames=["m__b"])
    a = mv.audit(clips_dir, out, pp)
    assert a["missing_idle_resume"] == []
    assert a["pending"] == []


def test_audit_status_not_done(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["x__y__z"],
                             primary={"x__y__z": {"status": "error", "error": "boom"}})
    a = mv.audit(clips_dir, out, pp)
    assert a["status_not_done"] == ["x__y__z"]
    assert a["pending"] == ["x__y__z"]


def test_merge_adds_foreign_done_only_primary_wins(tmp_path):
    clips_dir, out, pp = _mk(
        tmp_path, ["a__b__c", "d__e__f", "g__h__i"],
        primary={"a__b__c": {"status": "done", "idle_resume": 7}},
        foreign={"a__b__c": {"status": "done", "idle_resume": 99},   # conflict: primary wins
                 "d__e__f": dict(DONE),                              # added
                 "g__h__i": {"status": "error"}})                    # non-done: skipped
    added, backup = mv.merge_foreign(out, pp)
    assert added == 1
    assert backup and os.path.exists(backup)
    m = json.load(open(pp))
    assert m["a__b__c"]["idle_resume"] == 7
    assert m["d__e__f"]["status"] == "done"
    assert "g__h__i" not in m


def test_merge_noop_returns_zero_and_no_backup(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["a__b__c"], primary={"a__b__c": dict(DONE)},
                             foreign={"a__b__c": dict(DONE)})
    added, backup = mv.merge_foreign(out, pp)
    assert (added, backup) == (0, None)


def test_merge_ignores_bak_files(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["a__b__c"], primary={})
    (tmp_path / "out" / "manifest.json.bak-old.json").write_text(json.dumps({"a__b__c": dict(DONE)}))
    added, _ = mv.merge_foreign(out, pp)
    assert added == 0


def test_run_for_console_skips_merge_while_processing(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["a__b__c"], primary={},
                             foreign={"a__b__c": dict(DONE)}, frames=["a__b__c"])
    lines = mv.run_for_console(clips_dir, out, pp, processing_active=True)
    assert any("merge skipped" in ln for ln in lines)
    assert "a__b__c" not in json.load(open(pp))                  # untouched
    lines2 = mv.run_for_console(clips_dir, out, pp, processing_active=False)
    assert any("merged 1" in ln for ln in lines2)
    assert json.load(open(pp))["a__b__c"]["status"] == "done"
    assert any("pending for next Process run: 0" in ln for ln in lines2)


def test_report_pending_split(tmp_path):
    clips_dir, out, pp = _mk(tmp_path, ["m__b", "a__b__c"], primary={})
    lines = mv.format_report(mv.audit(clips_dir, out, pp))
    assert lines[-1] == "pending for next Process run: 2 (1 standalones + 1 karts)"
