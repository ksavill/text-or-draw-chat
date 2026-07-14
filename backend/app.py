import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
import uvicorn


@asynccontextmanager
async def lifespan(app: FastAPI):
    # raw-TCP room bus for the real-DS radio bridge and lightweight clients
    tcp_server = await asyncio.start_server(
        tcp_connection, "0.0.0.0", TCP_PORT, limit=MAX_PAYLOAD_BYTES * 2)
    # Expose the actual bound port.  Production uses TCP_PORT, while tests can
    # request port 0 so they never collide with a running development server.
    app.state.tcp_port = tcp_server.sockets[0].getsockname()[1]
    yield
    tcp_server.close()
    await tcp_server.wait_closed()


app = FastAPI(title="PictoChat Web", lifespan=lifespan)

WEB_ROOT = Path(__file__).resolve().parent / "web"
ASSET_ROOT = WEB_ROOT / "assets"
app.mount("/assets", StaticFiles(directory=ASSET_ROOT), name="assets")

ROOM_NAMES = ("A", "B", "C", "D")
TCP_PORT = 8083                 # newline-delimited JSON bridge/client bus
ROOM_CAPACITY = 16              # matches the DS — and the 16-colour palette
MAX_NICKNAME_CHARS = 10
MAX_MESSAGE_CHARS = 500
MAX_PAYLOAD_BYTES = 64 * 1024   # a 228x80 PNG data URL is ~2-6 KB
RATE_LIMIT_COUNT = 10           # messages allowed per window
RATE_LIMIT_WINDOW = 5.0         # seconds

# application close codes (4000-4999 are free for apps)
CLOSE_INVALID_ROOM = 4001
CLOSE_ROOM_FULL = 4002
CLOSE_BAD_NICKNAME = 4003
DS_BRIDGE_ROLE = "ds-bridge"


class Client:
    def __init__(
        self, transport, nickname: str, color: int, *, role: str | None = None,
    ):
        self.transport = transport      # a WebSocket or a TcpClientAdapter
        self.nickname = nickname
        self.color = color              # palette index, unique within the room
        self.role = role
        self.sent_times = deque()


class ConnectionManager:
    def __init__(self):
        self.rooms = {name: [] for name in ROOM_NAMES}

    def user_count(self, room: str) -> int:
        return len(self.rooms[room])

    def _free_color(self, room: str) -> int:
        used = {c.color for c in self.rooms[room]}
        for i in range(ROOM_CAPACITY):
            if i not in used:
                return i
        return 0

    async def connect(
        self, transport, room: str, nickname: str, *, role: str | None = None,
    ):
        """Validate and admit a client. Returns the Client, or None if rejected.

        `transport` is a WebSocket or TcpClientAdapter. The socket is accepted
        before closing so the close frame (code + reason) reaches the client;
        closing a WebSocket pre-accept only yields a 403.
        """
        await transport.accept()
        if room not in self.rooms:
            await transport.close(code=CLOSE_INVALID_ROOM, reason="That chat room does not exist.")
            return None
        nickname = nickname.strip()
        if not nickname or len(nickname) > MAX_NICKNAME_CHARS:
            await transport.close(code=CLOSE_BAD_NICKNAME,
                                  reason=f"Names must be 1-{MAX_NICKNAME_CHARS} characters.")
            return None
        if self.user_count(room) >= ROOM_CAPACITY:
            await transport.close(code=CLOSE_ROOM_FULL, reason=f"Chat Room {room} is full.")
            return None
        if any(c.nickname.lower() == nickname.lower() for c in self.rooms[room]):
            await transport.close(code=CLOSE_BAD_NICKNAME,
                                  reason=f"Someone in Room {room} is already called {nickname}.")
            return None
        client = Client(
            transport, nickname, self._free_color(room), role=role,
        )
        self.rooms[room].append(client)
        await self.broadcast(room, {
            "type": "system", "event": "join",
            "name": client.nickname, "color": client.color,
            "users": self.user_count(room),
        })
        return client

    async def disconnect(self, client: Client, room: str):
        if client not in self.rooms.get(room, []):
            return
        self.rooms[room].remove(client)
        await self.broadcast(room, {
            "type": "system", "event": "leave",
            "name": client.nickname,
            "users": self.user_count(room),
        })

    async def broadcast(self, room: str, payload: dict):
        text = json.dumps(payload)
        dead = []
        for client in list(self.rooms.get(room, [])):
            try:
                await client.transport.send_text(text)
            except Exception:
                dead.append(client)
        for client in dead:
            await self.disconnect(client, room)


manager = ConnectionManager()


def connected_ds_bridge_rooms() -> list[str]:
    """Rooms containing a radio-connected bridge TCP client."""

    return [
        room for room, clients in manager.rooms.items()
        if any(client.role == DS_BRIDGE_ROLE for client in clients)
    ]


def rate_limited(client: Client) -> bool:
    now = time.monotonic()
    while client.sent_times and now - client.sent_times[0] > RATE_LIMIT_WINDOW:
        client.sent_times.popleft()
    if len(client.sent_times) >= RATE_LIMIT_COUNT:
        return True
    client.sent_times.append(now)
    return False


