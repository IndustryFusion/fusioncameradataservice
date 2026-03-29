#!/usr/bin/env python3
"""
FusionCameraDataService — Entry Point
---------------------------------------
Starts the HTTPS Flask server for this digital-twin gateway node.

Run directly (development):
    python main.py

Run via gunicorn (production — recommended):
    gunicorn --bind 0.0.0.0:5443 \
             --workers 2 \
             --worker-class sync \
             --threads 4 \
             --timeout 120 \
             --keyfile  /app/certs/key.pem \
             --certfile /app/certs/cert.pem \
             "main:application"

The ``application`` name is the standard WSGI callable used by gunicorn.
"""

import logging
import os
import sys

# ── Validate required env before any other import ────────────────────────────
if not os.environ.get("DEVICE_ID"):
    print(
        "[FATAL] DEVICE_ID environment variable is required but not set.\n"
        "        Set it to a unique identifier for this gateway node, e.g.:\n"
        "          export DEVICE_ID=twin-factory-line-01\n"
        "        or pass it as a Docker/K3s environment variable.",
        file=sys.stderr,
    )
    sys.exit(1)

from app import create_app
from app.config import config
from app.utils.ssl_utils import ensure_ssl_context

logger = logging.getLogger(__name__)

# ── Gunicorn-compatible WSGI callable ─────────────────────────────────────────
application = create_app()


def main() -> None:
    """Development server entry-point (Flask built-in server)."""
    ssl_ctx = ensure_ssl_context(config)

    logger.info("=" * 60)
    logger.info("  FusionCameraDataService")
    logger.info("  Device ID : %s", config.DEVICE_ID)
    logger.info("  Listening : %s://%s:%d", "https" if ssl_ctx else "http", config.HOST, config.PORT)
    logger.info("  Stream    : %dx%d @ %d fps  (quality %d%%)",
                config.STREAM_WIDTH, config.STREAM_HEIGHT,
                config.STREAM_FPS, config.STREAM_JPEG_QUALITY)
    logger.info("=" * 60)

    application.run(
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
        ssl_context=ssl_ctx,
        # threaded=True so streaming responses don't block other requests
        threaded=True,
        # Never use the reloader in production — it forks the process and
        # breaks camera capture threads
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
