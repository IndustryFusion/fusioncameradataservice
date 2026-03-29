"""
Health & Readiness Routes
--------------------------
These endpoints are intentionally unauthenticated so that Kubernetes liveness
and readiness probes can reach them without knowing the DEVICE_ID.

GET /health   — liveness probe  (always returns 200 if the process is alive)
GET /ready    — readiness probe (200 when the service is fully initialised)
GET /         — redirect to /health (convenience for browser access)
"""

import time
import platform
from flask import Blueprint, jsonify, redirect, url_for
import psutil

from app.config import config

bp = Blueprint("health", __name__)

_START_TIME = time.time()


@bp.route("/", methods=["GET"])
def root():
    return redirect(url_for("health.liveness"))


@bp.route("/health", methods=["GET"])
def liveness():
    """Kubernetes liveness probe — always 200 while the process is running."""
    return jsonify(
        {
            "status": "alive",
            "device_id": config.DEVICE_ID,
            "uptime_seconds": round(time.time() - _START_TIME, 1),
        }
    ), 200


@bp.route("/ready", methods=["GET"])
def readiness():
    """
    Kubernetes readiness probe — returns 200 once config is validated and the
    stream manager is importable, 503 otherwise.
    """
    try:
        from app.services.stream_manager import stream_manager  # noqa: F401
        device_id_ok = bool(config.DEVICE_ID)
    except Exception as exc:
        return jsonify({"status": "not_ready", "reason": str(exc)}), 503

    if not device_id_ok:
        return jsonify({"status": "not_ready", "reason": "DEVICE_ID not configured"}), 503

    return jsonify({"status": "ready", "device_id": config.DEVICE_ID}), 200


@bp.route("/metrics", methods=["GET"])
def metrics():
    """Lightweight system metrics — for observability without Prometheus."""
    proc = psutil.Process()
    mem = proc.memory_info()
    return jsonify(
        {
            "device_id": config.DEVICE_ID,
            "uptime_seconds": round(time.time() - _START_TIME, 1),
            "python_version": platform.python_version(),
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_rss_mb": round(mem.rss / 1024 / 1024, 2),
            "memory_vms_mb": round(mem.vms / 1024 / 1024, 2),
            "platform": platform.platform(),
        }
    ), 200
