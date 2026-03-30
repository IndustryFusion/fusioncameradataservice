"""
Stream Manager — Thread-Safe Camera Lifecycle
-----------------------------------------------
Each physical camera (/dev/videoN) runs in its own daemon thread that
continuously captures frames, encodes them as JPEG and stores the latest one
in a tight lock-protected buffer.

Key design properties:
  • Auto-reconnect — if the camera becomes unavailable the thread keeps
    retrying at ``CAMERA_RECONNECT_DELAY`` second intervals.
  • Fallback frames — while the camera is unavailable the buffer holds a
    dynamically generated "NO SIGNAL" frame that consumers receive instead of
    an empty/frozen stream.
  • Lazy initialisation — a ``CameraStream`` is created on the first access
    to a particular camera index; nothing is allocated for cameras that are
    never requested.
  • Graceful shutdown — ``StreamManager.shutdown()`` stops all threads cleanly.

Usage::

    from app.services.stream_manager import stream_manager

    # Start capture (idempotent)
    stream_manager.get_or_create(0)

    # Get the latest frame (bytes)
    frame = stream_manager.get_frame(0)

    # Generator for MJPEG route
    for chunk in stream_manager.mjpeg_generator(0, fps=30):
        ...
"""

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import cv2

# Suppress OpenCV's internal V4L2 WARN messages (e.g. VIDIOC_REQBUFS errno=19
# "No such device") that fire when a USB camera is physically disconnected
# mid-stream and the kernel buffers can no longer be released.
cv2.setLogLevel(2)  # 0=SILENT 1=FATAL 2=ERROR 3=WARN(default) 4=INFO

from app.config import config
from app.utils.fallback import generate_no_signal_frame

logger = logging.getLogger(__name__)


# ── Status dataclass ──────────────────────────────────────────────────────────

@dataclass
class StreamStatus:
    index: int
    path: str
    is_running: bool = False
    is_capturing: bool = False
    frame_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None
    last_frame_at: Optional[float] = None
    actual_width: int = 0
    actual_height: int = 0
    actual_fps: float = 0.0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "path": self.path,
            "is_running": self.is_running,
            "is_capturing": self.is_capturing,
            "frame_count": self.frame_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_frame_at": self.last_frame_at,
            "resolution": {
                "width": self.actual_width,
                "height": self.actual_height,
                "fps": round(self.actual_fps, 2),
            },
        }


# ── Single-camera capture thread ──────────────────────────────────────────────

