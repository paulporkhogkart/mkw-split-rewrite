import commands as c


def test_tracker_cmd_has_required_flags():
    cmd = c.tracker_cmd("python", ws_port=8766)
    assert cmd == ["python", "-m", "mkw_tracker", "--clip-capture",
                   "--ws-port", "8766", "--no-display"]


def test_sweep_cmd_includes_stop_file_and_optional_start_from():
    base = c.sweep_cmd("python", "/at", "ws://127.0.0.1:8766", 7878, None, "/x/.stop")
    assert "--stop-file" in base and "/x/.stop" in base
    assert "--start-from" not in base
    resumed = c.sweep_cmd("python", "/at", "ws://127.0.0.1:8766", 7878, "luigi__base", "/x/.stop")
    assert resumed[resumed.index("--start-from") + 1] == "luigi__base"


def test_agent_cmd_points_at_start_agent():
    cmd = c.agent_cmd("python", "/at")
    assert cmd[0] == "python" and cmd[-1].endswith("start_agent.py")


def test_tracker_cmd_clip_out_appends_override():
    base = c.tracker_cmd("python", ws_port=8766)
    assert "--clip-out" not in base                     # default unchanged
    dark = c.tracker_cmd("python", ws_port=8766, clip_out="/d/dark/clips")
    assert dark[dark.index("--clip-out") + 1] == "/d/dark/clips"


def test_sweep_cmd_appends_sample_scope():
    scoped = c.sweep_cmd("python", "/at", "ws://x", 7878, None, "/x/.stop",
                         sample_chars="baby_daisy__base", sample_karts="all")
    assert scoped[scoped.index("--sample-chars") + 1] == "baby_daisy__base"
    assert scoped[scoped.index("--sample-karts") + 1] == "all"
    base = c.sweep_cmd("python", "/at", "ws://x", 7878, None, "/x/.stop")
    assert "--sample-chars" not in base and "--sample-karts" not in base


def test_process_cmd_runs_batch_driver_in_given_python():
    cmd = c.process_cmd("/gpu/py", "/repo", "/repo/clips", "/repo/out", "/repo/out/.stop")
    assert cmd[0] == "/gpu/py" and cmd[1].endswith("process_all.py")
    assert cmd[cmd.index("--clips") + 1] == "/repo/clips"
    assert cmd[cmd.index("--out") + 1] == "/repo/out"


def test_viewer_cmd_builds_make_viewer_invocation():
    cmd = c.viewer_cmd("python", "/repo", "/repo/out/matte", title="dark bd")
    assert cmd[0] == "python" and cmd[1].endswith("make_viewer.py")
    assert cmd[cmd.index("--matte") + 1] == "/repo/out/matte"
    assert cmd[cmd.index("--title") + 1] == "dark bd"


def test_sitepack_cmd_locked_recipe_and_stop_file():
    cmd = c.sitepack_cmd("python", "/repo", "D:/masters", "D:/pack", "/x/.sp_stop",
                         scale=0.2, fps=60, quality=60, alpha_bits=5, workers=12)
    assert cmd[0] == "python" and cmd[1].endswith("build_site_pack.py")
    for flag, val in [("--src", "D:/masters"), ("--out", "D:/pack"),
                      ("--stop-file", "/x/.sp_stop"), ("--scale", "0.2"), ("--fps", "60"),
                      ("--quality", "60"), ("--alpha-bits", "5"), ("--workers", "12")]:
        assert cmd[cmd.index(flag) + 1] == val
