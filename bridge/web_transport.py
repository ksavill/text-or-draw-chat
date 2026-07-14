"""Newline-delimited JSON client for this project's port-8083 chat bus."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class WebChatProtocolError(ValueError):
    """Raised when the local chat server sends an invalid bridge event."""


class WebChatConnection:
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        *, bridge_name: str,
    ):
        self.reader = reader
        self.writer = writer
        self.bridge_name = bridge_name
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls, host: str, port: int, *, room: str, bridge_name: str,
    ) -> "WebChatConnection":
        reader, writer = await asyncio.open_connection(host, port, limit=256 * 1024)
        connection = cls(reader, writer, bridge_name=bridge_name)
        await connection._send({
            "room": room, "name": bridge_name, "role": "ds-bridge",
        })
        first = await connection.receive_event()
        if first.get("type") == "close":
            await connection.close()
            raise WebChatProtocolError(first.get("reason", "bridge was rejected"))
        if not (first.get("type") == "system" and first.get("event") == "join"
                and first.get("name") == bridge_name):
            await connection.close()
            raise WebChatProtocolError("chat server did not acknowledge bridge join")
        return connection

    async def _send(self, value: dict[str, Any]) -> None:
        encoded = (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            self.writer.write(encoded)
            await self.writer.drain()

    async def send_drawing(self, data_url: str) -> None:
        await self._send({"type": "drawing", "data": data_url})

    async def receive_event(self) -> dict[str, Any]:
        line = await self.reader.readline()
        if not line:
            raise ConnectionError("chat server closed the bridge connection")
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WebChatProtocolError("chat server sent invalid JSON") from exc
        if not isinstance(event, dict):
            raise WebChatProtocolError("chat server event must be an object")
        return event

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