class CameraStream:
    """Manages one camera: capture thread + frame buffer + fallback."""

    def __init__(self, index: int):
        self._index = index
        self._path = f"/dev/video{index}"

        # Frame buffer — stores latest JPEG bytes
        self._frame: bytes = self._make_fallback()
        self._lock = threading.Lock()

        # Card name learned on first open — used to re-locate the device
        # after USB re-enumeration (camera gets a new /dev/videoN index).
        self._card: str = ""

        # Capture state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._status = StreamStatus(index=index, path=self._path)

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background capture thread (idempotent)."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"camera-{self._index}",
        )
        self._thread.start()
        self._status.is_running = True
        logger.info("Camera %d capture thread started", self._index)

    def stop(self) -> None:
        """Signal the capture thread to stop and wait for it."""
        self._running = False
        self._status.is_running = False
        self._status.is_capturing = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=6.0)
        logger.info("Camera %d stopped", self._index)

    def get_frame(self) -> bytes:
        """Return the latest captured (or fallback) JPEG frame."""
        with self._lock:
            return self._frame

    @property
    def status(self) -> StreamStatus:
        return self._status

    @property
    def is_capturing(self) -> bool:
        return self._status.is_capturing

    # ── Internal ──────────────────────────────────────────────────────────

    def _get_card_name(self, path: str) -> str:
        """
        Return the V4L2 card/model name for *path*.
        Reads from sysfs first (no external tool needed), then falls back
        to v4l2-ctl.  Returns an empty string if nothing is found.
        """
        # Fast path: sysfs — available on all Linux kernels with V4L2
        try:
            node = Path(path).name  # e.g. "video10"
            sysfs_name = Path(f"/sys/class/video4linux/{node}/name").read_text().strip()
            if sysfs_name:
                return sysfs_name
        except OSError:
            pass

        # Fallback: v4l2-ctl
        try:
            out = subprocess.run(
                ["v4l2-ctl", f"--device={path}", "--info"],
                capture_output=True, text=True, timeout=3,
            )
            for line in out.stdout.splitlines():
                if m := re.match(r"Card type\s*:\s*(.+)", line, re.I):
                    return m.group(1).strip()
        except Exception:
            pass
        return ""

    def _find_device_by_card(self, card: str) -> Optional[str]:
        """
        Scan /dev/video* for a device whose card name matches *card*.
        Used to track a USB camera that re-enumerated to a new index.
        Returns the first matching capture-capable path, or None.
        Skips paths already owned by other CameraStream instances.
        """
        # Collect paths that are already claimed by other streams so we
        # don't accidentally steal a sibling camera's device.
        claimed = {
            cam._path for cam in stream_manager._cameras.values() if cam is not self
        }

        # Enumerate all known V4L2 nodes via sysfs (avoids guessing a scan limit)
        sysfs_root = Path("/sys/class/video4linux")
        try:
            nodes = sorted(sysfs_root.iterdir(), key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
        except OSError:
            nodes = []

        for node_dir in nodes:
            path = f"/dev/{node_dir.name}"
            if not Path(path).exists() or path in claimed:
                continue
            if self._get_card_name(path) == card:
                return path
        return None

    def _make_fallback(self, error: Optional[str] = None) -> bytes:
        return generate_no_signal_frame(
            width=config.STREAM_WIDTH,
            height=config.STREAM_HEIGHT,
            device_id=config.DEVICE_ID,
            camera_index=self._index,
            quality=config.STREAM_JPEG_QUALITY,
            error_detail=error,
        )

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Try to open the camera; returns a VideoCapture or None on failure."""
        try:
            cap = cv2.VideoCapture(self._path)
            if not cap.isOpened():
                cap.release()
                logger.debug("Camera %d: %s not ready yet", self._index, self._path)
                return None
            # Request HD resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.STREAM_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.STREAM_HEIGHT)
            cap.set(cv2.CAP_PROP_FPS, config.STREAM_FPS)
            # Keep internal buffer small to avoid stale frames
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # Record actual values (camera may not honour our request exactly)
            self._status.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._status.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._status.actual_fps = cap.get(cv2.CAP_PROP_FPS)
            return cap
        except Exception as exc:
            logger.error("Camera %d: failed to open — %s", self._index, exc)
            self._status.last_error = str(exc)
            self._status.error_count += 1
            return None

    def _capture_loop(self) -> None:
        """Main loop: open → capture → encode → buffer.  Reconnect on failure."""
        cap: Optional[cv2.VideoCapture] = None
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.STREAM_JPEG_QUALITY]
        frame_interval = 1.0 / max(1, config.STREAM_FPS)

        while self._running:
            # ── (Re-)open camera ──────────────────────────────────────────
            if cap is None or not cap.isOpened():
                self._status.is_capturing = False
                with self._lock:
                    self._frame = self._make_fallback("Camera disconnected — reconnecting…")

                cap = self._open_capture()
                if cap is None:
                    self._status.error_count += 1
                    with self._lock:
                        self._frame = self._make_fallback(
                            f"Device {self._path} unavailable"
                        )
                    logger.warning(
                        "Camera %d: reconnect attempt failed — retrying in %.1f s",
                        self._index,
                        config.CAMERA_RECONNECT_DELAY,
                    )
                    # USB camera may have re-enumerated to a different /dev/videoN.
                    # Scan all devices for one with the same card/model name.
                    if self._card:
                        new_path = self._find_device_by_card(self._card)
                        if new_path and new_path != self._path:
                            logger.info(
                                "Camera %d: device moved %s → %s (USB re-enumeration)",
                                self._index, self._path, new_path,
                            )
                            self._path = new_path
                            self._status.path = new_path
                    time.sleep(config.CAMERA_RECONNECT_DELAY)
                    continue
                logger.info(
                    "Camera %d opened — %dx%d @ %.1f fps",
                    self._index,
                    self._status.actual_width,
                    self._status.actual_height,
                    self._status.actual_fps,
                )
                # Learn the card name once so we can re-locate this camera
                # if it re-enumerates to a different /dev/videoN after reconnect.
                if not self._card:
                    self._card = self._get_card_name(self._path)
                    if self._card:
                        logger.debug("Camera %d: card=%r", self._index, self._card)

            # ── Capture frame ─────────────────────────────────────────────
            t0 = time.monotonic()
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning("Camera %d: read failed — will reconnect", self._index)
                self._status.is_capturing = False
                self._status.error_count += 1
                self._status.last_error = "Frame read failed"
                cap.release()
                cap = None
                with self._lock:
                    self._frame = self._make_fallback("Frame read error — reconnecting…")
                time.sleep(config.CAMERA_RECONNECT_DELAY)
                continue

            # ── Encode to JPEG ────────────────────────────────────────────
            ok, jpeg_buf = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                logger.warning("Camera %d: JPEG encode failed", self._index)
                continue

            with self._lock:
                self._frame = jpeg_buf.tobytes()

            self._status.is_capturing = True
            self._status.frame_count += 1
            self._status.last_frame_at = time.time()
            self._status.last_error = None

            # Pace the capture loop to STREAM_FPS to avoid CPU saturation
            # and prevent stale frames from accumulating in the camera buffer.
            elapsed = time.monotonic() - t0
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # ── Cleanup ───────────────────────────────────────────────────────
        if cap is not None:
            cap.release()
        logger.info("Camera %d capture thread exited", self._index)


# ── Manager singleton ─────────────────────────────────────────────────────────

class StreamManager:
    """
    Central registry of :class:`CameraStream` instances.

    Cameras are created lazily on first request and kept running until
    explicitly stopped or :meth:`shutdown` is called.
    """

    def __init__(self):
        self._cameras: Dict[int, CameraStream] = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    def get_or_create(self, index: int) -> CameraStream:
        """
        Return the :class:`CameraStream` for *index*, creating and starting
        it if it does not yet exist.
        """
        with self._lock:
            if index not in self._cameras:
                cam = CameraStream(index)
                cam.start()
                self._cameras[index] = cam
                logger.info("StreamManager: registered camera %d", index)
            return self._cameras[index]

    def get(self, index: int) -> Optional[CameraStream]:
        """Return an existing stream or *None* if it has never been requested."""
        with self._lock:
            return self._cameras.get(index)

    def stop_camera(self, index: int) -> bool:
        """Stop and remove a camera stream.  Returns True if it existed."""
        with self._lock:
            cam = self._cameras.pop(index, None)
        if cam:
            cam.stop()
            return True
        return False

    def list_active(self) -> list[dict]:
        with self._lock:
            return [cam.status.to_dict() for cam in self._cameras.values()]

    def get_frame(self, index: int) -> bytes:
        """
        Convenience wrapper: get the latest frame for *index*.
        If the camera has not been started yet, start it and return a
        fallback frame (the first real frame will arrive within milliseconds).
        """
        return self.get_or_create(index).get_frame()

    def shutdown(self) -> None:
        """Stop all camera streams — called during application teardown."""
        with self._lock:
            indices = list(self._cameras.keys())
        for idx in indices:
            self.stop_camera(idx)
        logger.info("StreamManager: all cameras stopped")


# Module-level singleton — import this in routes
stream_manager = StreamManager()
