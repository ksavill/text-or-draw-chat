# PictoChat / Nintendo local-wireless protocol notes

These notes separate the parts confirmed by public Nintendo DS wireless
documentation and working code from the fields that are still only inferred
from captures. There is no official PictoChat protocol specification.

## What “NiFi” means here

PictoChat does not join an Internet access point. One participant acts as a
Nintendo local-multiplayer host: it emits 802.11 management beacons, accepts
authentication/association from nearby consoles, and polls associated clients
in tightly timed slots. A normal web server or normal Wi-Fi socket cannot speak
to stock PictoChat directly.

The DS local-multiplayer mechanism is built on 802.11b, but it has important
Nintendo-specific behavior:

- Host beacons use 1/2 Mbit/s rates and a vendor information element (`0xDD`).
  PictoChat's extension carries the room number and connected-user count.
- Authentication and association use ordinary 802.11 management frames, with
  Nintendo-specific beacon/SSID data.
- Data exchange uses Nintendo CMD/REPLY operation. The host starts a
  contention-free period with a CF-Poll/CMD frame. Clients respond in AID order
  during hardware-timed CF-ACK/REPLY slots, and the host closes the exchange
  with an ACK. Timing and association state matter as much as the payload.
- Nintendo local multiplayer uses channels 1, 7, or 13. The working firmware
  selected for this project is hardcoded to channel 7 and advertises Room B.

The current BlocksDS DSWifi design notes are the clearest maintained overview
of the MAC behavior. GBATEK documents the Nintendo beacon and room fields:

