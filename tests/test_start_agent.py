"""Pure-logic tests for the controller-agent launcher (config/path parsing)."""
from start_agent import (
    win_to_wsl_path, parse_cfg_value, parse_busid_from_usbipd, first_distro,
)


def test_win_to_wsl_path():
    assert (win_to_wsl_path(r"C:\development\mkw-split-rewrite\tools\autotemplate")
            == "/mnt/c/development/mkw-split-rewrite/tools/autotemplate")


def test_parse_cfg_value_json_string():
    assert parse_cfg_value('"Ubuntu"') == "Ubuntu"
    assert parse_cfg_value('"E0:EF:BF:03:74:19"') == "E0:EF:BF:03:74:19"


def test_parse_cfg_value_json_number():
    assert parse_cfg_value("7878") == "7878"


def test_parse_cfg_value_raw_fallback():
    assert parse_cfg_value("Ubuntu") == "Ubuntu"


def test_parse_busid_from_usbipd_finds_bluetooth():
    sample = (
        "Connected:\n"
        "BUSID  VID:PID    DEVICE                                   STATE\n"
        "4-15   8087:0029  Intel(R) Wireless Bluetooth(R)           Attached\n"
        "2-3    0bda:8153  Realtek USB GbE Family Controller        Not shared\n"
    )
    assert parse_busid_from_usbipd(sample) == "4-15"


def test_parse_busid_from_usbipd_none():
    assert parse_busid_from_usbipd("BUSID VID:PID DEVICE STATE\n2-3 x Realtek Not shared") == ""


def test_first_distro_picks_first_nonempty():
    assert first_distro("Ubuntu\n\nDebian\n") == "Ubuntu"


def test_first_distro_strips_bom():
    assert first_distro("﻿Ubuntu\n") == "Ubuntu"
