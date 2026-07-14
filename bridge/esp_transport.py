"""Transport for the ESP32-S3/W5500 ``pictochat-rs`` firmware.

The firmware uses WebSocket binary frames containing compact rmp-serde values.
In rmp-serde 1.3, enum variants are a one-entry map keyed by the variant name,
while structs use compact positional arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import msgpack
from websockets.asyncio.client import connect


class RadioProtocolError(ValueError):
    """Raised for a malformed or unsupported firmware event."""


@dataclass(frozen=True, slots=True)
class RadioMessage:
    mac_address: bytes | None
    message_content: bytes


@dataclass(frozen=True, slots=True)
class RadioState:
    mac_address: bytes
    is_leaving: bool
    birthday: int
    birthmonth: int
    name: str
    bio: str


RadioEvent = RadioMessage | RadioState


def pack_message_event(message_content: bytes) -> bytes:
    """Encode ``PictochatEventNetwork::MESSAGE`` for rmp-serde."""

    content = bytes(message_content)
    return msgpack.packb({"MESSAGE": [None, content]}, use_bin_type=True)


def _bytes(value: Any, field: str, *, size: int | None = None) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise RadioProtocolError(f"{field} must be MessagePack binary data")
    result = bytes(value)
    if size is not None and len(result) != size:
        raise RadioProtocolError(f"{field} must be exactly {size} bytes")
    return result


def unpack_event(payload: bytes) -> RadioEvent:
    """Decode a compact rmp-serde state or message event."""

    try:
        value = msgpack.unpackb(payload, raw=False, strict_map_key=False)
    except (msgpack.ExtraData, msgpack.FormatError, msgpack.StackError,
            ValueError) as exc:
        raise RadioProtocolError("invalid MessagePack radio event") from exc
    if not isinstance(value, dict) or len(value) != 1:
        raise RadioProtocolError("radio event must be a one-entry enum map")
    variant, body = next(iter(value.items()))
    # rmp-serde 1.3 emits names. Numeric indices are accepted as a compatibility
    # convenience because Serde's derived enum decoder also understands them.
    if variant in (1, "MESSAGE"):
        if not isinstance(body, (list, tuple)) or len(body) != 2:
            raise RadioProtocolError("MESSAGE body must contain MAC and bitmap")
        mac = None if body[0] is None else _bytes(body[0], "MAC", size=6)
        return RadioMessage(mac, _bytes(body[1], "message_content"))
    if variant in (0, "STATE"):
        if not isinstance(body, (list, tuple)) or len(body) != 6:
            raise RadioProtocolError("STATE body must contain six fields")
        mac = _bytes(body[0], "MAC", size=6)
        if not isinstance(body[1], bool):
            raise RadioProtocolError("STATE is_leaving must be boolean")
        if not all(not isinstance(item, bool) and isinstance(item, int)
                   and 0 <= item <= 255
                   for item in body[2:4]):
            raise RadioProtocolError("STATE birthday fields must be bytes")
        if not isinstance(body[4], str) or not isinstance(body[5], str):
            raise RadioProtocolError("STATE name and bio must be strings")
        return RadioState(mac, body[1], body[2], body[3], body[4], body[5])
    raise RadioProtocolError(f"unsupported radio event variant {variant!r}")


class EspRadioConnection:
    """Small adapter over the firmware's binary WebSocket endpoint."""

    def __init__(self, websocket: Any):
        self.websocket = websocket

    @classmethod
    async def open(cls, url: str) -> "EspRadioConnection":
        websocket = await connect(
            url, max_size=32 * 1024, ping_interval=20, ping_timeout=20,
        )
        return cls(websocket)

    async def send_bitmap(self, bitmap: bytes) -> None:
        await self.websocket.send(pack_message_event(bitmap))

    async def receive_event(self) -> RadioEvent:
        payload = await self.websocket.recv()
        if not isinstance(payload, bytes):
            raise RadioProtocolError("firmware sent a non-binary WebSocket frame")
        return unpack_event(payload)

    async def close(self) -> None:
        await self.websocket.close()
