# FusionCameraDataService

> Push-mode USB/V4L2 camera gateway for digital-twin nodes.  
> Captures video from local cameras and **pushes** JPEG frames to one or more NestJS socket.io servers over a persistent connection.  Consumers connect to those NestJS servers — never to this process.

---

## Architecture overview

```
  ┌───────────────────────────────────────────┐
  │  Gateway node  (Linux – K3s / bare-metal)  │
  │                                            │
  │  /dev/video0  ──┐                          │
  │  /dev/video1  ──┤  StreamManager           │
  │  /dev/videoN  ──┘  (capture threads)       │
  │                       │                    │
  │                       │ JPEG frame buffer  │
  │                       ▼                    │
  │              ┌──────────────────┐          │
  │              │  StreamPusher(s) │          │
  │              │  socket.io client│          │
  │              └────────┬─────────┘          │
  │                       │  push camera:frame │
  │  :9090  /health ◄──── │                    │
  │         /ready        │                    │
  └───────────────────────┼────────────────────┘
                          │
          ┌───────────────┼──────────────────┐
          ▼               ▼                  ▼
   NestJS server 1  NestJS server 2   NestJS server N
   (PUSH_TARGETS)   (PUSH_TARGETS)    (PUSH_TARGETS)
          │
   consumers connect here
   (browser / mobile / dashboard)
```

The gateway **never waits for a consumer**.  It establishes outbound socket.io connections to all configured NestJS servers and continuously pushes every frame.  NestJS buffers the latest frame and serves it to all subscribed consumers.

Each pushed frame identifies itself with `deviceId` (from `DEVICE_ID` env) so a single NestJS server can receive from multiple gateways and route streams by ID.

---

## Project structure

```
fusioncameradataservice/
├── app/
│   ├── __init__.py
│   ├── config.py                    All settings via environment variables
│   ├── services/
│   │   ├── device_scanner.py        V4L2 / USB camera discovery
│   │   ├── stream_manager.py        Thread-safe camera capture + fallback frames
│   │   ├── stream_pusher.py         socket.io push client (one per target URL)
│   │   └── health_server.py         Lightweight HTTP health server (:9090)
│   └── utils/
│       └── fallback.py              "NO SIGNAL" JPEG frame generator
├── main.py                          Entry point
├── requirements.txt
├── Dockerfile                       Multi-stage production image
├── docker-compose.yml
├── k3s-deployment.yaml              DaemonSet + ConfigMap + Secret refs
├── .env.example                     Copy to .env and configure
└── README.md
```

---

## Quick start (local, no Docker)

```bash
# 1. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Required environment variables
export DEVICE_ID=twin-factory-01
export PUSH_TARGETS=http://your-nestjs-server:3000

# 3. Start
python3 main.py
```

---

## Quick start (Docker)

```bash
cp .env.example .env
# Edit .env — set DEVICE_ID and PUSH_TARGETS at minimum

docker compose up --build
```

---

## Configuration reference

### Required

| Variable | Description |
|----------|-------------|
| `DEVICE_ID` | Unique identifier for this gateway node — sent with every pushed frame |
| `PUSH_TARGETS` | Comma-separated NestJS base URLs — e.g. `http://nest1:3000,http://nest2:3000` |

### Push

| Variable | Default | Description |
|----------|---------|-------------|
| `PUSH_PATH` | `/socket.io` | socket.io server path on the NestJS side |
| `PUSH_NAMESPACE` | `/camera` | socket.io namespace (must match `@WebSocketGateway`) |
| `PUSH_FPS` | `15` | Frames per second pushed to each target |
| `PUSH_SECRET` | _(empty)_ | Shared secret sent in socket.io `auth` — NestJS validates in `handleConnection` |
| `PUSH_RECONNECT_DELAY` | `5.0` | Seconds between reconnect attempts per target |

