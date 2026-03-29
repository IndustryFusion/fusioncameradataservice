"""
Streaming Routes
-----------------
These are the endpoints consumers use to actually receive video data.

GET /api/v1/<device_id>/cameras/<int:index>/stream
    Live MJPEG stream — endless multipart/x-mixed-replace response.
    Works in browsers (<img src="...">) and most video players.
    Falls back to animated "NO SIGNAL" frames when the camera is unavailable.

GET /api/v1/<device_id>/cameras/<int:index>/snapshot
    Single JPEG frame (latest captured, or fallback).  Useful for thumbnails
    or still-image consumers.

Both endpoints:
  • Validate device_id via @require_device_id
  • Accept optional query param ?fps=N to request a specific stream rate
    (capped at configured STREAM_FPS)
  • Return proper Cache-Control headers to prevent proxy caching of video data
"""

import logging
import time
from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.config import config
from app.middleware.device_auth import require_device_id
from app.services.stream_manager import stream_manager
from app.utils.fallback import generate_no_signal_frame

logger = logging.getLogger(__name__)

bp = Blueprint("stream", __name__)

# Headers applied to every streaming response
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Accel-Buffering": "no",  # Disable nginx proxy buffering
}


def _validate_index(index: int) -> tuple[bool, str]:
    if index < 0 or index >= config.MAX_CAMERAS:
        return False, f"Camera index must be between 0 and {config.MAX_CAMERAS - 1}."
    return True, ""


def _requested_fps() -> int:
    try:
        fps = int(request.args.get("fps", config.STREAM_FPS))
        return max(1, min(fps, config.STREAM_FPS))  # clamp to [1, configured_max]
    except (ValueError, TypeError):
        return config.STREAM_FPS


# ── MJPEG Live Stream ─────────────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/cameras/<int:index>/stream", methods=["GET"])
@require_device_id
def mjpeg_stream(device_id: str, index: int):
    """
    Serve a continuous MJPEG stream for camera *index*.

    The stream is always available: if the physical camera is disconnected or
    has not yet been opened the consumer receives animated "NO SIGNAL" frames
    until the camera comes back online — no connection drop required.
    """
    valid, msg = _validate_index(index)
    if not valid:
        return jsonify({"error": "invalid_camera_index", "message": msg, "device_id": device_id}), 400

    fps = _requested_fps()
    logger.info(
        "MJPEG stream started — device=%s camera=%d fps=%d client=%s",
        device_id,
        index,
        fps,
        request.remote_addr,
    )

    def generate():
        try:
            yield from stream_manager.mjpeg_generator(index, fps=fps)
        except GeneratorExit:
            logger.info(
                "MJPEG stream closed — device=%s camera=%d client=%s",
                device_id,
                index,
                request.remote_addr,
            )

    resp = Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    for k, v in _NO_CACHE_HEADERS.items():
        resp.headers[k] = v
    resp.headers["X-Device-ID"] = device_id
    resp.headers["X-Camera-Index"] = str(index)
    return resp


# ── Single-frame Snapshot ─────────────────────────────────────────────────────

@bp.route("/api/v1/<device_id>/cameras/<int:index>/snapshot", methods=["GET"])
@require_device_id
def snapshot(device_id: str, index: int):
    """
    Return the most recently captured JPEG frame for camera *index*.

    If the camera is unavailable a "NO SIGNAL" placeholder is returned with
    HTTP 206 (Partial Content) so the consumer can distinguish it from a real
    frame (status 200).
    """
    valid, msg = _validate_index(index)
    if not valid:
        return jsonify({"error": "invalid_camera_index", "message": msg, "device_id": device_id}), 400

    cam = stream_manager.get_or_create(index)
    frame = cam.get_frame()
    is_capturing = cam.is_capturing

    status_code = 200 if is_capturing else 206  # 206 = fallback / partial content

    resp = Response(frame, mimetype="image/jpeg", status=status_code)
    for k, v in _NO_CACHE_HEADERS.items():
        resp.headers[k] = v
    resp.headers["X-Device-ID"] = device_id
    resp.headers["X-Camera-Index"] = str(index)
    resp.headers["X-Is-Live"] = "true" if is_capturing else "false"
    resp.headers["X-Timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return resp