async def send_error(transport, reason: str):
    try:
        await transport.send_text(json.dumps({"type": "error", "reason": reason}))
    except Exception:
        pass


async def handle_incoming(client: Client, room: str, data: str):
    """One inbound frame/line from any transport -> broadcast (or reject)."""
    if len(data) > MAX_PAYLOAD_BYTES:
        await send_error(client.transport, "Message too large.")
        return
    if rate_limited(client):
        await send_error(client.transport, "You are sending messages too quickly.")
        return
    try:
        message = json.loads(data)
    except json.JSONDecodeError:
        message = {"type": "message", "message": data}
    if message.get("type") == "drawing" and isinstance(message.get("data"), str):
        await manager.broadcast(room, {
            "type": "drawing", "sender": client.nickname,
            "color": client.color, "data": message["data"],
        })
    elif isinstance(message.get("message"), str) and message["message"].strip():
        await manager.broadcast(room, {
            "type": "message", "sender": client.nickname,
            "color": client.color,
            "message": message["message"][:MAX_MESSAGE_CHARS],
        })
    # anything else is silently ignored


@app.websocket("/ws/{room}/{nickname}")
async def websocket_endpoint(websocket: WebSocket, room: str, nickname: str):
    client = await manager.connect(websocket, room, nickname)
    if client is None:
        return
    try:
        while True:
            data = await websocket.receive_text()
            await handle_incoming(client, room, data)
    except WebSocketDisconnect:
        await manager.disconnect(client, room)


class TcpClientAdapter:
    """Duck-types the parts of the WebSocket interface the manager uses,
    so the radio bridge or a homebrew client can join over plain TCP."""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.closed = False

    async def accept(self):
        pass

    async def send_text(self, text: str):
        if self.closed:
            raise ConnectionError("connection closed")
        self.writer.write((text + "\n").encode("utf-8"))
        await self.writer.drain()

    async def close(self, code: int = 1000, reason: str = ""):
        if reason and not self.closed:
            try:
                await self.send_text(json.dumps(
                    {"type": "close", "code": code, "reason": reason}))
            except Exception:
                pass
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


async def tcp_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Newline-delimited JSON over TCP (port 8083).

    First line joins: {"room": "A", "name": "kevin"} — after that the client
    sends the same payloads a WebSocket client would, one per line, and
    receives every room broadcast as a JSON line. Rejections arrive as
    {"type": "close", "code": ..., "reason": ...} and the socket closes.
    """
    adapter = TcpClientAdapter(writer)
    client = None
    room = ""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=30)
        hello = json.loads(raw.decode("utf-8", "replace"))
        room = str(hello.get("room", ""))
        role = hello.get("role")
        if role != DS_BRIDGE_ROLE:
            role = None
        client = await manager.connect(
            adapter, room, str(hello.get("name", "")), role=role,
        )
        if client is None:
            return
        while True:
            raw = await reader.readline()
            if not raw:
                break
            await handle_incoming(client, room, raw.decode("utf-8", "replace").rstrip("\r\n"))
    except (json.JSONDecodeError, asyncio.TimeoutError, asyncio.IncompleteReadError,
            ConnectionError, OSError):
        pass
    finally:
        if client is not None:
            await manager.disconnect(client, room)
        await adapter.close()


@app.get("/", response_class=HTMLResponse)
async def get():
    return HTMLResponse(content=(WEB_ROOT / "index.html").read_text(encoding="utf-8"))


def _static_file(path: Path, detail: str):
    if path.is_file():
        return FileResponse(path)
    raise HTTPException(status_code=404, detail=detail)


@app.get("/icon")
async def get_icon():
    return _static_file(ASSET_ROOT / "icons" / "favicon.png", "Icon not found")


@app.get("/icon-192.png")
async def get_icon_192():
    return _static_file(ASSET_ROOT / "icons" / "icon-192.png", "Icon not found")


@app.get("/icon-512.png")
async def get_icon_512():
    return _static_file(ASSET_ROOT / "icons" / "icon-512.png", "Icon not found")


@app.get("/font")
async def get_font():
    return _static_file(
        ASSET_ROOT / "fonts" / "press-start-2p.woff2", "Font not found",
    )


@app.get("/manifest.json")
async def get_manifest():
    return _static_file(WEB_ROOT / "manifest.json", "Manifest not found")


@app.get("/sw.js")
async def get_service_worker():
    return _static_file(WEB_ROOT / "sw.js", "Service worker not found")


@app.get("/audio-send")
async def get_audio_send():
    return _static_file(ASSET_ROOT / "audio" / "send.wav", "Audio not found")


@app.get("/audio-ping")
async def get_audio():
    return _static_file(ASSET_ROOT / "audio" / "receive.mp3", "Audio not found")


@app.get("/users/{room}")
async def get_users(room: str):
    if room in manager.rooms:
        return {"room": room, "users": manager.user_count(room)}
    return {"error": "Room not found"}


@app.get("/ds-bridge/status")
async def get_ds_bridge_status():
    rooms = connected_ds_bridge_rooms()
    return {"available": bool(rooms), "rooms": rooms}


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8082)


if __name__ == "__main__":
    main()