### Camera selection

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMERA_INDICES` | _(empty)_ | Comma-separated indices to push (e.g. `0,1`). Empty = auto-scan all |

### Camera capture

| Variable | Default | Description |
|----------|---------|-------------|
| `STREAM_WIDTH` | `1280` | Requested capture width |
| `STREAM_HEIGHT` | `720` | Requested capture height |
| `STREAM_FPS` | `30` | Internal capture loop rate |
| `STREAM_JPEG_QUALITY` | `85` | JPEG quality (1–100) |
| `MAX_CAMERAS` | `10` | Max `/dev/videoN` indices to probe |
| `CAMERA_RECONNECT_DELAY` | `2.0` | Seconds between camera reconnect attempts |

### Health server

| Variable | Default | Description |
|----------|---------|-------------|
| `HEALTH_PORT` | `9090` | Port for `/health`, `/ready`, `/metrics` |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |

---

## Health endpoints

The internal HTTP server (`:9090`) is for k3s probes only — no video traffic.

| Path | Status | Description |
|------|--------|-------------|
| `GET /health` | 200 | Always alive while the process is running |
| `GET /ready` | 200 / 503 | 200 when ≥1 camera is actively capturing |
| `GET /metrics` | 200 | CPU / memory snapshot |

---

## Fault tolerance

| Scenario | Behaviour |
|----------|-----------|
| Camera unplugged mid-run | Fallback "NO SIGNAL" frames pushed; reconnect every `CAMERA_RECONNECT_DELAY` s |
| Camera comes back | Real frames resume automatically |
| NestJS server unreachable | Pusher retries every `PUSH_RECONNECT_DELAY` s — no frames dropped on other targets |
| NestJS server restarts | socket.io auto-reconnect re-establishes within seconds |
| `DEVICE_ID` / `PUSH_TARGETS` missing | Process exits immediately with a descriptive error |

---

## K3s deployment

```bash
# 1. Build and push image
docker build -t <registry>/fusioncameradataservice:latest .
docker push <registry>/fusioncameradataservice:latest

# 2. Create the identity + push credentials Secret
#    Every key in this Secret becomes an env var inside the pod.
kubectl -n fusioncamera create secret generic fusioncamera-identity \
  --from-literal=DEVICE_ID=twin-factory-01 \
  --from-literal=PUSH_TARGETS=http://nest1:3000,http://nest2:3000 \
  --from-literal=PUSH_SECRET=your-shared-secret

# 3. Set your image name in k3s-deployment.yaml, then apply
kubectl apply -f k3s-deployment.yaml

# 4. Verify
kubectl get pods -n fusioncamera -o wide
kubectl logs -n fusioncamera -l app=fusioncamera --tail=40
```

`DEVICE_ID` is set explicitly per deployment via the `fusioncamera-identity` Secret — not derived from node names.  To run multiple gateway nodes pointing at different cameras, create a separate namespace + Secret per node.

---

## NestJS receiver — integration guide

The gateway pushes frames as socket.io events.  Your NestJS server must implement the following contract.

### Install dependencies

```bash
npm install @nestjs/websockets @nestjs/platform-socket.io socket.io
```

### 1 — WebSocket Gateway

```typescript
// camera.gateway.ts
import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  MessageBody,
  ConnectedSocket,
  OnGatewayConnection,
  OnGatewayDisconnect,
} from '@nestjs/websockets';
import { Server, Socket } from 'socket.io';

