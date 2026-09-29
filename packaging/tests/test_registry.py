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

_FORBIDDEN = frozenset(range(50000, 50101)) | {8766, 3479}

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


async def _register_login_relays_inventory(uri: str) -> None:
    config = {
        "v": 1,
        "type": "config",
        "link": "tailscale",
        "iface1": "wlp0s20f3",
        "iface2": None,
        "arm": "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00",
        "video": "/dev/video2",
    }
    status = {"v": 1, "type": "status", "phase": "ready", "detail": ""}
    bad_config = {
        "v": 1,
        "type": "error",
        "code": "bad_config",
        "detail": "unknown video",
    }
    async with connect(uri) as robot, connect(uri) as operator:
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}
        await _send(operator, _login())
        assert await _recv(operator) == _logged_in()
        assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
        await _send(robot, INVENTORY)
        assert await _recv(operator) == INVENTORY
        await _send(operator, config)
        assert await _recv(robot) == config
        await _send(robot, {"v": 1, "type": "config_ok"})
        assert await _recv(operator) == {"v": 1, "type": "config_ok"}
        await _send(operator, {"v": 1, "type": "start"})
        assert await _recv(robot) == {"v": 1, "type": "start"}
        await _send(robot, status)
        assert await _recv(operator) == status
        await _send(operator, {"v": 1, "type": "stop"})
        assert await _recv(robot) == {"v": 1, "type": "stop"}
        await _send(robot, bad_config)
        assert await _recv(operator) == bad_config
        await _send(robot, {"v": 2, "type": "inventory", "n": 1})
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "unknown_type"}
        await _send(robot, {"v": 1, "type": "start"})
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "unknown_type"}
        await _send(robot, {"v": 1, "type": "config_ok"})
        assert await _recv(operator) == {"v": 1, "type": "config_ok"}


def test_register_login_relays_inventory() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _register_login_relays_inventory(uri)

    _run(_body())


async def _wrong_password_does_not_relay(uri: str) -> None:
    async with (
        connect(uri) as robot,
        connect(uri) as bad,
        connect(uri) as good,
    ):
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}
        await _send(bad, _login(password="WRONGPWD"))
        assert await _recv(bad) == {"v": 1, "type": "error", "code": "auth"}
        await _send(good, _login())
        assert await _recv(good) == _logged_in()
        assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
        await _send(robot, INVENTORY)
        assert await _recv(good) == INVENTORY
        leaked = asyncio.create_task(bad.recv())
        done, _pending = await asyncio.wait({leaked}, timeout=0.4)
        if done:
            raise AssertionError(leaked.result())
        leaked.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
            await leaked


def test_wrong_password_does_not_relay() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _wrong_password_does_not_relay(uri)

    _run(_body())


async def _unknown_id_is_offline(uri: str) -> None:
    async with connect(uri) as operator:
        await _send(operator, _login(robot_id="000000000"))
        assert await _recv(operator) == {"v": 1, "type": "error", "code": "offline"}
        await _send(operator, _login(robot_id="12345678"))
        assert await _recv(operator) == {"v": 1, "type": "error", "code": "offline"}


def test_unknown_id_is_offline() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _unknown_id_is_offline(uri)

    _run(_body())


async def _second_operator_is_busy(uri: str) -> None:
    async with (
        connect(uri) as robot,
        connect(uri) as first,
        connect(uri) as second,
    ):
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}
        await _send(first, _login())
        assert await _recv(first) == _logged_in()
        assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
        await _send(second, _login())
        assert await _recv(second) == {"v": 1, "type": "error", "code": "busy"}
        await _send(second, _login(password="WRONGPWD"))
        assert await _recv(second) == {"v": 1, "type": "error", "code": "auth"}
        await _send(robot, INVENTORY)
        assert await _recv(first) == INVENTORY
        leaked = asyncio.create_task(second.recv())
        done, _pending = await asyncio.wait({leaked}, timeout=0.4)
        if done:
            raise AssertionError(leaked.result())
        leaked.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
            await leaked
        await first.close()
        logged_in = False
        for _ in range(10):
            await _send(second, _login())
            msg = await _recv(second)
            if msg == _logged_in():
                logged_in = True
                break
            assert msg == {"v": 1, "type": "error", "code": "busy"}
            await asyncio.sleep(0.05)
        assert logged_in
        assert await _recv(robot) == {"v": 1, "type": "operator_attached"}


