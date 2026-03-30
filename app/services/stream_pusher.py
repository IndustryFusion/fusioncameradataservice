"""
Stream Pusher — Push JPEG frames to NestJS socket.io endpoints
---------------------------------------------------------------
One ``StreamPusher`` instance manages the connection to ONE remote NestJS
endpoint and pushes frames from ALL configured cameras over it.

Protocol
--------
Each frame is emitted as a socket.io ``camera:frame`` event carrying a dict:

    {
        "deviceId":    str,   # DEVICE_ID of this gateway (from env)
        "cameraIndex": int,   # 0-based /dev/videoN index
        "timestamp":   float, # Unix epoch of the emission
        "jpeg":        bytes  # Raw JPEG bytes (socket.io binary attachment)
    }

NestJS example handler::

    @SubscribeMessage('camera:frame')
    handleFrame(
      @MessageBody() data: { deviceId: string; cameraIndex: number;
                              timestamp: number; jpeg: Buffer },
    ) { ... }

Authentication
--------------
If ``PUSH_SECRET`` is set the socket.io handshake ``auth`` field contains::

    { "deviceId": "<DEVICE_ID>", "secret": "<PUSH_SECRET>" }

NestJS can validate this in ``handleConnection`` or a ``CanActivate`` guard.

Reconnection
------------
``python-socketio``'s built-in reconnection handles transient disconnects.
If the *initial* connection attempt fails (server not yet up) the pusher
waits ``PUSH_RECONNECT_DELAY`` seconds and retries indefinitely.
"""

import logging
import threading
import time
from typing import Optional

import socketio

from app.config import config
from app.services.stream_manager import stream_manager

logger = logging.getLogger(__name__)


