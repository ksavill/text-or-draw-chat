# Architecture

The project separates the browser chat application from the experimental stock
Nintendo DS radio path. The ordinary web application has no dependency on the
hardware bridge.

```text
Browser
  │ HTTP/WebSocket :8082
  ▼
FastAPI backend ─── in-memory rooms A–D
  │ newline-delimited JSON :8083
  ▼
Python bridge
  │ MessagePack WebSocket :5678 over Ethernet
  ▼
ESP32-S3 + W5500
  │ Nintendo local wireless / 802.11b channel 7
  ▼
Stock PictoChat, physical Room B
```

## Repository boundaries

| Path | Responsibility |
|---|---|
| `backend/` | FastAPI room server, TCP adapter, health endpoint, and bundled browser assets. |
| `bridge/` | Runtime adapter between the web-room protocol and ESP firmware protocol. |
| `pictochat/` | Transport-independent capture-derived codecs and reassembly. |
| `hardware/` | Pinned third-party firmware preparation, build, flash, wiring, and physical test instructions. |
| `docs/` | Architecture, protocol research, assumptions, and roadmap. |
| `tests/unit/` | Pure codec, image, transport, and service behavior. |
| `tests/integration/` | FastAPI, WebSocket, and TCP-room integration. |
| `tests/e2e/` | Full socket path with a mock ESP firmware endpoint. |

`server.py` is intentionally a small compatibility launcher. Runtime logic
lives in `backend/app.py`, and console entry points are declared in
`pyproject.toml`.

## Runtime behavior

The browser sends text messages or PNG drawing data URLs to the FastAPI room
server. Browser text is also rendered into the shared drawing canvas, matching
PictoChat's unified compose model.

For physical DS communication, the Python bridge first connects to the ESP
radio endpoint. Only after that succeeds does it join browser Room B with the
explicit `ds-bridge` role. This ordering prevents repeated join/leave events
while hardware is unavailable and gives `/ds-bridge/status` a meaningful
definition: online means the radio-connected bridge is currently present.

The ESP firmware owns raw 802.11 management, association, and timed Nintendo
CMD/REPLY exchanges. Python owns image conversion and application transport;
it does not attempt raw Wi-Fi injection.

## Trust and deployment model

- Browser traffic should use TLS when exposed beyond localhost.
- TCP port 8083 should remain on a trusted LAN.
- The W5500/ESP WebSocket is a local plaintext control channel.
- Room state is process-local, so run one backend process.
- Uploaded drawings are size-checked and decoded with fixed dimensions before
  entering the radio path.

See [pictochat-protocol.md](pictochat-protocol.md) for protocol evidence and
[`../hardware/README.md`](../hardware/README.md) for physical setup.
