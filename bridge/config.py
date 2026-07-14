"""Configuration for the first, single-console PictoChat bridge."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Runtime settings, with environment-variable compatible defaults."""

    web_host: str = "127.0.0.1"
    web_port: int = 8083
    radio_url: str = "ws://pictochat_rs.local:5678/ws"
    room: str = "B"
    bridge_name: str = "DS-BRIDGE"
    x_offset: int = 0
    ink_threshold: int = 160
    alpha_threshold: int = 32
    max_png_bytes: int = 128 * 1024
    dedupe_ttl: float = 3.0
    reconnect_delay: float = 2.0

    def __post_init__(self) -> None:
        if not self.web_host:
            raise ValueError("web_host cannot be empty")
        if not 1 <= self.web_port <= 65535:
            raise ValueError("web_port must be from 1 to 65535")
        if not self.radio_url.startswith(("ws://", "wss://")):
            raise ValueError("radio_url must start with ws:// or wss://")
        if self.room not in ("A", "B", "C", "D"):
            raise ValueError("room must be A, B, C, or D")
        if not 1 <= len(self.bridge_name) <= 10:
            raise ValueError("bridge_name must contain 1 to 10 characters")
        if not 0 <= self.x_offset <= 28:
            raise ValueError("x_offset must be from 0 to 28")
        if not 0 <= self.ink_threshold <= 255:
            raise ValueError("ink_threshold must be from 0 to 255")
        if not 0 <= self.alpha_threshold <= 255:
            raise ValueError("alpha_threshold must be from 0 to 255")
        if self.max_png_bytes < 1024:
            raise ValueError("max_png_bytes must be at least 1024")
        if self.dedupe_ttl < 0 or self.reconnect_delay < 0:
            raise ValueError("timing values cannot be negative")

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        radio_url = os.getenv("PICTOCHAT_RADIO_URL")
        if not radio_url:
            radio_host = os.getenv("PICTOCHAT_RADIO_HOST", "pictochat_rs.local")
            radio_port = _integer(
                "PICTOCHAT_RADIO_PORT", 5678, minimum=1, maximum=65535
            )
            radio_url = f"ws://{radio_host}:{radio_port}/ws"
        return cls(
            web_host=os.getenv("PICTOCHAT_WEB_HOST", "127.0.0.1"),
            web_port=_integer(
                "PICTOCHAT_WEB_PORT", 8083, minimum=1, maximum=65535
            ),
            radio_url=radio_url,
            room=os.getenv("PICTOCHAT_ROOM", "B").upper(),
            bridge_name=os.getenv("PICTOCHAT_BRIDGE_NAME", "DS-BRIDGE"),
            x_offset=_integer(
                "PICTOCHAT_X_OFFSET", 0, minimum=0, maximum=28
            ),
            ink_threshold=_integer(
                "PICTOCHAT_INK_THRESHOLD", 160, minimum=0, maximum=255
            ),
            alpha_threshold=_integer(
                "PICTOCHAT_ALPHA_THRESHOLD", 32, minimum=0, maximum=255
            ),
            max_png_bytes=_integer(
                "PICTOCHAT_MAX_PNG_BYTES", 128 * 1024,
                minimum=1024, maximum=4 * 1024 * 1024,
            ),
            dedupe_ttl=_number(
                "PICTOCHAT_DEDUPE_TTL", 3.0, minimum=0.0
            ),
            reconnect_delay=_number(
                "PICTOCHAT_RECONNECT_DELAY", 2.0, minimum=0.0
            ),
        )
