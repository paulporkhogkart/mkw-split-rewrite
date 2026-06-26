from clip_segment import segment_spans


def test_segment_file_writes_expected_assets(tmp_path):
    import json, numpy as np, cv2
    # 4s @ 30fps synthetic clip: a moving bar (gives loop_probe a period to find)
    # Use .avi + MJPG instead of mp4v-into-.mkv — mp4v-in-mkv silently fails to
    # open on this OpenCV/ffmpeg build (isOpened() returns False -> 0-byte file).
    path = tmp_path / "mario__base__standard_kart.avi"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (320, 180))
    assert vw.isOpened(), "VideoWriter failed to open — codec or container mismatch"
    for i in range(120):
        f = np.zeros((180, 320, 3), np.uint8)
        x = (i * 8) % 300
        cv2.rectangle(f, (x, 40), (x + 20, 140), (255, 255, 255), -1)
        vw.write(f)
    vw.release()
    ev = {"fps": 30, "swap_t": 0.2, "flourish_t": 3.0, "flourish_end_t": 3.6, "duration_t": 3.6}
    (tmp_path / "ev.json").write_text(json.dumps(ev))
    from clip_segment import segment_file
    out = segment_file(str(path), str(tmp_path / "ev.json"), str(tmp_path))
    assert set(out) >= {"spawn_in", "idle_loop", "flourish"}
    for p in out.values():
        assert __import__("os").path.exists(p)


def test_kart_spans():
    ev = {"fps": 60, "swap_t": 0.5, "flourish_t": 11.0,
          "flourish_end_t": 13.4, "duration_t": 13.4}
    spans = segment_spans(ev, loop_start_frame=120, loop_len_frames=80)
    assert spans["spawn_in"] == (30, 120)      # swap_t*fps .. loop start
    assert spans["idle_loop"] == (120, 200)    # loop start .. +len
    assert spans["flourish"] == (660, 804)     # flourish_t*fps .. end*fps


def test_character_has_no_spawn_in():
    ev = {"fps": 60, "swap_t": None, "flourish_t": 10.0,
          "flourish_end_t": 12.0, "duration_t": 12.0}
    spans = segment_spans(ev, loop_start_frame=60, loop_len_frames=80)
    assert "spawn_in" not in spans
    assert spans["idle_loop"] == (60, 140)
    assert spans["flourish"] == (600, 720)


def test_segment_file_character_path(tmp_path):
    import json, numpy as np, cv2, os
    # 4s @ 30fps synthetic clip for a character item (one __ → _is_kart False)
    path = tmp_path / "mario__base.avi"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (320, 180))
    assert vw.isOpened(), "VideoWriter failed to open — codec or container mismatch"
    for i in range(120):
        f = np.zeros((180, 320, 3), np.uint8)
        x = (i * 8) % 300
        cv2.rectangle(f, (x, 40), (x + 20, 140), (200, 200, 200), -1)
        vw.write(f)
    vw.release()
    ev = {"fps": 30, "swap_t": None, "flourish_t": 3.0,
          "flourish_end_t": 3.6, "duration_t": 3.6, "item": "mario__base"}
    (tmp_path / "ev.json").write_text(json.dumps(ev))
    from clip_segment import segment_file
    out = segment_file(str(path), str(tmp_path / "ev.json"), str(tmp_path))
    assert "spawn_in" not in out, "character path must not produce spawn_in"
    assert "idle_loop" in out
    assert "flourish" in out
    for p in out.values():
        assert os.path.exists(p)
