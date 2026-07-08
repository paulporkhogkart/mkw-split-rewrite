import commands


def test_process_cmd_plain_has_no_dist_flags():
    cmd = commands.process_cmd("py.exe", "/repo", "/clips", "/out", "/out/.stop")
    assert "--claims-dir" not in cmd
    assert "--ship-dir" not in cmd
    assert cmd[-2:] == ["--stop-file", "/out/.stop"]


def test_process_cmd_appends_dist_flags_when_set():
    cmd = commands.process_cmd("py.exe", "/repo", "/clips", "/out", "/out/.stop",
                               claims_dir="/share/claims", ship_dir="/share")
    assert cmd[cmd.index("--claims-dir") + 1] == "/share/claims"
    assert cmd[cmd.index("--ship-dir") + 1] == "/share"
