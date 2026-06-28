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
