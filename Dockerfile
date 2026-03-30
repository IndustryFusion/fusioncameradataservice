# ──────────────────────────────────────────────────────────────────────────────
# FusionCameraDataService  —  Dockerfile
# ──────────────────────────────────────────────────────────────────────────────
# Multi-stage build:
#   Stage 1 (builder)  — install Python dependencies into a clean venv
#   Stage 2 (runtime)  — minimal runtime image; copies only the venv
#
# The image is designed to run on Ubuntu 22.04 / 24.04 host nodes and needs
# access to /dev/video* for USB camera capture.
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

# System packages required by OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Create an isolated virtualenv so the runtime stage is clean
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="FusionCameraDataService"
LABEL org.opencontainers.image.description="USB camera HTTPS streaming service for digital-twin gateways"
LABEL org.opencontainers.image.source="https://github.com/your-org/fusioncameradataservice"

# Runtime-only system packages
#   - v4l-utils      → v4l2-ctl for camera metadata
#   - libglib2.0-0   → required by OpenCV
#   - libgl1         → required by OpenCV
#   - fonts-dejavu   → required by Pillow for "NO SIGNAL" frame text
RUN apt-get update && apt-get install -y --no-install-recommends \
        v4l-utils \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgl1 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY --from=builder $VIRTUAL_ENV $VIRTUAL_ENV

# ── Application user ──────────────────────────────────────────────────────────
# We do NOT run as root.  The 'video' group (GID 44 on most Debian/Ubuntu
# systems) is required for access to /dev/video* devices.
RUN groupadd -g 44 video 2>/dev/null || true && \
    useradd -u 1001 -g video -ms /bin/bash appuser

# ── Application files ─────────────────────────────────────────────────────────
WORKDIR /app

COPY app/          ./app/
COPY main.py       ./main.py
COPY requirements.txt ./requirements.txt



USER appuser

# ── Runtime environment ───────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV STREAM_WIDTH=1280
ENV STREAM_HEIGHT=720
ENV STREAM_FPS=30
ENV STREAM_JPEG_QUALITY=85
ENV MAX_CAMERAS=10
ENV PUSH_FPS=15
ENV PUSH_PATH=/socket.io
ENV PUSH_NAMESPACE=/camera
ENV PUSH_RECONNECT_DELAY=5.0
ENV HEALTH_PORT=9090
ENV LOG_LEVEL=INFO

# DEVICE_ID and PUSH_TARGETS must be provided by the operator at runtime.
# Failing to set them causes main.py to exit immediately with a clear error.

EXPOSE 9090

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "\
import urllib.request; \
urllib.request.urlopen('http://localhost:9090/health', timeout=4)" \
    || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
CMD ["python", "main.py"]
