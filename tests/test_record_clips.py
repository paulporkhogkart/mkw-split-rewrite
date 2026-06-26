"""Pure-logic tests for the clip recorder (encoder pick, command build, parsing)."""
from mkw_tracker.tools.record_clips import (
    encoder_names, pick_encoder, build_record_cmd, _parse_name_dur,
)

# A trimmed sample of `ffmpeg -encoders` output.
ENC_WITH_NVENC = """\
 Encoders:
  V..... = Video
 V....D libx264              libx264 H.264 / AVC
 V....D libx265              libx265 H.265 / HEVC
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder
 A....D aac                  AAC
"""
ENC_SOFTWARE_ONLY = """\
 V....D libx264              libx264 H.264 / AVC
 V....D libx265              libx265 H.265 / HEVC
 A....D aac                  AAC
"""


def test_encoder_names_parses_capability_lines():
    have = encoder_names(ENC_WITH_NVENC)
    assert {"libx264", "h264_nvenc", "hevc_nvenc", "aac"} <= have


def test_pick_encoder_prefers_hardware():
    name, args = pick_encoder(ENC_WITH_NVENC, quality=14)
    assert name == "hevc_nvenc"
    assert "constqp" in args and "14" in args


def test_pick_encoder_falls_back_to_x264():
    name, args = pick_encoder(ENC_SOFTWARE_ONLY, quality=12)
    assert name == "libx264"
    assert "-crf" in args and "12" in args


def test_pick_encoder_respects_explicit_prefer():
    name, _ = pick_encoder(ENC_WITH_NVENC, prefer="libx264", quality=14)
    assert name == "libx264"


def test_build_record_cmd_has_dshow_input_duration_and_output():
    cmd = build_record_cmd("ffmpeg", "Elgato 4K", "3840x2160", 60, 8,
                           "temp/clips/mario_idle.mkv", "hevc_nvenc",
                           ["-rc", "constqp", "-qp", "14", "-pix_fmt", "yuv420p"])
    s = " ".join(cmd)
    assert "-f dshow" in s
    assert "video=Elgato 4K" in cmd[cmd.index("-i") + 1]
    assert cmd[cmd.index("-t") + 1] == "8"
    assert cmd[cmd.index("-video_size") + 1] == "3840x2160"
    assert cmd[cmd.index("-framerate") + 1] == "60"
    assert cmd[-1] == "temp/clips/mario_idle.mkv"
    assert "hevc_nvenc" in cmd


def test_build_record_cmd_optional_pixel_format():
    cmd = build_record_cmd("ffmpeg", "DEV", "3840x2160", 60, 5, "o.mkv",
                           "libx264", ["-crf", "12"], pixel_format="nv12")
    assert cmd[cmd.index("-pixel_format") + 1] == "nv12"


def test_parse_name_dur():
    assert _parse_name_dur("mario_idle", 8) == ("mario_idle", 8)
    assert _parse_name_dur("mario spin 12", 8) == ("mario_spin", 12.0)
    assert _parse_name_dur("bowser__base__hot_rod", 8) == ("bowser__base__hot_rod", 8)
