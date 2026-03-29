"""
USB / V4L2 Camera Device Scanner
----------------------------------
Discovers camera devices available on the host by probing /dev/video* nodes.
Works on Ubuntu 22.04 and 24.04 with standard V4L2 drivers.

Every discovered device is represented as a ``CameraDevice`` dataclass.
Metadata (driver name, card name, bus info) is gathered via ``v4l2-ctl`` when
available, with a graceful fallback to OpenCV property queries.
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2

logger = logging.getLogger(__name__)

_V4L2_AVAILABLE: Optional[bool] = None  # lazy-checked


def _has_v4l2ctl() -> bool:
    global _V4L2_AVAILABLE
    if _V4L2_AVAILABLE is None:
        _V4L2_AVAILABLE = (
            subprocess.run(
                ["which", "v4l2-ctl"], capture_output=True
            ).returncode == 0
        )
    return _V4L2_AVAILABLE


@dataclass
class CameraDevice:
    index: int
    path: str
    driver: str = ""
    card: str = ""
    bus_info: str = ""
    is_accessible: bool = False
    # Capabilities probed via OpenCV (populated lazily)
    native_width: int = 0
    native_height: int = 0
    native_fps: float = 0.0
    supported_resolutions: list[str] = field(default_factory=list)

    def to_dict(self, device_id: str = "", base_url: str = "") -> dict:
        return {
            "index": self.index,
            "path": self.path,
            "driver": self.driver,
            "card": self.card,
            "bus_info": self.bus_info,
            "is_accessible": self.is_accessible,
            "native_resolution": {
                "width": self.native_width,
                "height": self.native_height,
                "fps": self.native_fps,
            },
            "supported_resolutions": self.supported_resolutions,
            "endpoints": {
                "stream": f"{base_url}/api/v1/{device_id}/cameras/{self.index}/stream"
                if base_url and device_id
                else None,
                "snapshot": f"{base_url}/api/v1/{device_id}/cameras/{self.index}/snapshot"
                if base_url and device_id
                else None,
            },
        }


def _query_v4l2ctl_info(device_path: str) -> dict:
    """Run ``v4l2-ctl --device=<path> --info`` and parse the output."""
    result: dict = {}
    try:
        out = subprocess.run(
            ["v4l2-ctl", f"--device={device_path}", "--info"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        text = out.stdout
        for line in text.splitlines():
            line = line.strip()
            if m := re.match(r"Driver name\s*:\s*(.+)", line, re.I):
                result["driver"] = m.group(1).strip()
            elif m := re.match(r"Card type\s*:\s*(.+)", line, re.I):
                result["card"] = m.group(1).strip()
            elif m := re.match(r"Bus info\s*:\s*(.+)", line, re.I):
                result["bus_info"] = m.group(1).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return result


def _query_v4l2ctl_formats(device_path: str) -> list[str]:
    """Return a list of 'WxH' resolution strings the device supports."""
    resolutions: list[str] = []
    try:
        out = subprocess.run(
            ["v4l2-ctl", f"--device={device_path}", "--list-framesizes=MJPG"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in out.stdout.splitlines():
            if m := re.search(r"(\d+)x(\d+)", line):
                resolutions.append(f"{m.group(1)}x{m.group(2)}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for r in resolutions:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _probe_with_opencv(video_path: str, index: int) -> dict:
    """Open the device briefly with OpenCV to read native properties."""
    props: dict = {"is_accessible": False}
    cap = None
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            props["is_accessible"] = True
            props["native_width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            props["native_height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            props["native_fps"] = cap.get(cv2.CAP_PROP_FPS)
    except Exception as exc:  # pragma: no cover
        logger.debug("OpenCV probe failed for %s: %s", video_path, exc)
    finally:
        if cap is not None:
            cap.release()
    return props


def scan_cameras(max_devices: int = 10) -> list[CameraDevice]:
    """
    Return a list of :class:`CameraDevice` for every V4L2 video device found.

    Probing order:
    1. Enumerate /dev/video0 … /dev/video{max_devices-1}
    2. Query metadata via v4l2-ctl (if installed)
    3. Quick OpenCV open-and-probe for accessibility and native properties
    """
    devices: list[CameraDevice] = []

    for idx in range(max_devices):
        path = f"/dev/video{idx}"
        if not Path(path).exists():
            continue
        if not os.access(path, os.R_OK):
            logger.warning("No read access to %s — skipping", path)
            continue

        dev = CameraDevice(index=idx, path=path)

        # v4l2-ctl metadata
        if _has_v4l2ctl():
            info = _query_v4l2ctl_info(path)
            dev.driver = info.get("driver", "")
            dev.card = info.get("card", "")
            dev.bus_info = info.get("bus_info", "")
            dev.supported_resolutions = _query_v4l2ctl_formats(path)

        # OpenCV probe
        opencv_props = _probe_with_opencv(path, idx)
        dev.is_accessible = opencv_props.get("is_accessible", False)
        dev.native_width = opencv_props.get("native_width", 0)
        dev.native_height = opencv_props.get("native_height", 0)
        dev.native_fps = opencv_props.get("native_fps", 0.0)

        devices.append(dev)
        logger.debug("Found camera: %s  accessible=%s  v4l2=%s", path, dev.is_accessible, dev.card)

    logger.info("Camera scan complete — found %d device(s)", len(devices))
    return devices
