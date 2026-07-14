"""Bidirectional orchestration between the web room and ESP radio firmware."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import logging
import time
from typing import Callable

from .config import BridgeConfig
from .esp_transport import EspRadioConnection, RadioMessage, RadioState
from .images import (
    ImageConversionError,
    data_url_to_radio_bitmap,
    radio_bitmap_to_data_url,
)
from .web_transport import WebChatConnection


LOGGER = logging.getLogger("pictochat.bridge")


class RecentRadioMessages:
    """Bounded time-based guard against radio/application retransmissions."""

    def __init__(self, ttl: float, *, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._seen: dict[tuple[bytes | None, bytes], float] = {}

    def is_duplicate(self, mac: bytes | None, bitmap: bytes) -> bool:
        now = self.clock()
        expired = [key for key, seen_at in self._seen.items()
                   if now - seen_at >= self.ttl]
        for key in expired:
            del self._seen[key]
        # ``None`` identifies a host-originated firmware event and must remain
        # distinct from the valid all-zero MAC address.
        key = (mac, hashlib.sha256(bitmap).digest())
        previous = self._seen.get(key)
        self._seen[key] = now
        return previous is not None and now - previous < self.ttl


class PictoChatBridge:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.recent = RecentRadioMessages(config.dedupe_ttl)
        self.radio_profiles: dict[bytes, RadioState] = {}

    async def handle_web_event(self, event: dict, radio: EspRadioConnection) -> bool:
        """Forward one web drawing. Return true only when a bitmap was sent."""

        if event.get("type") != "drawing":
            return False
        # A physical-DS drawing injected into the web room is echoed to this
        # client by design. Its server-owned sender name is our loop marker.
        if event.get("sender") == self.config.bridge_name:
            return False
        try:
            bitmap = data_url_to_radio_bitmap(
                event.get("data"),
                x_offset=self.config.x_offset,
                ink_threshold=self.config.ink_threshold,
                alpha_threshold=self.config.alpha_threshold,
                max_png_bytes=self.config.max_png_bytes,
            )
        except ImageConversionError as exc:
            LOGGER.warning("Ignoring invalid web drawing from %r: %s",
                           event.get("sender"), exc)
            return False
        await radio.send_bitmap(bitmap)
        LOGGER.info("web -> DS: %s bytes from %s", len(bitmap), event.get("sender"))
        return True

    async def handle_radio_event(self, event, web: WebChatConnection) -> bool:
        """Forward one completed DS drawing. Return true only when injected."""

        if isinstance(event, RadioState):
            if event.is_leaving:
                self.radio_profiles.pop(event.mac_address, None)
                LOGGER.info("DS left: %s (%s)", event.name,
                            event.mac_address.hex(":"))
            else:
                self.radio_profiles[event.mac_address] = event
                LOGGER.info("DS joined: %s (%s)", event.name,
                            event.mac_address.hex(":"))
            return False
        if not isinstance(event, RadioMessage):
            return False
        if self.recent.is_duplicate(event.mac_address, event.message_content):
            LOGGER.debug("Ignoring duplicate DS bitmap")
            return False
        try:
            data_url = radio_bitmap_to_data_url(
                event.message_content, x_offset=self.config.x_offset,
            )
        except ImageConversionError as exc:
            LOGGER.warning("Ignoring malformed DS bitmap: %s", exc)
            return False
        await web.send_drawing(data_url)
        LOGGER.info("DS -> web: %s bytes from %s", len(event.message_content),
                    event.mac_address.hex(":") if event.mac_address else "host")
        return True

    async def _web_to_radio(self, web, radio) -> None:
        while True:
            await self.handle_web_event(await web.receive_event(), radio)

    async def _radio_to_web(self, radio, web) -> None:
        while True:
            await self.handle_radio_event(await radio.receive_event(), web)

    async def run_connected(self, web, radio) -> None:
        tasks = {
            asyncio.create_task(self._web_to_radio(web, radio), name="web-to-ds"),
            asyncio.create_task(self._radio_to_web(radio, web), name="ds-to-web"),
        }
        try:
            # Either direction ending means this connection pair can no longer
            # bridge bidirectionally, even if an adapter happens to return
            # normally instead of raising on EOF.
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_forever(self) -> None:
        LOGGER.info("Starting Room %s bridge as %s", self.config.room,
                    self.config.bridge_name)
        while True:
            web = radio = None
            try:
                # Prove the hardware side is available before joining the web
                # room. Otherwise a missing ESP/mDNS record makes DS-BRIDGE
                # join and leave on every reconnect attempt.
                radio = await EspRadioConnection.open(self.config.radio_url)
                LOGGER.info("Connected to DS radio at %s", self.config.radio_url)
                web = await WebChatConnection.open(
                    self.config.web_host, self.config.web_port,
                    room=self.config.room, bridge_name=self.config.bridge_name,
                )
                LOGGER.info("Connected to web room at %s:%d",
                            self.config.web_host, self.config.web_port)
                await self.run_connected(web, radio)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning("Bridge disconnected: %s", exc)
            finally:
                if radio is not None:
                    with suppress(Exception):
                        await radio.close()
                if web is not None:
                    with suppress(Exception):
                        await web.close()
            await asyncio.sleep(self.config.reconnect_delay)