class StreamPusher:
    """
    Maintains a socket.io connection to one NestJS endpoint and pushes
    frames from every configured camera at ``PUSH_FPS`` rate.
    """

    def __init__(self, target_url: str, camera_indices: list[int]) -> None:
        self._target_url = target_url
        self._camera_indices = camera_indices
        self._running = False

        self._sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,          # unlimited automatic reconnects
            reconnection_delay=config.PUSH_RECONNECT_DELAY,
            reconnection_delay_max=30.0,
            logger=False,
            engineio_logger=False,
        )

        @self._sio.event
        def connect():
            logger.info(
                "[pusher] CONNECTED → %s  (sid=%s)",
                self._target_url,
                self._sio.get_sid(),
            )

        @self._sio.event
        def disconnect():
            logger.warning(
                "[pusher] DISCONNECTED from %s — socket.io will attempt automatic reconnect",
                self._target_url,
            )

        @self._sio.event
        def connect_error(data):
            logger.error(
                "[pusher] CONNECTION ERROR → %s : %s",
                self._target_url,
                data,
            )

        @self._sio.event
        def reconnect(attempt):
            logger.info(
                "[pusher] RECONNECTED → %s (attempt #%d)",
                self._target_url,
                attempt,
            )

        @self._sio.event
        def reconnect_attempt(attempt):
            logger.debug(
                "[pusher] reconnect attempt #%d → %s",
                attempt,
                self._target_url,
            )

        @self._sio.event
        def reconnect_error(data):
            logger.warning(
                "[pusher] reconnect error → %s : %s",
                self._target_url,
                data,
            )

        @self._sio.event
        def reconnect_failed():
            logger.error(
                "[pusher] reconnect FAILED (all attempts exhausted) → %s",
                self._target_url,
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the connection thread and one push thread per camera."""
        self._running = True
        logger.info(
            "[pusher] starting → %s  cameras=%s",
            self._target_url,
            self._camera_indices,
        )

        conn_thread = threading.Thread(
            target=self._connection_loop,
            daemon=True,
            name=f"pusher-conn[{self._target_url}]",
        )
        conn_thread.start()

        for idx in self._camera_indices:
            push_thread = threading.Thread(
                target=self._push_loop,
                args=(idx,),
                daemon=True,
                name=f"pusher-cam{idx}[{self._target_url}]",
            )
            push_thread.start()

    def stop(self) -> None:
        logger.info("[pusher] stopping → %s", self._target_url)
        self._running = False
        try:
            self._sio.disconnect()
            logger.debug("[pusher] socket disconnected cleanly → %s", self._target_url)
        except Exception as exc:
            logger.debug("[pusher] disconnect raised (ignored): %s", exc)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connection_loop(self) -> None:
        """Establish and maintain the socket.io connection to the NestJS endpoint."""
        auth: dict = {"deviceId": config.DEVICE_ID}
        if config.PUSH_SECRET:
            auth["secret"] = config.PUSH_SECRET

        logger.debug(
            "[pusher] connection loop started → %s  namespace=%s path=%s",
            self._target_url,
            config.PUSH_NAMESPACE,
            config.PUSH_PATH,
        )

        while self._running:
            logger.debug("[pusher] attempting connect → %s", self._target_url)
            try:
                self._sio.connect(
                    self._target_url,
                    socketio_path=config.PUSH_PATH,
                    namespaces=[config.PUSH_NAMESPACE],
                    auth=auth,
                    wait_timeout=10,
                )
                # Blocks here; python-socketio handles automatic reconnection
                # internally while the connection lives.
                logger.debug("[pusher] entering wait() loop → %s", self._target_url)
                self._sio.wait()
                logger.debug("[pusher] wait() returned (connection ended) → %s", self._target_url)
            except socketio.exceptions.ConnectionError as exc:
                logger.warning(
                    "[pusher] cannot reach %s (%s) — retry in %.0fs",
                    self._target_url,
                    exc,
                    config.PUSH_RECONNECT_DELAY,
                )
                time.sleep(config.PUSH_RECONNECT_DELAY)
            except Exception as exc:
                logger.error(
                    "[pusher] unexpected error for %s: %s — retry in %.0fs",
                    self._target_url,
                    exc,
                    config.PUSH_RECONNECT_DELAY,
                    exc_info=True,
                )
                time.sleep(config.PUSH_RECONNECT_DELAY)

        logger.info("[pusher] connection loop stopped → %s", self._target_url)

    def _push_loop(self, camera_index: int) -> None:
        """Read the latest frame from StreamManager and emit it at PUSH_FPS."""
        interval = 1.0 / max(1, config.PUSH_FPS)
        namespace = config.PUSH_NAMESPACE

        # Ensure capture thread is running for this camera index
        stream_manager.get_or_create(camera_index)

        logger.debug(
            "[pusher] push loop started  cam%d → %s  fps=%d",
            camera_index,
            self._target_url,
            config.PUSH_FPS,
        )

        consecutive_skips = 0
        frames_emitted = 0

        while self._running:
            t0 = time.monotonic()

            if self._sio.connected:
                if consecutive_skips > 0:
                    logger.info(
                        "[pusher] cam%d → %s resumed pushing after %d skipped tick(s)",
                        camera_index,
                        self._target_url,
                        consecutive_skips,
                    )
                    consecutive_skips = 0

                frame = stream_manager.get_frame(camera_index)
                try:
                    self._sio.emit(
                        "camera:frame",
                        {
                            "deviceId": config.DEVICE_ID,
                            "cameraIndex": camera_index,
                            "timestamp": time.time(),
                            "jpeg": frame,            # bytes → socket.io binary attachment
                        },
                        namespace=namespace,
                    )
                    frames_emitted += 1
                    if frames_emitted % 300 == 0:
                        logger.debug(
                            "[pusher] cam%d → %s  total frames pushed: %d",
                            camera_index,
                            self._target_url,
                            frames_emitted,
                        )
                except Exception as exc:
                    logger.warning(
                        "[pusher] emit failed  cam%d → %s: %s",
                        camera_index,
                        self._target_url,
                        exc,
                    )
            else:
                consecutive_skips += 1
                if consecutive_skips == 1 or consecutive_skips % 50 == 0:
                    logger.debug(
                        "[pusher] cam%d → %s not connected, skipping frame (skip #%d)",
                        camera_index,
                        self._target_url,
                        consecutive_skips,
                    )

            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info(
            "[pusher] push loop stopped  cam%d → %s  total frames pushed: %d",
            camera_index,
            self._target_url,
            frames_emitted,
        )


# ── Manager ───────────────────────────────────────────────────────────────────

class PushManager:
    """
    Parses ``PUSH_TARGETS``, resolves camera indices, and starts one
    :class:`StreamPusher` per configured target URL.
    """

    def __init__(self) -> None:
        self._pushers: list[StreamPusher] = []

    def start(self) -> None:
        """Discover cameras and launch all pushers."""
        camera_indices = self._resolve_camera_indices()
        if not camera_indices:
            logger.warning(
                "No accessible cameras found — pushers will start but will "
                "send 'NO SIGNAL' fallback frames until a camera connects."
            )
            # Push camera 0 even without hardware so the remote side gets
            # consistent fallback frames rather than silence.
            camera_indices = [0]

        targets = [t.strip() for t in config.PUSH_TARGETS.split(",") if t.strip()]
        if not targets:
            logger.error("PUSH_TARGETS is empty — no streams will be pushed.")
            return

        logger.info(
            "PushManager: %d camera(s) × %d target(s)",
            len(camera_indices),
            len(targets),
        )
        for target_url in targets:
            pusher = StreamPusher(target_url, camera_indices)
            pusher.start()
            self._pushers.append(pusher)
            logger.info("  pushing cam%s → %s", camera_indices, target_url)

    def stop(self) -> None:
        for pusher in self._pushers:
            pusher.stop()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_camera_indices(self) -> list[int]:
        """Return explicit indices from CAMERA_INDICES or auto-scan."""
        if config.CAMERA_INDICES:
            try:
                indices = [
                    int(i.strip())
                    for i in config.CAMERA_INDICES.split(",")
                    if i.strip()
                ]
                logger.info("Using configured CAMERA_INDICES: %s", indices)
                return indices
            except ValueError:
                logger.error(
                    "Invalid CAMERA_INDICES value '%s' — falling back to auto-scan",
                    config.CAMERA_INDICES,
                )

        # Auto-scan
        from app.services.device_scanner import scan_cameras

        cameras = scan_cameras(max_devices=config.MAX_CAMERAS)
        accessible = [c.index for c in cameras if c.is_accessible]
        logger.info(
            "Auto-scan: found %d accessible camera(s): %s", len(accessible), accessible
        )
        return accessible


# Module-level singleton
push_manager = PushManager()
