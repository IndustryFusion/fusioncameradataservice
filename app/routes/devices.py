"""
Camera Device Routes
---------------------
All endpoints require the correct {device_id} in the URL path.

GET  /api/v1/<device_id>/info
    Service-level info: device ID, configured stream settings, etc.

GET  /api/v1/<device_id>/cameras
    Scan and list all available USB/V4L2 cameras on this host.

GET  /api/v1/<device_id>/cameras/<int:index>
    Detailed info for a single camera (including its current stream status if
    it has been started).

POST /api/v1/<device_id>/cameras/<int:index>/start
    Start the background capture thread for camera <index>.

POST /api/v1/<device_id>/cameras/<int:index>/stop
    Stop the background capture thread for camera <index>.

GET  /api/v1/<device_id>/streams
    List all currently-active stream threads managed by this instance.
"""

import time
from flask import Blueprint, jsonify, request

from app.config import config
from app.middleware.device_auth import require_device_id
from app.services.device_scanner import scan_cameras
from app.services.stream_manager import stream_manager

bp = Blueprint("devices", __name__)

_BASE_URL_KEY = "base_url"  # injected by app factory if available


def _base_url() -> str:
    """Derive the scheme://host:port prefix for building self-referential URLs."""
    scheme = "https" if config.SSL_ENABLED else "http"
    # Flask's request context is active in route handlers
    host = request.host  # includes port when non-default
    return f"{scheme}://{host}"


# ── Service info ──────────────────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/info", methods=["GET"])
@require_device_id
def service_info(device_id: str):
    return jsonify(
        {
            "device_id": device_id,
            "service": "FusionCameraDataService",
            "version": "1.0.0",
            "stream_config": {
                "width": config.STREAM_WIDTH,
                "height": config.STREAM_HEIGHT,
                "fps": config.STREAM_FPS,
                "jpeg_quality": config.STREAM_JPEG_QUALITY,
            },
            "ssl_enabled": config.SSL_ENABLED,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ), 200


# ── Camera listing ────────────────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/cameras", methods=["GET"])
@require_device_id
def list_cameras(device_id: str):
    """
    Performs a live USB/V4L2 scan on every call so hot-plug events are
    reflected immediately.
    """
    cameras = scan_cameras(max_devices=config.MAX_CAMERAS)
    base = _base_url()
    return jsonify(
        {
            "device_id": device_id,
            "count": len(cameras),
            "cameras": [cam.to_dict(device_id=device_id, base_url=base) for cam in cameras],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ), 200


@bp.route("/api/v1/<device_id>/cameras/<int:index>", methods=["GET"])
@require_device_id
def camera_detail(device_id: str, index: int):
    """Return info for a specific camera index, merged with its stream status."""
    cameras = scan_cameras(max_devices=max(index + 1, config.MAX_CAMERAS))

    matched = next((c for c in cameras if c.index == index), None)
    if matched is None:
        return jsonify(
            {
                "error": "camera_not_found",
                "message": f"/dev/video{index} was not found on this host.",
                "device_id": device_id,
            }
        ), 404

    base = _base_url()
    data = matched.to_dict(device_id=device_id, base_url=base)

    # Enrich with live stream status if a capture thread is already running
    existing = stream_manager.get(index)
    if existing:
        data["stream_status"] = existing.status.to_dict()

    return jsonify(data), 200


# ── Stream lifecycle control ──────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/cameras/<int:index>/start", methods=["POST"])
@require_device_id
def start_camera(device_id: str, index: int):
    """Explicitly start the capture thread for camera *index*."""
    if index < 0 or index >= config.MAX_CAMERAS:
        return jsonify(
            {
                "error": "invalid_camera_index",
                "message": f"Camera index must be between 0 and {config.MAX_CAMERAS - 1}.",
                "device_id": device_id,
            }
        ), 400

    cam = stream_manager.get_or_create(index)
    return jsonify(
        {
            "message": f"Camera {index} capture started.",
            "device_id": device_id,
            "status": cam.status.to_dict(),
        }
    ), 200


@bp.route("/api/v1/<device_id>/cameras/<int:index>/stop", methods=["POST"])
@require_device_id
def stop_camera(device_id: str, index: int):
    """Stop and release the capture thread for camera *index*."""
    stopped = stream_manager.stop_camera(index)
    if not stopped:
        return jsonify(
            {
                "error": "camera_not_running",
                "message": f"Camera {index} was not running.",
                "device_id": device_id,
            }
        ), 404

    return jsonify(
        {"message": f"Camera {index} stopped.", "device_id": device_id}
    ), 200


# ── Active streams overview ───────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/streams", methods=["GET"])
@require_device_id
def list_streams(device_id: str):
    """Return runtime status for every camera that has been started."""
    active = stream_manager.list_active()
    return jsonify(
        {
            "device_id": device_id,
            "active_stream_count": len(active),
            "streams": active,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    ), 200
