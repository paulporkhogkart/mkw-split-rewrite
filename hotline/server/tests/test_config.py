from __future__ import annotations

import pytest

from hotline.config import Config


def test_from_env_defaults_dev():
    cfg = Config.from_env({"HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": "./tmpdata"})
    assert cfg.http_port == 9100
    assert cfg.audiosocket_port == 9101
    assert cfg.delay_n == 4.0
    assert cfg.admin_token == "dev-token"
    assert cfg.echo_mode is False


def test_prod_requires_token():
    with pytest.raises(ValueError, match="HOTLINE_ADMIN_TOKEN"):
        Config.from_env({"HOTLINE_ENV": "prod", "HOTLINE_DATA_DIR": "./d"})


def test_overrides_parse():
    cfg = Config.from_env({
        "HOTLINE_ENV": "prod", "HOTLINE_DATA_DIR": "/opt/hotline-data",
        "HOTLINE_ADMIN_TOKEN": "s3cret", "HOTLINE_HTTP_PORT": "9200",
        "HOTLINE_DELAY_N": "2.5", "HOTLINE_ECHO": "1",
    })
    assert cfg.http_port == 9200 and cfg.delay_n == 2.5
    assert cfg.admin_token == "s3cret" and cfg.echo_mode is True


def test_new_knobs_defaults():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x"})
    assert cfg.claim_window_s == 10.0
    assert cfg.ws_grace_s == 15.0
    assert cfg.ring_timeout_s == 30
    assert cfg.call_backstop_s == 1800
    assert cfg.ata_poll_s == 15.0
    # dev origins derived from the http port; prod origin always present
    assert "https://phone.thekartoff.com" in cfg.allowed_origins
    assert "http://127.0.0.1:9100" in cfg.allowed_origins
    assert "http://localhost:9100" in cfg.allowed_origins


def test_allowed_origins_env_override():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x",
                           "HOTLINE_ALLOWED_ORIGINS": "https://a.example, https://b.example"})
    assert "https://a.example" in cfg.allowed_origins
    assert "https://b.example" in cfg.allowed_origins
    # localhost dev origins are still appended so echo-mode dev keeps working
    assert "http://127.0.0.1:9100" in cfg.allowed_origins


def test_echo_ring_delay_default_zero():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x"})
    assert cfg.echo_ring_s == 0.0


def test_echo_ring_delay_parsed():
    cfg = Config.from_env({"HOTLINE_DATA_DIR": "/tmp/x",
                           "HOTLINE_ECHO_RING_S": "3.5"})
    assert cfg.echo_ring_s == 3.5


def test_snmp_defaults_disabled(tmp_path):
    cfg = Config.from_env({"HOTLINE_ENV": "dev",
                           "HOTLINE_DATA_DIR": str(tmp_path)})
    assert cfg.snmp_host == ""
    assert cfg.snmp_community == ""
    assert cfg.snmp_hook_oid == ""
    assert cfg.snmp_offhook_values == ()
    assert cfg.snmp_poll_s == 2.0


def test_snmp_values_parse(tmp_path):
    cfg = Config.from_env({
        "HOTLINE_ENV": "dev", "HOTLINE_DATA_DIR": str(tmp_path),
        "HOTLINE_SNMP_HOST": "192.168.3.226",
        "HOTLINE_SNMP_COMMUNITY": "s3cret",
        "HOTLINE_SNMP_HOOK_OID": "1.3.6.1.4.1.42397.1.2.1.1.3.1",
        "HOTLINE_SNMP_OFFHOOK_VALUES": "2, Off-Hook",
        "HOTLINE_SNMP_POLL_S": "1.5",
    })
    assert cfg.snmp_host == "192.168.3.226"
    assert cfg.snmp_offhook_values == ("2", "Off-Hook")
    assert cfg.snmp_poll_s == 1.5
