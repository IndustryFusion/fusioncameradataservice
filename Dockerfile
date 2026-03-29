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

# System packages required by OpenCV and cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgl1 \
        libffi-dev \
        libssl-dev \
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

# Certificate directory (mounted by docker / k3s or auto-generated at first start)
RUN mkdir -p /app/certs && chown appuser:video /app/certs
VOLUME ["/app/certs"]

USER appuser

# ── Runtime environment ───────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=5443
ENV HOST=0.0.0.0
ENV SSL_ENABLED=true
ENV SSL_SELF_SIGNED=true
ENV SSL_CERT_PATH=/app/certs/cert.pem
ENV SSL_KEY_PATH=/app/certs/key.pem
ENV STREAM_WIDTH=1280
ENV STREAM_HEIGHT=720
ENV STREAM_FPS=30
ENV STREAM_JPEG_QUALITY=85
ENV MAX_CAMERAS=10
ENV LOG_LEVEL=INFO

# DEVICE_ID must be provided by the operator at runtime — no default.
# Failing to set it will cause main.py to exit immediately with a clear error.

EXPOSE 5443

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "\
import urllib.request, ssl; \
ctx = ssl.create_default_context(); \
ctx.check_hostname = False; \
ctx.verify_mode = ssl.CERT_NONE; \
urllib.request.urlopen('https://localhost:5443/health', context=ctx, timeout=4)" \
    || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Use gunicorn in production for multi-threaded request handling.
# --threads 4 allows concurrent MJPEG streams + REST calls per worker.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5443", \
     "--workers", "1", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--keyfile",  "/app/certs/key.pem", \
     "--certfile", "/app/certs/cert.pem", \
     "--access-logfile", "-", \
     "--error-logfile",  "-", \
     "--log-level", "info", \
     "main:application"]
