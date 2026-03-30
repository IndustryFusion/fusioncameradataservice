#!/usr/bin/env python3
"""
FusionCameraDataService — Push-Mode Entry Point
------------------------------------------------
Captures video from local USB/V4L2 cameras and continuously pushes JPEG
frames to one or more remote NestJS socket.io endpoints.

No HTTP server is exposed to video consumers from this process.
A lightweight health server (default :9090) handles /health and /ready
for Kubernetes liveness / readiness probes only.

Required environment variables
-------------------------------
  DEVICE_ID       Unique ID for this gateway node (e.g. "twin-factory-01").
  PUSH_TARGETS    Comma-separated socket.io endpoint URLs.
                  e.g. "http://nest1:3000,http://nest2:3000"

Common optional variables (see app/config.py for the full list)
----------------------------------------------------------------
  CAMERA_INDICES  Comma-separated camera indices to capture.
                  Omit to auto-scan all accessible cameras.
  PUSH_FPS        Push frame rate (default 15).
  PUSH_NAMESPACE  socket.io namespace on the NestJS side (default /camera).
  PUSH_SECRET     Shared secret sent in the socket.io auth handshake.
  HEALTH_PORT     Port for the k3s health server (default 9090).
"""

import logging
import os
import signal
import sys
import time

# ── Validate required env before any other import ────────────────────────────
_missing: list[str] = []
if not os.environ.get("DEVICE_ID"):
    _missing.append(
        "  DEVICE_ID    — unique identifier for this gateway node\n"
        "                 e.g. export DEVICE_ID=twin-factory-line-01"
    )
if not os.environ.get("PUSH_TARGETS"):
    _missing.append(
        "  PUSH_TARGETS — comma-separated socket.io endpoint URLs\n"
        "                 e.g. export PUSH_TARGETS=http://nest1:3000,http://nest2:3000"
    )
if _missing:
    print(
        "[FATAL] The following required environment variables are not set:\n\n"
        + "\n".join(_missing),
        file=sys.stderr,
    )
    sys.exit(1)

from app.config import config
from app.services.health_server import start_health_server
from app.services.stream_pusher import push_manager

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _shutdown(signum, frame) -> None:
    logger.info("Shutdown signal received — stopping all pushers…")
    push_manager.stop()
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("=" * 60)
    logger.info("  FusionCameraDataService  [push mode]")
    logger.info("  Device ID    : %s", config.DEVICE_ID)
    logger.info("  Push targets : %s", config.PUSH_TARGETS)
    logger.info("  Push FPS     : %d", config.PUSH_FPS)
    logger.info("  Namespace    : %s", config.PUSH_NAMESPACE)
    logger.info(
        "  Capture      : %dx%d @ %d fps  JPEG quality %d%%",
        config.STREAM_WIDTH, config.STREAM_HEIGHT,
        config.STREAM_FPS, config.STREAM_JPEG_QUALITY,
    )
    logger.info("  Health HTTP  : :%d", config.HEALTH_PORT)
    logger.info("=" * 60)

    # Start the tiny k3s health/readiness probe HTTP server
    start_health_server()

    # Discover cameras and start pushing to all configured targets
    push_manager.start()

    # Keep the main thread alive — all work happens in daemon threads
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        _shutdown(None, None)


if __name__ == "__main__":
    main()
