"""
Minimal HTTP Health Server
--------------------------
Serves /health and /ready endpoints for Kubernetes liveness and readiness
probes.  Uses Python's built-in ``http.server`` — no additional dependencies.

Endpoints
---------
GET /health   Always 200 while the process is alive.
GET /ready    200 when at least one camera is actively capturing;
              503 while starting up or all cameras are in reconnect state.
GET /metrics  Lightweight CPU/memory snapshot (no Prometheus dependency).

Port is controlled by the ``HEALTH_PORT`` env var (default: 9090).
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import psutil

from app.config import config

logger = logging.getLogger(__name__)

_START_TIME = time.time()


class _HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = json.dumps({
                "status": "alive",
                "device_id": config.DEVICE_ID,
                "uptime_seconds": round(time.time() - _START_TIME, 1),
            }).encode()
            self._respond(200, body)

        elif self.path == "/ready":
            from app.services.stream_manager import stream_manager
            active = stream_manager.list_active()
            capturing = any(s["is_capturing"] for s in active)
            body = json.dumps({
                "status": "ready" if capturing else "starting",
                "device_id": config.DEVICE_ID,
                "cameras_capturing": sum(1 for s in active if s["is_capturing"]),
                "cameras_total": len(active),
            }).encode()
            self._respond(200 if capturing else 503, body)

        elif self.path == "/metrics":
            proc = psutil.Process()
            mem = proc.memory_info()
            body = json.dumps({
                "device_id": config.DEVICE_ID,
                "uptime_seconds": round(time.time() - _START_TIME, 1),
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_rss_mb": round(mem.rss / 1024 / 1024, 2),
                "memory_vms_mb": round(mem.vms / 1024 / 1024, 2),
            }).encode()
            self._respond(200, body)

        else:
            self._respond(404, b'{"error":"not_found"}')

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silence the default per-request stdout log lines
    def log_message(self, fmt, *args):  # noqa: N802
        pass


def start_health_server() -> None:
    """Start the health HTTP server in a background daemon thread."""
    port = config.HEALTH_PORT
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="health-server",
    )
    thread.start()
    logger.info("Health server listening on :%d", port)