def test_second_operator_is_busy() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _second_operator_is_busy(uri)

    _run(_body())


async def _reregister_replaces_robot_socket(uri: str) -> None:
    fresh = dict(INVENTORY)
    fresh["camera_page"] = "http://100.120.193.52:8889/cam/?new=1"
    async with (
        connect(uri) as old_robot,
        connect(uri) as new_robot,
        connect(uri) as operator,
    ):
        await _send(old_robot, _register(password="AB23CD45"))
        assert await _recv(old_robot) == {"v": 1, "type": "registered"}
        await _send(operator, _login(password="AB23CD45"))
        assert await _recv(operator) == _logged_in()
        assert await _recv(old_robot) == {"v": 1, "type": "operator_attached"}
        await _send(new_robot, _register(password="ZZ99YY88"))
        assert await _recv(new_robot) == {"v": 1, "type": "registered"}
        assert await _recv(new_robot) == {"v": 1, "type": "operator_attached"}
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(old_robot.recv(), 2)
        await _send(new_robot, fresh)
        assert await _recv(operator) == fresh
        async with connect(uri) as other:
            await _send(other, _login(password="AB23CD45"))
            assert await _recv(other) == {"v": 1, "type": "error", "code": "auth"}
            await _send(other, _login(password="ZZ99YY88"))
            assert await _recv(other) == {"v": 1, "type": "error", "code": "busy"}


def test_reregister_replaces_robot_socket() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _reregister_replaces_robot_socket(uri)

    _run(_body())


async def _robot_disconnect_is_offline(uri: str) -> None:
    async with connect(uri) as robot, connect(uri) as operator:
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}
        await _send(operator, _login())
        assert await _recv(operator) == _logged_in()
        assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
        await robot.close()
        assert await _recv(operator) == {"v": 1, "type": "error", "code": "offline"}
    async with connect(uri) as again:
        await _send(again, _login())
        assert await _recv(again) == {"v": 1, "type": "error", "code": "offline"}


def test_robot_disconnect_is_offline() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _robot_disconnect_is_offline(uri)

    _run(_body())


async def _bad_id(uri: str) -> None:
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
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "bad_id"}
        await robot.send("{")
        assert await _recv(robot) == {"v": 1, "type": "error", "code": "bad_json"}
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}


def test_bad_id() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _bad_id(uri)

    _run(_body())


async def _two_robots_do_not_cross(uri: str) -> None:
    inv_a = dict(INVENTORY)
    inv_a["camera_page"] = "http://100.120.193.52:8889/cam/?a=1"
    inv_b = dict(INVENTORY)
    inv_b["camera_page"] = "http://100.120.193.52:8889/cam/?b=1"
    async with (
        connect(uri) as robot_a,
        connect(uri) as robot_b,
        connect(uri) as op_a,
        connect(uri) as op_b,
    ):
        await _send(robot_a, _register(robot_id="111111111", password="AAAAAAAA"))
        await _send(robot_b, _register(robot_id="222222222", password="BBBBBBBB"))
        assert await _recv(robot_a) == {"v": 1, "type": "registered"}
        assert await _recv(robot_b) == {"v": 1, "type": "registered"}
        await _send(op_a, _login(robot_id="111111111", password="AAAAAAAA"))
        await _send(op_b, _login(robot_id="222222222", password="BBBBBBBB"))
        assert await _recv(op_a) == _logged_in()
        assert await _recv(op_b) == _logged_in()
        assert await _recv(robot_a) == {"v": 1, "type": "operator_attached"}
        assert await _recv(robot_b) == {"v": 1, "type": "operator_attached"}
        await _send(robot_a, inv_a)
        await _send(robot_b, inv_b)
        assert await _recv(op_a) == inv_a
        assert await _recv(op_b) == inv_b


def test_two_robots_do_not_cross() -> None:
    async def _body() -> None:
        async with _listening() as uri:
            await _two_robots_do_not_cross(uri)

    _run(_body())


async def _ice_join_still_reaches_room(uri: str) -> None:
    async with (
        connect(uri) as robot,
        connect(uri) as controlling,
        connect(uri) as controlled,
    ):
        await _send(robot, _register())
        assert await _recv(robot) == {"v": 1, "type": "registered"}
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
            assert await _recv(operator) == _logged_in()
            assert await _recv(robot) == {"v": 1, "type": "operator_attached"}


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
