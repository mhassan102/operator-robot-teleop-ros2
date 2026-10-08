"""Token register, username login, approve, and attach for app tests.

The registry under test is the one already in the tree. This module
only drives it. A file database is required because ``:memory:`` is
not shared with ``add-operator``.
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import os
from pathlib import Path
from typing import Any

from packaging.registry.admin import main as admin_main
from signalling.server import listening_uri, start_server

OPERATOR_PASSWORD = "operator-secret"
TOKEN = "ab" * 32
_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)


def seed(db: Path, *usernames: str) -> None:
    """Create operator rows. The password is not printed. The env is restored."""
    previous = os.environ.get("TELEOP_REGISTRY_DB")
    os.environ["TELEOP_REGISTRY_DB"] = str(db)
    original = getpass.getpass
    getpass.getpass = lambda prompt="": OPERATOR_PASSWORD
    try:
        for username in usernames:
            assert admin_main(["add-operator", username]) == 0
    finally:
        getpass.getpass = original
        if previous is None:
            os.environ.pop("TELEOP_REGISTRY_DB", None)
        else:
            os.environ["TELEOP_REGISTRY_DB"] = previous


@contextlib.asynccontextmanager
async def listening(db: Path):
    server = None
    for _ in range(16):
        candidate = await start_server("127.0.0.1", 0, db_path=str(db))
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


async def next_frame(ws: Any, timeout: float = 5) -> dict[str, Any]:
    """Next registry object that is not a fleet snapshot."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError("timed out waiting for a registry frame")
        msg = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        if isinstance(msg, dict) and msg.get("type") == "fleet":
            continue
        if not isinstance(msg, dict):
            raise AssertionError(msg)
        return msg


async def recv_type(ws: Any, kind: str, timeout: float = 5) -> dict[str, Any]:
    msg = await next_frame(ws, timeout)
    if msg.get("type") != kind:
        raise AssertionError(msg)
    return msg


async def login(ws: Any, username: str, password: str = OPERATOR_PASSWORD) -> dict[str, Any]:
    await ws.send(
        json.dumps(
            {
                "v": 1,
                "type": "login",
                "username": username,
                "password": password,
            }
        )
    )
    return await recv_type(ws, "logged_in")


async def approve_when_up(
    ws: Any, robot_id: str, timeout: float = 5
) -> dict[str, Any]:
    """Retry while the robot has not registered yet."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        await ws.send(
            json.dumps({"v": 1, "type": "approve", "robot_id": robot_id})
        )
        msg = await next_frame(ws, timeout=2)
        if msg.get("type") == "approved":
            return msg
        if msg.get("type") == "error" and msg.get("code") == "not_found":
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("robot did not register")
            await asyncio.sleep(0.05)
            continue
        raise AssertionError(msg)


async def attach(ws: Any, robot_id: str) -> dict[str, Any]:
    await ws.send(json.dumps({"v": 1, "type": "attach", "robot_id": robot_id}))
    msg = await next_frame(ws)
    if msg.get("type") != "attached":
        raise AssertionError(msg)
    return msg
