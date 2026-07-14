# First real-DS test: ESP32-S3 + W5500

This is the supported first hardware target: one stock Nintendo DS-family
console connected to the pinned `pictochat-rs` host in physical PictoChat Room
B. The W5500 puts that host on the same normal Ethernet LAN as this web app.

The upstream firmware has demonstrated Room B beacons, one-console association,
and drawing transfer in both directions. It is experimental: there is no
published board schematic, release binary, CI build, or licence. This project
therefore fetches the exact source commit for local evaluation instead of
vendoring or redistributing it.

## Hardware

- ESP32-S3 board with **16 MB QIO flash** and the GPIOs below exposed. An
  ESP32-S3-DevKitC-1 with an N16 or N16R8 module is the least surprising choice;
  PSRAM is not required by this build.
- W5500 Ethernet module exposing SPI, active-low reset, and interrupt.
  [WIZ850io](https://docs.wiznet.io/Product/ioModule/WIZ850io) is one known
  3.3 V option. Generic module power inputs are not standardized.
- USB data cable, Ethernet cable to a DHCP-enabled LAN, and short jumper wires.
- A Nintendo DS/DS Lite for the lowest-risk first attempt. DSi PictoChat uses
  the same family of protocol but coloured-ink behavior is a separate variable.

Do **not** assume a W5500 module's `VCC` pin accepts 3.3 V or 5 V: use its own
datasheet/schematic. All SPI logic connected to the ESP32-S3 must be 3.3 V, and
the grounds must be common. A WIZ850io can draw roughly 140 mA, so confirm that
the dev board's 3.3 V regulator has enough headroom. Keep the initial SPI wiring
short because the firmware drives it at 40 MHz.

The firmware pinout is hardcoded in its
[`main.rs`](https://github.com/mjwells2002/pictochat-rs/blob/5c75302e907148058366ccaef098fb5ac3cf2cd8/src/main.rs#L220-L275):

| ESP32-S3 | Direction | W5500 |
|---:|:---:|---|
| GPIO 12 | → | SCLK/SCK |
| GPIO 11 | → | MOSI |
| GPIO 13 | ← | MISO |
| GPIO 15 | → | CS/SCSn |
| GPIO 4 | → | RSTn/RESET |
| GPIO 5 | ← | INTn/INT |
| GND | — | GND |

An all-in-one ESP32-S3 Ethernet board is only suitable if its W5500 is wired to
those exact pins. For example, the common Waveshare ESP32-S3-ETH pin mapping is
different and is not a drop-in target for this unmodified firmware.

## 1. Install the Windows build tools

Install Git, Python, and the standard Rust toolchain first. Then install the
official Espressif Rust tools in PowerShell:

```powershell
cargo install espup --locked
espup install --name esp --toolchain-version 1.95.0.0 --targets esp32s3 --std
cargo install ldproxy --locked
cargo install espflash --locked
```

The compiler pin is intentional: Espressif Rust 1.95.0.0 is the exact Xtensa
toolchain used for the verified release build. This August 2024 firmware also
depends on `esp-idf-svc` 0.49.0, whose hardcoded Rust `i8` C-character pointers
fail with current bindgen output ([esp-rs issue #570](https://github.com/esp-rs/esp-idf-svc/issues/570)).
The build helper applies the corresponding `c_char` fix to that exact crate in
the local Cargo registry cache after verifying the complete affected source
files; it does not vendor or alter the dependency graph. On Windows, open a new
PowerShell window after `espup` completes. `rustc +esp --version` should print
`rustc 1.95.0-nightly (95e5bda86 2026-04-15) (1.95.0.0)`. The helper checks
that full release identity. Confirm that `cargo`, `ldproxy`, and `espflash` are
all on `PATH`.

The ESP-IDF build rejects long Windows project/output paths, and its compiler
probe can lose Newlib headers when the tools path is long. Keep the firmware
checkout at `C:\pcx`; the provided build script also pins downloaded ESP-IDF
tools under `C:\e`. Both locations can be overridden, but keep them short.

## 2. Fetch and build the pinned firmware

From this repository:

```powershell
.\hardware\prepare-pictochat-rs.ps1
.\hardware\build-pictochat-rs.ps1
```

The prepare script fetches commit
`5c75302e907148058366ccaef098fb5ac3cf2cd8` and applies a reviewed compatibility
set: the manifest typo (`verison` → `version`), removal of the obsolete
`cursor_remaining` feature gate, a stable replacement for `remaining_slice()`,
and safe initialization of two ESP-IDF C out-parameters that the 1.95 compiler
correctly rejects as null-pointer dereferences. It accepts only the exact
original/patched forms and refuses unrelated, staged, or untracked changes. A
rerun also recognizes the one exact v2 `components_esp32s3.lock` form generated
by ESP-IDF's component manager; arbitrary lock edits still fail. The source
repository has no licence file, so keep the checkout and derived binary local
unless the author grants redistribution permission.

The build helper runs `cargo +esp fetch --locked`, verifies and patches only the
known `esp-idf-svc` 0.49.0 C-character incompatibility in the local registry,
then runs:

```text
cargo +esp build --release --locked --features use-embuild
```

It explicitly uses the pinned `sdkconfig.defaults`. The helper rejects
`CARGO_TARGET_DIR` and any other Cargo target-directory override because the
flash helper intentionally reads the release ELF from `$Source\target` (by
default `C:\pcx\target`); this prevents an older image at that path from being
flashed by mistake. The first
build downloads and compiles ESP-IDF 5.1.4 dependencies and can take several
minutes. If `C:\e` is unavailable, select another short root explicitly:

```powershell
.\hardware\build-pictochat-rs.ps1 -EspIdfToolsDir G:\e
```

## 3. Wire, flash, and monitor

Disconnect USB power while wiring. Re-check `VCC`, ground, and every signal,
then connect Ethernet and USB. The pinned
[`espflash.toml`](https://github.com/mjwells2002/pictochat-rs/blob/5c75302e907148058366ccaef098fb5ac3cf2cd8/espflash.toml)
declares QIO, 16 MB, and 80 MHz. Flash the release ELF (supply `-Port COMx`
when more than one serial device is attached):

```powershell
.\hardware\flash-pictochat-rs.ps1 -Port COM5
```

Leave the serial monitor open. A useful boot sequence includes:

```text
Ethernet Link Up!
Got IP!
Started SoftAP
Ready!
```

The firmware currently logs only `Got IP!`, not the numeric DHCP address. It
advertises the mDNS hostname `pictochat_rs.local` and service
`_pictochat._tcp` on port 5678. Try:

```powershell
Resolve-DnsName pictochat_rs.local
Test-NetConnection pictochat_rs.local -Port 5678
```

If mDNS is unavailable, find the ESP32-S3/W5500 lease in the router's DHCP
client list and use that address below.

## 4. Start the app and bridge

In PowerShell window 1:

```powershell
python -m pip install -r requirements.txt
python server.py
```

Open `http://localhost:8082`, choose a nickname other than `DS-BRIDGE`, and
join web Room B.

In PowerShell window 2:

```powershell
$env:PICTOCHAT_RADIO_HOST = "pictochat_rs.local" # or the DHCP address
$env:PICTOCHAT_ROOM = "B"
python -m bridge
```

Do not continue until the bridge logs both the web-room and DS-radio
connections. If port 5678 is closed, solve Ethernet/DHCP before debugging RF.

## 5. Join from the physical DS

1. Keep the DS close to the ESP32-S3 for the first attempt and away from a busy
   2.4 GHz access point if possible.
2. Launch stock PictoChat only after the firmware has printed `Ready!`.
3. Enter **Room B**. If PictoChat was already scanning, return to room select
   and enter B again.
4. The serial log should show the association, and the Python bridge should log
   `DS joined: <profile name> (<MAC>)`.

The firmware is fixed to physical Room B on Wi-Fi channel 7 regardless of the
web server's other rooms. Only one physical console is supported in this test.

## 6. Bidirectional acceptance test

- In the browser, draw a short black mark near the center and send it. Expect
  `web -> DS: 2048 bytes ...` (or a larger multiple of 2048 if ink is lower on
  the canvas), followed by the drawing appearing in PictoChat.
- On the DS, draw a different mark and send it. Expect `DS -> web: ... bytes`,
  followed by a `DS-BRIDGE` drawing in the browser.
- Send one message each way again. There should be one copy, not a feedback
  loop, and the bridge should stay connected.
- Finally draw marks at the browser canvas's left and right boundaries. If they
  are horizontally displaced or clipped in a way that is not PictoChat's own UI
  mask, retry with `PICTOCHAT_X_OFFSET` from 0 through 28 and record the correct
  value for the target console.

Passing those checks is the first real-hardware milestone. A failure should be
classified by the last successful boundary: serial boot, Ethernet port 5678,
Room B visibility/association, web→DS, or DS→web. Capture the complete serial
and bridge logs before resetting anything.

The protocol rationale and remaining unknown fields are documented in
[`docs/pictochat-protocol.md`](../docs/pictochat-protocol.md).