- [BlocksDS DSWifi multiplayer design](https://blocksds.skylyrac.net/dswifi/md_documentation_2library__design.html)
- [GBATEK: Nintendo beacons](https://problemkaputt.de/gbatek-ds-wifi-nintendo-beacons.htm)
- [GBATEK: Nintendo DS Wi-Fi chapters](https://problemkaputt.de/gbatek-contents.htm)

## PictoChat application records

Once the MAC layer has delivered a complete PictoChat transfer, the useful
application data has the following capture-derived shape. The implementation is
in [`pictochat/codec.py`](../pictochat/codec.py); unresolved assumptions are
listed in [`pictochat/ASSUMPTIONS.md`](../pictochat/ASSUMPTIONS.md).

### Identity/profile record

The observed profile record is 84 bytes:

| Offset | Size | Meaning |
|---:|---:|---|
| `0x00` | 1 | magic, observed as `3` |
| `0x01` | 1 | identity subtype (`0` or `1`) |
| `0x02` | 6 | subject MAC, each adjacent byte pair swapped |
| `0x08` | 20 | nickname, fixed UTF-16LE, at most 10 code units |
| `0x1c` | 52 | personal message, fixed UTF-16LE |
| `0x50` | 2 | user colour, little-endian |
| `0x52` | 1 | birth month (capture-derived) |
| `0x53` | 1 | birth day (capture-derived) |

### Drawing record

A reassembled drawing record starts with a 36-byte header:

| Offset | Size | Meaning |
|---:|---:|---|
| `0x00` | 1 | magic `3` |
| `0x01` | 1 | subtype `2` (drawing) |
| `0x02` | 6 | author MAC, adjacent byte pairs swapped |
| `0x08` | 14 | metadata whose individual meanings remain unresolved |
| `0x16` | 14 | zero/safe region in current captures |
| `0x24` | variable | tiled drawing bitmap |

The bitmap is 256 pixels wide and four bits per pixel. It is stored as
row-major 8x8 tiles; within each byte the left pixel is the low nibble and the
right pixel is the high nibble. A message is cropped vertically to 16, 32, 48,
64, or 80 pixels, making each 16-pixel band exactly 2048 bytes. Palette index
zero is paper/transparent; indices 1–15 are ink colours (the original DS uses
black, while DSi can produce coloured/rainbow ink).

The browser composes a 228x80 image. The bridge pads that into the 256-pixel
backing bitmap. Its horizontal placement is configurable with
`PICTOCHAT_X_OFFSET=0..28`; the default of zero must be checked with the first
real-console boundary drawing.

### Transfer framing

Application records larger than one poll are announced and fragmented:

- Type `0`/`1`: a 20-byte transfer announcement. The working firmware models
  the first two body bytes as one little-endian console ID, while the codec
  exposes them as two raw byte-sized fields for lossless capture work; their
  finer semantics are not established. The record also carries total size, a
  `0xffff` marker, and ten capture-dependent bytes.
- Type `2`: a 12-byte header followed by up to 255 payload bytes. It carries a
  sender ID, payload type, byte count, flags, and a 16-bit write offset.
- The working implementation sends 180-byte chunks. Its observed first,
  middle, and final payload types are `0xff`, `0x97`, and `0x04`; flag bit zero
  marks the final fragment.
- Reassembly must be offset-based, tolerate exact retransmissions, and reject
  conflicting overlap or a final fragment that ends at the wrong total size.

Several announcement/metadata bytes are still not understood well enough to
synthesize independently. That is why this project lets the proven radio
firmware own the live transfer state rather than sending raw 802.11 frames from
Python.

## Hardware bridge selected for the first test

[`mjwells2002/pictochat-rs`](https://github.com/mjwells2002/pictochat-rs)
demonstrates all four operations needed for one physical console: Room B
beacons, association, sending a drawing, and receiving a drawing. It runs on an
ESP32-S3 and uses W5500 Ethernet for the ordinary IP side, leaving the ESP32-S3
Wi-Fi radio available for Nintendo local-wireless timing. The project is pinned
at commit
[`5c75302e907148058366ccaef098fb5ac3cf2cd8`](https://github.com/mjwells2002/pictochat-rs/tree/5c75302e907148058366ccaef098fb5ac3cf2cd8).

The firmware exposes `ws://<ethernet-address>:5678/ws`. Binary WebSocket frames
contain compact `rmp-serde` MessagePack values:

```text
STATE   { "STATE": [mac_bin, is_leaving, birth_day, birth_month, name, bio] }
MESSAGE { "MESSAGE": [mac_bin_or_nil, tiled_bitmap_bin] }
```

For example, a host MESSAGE containing bytes `aa bb` is exactly:

```text
81 a7 4d 45 53 53 41 47 45 92 c0 c4 02 aa bb
```

The Python bridge sends only the tiled bitmap in `MESSAGE`. The firmware adds
the 36-byte drawing record header, announces the transfer, fragments it, and
performs the CMD/REPLY radio exchange. In the other direction it reassembles a
console transfer and emits only the bitmap to Python.

The upstream repository has no release artifact, CI workflow, schematic, or
licence file as of July 13, 2026. Its source is therefore not vendored here.
The hardware helper fetches the exact commit for local evaluation and applies a
strictly verified compatibility set: the Cargo manifest typo, stable cursor API
replacement, and safe initialization for two ESP-IDF C out-parameters. The
build helper also applies the upstream-style `c_char` correction to the exact
locked `esp-idf-svc` 0.49.0 source in the local Cargo registry cache. These
edits make the pinned source build with the verified Espressif Rust 1.95.0.0
toolchain without vendoring it or changing its dependency graph. Do not
redistribute a derived firmware image without permission/licensing from the
upstream author.

The newer [`foa_dswifi`](https://github.com/mjwells2002/foa_dswifi) port was
also evaluated. It currently requires an original ESP32 plus a separate
ESP-Hosted coprocessor, contains author-local dependency paths, and has its
DS-to-network message path commented out. It is not the shortest path to the
first bidirectional hardware test.

## Current limits

- One associated physical console.
- Physical PictoChat Room B only (Wi-Fi channel 7).
- Browser Room B is the default mapping; all DS messages appear as the single
  synthetic web user `DS-BRIDGE`.
- Web text works because this UI stamps text into its drawing canvas. Legacy
  JSON text-only messages are intentionally not sent to PictoChat.
- Multi-console host migration, rooms A/C/D, and exact horizontal mask
  calibration remain future hardware work.

Additional reverse-engineering references:

- [Capture-based PictoChat notes by Matthias Hildebrandt](https://www.matthil.de/w/pictochat.html)
- [VSR Wireshark dissector history](https://git.pipeframe.xyz/school/vsr/log/wireshark)
- [Pinned firmware MessagePayload implementation](https://github.com/mjwells2002/pictochat-rs/blob/5c75302e907148058366ccaef098fb5ac3cf2cd8/src/pictochat_app_driver/pictochat_packets.rs)
