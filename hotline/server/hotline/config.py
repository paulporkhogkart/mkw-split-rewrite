from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    env: str
    http_port: int
    audiosocket_port: int
    admin_token: str
    data_dir: Path
    delay_n: float
    ari_url: str
    ari_user: str
    ari_password: str
    echo_mode: bool

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Config":
        env = environ.get("HOTLINE_ENV", "dev")
        token = environ.get("HOTLINE_ADMIN_TOKEN", "")
        if not token:
            if env == "dev":
                token = "dev-token"
            else:
                raise ValueError("HOTLINE_ADMIN_TOKEN is required outside dev")
        data_dir = environ.get("HOTLINE_DATA_DIR", "")
        if not data_dir:
            raise ValueError("HOTLINE_DATA_DIR is required")
        return cls(
            env=env,
            http_port=int(environ.get("HOTLINE_HTTP_PORT", "9100")),
            audiosocket_port=int(environ.get("HOTLINE_AUDIOSOCKET_PORT", "9101")),
            admin_token=token,
            data_dir=Path(data_dir),
            delay_n=float(environ.get("HOTLINE_DELAY_N", "4")),
            ari_url=environ.get("HOTLINE_ARI_URL", "http://127.0.0.1:8088"),
            ari_user=environ.get("HOTLINE_ARI_USER", "hotline"),
            ari_password=environ.get("HOTLINE_ARI_PASSWORD", ""),
            echo_mode=environ.get("HOTLINE_ECHO", "") in ("1", "true", "yes"),
        )
