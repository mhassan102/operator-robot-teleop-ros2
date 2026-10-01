"""Pair a robot app with one operator and relay the session JSON.

Passwords are stored as a salted hash and checked with a constant-time
compare. Sessions live in memory only. The plaintext password is never
logged and never copied into an outbound message.

``logged_in`` includes the hostname from the robot's ``register`` so the
operator window can show it. The field is extra; ``v`` stays 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
from typing import Any

from websockets.exceptions import ConnectionClosed

_SALT_LEN = 16
_DK_LEN = 32
_ITERATIONS = 20_000

_FROM_ROBOT = frozenset({"inventory", "config_ok", "status", "error"})
_FROM_OPERATOR = frozenset({"config", "start", "stop"})


def hash_password(password: str) -> bytes:
    salt = os.urandom(_SALT_LEN)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
        dklen=_DK_LEN,
    )
    return salt + digest


def password_matches(stored: bytes, password: object) -> bool:
    if not isinstance(stored, (bytes, bytearray)) or len(stored) != _SALT_LEN + _DK_LEN:
        return False
    if not isinstance(password, str):
        password = ""
    salt = bytes(stored[:_SALT_LEN])
    expected = bytes(stored[_SALT_LEN:])
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
        dklen=_DK_LEN,
    )
    return hmac.compare_digest(digest, expected)


def _error(code: str) -> dict[str, Any]:
    return {"v": 1, "type": "error", "code": code}


def _loads(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict):
        return None
    return msg


async def _send(ws: Any, msg: dict[str, Any]) -> None:
    await ws.send(json.dumps(msg))


def _valid_id(robot_id: object) -> bool:
    return isinstance(robot_id, str) and len(robot_id) == 9 and robot_id.isdigit()


class Session:
    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id
        self.password_hash = b""
        self.hostname = ""
        self.robot_ws: Any = None
        self.operator_ws: Any = None
        self.lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            f"Session({self.robot_id!r}, "
            f"robot={self.robot_ws is not None}, "
            f"operator={self.operator_ws is not None})"
        )


class Registry:
    """In-memory map of robot id → one robot socket and at most one operator."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def handle(self, ws: Any, first: dict[str, Any]) -> None:
        try:
            kind = first.get("type")
            if kind == "register":
                await self._run_robot(ws, first)
            elif kind == "login":
                await self._run_operator(ws, first)
            else:
                await _send(ws, _error("unknown_type"))
        except ConnectionClosed:
            pass
        finally:
            await self._drop(ws)

    async def _run_robot(self, ws: Any, first: dict[str, Any]) -> None:
        session = await self._accept_register(ws, first)
        if session is None:
            async for raw in ws:
                msg = _loads(raw)
                if msg is None:
                    await _send(ws, _error("bad_json"))
                    continue
                if msg.get("type") == "register":
                    session = await self._accept_register(ws, msg)
                    if session is not None:
                        break
                else:
                    await _send(ws, _error("unknown_type"))
            else:
                return
        await self._relay_loop(ws, session, "robot")

    async def _run_operator(self, ws: Any, first: dict[str, Any]) -> None:
        session: Session | None = None
        msg: dict[str, Any] | None = first
        while True:
            if msg is None:
                await _send(ws, _error("bad_json"))
            elif session is not None and not self._still(session, ws, "operator"):
                session = None
                if msg.get("type") == "login":
                    session = await self._accept_login(ws, msg)
                else:
                    await _send(ws, _error("offline"))
            elif session is None:
                if msg.get("type") == "login":
                    session = await self._accept_login(ws, msg)
                else:
                    await _send(ws, _error("unknown_type"))
            elif msg.get("v") == 1 and msg.get("type") == "logout":
                await self._logout(session, ws)
            else:
                await self._forward(session, ws, "operator", msg)
            msg = _loads(await ws.recv())

    async def _relay_loop(self, ws: Any, session: Session, side: str) -> None:
        async for raw in ws:
            if not self._still(session, ws, side):
                return
            msg = _loads(raw)
            if msg is None:
                await _send(ws, _error("bad_json"))
                continue
            if side == "robot" and msg.get("type") == "register":
                updated = await self._accept_register(ws, msg)
                if updated is not None:
                    session = updated
                continue
            await self._forward(session, ws, side, msg)

    async def _accept_register(self, ws: Any, msg: dict[str, Any]) -> Session | None:
        if msg.get("v") != 1 or msg.get("type") != "register":
            await _send(ws, _error("unknown_type"))
            return None
        robot_id = msg.get("robot_id")
        password = msg.get("password")
        hostname = msg.get("hostname", "")
        if (
            not isinstance(robot_id, str)
            or not _valid_id(robot_id)
            or not isinstance(password, str)
            or password == ""
            or not isinstance(hostname, str)
        ):
            await _send(ws, _error("bad_id"))
            return None
        digest = hash_password(password)
        session = self.sessions.get(robot_id)
        if session is None:
            session = Session(robot_id)
            self.sessions[robot_id] = session
        old: Any = None
        async with session.lock:
            old = session.robot_ws
            session.robot_ws = ws
            session.password_hash = digest
            session.hostname = hostname
        if self.sessions.get(robot_id) is not session or session.robot_ws is not ws:
            return None
        if old is not None and old is not ws:
            with contextlib.suppress(ConnectionClosed):
                await old.close()
        async with session.lock:
            if session.robot_ws is not ws:
                return None
            operator = session.operator_ws
        await _send(ws, {"v": 1, "type": "registered"})
        if operator is not None:
            async with session.lock:
                still = session.robot_ws is ws and session.operator_ws is operator
            if still:
                await _send(ws, {"v": 1, "type": "operator_attached"})
        return session

    async def _accept_login(self, ws: Any, msg: dict[str, Any]) -> Session | None:
        if msg.get("v") != 1 or msg.get("type") != "login":
            await _send(ws, _error("unknown_type"))
            return None
        robot_id = msg.get("robot_id")
        if not isinstance(robot_id, str) or not _valid_id(robot_id):
            await _send(ws, _error("offline"))
            return None
        session = self.sessions.get(robot_id)
        if session is None or session.robot_ws is None:
            await _send(ws, _error("offline"))
            return None
        hostname = ""
        async with session.lock:
            if session.robot_ws is None:
                code = "offline"
            elif not password_matches(session.password_hash, msg.get("password")):
                code = "auth"
            elif session.operator_ws is not None:
                code = "busy"
            else:
                session.operator_ws = ws
                code = ""
                hostname = session.hostname if isinstance(session.hostname, str) else ""
        if code:
            await _send(ws, _error(code))
            return None
        await _send(ws, {"v": 1, "type": "logged_in", "hostname": hostname})
        async with session.lock:
            robot = session.robot_ws if session.operator_ws is ws else None
        if robot is not None:
            with contextlib.suppress(ConnectionClosed):
                await _send(robot, {"v": 1, "type": "operator_attached"})
        return session

    async def _logout(self, session: Session, ws: Any) -> None:
        """Detach this operator. The robot id and password hash stay."""
        robot: Any = None
        async with session.lock:
            if session.operator_ws is not ws:
                attached = False
            else:
                session.operator_ws = None
                robot = session.robot_ws
                attached = True
        if not attached:
            await _send(ws, _error("offline"))
            return
        if robot is not None:
            with contextlib.suppress(ConnectionClosed):
                await _send(robot, {"v": 1, "type": "operator_detached"})
        await _send(ws, {"v": 1, "type": "logged_out"})

    async def _forward(
        self, session: Session, ws: Any, side: str, msg: dict[str, Any]
    ) -> None:
        allowed = _FROM_ROBOT if side == "robot" else _FROM_OPERATOR
        if msg.get("v") != 1 or msg.get("type") not in allowed:
            await _send(ws, _error("unknown_type"))
            return
        async with session.lock:
            if side == "robot" and session.robot_ws is not ws:
                return
            if side == "operator" and session.operator_ws is not ws:
                await _send(ws, _error("offline"))
                return
            peer = session.operator_ws if side == "robot" else session.robot_ws
            if peer is None:
                if side == "operator":
                    await _send(ws, _error("offline"))
                return
            await _send(peer, msg)

    def _still(self, session: Session, ws: Any, side: str) -> bool:
        if self.sessions.get(session.robot_id) is not session:
            return False
        if side == "robot":
            return session.robot_ws is ws
        return session.operator_ws is ws

    async def _drop(self, ws: Any) -> None:
        found: tuple[Session, str] | None = None
        for session in self.sessions.values():
            if session.robot_ws is ws:
                found = (session, "robot")
                break
            if session.operator_ws is ws:
                found = (session, "operator")
                break
        if found is None:
            return
        session, side = found
        notify: Any = None
        notify_robot: Any = None
        async with session.lock:
            if side == "robot" and session.robot_ws is ws:
                session.robot_ws = None
                notify = session.operator_ws
                session.operator_ws = None
                if self.sessions.get(session.robot_id) is session:
                    self.sessions.pop(session.robot_id, None)
            elif side == "operator" and session.operator_ws is ws:
                session.operator_ws = None
                notify_robot = session.robot_ws
                if (
                    session.robot_ws is None
                    and self.sessions.get(session.robot_id) is session
                ):
                    self.sessions.pop(session.robot_id, None)
            else:
                return
        if notify is not None and notify is not ws:
            with contextlib.suppress(ConnectionClosed):
                await _send(notify, _error("offline"))
        if notify_robot is not None and notify_robot is not ws:
            with contextlib.suppress(ConnectionClosed):
                await _send(notify_robot, {"v": 1, "type": "operator_detached"})
