import os

from supervisor import ProcessSupervisor, parse_matte_progress


def test_set_clips_dir_repoints_target_and_resume_marker():
    """set_clips_dir (used to point the rig at the data drive) must move BOTH the clip target and
    the resume marker, so the sweep's resume position always lives next to the clips."""
    sup = ProcessSupervisor(os.path.join("/repo"), lambda *_: None, py="python")
    assert sup.clips_dir.endswith("clips")
    assert sup._resume == os.path.join(sup.clips_dir, ".resume_char")

    data = os.path.join(os.sep, "data", "captures_sdr", "en_uk", "clips")
    sup.set_clips_dir(data)
    assert sup.clips_dir == data
    assert sup._resume == os.path.join(data, ".resume_char")


def test_parse_matte_progress():
    """Per-frame matte lines drive the processing preview; clip/segment/summary lines must not."""
    seg, frac = parse_matte_progress("  matte baby_daisy__base__billdozer__idle 60/120")
    assert seg == "baby_daisy__base__billdozer__idle"
    assert frac == "60/120"
    assert parse_matte_progress("    matting baby_daisy__base__billdozer__spawn (150f)...") is None
    assert parse_matte_progress("--- baby_daisy__base (5/41) segmenting...") is None
    assert parse_matte_progress("PROCESSED baby_daisy__base (5/41) {'idle': 120} 484s") is None


def test_gpu_py_points_at_unified_matte_venv():
    sup = ProcessSupervisor("/repo", lambda *_: None, py="python")
    assert sup.gpu_py.replace("\\", "/").endswith("temp/asset-venv-matte/Scripts/python.exe")
