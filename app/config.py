"""
FusionCameraDataService – Push-Mode Configuration
--------------------------------------------------
All settings are driven by environment variables so that each machine running
its own instance can be configured without touching code.

Required
--------
  DEVICE_ID       Unique identifier for this gateway node (e.g. "twin-factory-01").
                  Sent with every pushed frame so the NestJS server knows the source.

  PUSH_TARGETS    Comma-separated socket.io endpoint URLs to push frames to.
                  e.g. "http://nest1:3000,http://nest2:3000"

Optional (sensible defaults shown)
-----------------------------------
  PUSH_PATH             /socket.io   socket.io server path on the target NestJS server
  PUSH_NAMESPACE        /camera      socket.io namespace declared on the NestJS gateway
  PUSH_FPS              15           frames per second to push (should be ≤ STREAM_FPS)
  PUSH_SECRET                        shared secret included in socket.io auth handshake;
                                     NestJS can validate this in handleConnection / a guard
  PUSH_RECONNECT_DELAY  5.0          seconds between reconnect attempts on each target

  CAMERA_INDICES                     comma-separated camera indices to capture and push
                                     (empty = auto-scan and push all accessible cameras)

  STREAM_WIDTH          1280         capture resolution width
  STREAM_HEIGHT         720          capture resolution height
  STREAM_FPS            30           capture frame rate (camera capture loop rate)
  STREAM_JPEG_QUALITY   85           JPEG quality 1–100
  MAX_CAMERAS           10           maximum /dev/videoN devices to probe during auto-scan
  CAMERA_RECONNECT_DELAY  2.0        seconds between camera reconnect attempts

  HEALTH_PORT           9090         port for the lightweight k3s health/readiness HTTP server

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

    # ── Push targets ──────────────────────────────────────────────────────────
    PUSH_TARGETS: str = field(
        default_factory=lambda: _env("PUSH_TARGETS", "")
    )
    PUSH_PATH: str = field(
        default_factory=lambda: _env("PUSH_PATH", "/socket.io")
    )
    PUSH_NAMESPACE: str = field(
        default_factory=lambda: _env("PUSH_NAMESPACE", "/camera")
    )
    PUSH_FPS: int = field(
        default_factory=lambda: _env_int("PUSH_FPS", 15)
    )
    PUSH_SECRET: str = field(
        default_factory=lambda: _env("PUSH_SECRET", "")
    )
    PUSH_RECONNECT_DELAY: float = field(
        default_factory=lambda: _env_float("PUSH_RECONNECT_DELAY", 5.0)
    )

    # ── Camera selection ──────────────────────────────────────────────────────
    # Comma-separated indices, e.g. "0,1,2".  Empty string = auto-scan.
    CAMERA_INDICES: str = field(
        default_factory=lambda: _env("CAMERA_INDICES", "")
    )

    # ── Camera / Streaming ────────────────────────────────────────────────────
    STREAM_WIDTH: int = field(default_factory=lambda: _env_int("STREAM_WIDTH", 1280))
    STREAM_HEIGHT: int = field(default_factory=lambda: _env_int("STREAM_HEIGHT", 720))
    STREAM_FPS: int = field(default_factory=lambda: _env_int("STREAM_FPS", 30))
    STREAM_JPEG_QUALITY: int = field(
        default_factory=lambda: _env_int("STREAM_JPEG_QUALITY", 85)
    )
    MAX_CAMERAS: int = field(default_factory=lambda: _env_int("MAX_CAMERAS", 32))
    CAMERA_RECONNECT_DELAY: float = field(
        default_factory=lambda: _env_float("CAMERA_RECONNECT_DELAY", 2.0)
    )

    # ── Health server ─────────────────────────────────────────────────────────
    HEALTH_PORT: int = field(default_factory=lambda: _env_int("HEALTH_PORT", 9090))

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = field(
        default_factory=lambda: _env("LOG_LEVEL", "INFO").upper()
    )


# Module-level singleton — import this everywhere
config = Config()
