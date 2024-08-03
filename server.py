from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from starlette.responses import FileResponse
from fastapi.responses import HTMLResponse
import os
import json
import uvicorn

app = FastAPI()

rooms = {"A": [], "B": [], "C": [], "D": []}

@app.get("/", response_class=HTMLResponse)
async def get():
    with open("index.html", "r") as file:
        return HTMLResponse(content=file.read())
    
@app.get("/icon")
async def get_icon():
    icon_path = "chat-icon.png"
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    else:
        raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/audio-ping")
async def get_audio():
    audio_path = "ping.mp3"
    if os.path.exists(audio_path):
        return FileResponse(audio_path)
    else:
        raise HTTPException(status_code=404, detail="Audio not found")
    
@app.get("/audio-user-joined")
async def get_audio_user_joined():
    audio_path = "audio-user-joined.mp3"
    if os.path.exists(audio_path):
        return FileResponse(audio_path)
    else:
        raise HTTPException(status_code=404, detail="Audio not found")
    
@app.get("/audio-user-left")
async def get_audio_user_left():
    audio_path = "audio-user-left.mp3"
    if os.path.exists(audio_path):
        return FileResponse(audio_path)
    else:
        raise HTTPException(status_code=404, detail="Audio not found")

@app.get("/users/{room}")
async def get_users(room: str):
    if room in rooms:
        return {"room": room, "users": len(rooms[room])}
    return {"error": "Room not found"}

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket: WebSocket, room: str, nickname: str):
        await websocket.accept()
        if room not in self.active_connections:
            self.active_connections[room] = []
        self.active_connections[room].append((websocket, nickname))
        rooms[room].append(nickname)
        await self.broadcast(f"System: {nickname} has joined the room", room)

    async def disconnect(self, websocket: WebSocket, room: str, nickname: str):
        self.active_connections[room] = [(ws, nick) for ws, nick in self.active_connections[room] if ws != websocket]
        rooms[room].remove(nickname)
        await self.broadcast(f"System: {nickname} has left the room", room)

    async def broadcast(self, message: str, room: str):
        for connection, _ in self.active_connections[room]:
            await connection.send_text(message)

    async def broadcast_message(self, sender: str, message: str, room: str):
        for connection, _ in self.active_connections[room]:
            await connection.send_text(json.dumps({ "type": "message", "sender": sender, "message": message }))

    async def broadcast_drawing(self, sender: str, drawing_data: str, room: str):
        for connection, _ in self.active_connections[room]:
            await connection.send_text(json.dumps({ "type": "drawing", "sender": sender, "data": drawing_data }))

manager = ConnectionManager()

@app.websocket("/ws/{room}/{nickname}")
async def websocket_endpoint(websocket: WebSocket, room: str, nickname: str):
    await manager.connect(websocket, room, nickname)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                if message["type"] == "drawing":
                    await manager.broadcast_drawing(nickname, message["data"], room)
                else:
                    await manager.broadcast_message(nickname, message["message"], room)
            except json.JSONDecodeError:
                await manager.broadcast_message(nickname, data, room)
    except WebSocketDisconnect:
        await manager.disconnect(websocket, room, nickname)

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8082)