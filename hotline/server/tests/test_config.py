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
