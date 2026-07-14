import contextlib
import json
import socket
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.app as server

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def reset_rooms():
    server.manager.rooms = {name: [] for name in server.ROOM_NAMES}
    yield


def recv(ws):
    return json.loads(ws.receive_text())


def test_users_endpoint():
    assert client.get("/users/A").json() == {"room": "A", "users": 0}
    assert "error" in client.get("/users/Z").json()


def test_ds_bridge_status_is_offline_without_radio_bridge():
    assert client.get("/ds-bridge/status").json() == {
        "available": False,
        "rooms": [],
    }

    # A browser user cannot make the hardware status appear online merely by
    # choosing the bridge's display name.
    with client.websocket_connect("/ws/B/DS-BRIDGE") as web:
        recv(web)
        assert client.get("/ds-bridge/status").json() == {
            "available": False,
            "rooms": [],
        }


def test_static_routes_serve():
    home = client.get("/")
    assert home.status_code == 200
    assert 'id="ds-bridge-status"' in home.text
    assert 'href="/assets/app.css"' in home.text
    assert 'src="/assets/app.js"' in home.text
    script = client.get("/assets/app.js")
    assert script.status_code == 200
    assert 'fetch("/ds-bridge/status"' in script.text
    for path in ("/font", "/manifest.json", "/sw.js", "/audio-send",
                 "/icon-192.png", "/icon-512.png", "/assets/app.css"):
        assert client.get(path).status_code == 200, path


def test_join_broadcasts_system_event_with_color():
    with client.websocket_connect("/ws/A/kevin") as ws:
        msg = recv(ws)
        assert msg == {"type": "system", "event": "join",
                       "name": "kevin", "color": 0, "users": 1}


def test_invalid_room_rejected():
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/Z/kevin") as ws:
            ws.receive_text()
    assert exc.value.code == server.CLOSE_INVALID_ROOM


def test_blank_nickname_rejected():
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/A/%20%20") as ws:
            ws.receive_text()
    assert exc.value.code == server.CLOSE_BAD_NICKNAME


def test_room_capacity_enforced():
    with contextlib.ExitStack() as stack:
        for i in range(server.ROOM_CAPACITY):
            stack.enter_context(client.websocket_connect(f"/ws/A/user{i}"))
        assert client.get("/users/A").json()["users"] == server.ROOM_CAPACITY
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/A/late") as ws:
                ws.receive_text()
        assert exc.value.code == server.CLOSE_ROOM_FULL


def test_duplicate_nickname_rejected_case_insensitive():
    with client.websocket_connect("/ws/A/kevin"):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/A/KEVIN") as ws:
                ws.receive_text()
        assert exc.value.code == server.CLOSE_BAD_NICKNAME


def test_same_nickname_allowed_in_other_room():
    with client.websocket_connect("/ws/A/kevin"), \
         client.websocket_connect("/ws/B/kevin") as ws_b:
        assert recv(ws_b)["name"] == "kevin"


def test_message_relay_carries_color():
    with client.websocket_connect("/ws/A/alice") as alice, \
         client.websocket_connect("/ws/A/bob") as bob:
        recv(alice)          # alice join
        recv(alice)          # bob join
        recv(bob)            # bob join
        bob.send_text(json.dumps({"type": "message", "message": "hi"}))
        msg = recv(alice)
        assert msg == {"type": "message", "sender": "bob", "color": 1, "message": "hi"}
        assert recv(bob) == msg   # sender gets the echo too


def test_drawing_relay():
    with client.websocket_connect("/ws/A/alice") as alice, \
         client.websocket_connect("/ws/A/bob") as bob:
        recv(alice); recv(alice); recv(bob)
        bob.send_text(json.dumps({"type": "drawing", "data": "data:image/png;base64,AAA"}))
        msg = recv(alice)
        assert msg["type"] == "drawing"
        assert msg["sender"] == "bob"
        assert msg["data"].startswith("data:image/png")


def test_leave_broadcasts_updated_count():
    with client.websocket_connect("/ws/A/alice") as alice:
        recv(alice)
        with client.websocket_connect("/ws/A/bob"):
            assert recv(alice)["users"] == 2
        msg = recv(alice)
        assert msg == {"type": "system", "event": "leave", "name": "bob", "users": 1}


def test_colors_are_reused_after_leave():
    with client.websocket_connect("/ws/A/alice") as alice:
        recv(alice)
        with client.websocket_connect("/ws/A/bob") as bob:
            assert recv(bob)["color"] == 1
            recv(alice)
        recv(alice)   # bob's leave
        with client.websocket_connect("/ws/A/carol") as carol:
            assert recv(carol)["color"] == 1   # bob's colour freed


