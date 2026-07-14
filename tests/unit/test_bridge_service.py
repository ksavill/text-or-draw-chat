import asyncio
import base64
from io import BytesIO

from PIL import Image

from bridge.config import BridgeConfig
from bridge.esp_transport import RadioMessage, RadioState
from bridge.images import PNG_DATA_URL_PREFIX
import bridge.service as service_module
from bridge.service import PictoChatBridge, RecentRadioMessages
from pictochat import decode_visible_image, encode_visible_image


def drawing_data_url(*points):
    image = Image.new("RGBA", (228, 80), (0, 0, 0, 0))
    for point in points:
        image.putpixel(point, (0, 0, 0, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return PNG_DATA_URL_PREFIX + base64.b64encode(output.getvalue()).decode("ascii")


class FakeRadio:
    def __init__(self):
        self.bitmaps = []

    async def send_bitmap(self, bitmap):
        self.bitmaps.append(bitmap)


class FakeWeb:
    def __init__(self):
        self.drawings = []

    async def send_drawing(self, data_url):
        self.drawings.append(data_url)


def test_web_drawing_is_forwarded_but_bridge_echo_is_not():
    async def scenario():
        bridge = PictoChatBridge(BridgeConfig(x_offset=14))
        radio = FakeRadio()
        sent = await bridge.handle_web_event(
            {"type": "drawing", "sender": "alice", "data": drawing_data_url((3, 20))},
            radio,
        )
        echoed = await bridge.handle_web_event(
            {
                "type": "drawing",
                "sender": "DS-BRIDGE",
                "data": drawing_data_url((4, 4)),
            },
            radio,
        )
        return sent, echoed, radio

    sent, echoed, radio = asyncio.run(scenario())
    assert sent is True
    assert echoed is False
    assert len(radio.bitmaps) == 1
    assert len(radio.bitmaps[0]) == 4096
    assert decode_visible_image(radio.bitmaps[0], x_offset=14)[20][3] == 1


def test_non_drawing_and_invalid_web_events_are_ignored():
    async def scenario():
        bridge = PictoChatBridge(BridgeConfig())
        radio = FakeRadio()
        outcomes = [
            await bridge.handle_web_event({"type": "message"}, radio),
            await bridge.handle_web_event(
                {"type": "drawing", "sender": "alice", "data": "bad"}, radio
            ),
        ]
        return outcomes, radio

    outcomes, radio = asyncio.run(scenario())
    assert outcomes == [False, False]
    assert radio.bitmaps == []


def test_radio_drawing_is_injected_once_and_state_is_tracked():
    async def scenario():
        bridge = PictoChatBridge(BridgeConfig())
        web = FakeWeb()
        mac = b"\x00\x11\x22\x33\x44\x55"
        joined = RadioState(mac, False, 13, 7, "Mii", "hello")
        left = RadioState(mac, True, 13, 7, "Mii", "hello")
        bitmap = encode_visible_image([[1] + [0] * 227 for _ in range(16)])
        assert await bridge.handle_radio_event(joined, web) is False
        profile_after_join = bridge.radio_profiles.get(mac)
        first = await bridge.handle_radio_event(RadioMessage(mac, bitmap), web)
        duplicate = await bridge.handle_radio_event(RadioMessage(mac, bitmap), web)
        assert await bridge.handle_radio_event(left, web) is False
        return bridge, web, joined, profile_after_join, first, duplicate

    bridge, web, joined, profile_after_join, first, duplicate = asyncio.run(scenario())
    assert profile_after_join == joined
    assert bridge.radio_profiles == {}
    assert first is True
    assert duplicate is False
    assert len(web.drawings) == 1
    assert web.drawings[0].startswith(PNG_DATA_URL_PREFIX)


def test_invalid_radio_bitmap_is_ignored_without_web_send():
    async def scenario():
        bridge = PictoChatBridge(BridgeConfig())
        web = FakeWeb()
        forwarded = await bridge.handle_radio_event(
            RadioMessage(bytes(6), b"not tiled image data"), web
        )
        return forwarded, web

    forwarded, web = asyncio.run(scenario())
    assert forwarded is False
    assert web.drawings == []


def test_recent_message_deduplication_is_per_sender_and_expires():
    now = [100.0]
    recent = RecentRadioMessages(3.0, clock=lambda: now[0])
    bitmap = b"same pixels"

    assert recent.is_duplicate(b"\x01" * 6, bitmap) is False
    assert recent.is_duplicate(b"\x01" * 6, bitmap) is True
    assert recent.is_duplicate(b"\x02" * 6, bitmap) is False
    assert recent.is_duplicate(None, bitmap) is False
    assert recent.is_duplicate(bytes(6), bitmap) is False
    now[0] += 3.0
    assert recent.is_duplicate(b"\x01" * 6, bitmap) is False


def test_connected_bridge_cancels_other_direction_after_disconnect():
    cancelled = asyncio.Event()

    class DisconnectingWeb:
        async def receive_event(self):
            raise ConnectionError("web disconnected")

    class BlockingRadio:
        async def receive_event(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    async def scenario():
        bridge = PictoChatBridge(BridgeConfig())
        try:
            await bridge.run_connected(DisconnectingWeb(), BlockingRadio())
        except ConnectionError as exc:
            assert str(exc) == "web disconnected"
        else:
            raise AssertionError("disconnect should escape run_connected")
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_radio_failure_does_not_join_web_room(monkeypatch):
    radio_attempted = asyncio.Event()
    web_calls = []

    async def failing_radio_open(url):
        radio_attempted.set()
        raise OSError("radio unavailable")

    async def unexpected_web_open(*args, **kwargs):
        web_calls.append((args, kwargs))
        raise AssertionError("web room must not be joined before the radio")

    monkeypatch.setattr(
        service_module.EspRadioConnection, "open", failing_radio_open
    )
    monkeypatch.setattr(
        service_module.WebChatConnection, "open", unexpected_web_open
    )

    async def scenario():
        bridge = PictoChatBridge(BridgeConfig(reconnect_delay=60))
        task = asyncio.create_task(bridge.run_forever())
        try:
            await asyncio.wait_for(radio_attempted.wait(), 1)
            await asyncio.sleep(0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert web_calls == []


def test_radio_is_closed_if_web_room_connection_fails(monkeypatch):
    web_attempted = asyncio.Event()

    class ConnectedRadio:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    radio = ConnectedRadio()

    async def radio_open(url):
        return radio

    async def failing_web_open(*args, **kwargs):
        web_attempted.set()
        raise ConnectionError("web unavailable")

    monkeypatch.setattr(service_module.EspRadioConnection, "open", radio_open)
    monkeypatch.setattr(
        service_module.WebChatConnection, "open", failing_web_open
    )

    async def scenario():
        bridge = PictoChatBridge(BridgeConfig(reconnect_delay=60))
        task = asyncio.create_task(bridge.run_forever())
        try:
            await asyncio.wait_for(web_attempted.wait(), 1)
            for _ in range(10):
                if radio.closed:
                    break
                await asyncio.sleep(0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert radio.closed is True
