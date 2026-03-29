# FusionCameraDataService

> HTTPS REST streaming service for USB/V4L2 cameras — built for digital-twin gateway nodes running Ubuntu 22.04 / 24.04.  
> Every machine runs its own isolated instance identified by a **DEVICE_ID**.  Consumers must send this ID in every request.

---

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Physical node  (Ubuntu 22/24 – K3s worker or bare-metal)        │
│                                                                    │
│   /dev/video0   ──┐                                               │
│   /dev/video1   ──┤   StreamManager   ─── CameraStream threads   │
│   /dev/videoN   ──┘        │                                      │
│                             │  JPEG frame buffer (per camera)     │
│                             ▼                                      │
│              ┌──────────────────────────────┐                     │
│              │  Flask / gunicorn HTTPS app   │                     │
│              │  Port 5443  (TLS 1.2+)        │                     │
│              │                               │                     │
│              │  GET /health                  │ ← k8s liveness     │
│              │  GET /ready                   │ ← k8s readiness    │
│              │  GET /api/v1/{DEVICE_ID}/...  │ ← authenticated    │
│              └──────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────┘
```

Each node's `DEVICE_ID` uniquely identifies its digital-twin. Requests with a wrong ID are rejected with **HTTP 403**.

---

## Project structure

```
fusioncameradataservice/
├── app/
│   ├── __init__.py              Flask app factory
│   ├── config.py                All settings via environment variables
│   ├── middleware/
│   │   └── device_auth.py       @require_device_id decorator
│   ├── routes/
│   │   ├── health.py            /health  /ready  /metrics
│   │   ├── devices.py           /cameras  /cameras/<n>  /streams
│   │   └── stream.py            /cameras/<n>/stream  /snapshot
│   ├── services/
│   │   ├── device_scanner.py    V4L2 / USB camera discovery
│   │   └── stream_manager.py    Thread-safe camera lifecycle + fallback
│   └── utils/
│       ├── fallback.py          "NO SIGNAL" JPEG frame generator
│       └── ssl_utils.py         Self-signed TLS certificate generator
├── main.py                      Entry point (dev + gunicorn WSGI callable)
├── requirements.txt
├── Dockerfile                   Multi-stage production image
├── docker-compose.yml           Local development
├── k3s-deployment.yaml          DaemonSet + Service + ConfigMap
├── .env.example                 Template – copy to .env
└── .gitignore
```

---

## Quick start (local, no Docker)

```bash
# 1. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Set DEVICE_ID (required)
export DEVICE_ID=twin-factory-01

# 3. Start the service
python main.py
```

The service auto-generates a self-signed TLS cert in `./certs/` on first start.  
Open `https://localhost:5443/` — accept the browser cert warning.

---

## Quick start (Docker)

```bash
# 1. Configure
cp .env.example .env
# Edit .env and set DEVICE_ID

# 2. Build and run
docker compose up --build

# 3. Verify
curl -k https://localhost:5443/health
```

### Adding more cameras

Edit `docker-compose.yml` → `services.fusioncamera.devices`:

```yaml
devices:
  - /dev/video0:/dev/video0
  - /dev/video1:/dev/video1
```

---

## API Reference

All authenticated endpoints share the prefix `/api/v1/{device_id}`.  
Replace `{device_id}` with the value of your `DEVICE_ID` environment variable.

### Unauthenticated (no device ID required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe — always 200 |
| GET | `/ready` | Readiness probe — 200 when fully initialised |
| GET | `/metrics` | Process CPU / memory metrics |

### Authenticated (device_id must match DEVICE_ID env var)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/{id}/info` | Service & stream configuration |
| GET | `/api/v1/{id}/cameras` | List all detected USB cameras |
| GET | `/api/v1/{id}/cameras/{n}` | Details for camera at `/dev/videoN` |
| POST | `/api/v1/{id}/cameras/{n}/start` | Start background capture thread |
| POST | `/api/v1/{id}/cameras/{n}/stop` | Stop and release camera |
| **GET** | **`/api/v1/{id}/cameras/{n}/stream`** | **Live MJPEG HD stream** |
| GET | `/api/v1/{id}/cameras/{n}/snapshot` | Single JPEG frame |
| GET | `/api/v1/{id}/streams` | List all active stream threads |

### Stream endpoint details

