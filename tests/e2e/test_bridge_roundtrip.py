"""Socket-level bridge test with a mock of the ESP PictoChat firmware."""

import asyncio
import base64
from io import BytesIO
import json
import socket

import msgpack
from PIL import Image
import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import backend.app as server
from bridge.config import BridgeConfig
from bridge.esp_transport import (
    EspRadioConnection,
    RadioMessage,
    unpack_event,
)
from bridge.images import PNG_DATA_URL_PREFIX
from bridge.service import PictoChatBridge
from bridge.web_transport import WebChatConnection
from pictochat import decode_visible_image, encode_visible_image


def _drawing_data_url(x: int, y: int) -> str:
    image = Image.new("RGBA", (228, 80), (0, 0, 0, 0))
    image.putpixel((x, y), (0, 0, 0, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return PNG_DATA_URL_PREFIX + base64.b64encode(output.getvalue()).decode("ascii")


def _decode_data_url(data_url: str) -> Image.Image:
    assert data_url.startswith(PNG_DATA_URL_PREFIX)
    raw = base64.b64decode(data_url[len(PNG_DATA_URL_PREFIX):])
    image = Image.open(BytesIO(raw))
    image.load()
    return image.convert("RGBA")


async def _wait_for_uvicorn(server_task, uvicorn_server) -> None:
    while not uvicorn_server.started:
        if server_task.done():
            # Propagate startup failures instead of polling until the outer
            # timeout obscures the useful exception.
            await server_task
        await asyncio.sleep(0.01)


async def _bridge_round_trip(http_port: int) -> None:
    outbound_radio_frames = asyncio.Queue()
    firmware_connected = asyncio.get_running_loop().create_future()

    async def firmware_endpoint(websocket):
        if not firmware_connected.done():
            firmware_connected.set_result(websocket)
        async for payload in websocket:
            await outbound_radio_frames.put(payload)

    async with serve(firmware_endpoint, "127.0.0.1", 0) as firmware_server:
        firmware_port = firmware_server.sockets[0].getsockname()[1]
        browser = await connect(f"ws://127.0.0.1:{http_port}/ws/B/alice")
        web = radio = bridge_task = None
        try:
            assert json.loads(await asyncio.wait_for(browser.recv(), 2)) == {
                "type": "system",
                "event": "join",
                "name": "alice",
                "color": 0,
                "users": 1,
            }

            web = await asyncio.wait_for(
                WebChatConnection.open(
                    "127.0.0.1",
                    server.app.state.tcp_port,
                    room="B",
                    bridge_name="DS-BRIDGE",
                ),
                2,
            )
            assert json.loads(await asyncio.wait_for(browser.recv(), 2))["name"] == (
                "DS-BRIDGE"
            )
            radio = await asyncio.wait_for(
                EspRadioConnection.open(f"ws://127.0.0.1:{firmware_port}/ws"),
                2,
            )
            firmware_peer = await asyncio.wait_for(firmware_connected, 2)

            bridge = PictoChatBridge(
                BridgeConfig(
                    web_port=server.app.state.tcp_port,
                    radio_url=f"ws://127.0.0.1:{firmware_port}/ws",
                    room="B",
                )
            )
            bridge_task = asyncio.create_task(bridge.run_connected(web, radio))

            # Browser -> FastAPI WebSocket -> TCP bridge client -> firmware WS.
            browser_drawing = _drawing_data_url(12, 20)
            await browser.send(json.dumps({"type": "drawing", "data": browser_drawing}))
            browser_echo = json.loads(await asyncio.wait_for(browser.recv(), 2))
            assert browser_echo["sender"] == "alice"
            outbound = await asyncio.wait_for(outbound_radio_frames.get(), 2)
            decoded = unpack_event(outbound)
            assert isinstance(decoded, RadioMessage)
            assert decoded.mac_address is None
            assert len(decoded.message_content) == 4096
            assert decode_visible_image(decoded.message_content)[20][12] == 1

            # Firmware WS -> TCP bridge client -> FastAPI -> browser WebSocket.
            ds_pixels = [[0] * 228 for _ in range(16)]
            ds_pixels[9][30] = 7
            ds_bitmap = encode_visible_image(ds_pixels)
            ds_mac = b"\x00\x11\x22\x33\x44\x55"
            await firmware_peer.send(
                msgpack.packb({"MESSAGE": [ds_mac, ds_bitmap]}, use_bin_type=True)
            )
            ds_event = json.loads(await asyncio.wait_for(browser.recv(), 2))
            assert ds_event["type"] == "drawing"
            assert ds_event["sender"] == "DS-BRIDGE"
            rendered = _decode_data_url(ds_event["data"])
            assert rendered.getpixel((30, 9)) == (251, 162, 0, 255)

            # The room echoes the injected drawing to DS-BRIDGE. Its sender
            # marker must suppress a second radio send (an infinite loop on
            # real hardware).
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(outbound_radio_frames.get(), 0.3)
        finally:
            if bridge_task is not None:
                bridge_task.cancel()
                await asyncio.gather(bridge_task, return_exceptions=True)
            if radio is not None:
                await radio.close()
            if web is not None:
                await web.close()
            await browser.close()


async def _scenario() -> None:
    # Pre-bind port zero so Uvicorn and the firmware mock never rely on fixed
    # host ports. The app lifespan separately binds its TCP adapter to zero.
    http_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    http_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    http_socket.bind(("127.0.0.1", 0))
    http_socket.listen(128)
    http_socket.setblocking(False)
    http_port = http_socket.getsockname()[1]

    uvicorn_server = uvicorn.Server(
        uvicorn.Config(server.app, log_level="warning", lifespan="on")
    )
    server_task = asyncio.create_task(uvicorn_server.serve(sockets=[http_socket]))
    try:
        await _wait_for_uvicorn(server_task, uvicorn_server)
        await _bridge_round_trip(http_port)
    finally:
        uvicorn_server.should_exit = True
        try:
            await asyncio.wait_for(server_task, 3)
        finally:
            http_socket.close()


def test_real_server_bridge_and_mock_firmware_round_trip(monkeypatch):
    monkeypatch.setattr(server, "TCP_PORT", 0)
    server.manager.rooms = {name: [] for name in server.ROOM_NAMES}
    try:
        asyncio.run(asyncio.wait_for(_scenario(), 10))
    finally:
        server.manager.rooms = {name: [] for name in server.ROOM_NAMES}
