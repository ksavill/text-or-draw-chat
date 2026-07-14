import asyncio
import json

import msgpack
import pytest

from bridge.config import BridgeConfig
from bridge.esp_transport import (
    EspRadioConnection,
    RadioMessage,
    RadioProtocolError,
    RadioState,
    pack_message_event,
    unpack_event,
)
from bridge.web_transport import WebChatConnection, WebChatProtocolError


class FakeWebSocket:
    def __init__(self, incoming=None):
        self.incoming = incoming
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        return self.incoming

    async def close(self):
        self.closed = True


class FakeWriter:
    def __init__(self):
        self.writes = []
        self.drains = 0
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        self.drains += 1

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def test_message_event_has_exact_compact_rmp_serde_layout():
    encoded = pack_message_event(b"\xaa\xbb")

    assert encoded == b"\x81\xa7MESSAGE\x92\xc0\xc4\x02\xaa\xbb"
    assert msgpack.unpackb(encoded, raw=False, strict_map_key=False) == {
        "MESSAGE": [None, b"\xaa\xbb"]
    }
    assert unpack_event(encoded) == RadioMessage(None, b"\xaa\xbb")


def test_unpack_numeric_and_named_radio_events():
    state = msgpack.packb(
        {0: [b"\x00\x11\x22\x33\x44\x55", False, 13, 7, "Mii", "Hi"]},
        use_bin_type=True,
    )
    named_message = msgpack.packb(
        {"MESSAGE": [b"\x00\x11\x22\x33\x44\x55", b"bitmap"]},
        use_bin_type=True,
    )

    assert unpack_event(state) == RadioState(
        b"\x00\x11\x22\x33\x44\x55", False, 13, 7, "Mii", "Hi"
    )
    assert unpack_event(named_message) == RadioMessage(
        b"\x00\x11\x22\x33\x44\x55", b"bitmap"
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        {2: []},
        {1: [None]},
        {1: [b"short", b"bitmap"]},
        {0: [bytes(6), 0, 1, 1, "name", "bio"]},
        {0: [bytes(6), False, True, 1, "name", "bio"]},
        {0: [bytes(6), False, -1, 1, "name", "bio"]},
    ],
)
def test_malformed_radio_events_are_rejected(value):
    with pytest.raises(RadioProtocolError):
        unpack_event(msgpack.packb(value, use_bin_type=True))


def test_esp_connection_sends_binary_messages_and_rejects_text_frames():
    async def scenario():
        websocket = FakeWebSocket(incoming="not binary")
        connection = EspRadioConnection(websocket)
        await connection.send_bitmap(b"pixels")
        with pytest.raises(RadioProtocolError, match="non-binary"):
            await connection.receive_event()
        await connection.close()
        return websocket

    websocket = asyncio.run(scenario())
    assert unpack_event(websocket.sent[0]) == RadioMessage(None, b"pixels")
    assert websocket.closed


def test_web_chat_connection_serialises_drawings_and_parses_events():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"type":"system","event":"join"}\n')
        reader.feed_eof()
        writer = FakeWriter()
        connection = WebChatConnection(reader, writer, bridge_name="DS-BRIDGE")
        event = await connection.receive_event()
        await connection.send_drawing("data:image/png;base64,AAA")
        await connection.close()
        return event, writer

    event, writer = asyncio.run(scenario())
    assert event == {"type": "system", "event": "join"}
    assert json.loads(writer.writes[0]) == {
        "type": "drawing",
        "data": "data:image/png;base64,AAA",
    }
    assert writer.drains == 1
    assert writer.closed


def test_web_chat_open_identifies_hardware_bridge(monkeypatch):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(
            b'{"type":"system","event":"join","name":"DS-BRIDGE"}\n'
        )
        writer = FakeWriter()

        async def open_connection(host, port, *, limit):
            assert (host, port, limit) == ("127.0.0.1", 8083, 256 * 1024)
            return reader, writer

        monkeypatch.setattr(asyncio, "open_connection", open_connection)
        connection = await WebChatConnection.open(
            "127.0.0.1", 8083, room="B", bridge_name="DS-BRIDGE",
        )
        await connection.close()
        return json.loads(writer.writes[0])

    assert asyncio.run(scenario()) == {
        "room": "B",
        "name": "DS-BRIDGE",
        "role": "ds-bridge",
    }


def test_web_chat_connection_rejects_invalid_json_and_closed_stream():
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(b"[]\nnot-json\n")
        reader.feed_eof()
        connection = WebChatConnection(reader, FakeWriter(), bridge_name="bridge")
        with pytest.raises(WebChatProtocolError, match="object"):
            await connection.receive_event()
        with pytest.raises(WebChatProtocolError, match="invalid JSON"):
            await connection.receive_event()
        with pytest.raises(ConnectionError, match="closed"):
            await connection.receive_event()

    asyncio.run(scenario())


def test_config_from_environment_and_validation(monkeypatch):
    monkeypatch.setenv("PICTOCHAT_RADIO_HOST", "esp.local")
    monkeypatch.setenv("PICTOCHAT_RADIO_PORT", "6000")
    monkeypatch.setenv("PICTOCHAT_ROOM", "b")
    monkeypatch.setenv("PICTOCHAT_X_OFFSET", "14")
    monkeypatch.setenv("PICTOCHAT_DEDUPE_TTL", "0.5")

    config = BridgeConfig.from_env()

    assert config.radio_url == "ws://esp.local:6000/ws"
    assert config.room == "B"
    assert config.x_offset == 14
    assert config.dedupe_ttl == 0.5
    with pytest.raises(ValueError, match="room"):
        BridgeConfig(room="Z")
    monkeypatch.setenv("PICTOCHAT_RADIO_PORT", "not-a-port")
    with pytest.raises(ValueError, match="integer"):
        BridgeConfig.from_env()
