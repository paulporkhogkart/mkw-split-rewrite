import os

from supervisor import ProcessSupervisor


def test_set_clips_dir_repoints_target_and_resume_marker():
    """Switching to the dark set must move BOTH the clip target and the resume marker, so a
    dark run never reads/writes the bright sweep's resume position."""
    sup = ProcessSupervisor(os.path.join("/repo"), lambda *_: None, py="python")
    assert sup.clips_dir.endswith("clips")
    assert sup._resume == os.path.join(sup.clips_dir, ".resume_char")

    dark = os.path.join("/repo", "captures_sdr_dark", "en_uk", "clips")
    sup.set_clips_dir(dark)
    assert sup.clips_dir == dark
    assert sup._resume == os.path.join(dark, ".resume_char")
