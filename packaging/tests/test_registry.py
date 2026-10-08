"""Login, pairing, and session relay on the ICE signalling server.

The server binds 127.0.0.1 on a free port. It must not bind 8766,
3479, or anything in 50000–50100. No STUN and no mlink.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_TURN = Path(__file__).resolve().parents[2] / "turn"
if str(_TURN) not in sys.path:
    sys.path.insert(0, str(_TURN))

from packaging.registry.session import hash_password, password_matches
from signalling.server import listening_uri, start_server
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)

INVENTORY = {
    "v": 1,
    "type": "inventory",
    "interfaces": [
        {
            "name": "wlp0s20f3",
            "ipv4": "10.255.254.58",
            "up": True,
            "default_route": True,
        }
    ],
    "arms": [
        {
            "path": "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00",
            "tty": "/dev/ttyACM0",
            "label": "follower",
        }
    ],
    "videos": [{"path": "/dev/video2", "name": "USB2.0_CAM1", "kind": "capture"}],
    "camera_page": "http://100.120.193.52:8889/cam/",
}

CANDIDATES = {
    "type": "candidates",
    "username": "ufragA",
    "password": "passwordA_is_22charsX",
    "candidates": [
        {
            "ip": "192.0.2.10",
            "port": 40000,
            "type": "host",
            "foundation": "1",
            "component": 1,
            "priority": 2130706431,
        }
    ],
}


def _register(
    robot_id: str = "123456789",
    password: str = "AB23CD45",
    hostname: str = "AUTOOS-DEV-MUHAMMADOSAMA",
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "register",
        "robot_id": robot_id,
        "password": password,
        "hostname": hostname,
    }


def _login(
    robot_id: str = "123456789", password: str = "AB23CD45"
) -> dict[str, Any]:
    return {"v": 1, "type": "login", "robot_id": robot_id, "password": password}


def _logged_in(hostname: str = "AUTOOS-DEV-MUHAMMADOSAMA") -> dict[str, Any]:
    return {"v": 1, "type": "logged_in", "hostname": hostname}


async def _send(ws: Any, msg: dict[str, Any]) -> None:
    await ws.send(json.dumps(msg))


async def _recv(ws: Any) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), 2)
    msg = json.loads(raw)
    assert isinstance(msg, dict)
    assert "AB23CD45" not in json.dumps(msg)
    return msg


@contextlib.asynccontextmanager
async def _listening():
    server = None
    for _ in range(16):
        candidate = await start_server("127.0.0.1", 0)
        safe = True
        for sock in candidate.sockets:
            host, port = sock.getsockname()[:2]
            if host != "127.0.0.1" or port in _FORBIDDEN:
                safe = False
                break
        if safe and candidate.sockets:
            server = candidate
            break
        candidate.close()
        await candidate.wait_closed()
    if server is None:
        raise RuntimeError("could not bind 127.0.0.1 on a permitted port")
    try:
        yield listening_uri(server)
    finally:
        server.close()
        await server.wait_closed()


def _run(coro: Any) -> None:
    asyncio.run(asyncio.wait_for(coro, 10))


def test_password_hash_hides_secret() -> None:
    stored = hash_password("AB23CD45")
    again = hash_password("AB23CD45")
    assert stored != again
    assert b"AB23CD45" not in stored
    assert password_matches(stored, "AB23CD45")
    assert not password_matches(stored, "AB23CD46")
    assert not password_matches(stored, "")
    assert not password_matches(stored, None)
    assert not password_matches(b"", "AB23CD45")


async def _closed_after(ws: Any, code: str) -> None:
    assert await _recv(ws) == {"v": 1, "type": "error", "code": code}
    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(ws.recv(), 2)


async def _password_register_closes(uri: str) -> None:
    async with connect(uri) as robot, connect(uri) as again:
        await _send(robot, _register())
        await _closed_after(robot, "legacy_robot")
        await _send(again, _register(password="ZZ99YY88"))
        await _closed_after(again, "legacy_robot")


def test_password_register_is_legacy_robot() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _password_register_closes(uri)

    _run(_body())


async def _robot_id_login_closes(uri: str) -> None:
    bodies = (
        _login(),
        _login(robot_id="000000000"),
        _login(robot_id="12345678"),
        {"v": 1, "type": "login", "password": "AB23CD45"},
    )
    for body in bodies:
        async with connect(uri) as operator:
            await _send(operator, body)
            await _closed_after(operator, "legacy_login")


def test_robot_id_login_is_legacy_login() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _robot_id_login_closes(uri)

    _run(_body())


async def _logout_is_unknown(uri: str) -> None:
    async with connect(uri) as bare:
        await _send(bare, {"v": 1, "type": "logout"})
        assert await _recv(bare) == {"type": "error", "error": "unknown_type"}
        await _send(bare, {"v": 1, "type": "logout"})
        assert await _recv(bare) == {"type": "error", "error": "unknown_type"}
    async with connect(uri) as operator:
        await _send(
            operator,
            {"v": 1, "type": "login", "username": "nobody", "password": "x"},
        )
        assert await _recv(operator) == {"v": 1, "type": "error", "code": "auth"}
        await _send(operator, {"v": 1, "type": "logout"})
        assert await _recv(operator) == {
            "v": 1,
            "type": "error",
            "code": "unknown_type",
        }
        await _send(operator, {"v": 1, "type": "logout"})
        assert await _recv(operator) == {
            "v": 1,
            "type": "error",
            "code": "unknown_type",
        }


def test_logout_is_unknown_type() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _logout_is_unknown(uri)

    _run(_body())


async def _bad_id_stays_open(uri: str) -> None:
    async with connect(uri) as robot:
        await _send(robot, _register(robot_id="12"))
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "bad_id"}
        await _send(
            robot,
            {
                "v": 1,
                "type": "register",
                "robot_id": 123456789,
                "password": "AB23CD45",
                "hostname": "h",
            },
        )
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "bad_id"}
        await _send(robot, _register(password=""))
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "auth"}
        await robot.send("{")
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "bad_json"}
        await _send(robot, _register())
        await _closed_after(robot, "legacy_robot")


def test_bad_id_stays_open_until_a_password_register() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _bad_id_stays_open(uri)

    _run(_body())


async def _ice_join_still_reaches_room(uri: str) -> None:
    async with (
        connect(uri) as robot,
        connect(uri) as controlling,
        connect(uri) as controlled,
    ):
        await _send(robot, _register())
        await _closed_after(robot, "legacy_robot")
        await _send(
            controlling, {"type": "join", "room": "hello-world", "role": "controlling"}
        )
        await _send(
            controlled, {"type": "join", "room": "hello-world", "role": "controlled"}
        )
        joined_a = await _recv(controlling)
        joined_b = await _recv(controlled)
        assert joined_a["type"] == "joined"
        assert joined_a["room"] == "hello-world"
        assert joined_a["role"] == "controlling"
        assert "v" not in joined_a
        assert joined_b["type"] == "joined"
        assert joined_b["role"] == "controlled"
        await _send(controlling, CANDIDATES)
        peer = await _recv(controlled)
        assert peer["type"] == "candidates"
        assert peer["username"] == "ufragA"
        assert peer["role"] == "controlling"
        assert peer["candidates"] == CANDIDATES["candidates"]
        async with connect(uri) as operator:
            await _send(operator, _login())
            await _closed_after(operator, "legacy_login")


def test_ice_join_still_reaches_room() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _ice_join_still_reaches_room(uri)

    _run(_body())


async def _garbage_first_frame_stays_on_ice(uri: str) -> None:
    async with connect(uri) as ws:
        await ws.send("not-json")
        assert json.loads(await ws.recv()) == {"type": "error", "error": "bad_json"}
        await _send(ws, _register())
        assert json.loads(await ws.recv()) == {"type": "error", "error": "unknown_type"}
        await _send(ws, {"type": "join", "room": "hello-world", "role": "controlling"})
        joined = json.loads(await ws.recv())
        assert joined["type"] == "joined"
        assert joined["role"] == "controlling"


def test_garbage_first_frame_stays_on_ice() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _garbage_first_frame_stays_on_ice(uri)

    _run(_body())