def test_oversized_payload_rejected_not_broadcast():
    with client.websocket_connect("/ws/A/alice") as alice, \
         client.websocket_connect("/ws/A/bob") as bob:
        recv(alice); recv(alice); recv(bob)
        bob.send_text("x" * (server.MAX_PAYLOAD_BYTES + 1))
        assert recv(bob)["type"] == "error"
        bob.send_text(json.dumps({"type": "message", "message": "after"}))
        assert recv(alice)["message"] == "after"   # big frame never arrived


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_COUNT", 3)
    with client.websocket_connect("/ws/A/alice") as alice:
        recv(alice)
        for i in range(4):
            alice.send_text(json.dumps({"type": "message", "message": f"m{i}"}))
        types = [recv(alice)["type"] for _ in range(4)]
        assert types == ["message", "message", "message", "error"]


def test_long_text_truncated():
    with client.websocket_connect("/ws/A/alice") as alice:
        recv(alice)
        alice.send_text(json.dumps({"type": "message", "message": "y" * 2000}))
        assert len(recv(alice)["message"]) == server.MAX_MESSAGE_CHARS


# ---- raw-TCP adapter (DS homebrew transport) ----

@contextlib.contextmanager
def tcp_client():
    port = getattr(server.app.state, "tcp_port", server.TCP_PORT)
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    f = s.makefile("rw", encoding="utf-8", newline="\n")
    try:
        yield f
    finally:
        # close the file wrapper too, or the server never sees EOF and the
        # lifespan shutdown waits on the handler forever
        try:
            f.close()
        except OSError:
            pass
        s.close()


def tcp_send(f, obj):
    f.write(json.dumps(obj) + "\n")
    f.flush()


def tcp_recv(f):
    return json.loads(f.readline())


def test_tcp_join_and_cross_transport_relay(monkeypatch):
    monkeypatch.setattr(server, "TCP_PORT", 0)
    with TestClient(server.app) as c:          # context manager starts the TCP server
        with c.websocket_connect("/ws/A/web") as web:
            recv(web)                          # web's own join
            with tcp_client() as ds:
                tcp_send(ds, {"room": "A", "name": "dsuser"})
                join = tcp_recv(ds)
                assert join["type"] == "system" and join["event"] == "join"
                assert join["name"] == "dsuser" and join["color"] == 1
                assert recv(web)["name"] == "dsuser"       # web saw the DS join

                tcp_send(ds, {"type": "message", "message": "hi from ds"})
                assert recv(web)["message"] == "hi from ds"    # ws <- tcp
                assert tcp_recv(ds)["message"] == "hi from ds" # echo to tcp

                web.send_text(json.dumps({"type": "message", "message": "hi from web"}))
                assert tcp_recv(ds)["message"] == "hi from web"    # tcp <- ws
            assert recv(web)["message"] == "hi from web"   # ws own echo
            assert recv(web)["event"] == "leave"           # DS socket closed


def test_tcp_bridge_role_controls_health_status(monkeypatch):
    monkeypatch.setattr(server, "TCP_PORT", 0)
    with TestClient(server.app) as c:
        assert c.get("/ds-bridge/status").json()["available"] is False
        with tcp_client() as bridge:
            tcp_send(bridge, {
                "room": "B", "name": "DS-BRIDGE", "role": "ds-bridge",
            })
            join = tcp_recv(bridge)
            assert join["type"] == "system" and join["event"] == "join"
            assert c.get("/ds-bridge/status").json() == {
                "available": True,
                "rooms": ["B"],
            }
        # Let the TCP handler process EOF before checking the endpoint.
        for _ in range(50):
            if not c.get("/ds-bridge/status").json()["available"]:
                break
            time.sleep(0.01)
        assert c.get("/ds-bridge/status").json()["available"] is False


def test_tcp_invalid_room_rejected(monkeypatch):
    monkeypatch.setattr(server, "TCP_PORT", 0)
    with TestClient(server.app):
        with tcp_client() as ds:
            tcp_send(ds, {"room": "Z", "name": "x"})
            line = tcp_recv(ds)
            assert line["type"] == "close"
            assert line["code"] == server.CLOSE_INVALID_ROOM


def test_tcp_duplicate_nickname_rejected(monkeypatch):
    monkeypatch.setattr(server, "TCP_PORT", 0)
    with TestClient(server.app) as c:
        with c.websocket_connect("/ws/A/kevin"):
            with tcp_client() as ds:
                tcp_send(ds, {"room": "A", "name": "Kevin"})
                line = tcp_recv(ds)
                assert line["type"] == "close"
                assert line["code"] == server.CLOSE_BAD_NICKNAME
