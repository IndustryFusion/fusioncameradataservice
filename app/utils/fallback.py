"""
Fallback Frame Generator
------------------------
Generates an animated "NO SIGNAL" JPEG frame that is served whenever a camera
is unavailable, disconnected, or not yet initialised.  The frame carries the
device ID, camera index, and a live UTC timestamp so consumers can tell it is
a placeholder.
"""

import io
import time
import math
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


# Paths to try for a TrueType font (available in most Linux distros)
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
]
_FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
]


def _load_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    # Pillow built-in bitmap font (always available)
    return ImageFont.load_default()


def generate_no_signal_frame(
    width: int = 1280,
    height: int = 720,
    device_id: str = "",
    camera_index: int = 0,
    quality: int = 85,
    message: str = "NO SIGNAL",
    error_detail: Optional[str] = None,
) -> bytes:
    """
    Return a JPEG-encoded bytes object containing a "NO SIGNAL" placeholder.

    The frame is dynamically generated so each call produces a fresh
    timestamp — making it easy for consumers to detect it is a live
    placeholder and not a frozen frame.
    """
    img = Image.new("RGB", (width, height), color=(10, 10, 15))
    draw = ImageDraw.Draw(img)

    # ── Animated scanline pattern ──────────────────────────────────────────
    t = time.time()
    for y in range(0, height, 6):
        wave = int(8 * abs(math.sin(y * 0.04 + t * 1.5)))
        lum = wave
        draw.line([(0, y), (width, y)], fill=(lum, lum, lum + 4))

    # ── Diagonal interference bars ─────────────────────────────────────────
    bar_offset = int((t * 60) % height)
    for i in range(3):
        by = (bar_offset + i * (height // 3)) % height
        draw.rectangle([(0, by), (width, by + 3)], fill=(30, 30, 35))

    # ── Border ────────────────────────────────────────────────────────────
    border_col = (40, 40, 50)
    draw.rectangle([(4, 4), (width - 5, height - 5)], outline=border_col, width=2)

    # ── Fonts ─────────────────────────────────────────────────────────────
    font_title = _load_font(_FONT_PATHS, max(32, height // 10))
    font_sub = _load_font(_FONT_PATHS_REGULAR, max(18, height // 25))
    font_info = _load_font(_FONT_PATHS_REGULAR, max(14, height // 35))

    def _centered_text(text: str, font, y_pos: int, color: tuple):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, y_pos), text, fill=color, font=font)

    cy = height // 2

    # Main "NO SIGNAL" label
    _centered_text(message, font_title, cy - 80, (210, 50, 60))

    # Red separator line
    lw = width // 4
    lx = (width - lw) // 2
    draw.line([(lx, cy - 8), (lx + lw, cy - 8)], fill=(150, 30, 40), width=2)

    # Device / camera info
    if device_id:
        _centered_text(f"Device  :  {device_id}", font_sub, cy + 15, (160, 160, 180))
    _centered_text(f"Camera  :  /dev/video{camera_index}", font_sub, cy + 50, (130, 130, 155))

    if error_detail:
        # Trim long messages to avoid overflowing the frame
        short_err = (error_detail[:72] + "…") if len(error_detail) > 72 else error_detail
        _centered_text(f"Fault   :  {short_err}", font_info, cy + 90, (200, 100, 60))

    # Timestamp
    ts = time.strftime("%Y-%m-%d  %H:%M:%S  UTC", time.gmtime())
    _centered_text(ts, font_info, height - 36, (80, 80, 100))

    # ── Encode ────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue()
