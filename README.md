# PictoChat (web)

A Nintendo DS **PictoChat** recreation in the browser: two DS screens, four chat rooms of
16 users, and the real PictoChat mechanic — text and drawing share one 228×80 message
canvas. Typed characters are stamped into the bitmap next to your pen strokes and the whole
box is sent as a single message. FastAPI + WebSockets power the backend, with the browser
markup, styles, scripts, and licensed assets bundled alongside it.

## Run

```powershell
python -m pip install -r requirements.txt
python server.py
```

Open http://localhost:8082 — enter a name, pick a room. Type with your keyboard or the
on-screen one (5 tabs: letters / accents / symbols / kana / pictograms), draw with the
mouse or touch. Enter sends, Shift+Enter starts a new line.

Run it with a **single** server process — rooms live in process memory.

Installing the project also provides `pictochat-server` and
`pictochat-bridge` console commands. Or run it with Docker:

```powershell
docker compose up --build
```

Port 8082 serves the web app; port 8083 is a LAN-only raw-TCP adapter
(newline-delimited JSON) used by the real-DS radio bridge and available to homebrew
clients. First line joins
(`{"room": "A", "name": "kevin"}`), then the same payloads as the WebSocket protocol, one
per line; every room broadcast arrives as a JSON line. For internet deployment, put a
reverse proxy (Caddy/nginx) with TLS in front of 8082 — the client upgrades to `wss://`
automatically — and expose 8083 only on networks you trust (it is plaintext by design).

## Real Nintendo DS bridge

The first physical-hardware path uses an ESP32-S3 as the Nintendo
local-multiplayer/“NiFi” host and a W5500 Ethernet module for the normal LAN side. It
supports one stock console in physical PictoChat Room B and transfers drawings in both
directions. Start with the exact parts, wiring, build, and acceptance checklist in
[`hardware/README.md`](hardware/README.md).

### Hardware required for a stock DS

> [!IMPORTANT]
> A normal PC Wi-Fi adapter or antenna cannot host stock PictoChat. Nintendo local
> wireless needs raw 802.11b management frames and hardware-timed CMD/REPLY polling that
> normal Windows Wi-Fi drivers do not expose.

True two-way communication with an unmodified Nintendo DS requires:

- An **ESP32-S3 board with 16 MB QIO flash**, preferably an ESP32-S3-DevKitC-1 using an
  N16 or N16R8 module.
- A separate **W5500 Ethernet module** with SPI, reset, and interrupt exposed; WIZ850io is
  the documented 3.3 V reference option.
- Short jumper wires, a USB data cable, Ethernet to a DHCP-enabled LAN, and one stock
  DS/DS Lite.

The ESP32-S3 Wi-Fi radio speaks Nintendo local wireless while the W5500 connects the
bridge to the normal LAN. An all-in-one Ethernet board is not automatically compatible:
its W5500 must use the exact GPIO mapping listed in
[`hardware/README.md`](hardware/README.md). The landing page reports **DS HARDWARE:
ONLINE** only after this radio endpoint is reachable and the bridge has joined Room B.

### First-time firmware setup

After wiring the supported ESP32-S3 and W5500 hardware, prepare, build, and flash the
pinned radio firmware from PowerShell:

```powershell
.\hardware\prepare-pictochat-rs.ps1
.\hardware\build-pictochat-rs.ps1
.\hardware\flash-pictochat-rs.ps1 -Port COM5 # replace COM5 with the board's port
```

The flash command leaves a serial monitor open. Before testing, confirm that the firmware
reaches `Ethernet Link Up!`, `Got IP!`, `Started SoftAP`, and `Ready!`. The firmware uses
the W5500 for ordinary LAN traffic and reserves the ESP32-S3 Wi-Fi radio for Nintendo
local wireless on channel 7.

### Start NiFi/PictoChat interoperability

Use this order each time you want a stock DS to participate:

1. Power the flashed ESP32-S3, connect its W5500 to the LAN, and wait for `Ready!` in the
   serial monitor. It normally appears as `pictochat_rs.local`; otherwise find its address
   in the router's DHCP client list.
2. Start the web server in PowerShell window 1:

   ```powershell
   python -m pip install -r requirements.txt # first run, or after dependencies change
   python server.py
   ```

3. Open <http://localhost:8082>, choose a name other than `DS-BRIDGE`, and join **Room B**.
4. Start the radio bridge in PowerShell window 2:

   ```powershell
   $env:PICTOCHAT_RADIO_HOST = "pictochat_rs.local" # or its W5500 DHCP address
   $env:PICTOCHAT_ROOM = "B"
   python -m bridge
   ```

5. Wait until the bridge reports both the web-room and radio connections. Then open stock
   PictoChat on one DS and enter **Room B**. A successful association appears in the bridge
   log as `DS joined: <name>`. If the ESP is unavailable, the bridge retries the radio
   connection without joining Room B, so it does not generate repeated join/leave events.
6. Send one browser drawing and one DS drawing to verify both directions. DS-originated
   drawings appear in the browser as `DS-BRIDGE`.

Stop the bridge and server with `Ctrl+C`; the ESP32-S3 can then be unplugged or reset.
This workflow does not change the computer's network adapter, routes, DNS, or DHCP. It
only opens local ports 8082/8083 and connects to the ESP on port 5678. The hidden DS radio
network has its DHCP server disabled, though channel-7 activity can add minor 2.4 GHz
congestion if the normal Wi-Fi network uses an overlapping channel.

The first hardware milestone supports one physical console and Room B only. Protocol
research, confirmed record layouts, and unresolved fields are documented in
[`docs/pictochat-protocol.md`](docs/pictochat-protocol.md).

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Protocol

Client → server: `{"type": "message", "message": str}` or `{"type": "drawing", "data": dataURL}`.

Server → clients (all JSON):

| type      | fields                                    |
|-----------|-------------------------------------------|
| `system`  | `event` (`join`/`leave`), `name`, `color` (palette index, join only), `users` |
| `message` | `sender`, `color`, `message`               |
| `drawing` | `sender`, `color`, `data` (PNG data URL)   |
| `error`   | `reason` (sent only to the offender)       |

Rejections close the socket with an application code: `4001` invalid room, `4002` room
full, `4003` bad/duplicate nickname — the reason string is shown on the room-select screen.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI application and its bundled HTML/PWA/audio/font/icon assets. |
| `bridge/` | Web-room ↔ ESP32 MessagePack bridge and bitmap conversion. |
| `pictochat/` | Capture-derived records, tiled bitmap codec, and reassembly. |
| `hardware/` | Pinned firmware helpers, wiring, and the physical acceptance checklist. |
| `docs/` | [Architecture](docs/architecture.md), [protocol research](docs/pictochat-protocol.md), and [roadmap](docs/roadmap.md). |
| `tests/` | Unit, integration, and socket-level end-to-end suites. |
| `server.py` | Small compatibility launcher; the implementation is `backend/app.py`. |
| `pyproject.toml` | Canonical dependencies, packaging rules, and console entry points. |

Development and review expectations are in [CONTRIBUTING.md](CONTRIBUTING.md).

The UI is tuned against a real PictoChat screenshot: light screens, striped PICTOCHAT
header, black event ticker, white message panels with the sender's colour, a left tool
rail, and a flat keyboard with a wide blue ENTER.