@WebSocketGateway({
  namespace: '/camera',       // must match PUSH_NAMESPACE (default: /camera)
  cors: { origin: '*' },      // restrict in production
})
export class CameraGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer()
  server: Server;

  // Latest frame per device+camera, keyed as "deviceId:cameraIndex"
  private frames = new Map<string, Buffer>();

  // ── Incoming connection from gateway ──────────────────────────────────────

  handleConnection(client: Socket) {
    const { deviceId, secret } = client.handshake.auth as {
      deviceId?: string;
      secret?: string;
    };

    // Validate shared secret (compare with your PUSH_SECRET env var)
    const expected = process.env.CAMERA_PUSH_SECRET ?? '';
    if (expected && secret !== expected) {
      client.disconnect(true);
      return;
    }

    if (!deviceId) {
      client.disconnect(true);
      return;
    }

    client.data.deviceId = deviceId;
    console.log(`[CameraGateway] gateway connected: ${deviceId}`);
  }

  handleDisconnect(client: Socket) {
    console.log(`[CameraGateway] gateway disconnected: ${client.data.deviceId}`);
  }

  // ── Receive pushed frame ───────────────────────────────────────────────────

  @SubscribeMessage('camera:frame')
  handleFrame(
    @MessageBody()
    data: {
      deviceId: string;    // gateway DEVICE_ID
      cameraIndex: number; // /dev/videoN index
      timestamp: number;   // Unix epoch (seconds, float)
      jpeg: Buffer;        // raw JPEG bytes
    },
    @ConnectedSocket() client: Socket,
  ) {
    const key = `${data.deviceId}:${data.cameraIndex}`;
    this.frames.set(key, Buffer.from(data.jpeg));

    // Broadcast the latest frame to all subscribed consumers in the room
    this.server.to(key).emit('camera:frame', {
      deviceId: data.deviceId,
      cameraIndex: data.cameraIndex,
      timestamp: data.timestamp,
      jpeg: data.jpeg,
    });
  }

  // ── Consumer subscription (browser / app joins a room) ────────────────────

  @SubscribeMessage('camera:subscribe')
  handleSubscribe(
    @MessageBody() data: { deviceId: string; cameraIndex: number },
    @ConnectedSocket() client: Socket,
  ) {
    const key = `${data.deviceId}:${data.cameraIndex}`;
    client.join(key);

    // Send the latest buffered frame immediately so the consumer doesn't
    // need to wait for the next push cycle
    const latest = this.frames.get(key);
    if (latest) {
      client.emit('camera:frame', {
        deviceId: data.deviceId,
        cameraIndex: data.cameraIndex,
        timestamp: Date.now() / 1000,
        jpeg: latest,
      });
    }
  }

  @SubscribeMessage('camera:unsubscribe')
  handleUnsubscribe(
    @MessageBody() data: { deviceId: string; cameraIndex: number },
    @ConnectedSocket() client: Socket,
  ) {
    client.leave(`${data.deviceId}:${data.cameraIndex}`);
  }
}
```

### 2 — Register the gateway in your module

```typescript
// camera.module.ts
import { Module } from '@nestjs/common';
import { CameraGateway } from './camera.gateway';

@Module({
  providers: [CameraGateway],
})
export class CameraModule {}
```

```typescript
// app.module.ts
import { Module } from '@nestjs/common';
import { CameraModule } from './camera/camera.module';

@Module({
  imports: [CameraModule],
})
export class AppModule {}
```

### 3 — HTTP snapshot endpoint (optional)

Expose the latest buffered frame as a plain JPEG over HTTP so browser `<img>` tags and REST clients can consume it without a socket.io library:

```typescript
// camera.controller.ts
import { Controller, Get, Param, Res, NotFoundException } from '@nestjs/common';
import { Response } from 'express';
import { CameraGateway } from './camera.gateway';

@Controller('cameras')
export class CameraController {
  constructor(private readonly gateway: CameraGateway) {}

  @Get(':deviceId/:cameraIndex/snapshot')
  snapshot(
    @Param('deviceId') deviceId: string,
    @Param('cameraIndex') cameraIndex: string,
    @Res() res: Response,
  ) {
    const key = `${deviceId}:${cameraIndex}`;
    const frame = this.gateway.frames.get(key);
    if (!frame) throw new NotFoundException('No frame available yet');

    res.set({
      'Content-Type': 'image/jpeg',
      'Content-Length': frame.length,
      'Cache-Control': 'no-cache',
    });
    res.send(frame);
  }
}
```

> Make `frames` public (or expose it via a service) for the controller to access it.

### 4 — Browser consumer example

```typescript
import { io } from 'socket.io-client';

const socket = io('http://your-nestjs-server:3000/camera');

socket.emit('camera:subscribe', { deviceId: 'twin-factory-01', cameraIndex: 0 });

socket.on('camera:frame', ({ jpeg }: { jpeg: ArrayBuffer }) => {
  const blob = new Blob([jpeg], { type: 'image/jpeg' });
  const url  = URL.createObjectURL(blob);
  const img  = document.getElementById('camera') as HTMLImageElement;
  const prev = img.src;
  img.src = url;
  if (prev) URL.revokeObjectURL(prev);
});
```

---

## Security notes

- `PUSH_SECRET` is validated by NestJS in `handleConnection` via constant-time comparison — always set it in production and store it in a Kubernetes Secret (not a ConfigMap).
- The gateway makes **outbound-only** connections — no inbound ports need to be opened on the gateway node besides the internal health port.
- The container runs as a non-root user in the `video` group (UID 1001, GID 44). `privileged: true` in K3s is needed only for raw USB device access; replace with an explicit device list for hardened nodes.
- `no-new-privileges` security option is set in Docker Compose.