```
GET https://<nodeIP>:5443/api/v1/twin-factory-01/cameras/0/stream
                                                          └── camera /dev/video0
```

Optional query parameters:

| Param | Default | Description |
|-------|---------|-------------|
| `fps` | `STREAM_FPS` (30) | Requested stream frame rate (capped at configured max) |

The stream uses **MJPEG** (`multipart/x-mixed-replace`) — compatible with:
- Browsers (`<img src="...">`)
- VLC, ffplay: `ffplay -i "https://host:5443/api/v1/DEVICE_ID/cameras/0/stream"`
- OpenCV: `cv2.VideoCapture("https://host:5443/api/v1/DEVICE_ID/cameras/0/stream")`

**Fallback behaviour**: if the camera is disconnected a live animated "NO SIGNAL" frame is served. The stream **never drops** — consumers do not need to reconnect.

---

## Fallback / Fault tolerance

| Scenario | Behaviour |
|----------|-----------|
| Camera never plugged in | NO SIGNAL frame with device ID + error |
| Camera unplugged mid-stream | Fallback frame served; reconnect attempted every `CAMERA_RECONNECT_DELAY` seconds |
| Camera comes back online | Real frames resume automatically in < 2 s |
| Snapshot while offline | Returns fallback JPEG with HTTP 206 + `X-Is-Live: false` header |
| DEVICE_ID missing at start | Process exits immediately with clear error message |
| Wrong DEVICE_ID in request | HTTP 403 with JSON error |

---

## K3s deployment

### Prerequisites

- K3s cluster (v1.28+)
- Image pushed to a registry accessible by your nodes

### Steps

```bash
# 1. Push image
docker build -t <registry>/fusioncameradataservice:latest .
docker push <registry>/fusioncameradataservice:latest

# 2. Edit k3s-deployment.yaml — set your image name:
#    containers[0].image: your-registry/fusioncameradataservice:latest

# 3. Rename nodes to match your digital-twin IDs
#    (nodes become the DEVICE_ID via spec.nodeName)
kubectl label node k3s-node-01 fusion.camera/role=gateway

# 4. Deploy
kubectl apply -f k3s-deployment.yaml

# 5. Check pods
kubectl get pods -n fusioncamera -o wide

# 6. Access stream from any matching node
# DEVICE_ID = node name (check with: kubectl get nodes)
curl -k https://<nodeIP>:5443/api/v1/<nodeName>/cameras
```

### Per-node DEVICE_ID override

If you need a DEVICE_ID different from the node name, patch the DaemonSet:

```bash
kubectl -n fusioncamera patch daemonset fusioncamera \
  --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/env/1/value","value":"my-custom-id"}]'
```

Or use a node label annotation and a mutating webhook (advanced).

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVICE_ID` | **required** | Unique gateway identity |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `5443` | HTTPS port |
| `DEBUG` | `false` | Flask debug mode |
| `SSL_ENABLED` | `true` | Enable HTTPS |
| `SSL_SELF_SIGNED` | `true` | Auto-generate self-signed cert |
| `SSL_CERT_DAYS` | `3650` | Auto-cert validity (days) |
| `SSL_CERT_PATH` | `/app/certs/cert.pem` | Path to TLS certificate |
| `SSL_KEY_PATH` | `/app/certs/key.pem` | Path to private key |
| `STREAM_WIDTH` | `1280` | Requested capture width (HD) |
| `STREAM_HEIGHT` | `720` | Requested capture height (HD) |
| `STREAM_FPS` | `30` | Max stream frame rate |
| `STREAM_JPEG_QUALITY` | `85` | JPEG quality (1–100) |
| `MAX_CAMERAS` | `10` | Number of /dev/videoN to scan |
| `CAMERA_RECONNECT_DELAY` | `2.0` | Seconds between reconnect attempts |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Security notes

- Device ID validation uses `hmac.compare_digest` (constant-time) to prevent timing oracle attacks.
- Private key files are created with `chmod 600`.
- The container runs as a non-root user in the `video` group (UID 1001, GID 44) — `privileged: true` is only needed for raw USB device access on K3s; remove it if you mount specific devices instead.
- TLS 1.2 minimum enforced; TLS 1.0/1.1 disabled.
- `no-new-privileges` security option applied in Docker Compose.
