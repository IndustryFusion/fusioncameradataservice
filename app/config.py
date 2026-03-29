"""
FusionCameraDataService – Application Configuration
----------------------------------------------------
All settings are driven by environment variables so that each machine running
its own instance can be configured without touching code.

Required
--------
  DEVICE_ID   Unique identifier for this gateway node (e.g. "twin-factory-01").
              Consumers MUST send this exact ID in their request URL.

Optional (sensible defaults shown)
-----------------------------------
  HOST                  0.0.0.0
  PORT                  5443
  DEBUG                 false
  SSL_ENABLED           true
  SSL_CERT_PATH         /app/certs/cert.pem
  SSL_KEY_PATH          /app/certs/key.pem
  SSL_SELF_SIGNED       true   (auto-generate cert when cert files are absent)
  SSL_CERT_DAYS         3650   (validity of auto-generated cert in days)
  STREAM_WIDTH          1280
  STREAM_HEIGHT         720
  STREAM_FPS            30
  STREAM_JPEG_QUALITY   85     (1–100)
  MAX_CAMERAS           10     (maximum video devices to probe)
  CAMERA_RECONNECT_DELAY  2.0  (seconds between reconnect attempts)
  WORKERS               2      (gunicorn worker count)
  LOG_LEVEL             INFO
"""

import os
from dataclasses import dataclass, field
from typing import Final

_MISSING: Final = object()


def _env(key: str, default=_MISSING):
    val = os.environ.get(key)
    if val is None:
        if default is _MISSING:
            raise EnvironmentError(
                f"Required environment variable '{key}' is not set."
            )
        return default
    return val


def _env_int(key: str, default: int) -> int:
    return int(_env(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(_env(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Config:
    # ── Identity ──────────────────────────────────────────────────────────────
    DEVICE_ID: str = field(default_factory=lambda: _env("DEVICE_ID"))

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    PORT: int = field(default_factory=lambda: _env_int("PORT", 5443))
    DEBUG: bool = field(default_factory=lambda: _env_bool("DEBUG", False))
    WORKERS: int = field(default_factory=lambda: _env_int("WORKERS", 2))
    LOG_LEVEL: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    # ── TLS / HTTPS ───────────────────────────────────────────────────────────
    SSL_ENABLED: bool = field(default_factory=lambda: _env_bool("SSL_ENABLED", True))
    SSL_CERT_PATH: str = field(
        default_factory=lambda: _env("SSL_CERT_PATH", "/app/certs/cert.pem")
    )
    SSL_KEY_PATH: str = field(
        default_factory=lambda: _env("SSL_KEY_PATH", "/app/certs/key.pem")
    )
    SSL_SELF_SIGNED: bool = field(
        default_factory=lambda: _env_bool("SSL_SELF_SIGNED", True)
    )
    SSL_CERT_DAYS: int = field(default_factory=lambda: _env_int("SSL_CERT_DAYS", 3650))

    # ── Camera / Streaming ────────────────────────────────────────────────────
    STREAM_WIDTH: int = field(default_factory=lambda: _env_int("STREAM_WIDTH", 1280))
    STREAM_HEIGHT: int = field(default_factory=lambda: _env_int("STREAM_HEIGHT", 720))
    STREAM_FPS: int = field(default_factory=lambda: _env_int("STREAM_FPS", 30))
    STREAM_JPEG_QUALITY: int = field(
        default_factory=lambda: _env_int("STREAM_JPEG_QUALITY", 85)
    )
    MAX_CAMERAS: int = field(default_factory=lambda: _env_int("MAX_CAMERAS", 10))
    CAMERA_RECONNECT_DELAY: float = field(
        default_factory=lambda: _env_float("CAMERA_RECONNECT_DELAY", 2.0)
    )


# Module-level singleton — import this everywhere
config = Config()
