"""
Device-ID Authentication Middleware
-------------------------------------
Every protected API endpoint uses ``require_device_id`` to ensure the
{device_id} path segment sent by the consumer matches the DEVICE_ID that was
configured for *this* specific gateway instance.

If the IDs do not match the request is immediately rejected with HTTP 403 so
no camera data leaks to the wrong consumer / digital-twin.
"""

import functools
import logging
from flask import request, jsonify, g
from app.config import config

logger = logging.getLogger(__name__)


def _error_response(status: int, code: str, message: str):
    body = {
        "error": code,
        "message": message,
        "device_id": config.DEVICE_ID,
    }
    return jsonify(body), status


def require_device_id(view_fn):
    """
    Route decorator — validates <device_id> URL variable against DEVICE_ID env.

    Usage::

        @bp.route("/api/v1/<device_id>/cameras")
        @require_device_id
        def list_cameras(device_id: str):
            ...
    """
    @functools.wraps(view_fn)
    def wrapper(*args, **kwargs):
        # device_id is always a URL keyword argument when this decorator is used
        url_device_id: str = kwargs.get("device_id", "")

        if not url_device_id:
            return _error_response(400, "missing_device_id", "Device ID is required in the URL path.")

        # Constant-time comparison to prevent timing oracle attacks
        import hmac
        if not hmac.compare_digest(url_device_id.strip(), config.DEVICE_ID.strip()):
            logger.warning(
                "Rejected request — wrong device_id '%s' (expected '%s') from %s",
                url_device_id,
                config.DEVICE_ID,
                request.remote_addr,
            )
            return _error_response(
                403,
                "device_id_mismatch",
                f"Device ID '{url_device_id}' does not match this gateway instance.",
            )

        # Store validated ID in Flask request context for downstream use
        g.device_id = url_device_id
        return view_fn(*args, **kwargs)

    return wrapper
