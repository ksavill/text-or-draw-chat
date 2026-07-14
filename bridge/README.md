# Real-DS bridge

This process joins the web server's newline-delimited JSON room as `DS-BRIDGE`
and connects to the ESP32-S3/W5500 `pictochat-rs` firmware WebSocket. The ESP
owns raw 802.11b/Nintendo local-multiplayer timing; this process only converts
completed 228x80 PNG drawings to and from PictoChat's 256-wide tiled bitmap.

The first hardware milestone intentionally supports one physical console in
Room B. All DS-originated messages appear in the browser as `DS-BRIDGE`, and
all browser-originated messages appear on the DS as the firmware host.
The bridge connects to the ESP radio before joining the web room; while the ESP
is unavailable, retries do not create `DS-BRIDGE` join/leave notifications.

## Run

Install `requirements.txt`, start `python server.py`, and then run the bridge
with `python -m bridge` (or the installed `pictochat-bridge` command).
The firmware advertises `pictochat_rs.local` over mDNS; set its DHCP address
explicitly if that name does not resolve on your computer:

```powershell
$env:PICTOCHAT_RADIO_HOST = "192.168.1.50" # omit if mDNS works
$env:PICTOCHAT_ROOM = "B"
python -m bridge
```

Settings:

| variable | default | purpose |
|---|---:|---|
| `PICTOCHAT_WEB_HOST` | `127.0.0.1` | FastAPI/TCP adapter host |
| `PICTOCHAT_WEB_PORT` | `8083` | FastAPI/TCP adapter port |
| `PICTOCHAT_RADIO_URL` | derived | complete firmware WebSocket URL |
| `PICTOCHAT_RADIO_HOST` | `pictochat_rs.local` | firmware mDNS name or W5500 DHCP address |
| `PICTOCHAT_RADIO_PORT` | `5678` | firmware HTTP/WebSocket port |
| `PICTOCHAT_ROOM` | `B` | web room mapped to the firmware's fixed physical Room B |
| `PICTOCHAT_BRIDGE_NAME` | `DS-BRIDGE` | single synthetic DS identity |
| `PICTOCHAT_X_OFFSET` | `0` | visible image placement in 256px backing |

`PICTOCHAT_X_OFFSET` remains a calibration value until a generated boundary
test image has been compared on a physical DS.

For the pinned firmware build, GPIO wiring, electrical cautions, DHCP/mDNS
discovery, and the first-console acceptance test, follow
[`hardware/README.md`](../hardware/README.md). Protocol-layer details are in
[`docs/pictochat-protocol.md`](../docs/pictochat-protocol.md).
