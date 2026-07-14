# Roadmap

This roadmap tracks product-level work. Historical implementation notes belong
in commit and pull-request history rather than in this document.

## Shipped

- Dual-screen browser UI with a unified 228×80 text-and-drawing canvas.
- Four in-memory chat rooms with capacity, nickname, payload, and rate limits.
- WebSocket browser transport and a LAN-only newline-delimited JSON adapter.
- PWA metadata, local font/audio assets, and container deployment.
- Capture-derived PictoChat profile, drawing, bitmap, fragment, and reassembly
  codecs.
- Bidirectional browser/ESP bridge with image conversion, reconnect handling,
  retransmission deduplication, and echo suppression.
- Landing-page DS hardware health indicator.
- Pinned ESP32-S3/W5500 firmware prepare, build, flash, and acceptance workflow.
- Unit, integration, and socket-level end-to-end test suites.

## Next milestone

Run and document the first physical test with:

- One ESP32-S3 N16/N16R8 development board.
- One correctly wired W5500/WIZ850io Ethernet module.
- One stock Nintendo DS or DS Lite in PictoChat Room B.

The acceptance criteria are browser-to-DS and DS-to-browser drawing delivery,
no feedback loop, reconnection after interruption, and a recorded horizontal
image offset for the tested console.

## Follow-up work

- Calibrate and lock the 228-pixel canvas placement within the 256-pixel radio
  backing bitmap.
- Add structured bridge diagnostics beyond the current online/offline signal.
- Evaluate multiple physical consoles and host migration.
- Investigate physical rooms A, C, and D; the current firmware is fixed to Room
  B on channel 7.
- Expand DSi colour/palette validation.
- Add a persistent room backend only if multi-process deployment becomes a real
  requirement.

## Current constraints

- Stock-DS communication requires the external ESP32-S3/W5500 radio bridge;
  normal PC Wi-Fi adapters are not supported.
- Only one physical DS and physical Room B are supported by the selected
  firmware.
- Rooms and participants live in one server process.
- Port 8083 and the ESP WebSocket transport are plaintext and intended for a
  trusted LAN.
- The selected third-party firmware has no upstream licence file and must not
  be redistributed without permission.
