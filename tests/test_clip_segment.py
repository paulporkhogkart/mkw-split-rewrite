from clip_segment import segment_spans


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
