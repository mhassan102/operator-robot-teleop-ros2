"""Pair a robot with one operator and relay the session JSON.

Password register and password login stay in memory. Operator accounts
and robot token rows live in sqlite. The plaintext password and the
device token are never logged and never copied into an outbound message.

``logged_in`` for a password session includes the robot hostname.
A username login includes ``username`` and then one ``fleet`` snapshot.
``v`` stays 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from websockets.exceptions import ConnectionClosed

from packaging.registry.store import (
    FLEET_COALESCE_S,
    FLEET_ITERATIONS,
    LAST_SEEN_INTERVAL_S,
    PENDING_CAP,
    RobotRow,
    classify,
    counts_from_classes,
    field_text,
    insert_robot,
    load_robots,
    read_operator,
    status_label,
    connect as connect_db,
    update_robot_last_seen,
    update_robot_registration,
    update_robot_state,
    utc_now,
    valid_robot_id,
)

_SALT_LEN = 16
_DK_LEN = 32
_ITERATIONS = 20_000
_LOCKOUT_AFTER = 4
_UNSEEN = object()

_FROM_ROBOT = frozenset({"inventory", "config_ok", "status", "error"})
_FROM_OPERATOR = frozenset({"config", "start", "stop"})
_PHASES = frozenset({"idle", "starting", "ready", "stopped", "error"})
_MODES = frozenset({"manual", "autonomous"})

log = logging.getLogger(__name__)


def hash_password(password: str, iterations: int = _ITERATIONS) -> bytes:
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations")
    salt = os.urandom(_SALT_LEN)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_DK_LEN,
    )
    return salt + digest


def password_matches(
    stored: bytes, password: object, iterations: int = _ITERATIONS
) -> bool:
    if not isinstance(stored, (bytes, bytearray)) or len(stored) != _SALT_LEN + _DK_LEN:
        return False
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        return False
    if not isinstance(password, str):
        password = ""
    salt = bytes(stored[:_SALT_LEN])
    expected = bytes(stored[_SALT_LEN:])
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_DK_LEN,
    )
    return hmac.compare_digest(digest, expected)


def _error(code: str) -> dict:
    return {"v": 1, "type": "error", "code": code}


def _loads(raw: Any) -> Optional[dict]:
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


async def _send(ws: Any, msg: dict) -> None:
    await ws.send(json.dumps(msg))


class Session:
    """In-memory password scaffold. Not a sqlite row."""

    durable = False

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id
        self.password_hash = b""
        self.hostname = ""
        self.robot_ws: Any = None
        self.operator_ws: Any = None
        self.lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            "Session(%r, robot=%s, operator=%s)"
            % (self.robot_id, self.robot_ws is not None, self.operator_ws is not None)
        )


class RobotRecord:
    """Live sockets for one sqlite robot row. The row is not popped on close."""

    durable = True

    def __init__(self, row: RobotRow) -> None:
        self.robot_id = row.robot_id
        self.secret_hash = row.secret_hash
        self.iterations = row.iterations
        self.hostname = row.hostname
        self.location = row.location
        self.mode = row.mode
        self.approval = row.approval
        self.phase = row.phase
        self.last_seen = row.last_seen
        self.created_at = row.created_at
        self.robot_ws: Any = None
        self.operator_ws: Any = None
        self.operator_name: Optional[str] = None
        self.start_in_progress = False
        self.last_seen_written = time.monotonic()
        self.lock = asyncio.Lock()


class Registry:
    """Password sessions in memory, plus sqlite operators and robot tokens."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._db = connect_db(db_path)
        self.sessions: dict = {}
        self.robots: dict = {}
        for row in load_robots(self._db):
            self.robots[row.robot_id] = RobotRecord(row)
        self._operator_ws: dict = {}
        self._ws_user: dict = {}
        self._fail_count: dict = {}
        self._fail_hash: dict = {}
        self._fleet_task: Optional[asyncio.Task] = None
        self._scale_robots = False
        self._scale_dashboards = False

    def close(self) -> None:
        task = self._fleet_task
        self._fleet_task = None
        if task is not None and not task.done():
            task.cancel()
        self._db.close()

    async def handle(self, ws: Any, first: dict) -> None:
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

    async def _run_robot(self, ws: Any, first: dict) -> None:
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

    async def _run_operator(self, ws: Any, first: dict) -> None:
        session: Any = None
        msg: Optional[dict] = first
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

    async def _relay_loop(self, ws: Any, session: Any, side: str) -> None:
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
            if side == "robot" and msg.get("type") == "presence":
                if getattr(session, "durable", False):
                    await self._presence(session, ws, msg)
                else:
                    await _send(ws, _error("unknown_type"))
                continue
            await self._forward(session, ws, side, msg)

    async def _accept_register(self, ws: Any, msg: dict) -> Any:
        if msg.get("v") != 1 or msg.get("type") != "register":
            await _send(ws, _error("unknown_type"))
            return None
        token = msg.get("token")
        if isinstance(token, str) and token != "":
            return await self._accept_token_register(ws, msg, token)
        return await self._accept_password_register(ws, msg)

    async def _accept_password_register(self, ws: Any, msg: dict) -> Optional[Session]:
        robot_id = msg.get("robot_id")
        password = msg.get("password")
        hostname = msg.get("hostname", "")
        if (
            not isinstance(robot_id, str)
            or not valid_robot_id(robot_id)
            or not isinstance(password, str)
            or password == ""
            or not isinstance(hostname, str)
        ):
            await _send(ws, _error("bad_id"))
            return None
        self._db.commit()
        digest = await asyncio.to_thread(hash_password, password)
        session = self.sessions.get(robot_id)
        if session is None:
            session = Session(robot_id)
            current = self.sessions.get(robot_id)
            if current is None:
                self.sessions[robot_id] = session
            else:
                session = current
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

    async def _accept_token_register(self, ws: Any, msg: dict, token: str) -> Any:
        robot_id = msg.get("robot_id")
        if not valid_robot_id(robot_id):
            await _send(ws, _error("bad_id"))
            return None
        parsed = _token_fields(msg)
        if parsed is None:
            await _send(ws, _error("bad_field"))
            return None
        hostname, location, mode = parsed
        existing = self.robots.get(robot_id)
        if existing is None:
            if self._pending_count() >= PENDING_CAP:
                await _send(ws, _error("full"))
                return None
            self._db.commit()
            digest = await asyncio.to_thread(hash_password, token, FLEET_ITERATIONS)
            existing = self.robots.get(robot_id)
            if existing is None:
                if self._pending_count() >= PENDING_CAP:
                    await _send(ws, _error("full"))
                    return None
                return await self._insert_token_robot(
                    ws, robot_id, digest, hostname, location, mode
                )
        stored = bytes(existing.secret_hash)
        iterations = existing.iterations
        self._db.commit()
        matched = await asyncio.to_thread(
            password_matches, stored, token, iterations
        )
        existing = self.robots.get(robot_id)
        if existing is None or bytes(existing.secret_hash) != stored:
            await _send(ws, _error("auth"))
            return None
        if not matched:
            await _send(ws, _error("auth"))
            return None
        return await self._take_token_socket(ws, existing, hostname, location, mode)

    async def _insert_token_robot(
        self,
        ws: Any,
        robot_id: str,
        digest: bytes,
        hostname: str,
        location: str,
        mode: str,
    ) -> RobotRecord:
        now = utc_now()
        record = RobotRecord(
            RobotRow(
                robot_id=robot_id,
                secret_hash=digest,
                iterations=FLEET_ITERATIONS,
                hostname=hostname,
                location=location,
                mode=mode,
                approval="pending",
                phase="idle",
                last_seen=now,
                created_at=now,
            )
        )
        record.robot_ws = ws
        record.last_seen_written = time.monotonic()
        insert_robot(
            self._db,
            robot_id,
            digest,
            FLEET_ITERATIONS,
            hostname,
            location,
            mode,
            "pending",
            "idle",
            now,
            now,
        )
        self.robots[robot_id] = record
        await _send(ws, {"v": 1, "type": "registered", "approval": "pending"})
        self._kick_fleet()
        return record

    async def _take_token_socket(
        self,
        ws: Any,
        record: RobotRecord,
        hostname: str,
        location: str,
        mode: str,
    ) -> Optional[RobotRecord]:
        now = utc_now()
        # Point robot_ws at this socket before closing the previous one.
        # _drop ignores a close that is no longer current, so a replaced
        # connection does not pop the row or clear a hold.
        old: Any = None
        async with record.lock:
            old = record.robot_ws
            record.robot_ws = ws
            record.hostname = hostname
            record.location = location
            record.mode = mode
            record.last_seen = now
        record.last_seen_written = time.monotonic()
        update_robot_registration(
            self._db, record.robot_id, hostname, location, mode, now
        )
        if old is not None and old is not ws:
            with contextlib.suppress(ConnectionClosed):
                await old.close()
        async with record.lock:
            if record.robot_ws is not ws:
                return None
            operator = record.operator_ws
            approval = record.approval
        await _send(ws, {"v": 1, "type": "registered", "approval": approval})
        if operator is not None:
            async with record.lock:
                still = record.robot_ws is ws and record.operator_ws is operator
            if still:
                await _send(ws, {"v": 1, "type": "operator_attached"})
        self._kick_fleet()
        return record

    async def _presence(self, record: RobotRecord, ws: Any, msg: dict) -> None:
        if msg.get("v") != 1:
            await _send(ws, _error("unknown_type"))
            return
        if record.robot_ws is not ws:
            return
        phase = msg.get("phase")
        if phase not in _PHASES:
            await _send(ws, _error("bad_field"))
            return
        if "mode" in msg:
            mode = msg.get("mode")
            if mode not in _MODES:
                await _send(ws, _error("bad_field"))
                return
        else:
            mode = record.mode
        if "hostname" in msg:
            hostname = msg.get("hostname")
            if not field_text(hostname):
                await _send(ws, _error("bad_field"))
                return
        else:
            hostname = record.hostname
        if "location" in msg:
            location = msg.get("location")
            if not field_text(location):
                await _send(ws, _error("bad_field"))
                return
        else:
            location = record.location
        changed = (
            mode != record.mode
            or location != record.location
            or hostname != record.hostname
            or phase != record.phase
        )
        now = utc_now()
        record.mode = mode
        record.location = location
        record.hostname = hostname
        record.phase = phase
        record.last_seen = now
        if changed:
            update_robot_state(
                self._db,
                record.robot_id,
                hostname,
                location,
                mode,
                phase,
                now,
            )
            record.last_seen_written = time.monotonic()
            self._kick_fleet()
            return
        if time.monotonic() - record.last_seen_written >= LAST_SEEN_INTERVAL_S:
            update_robot_last_seen(self._db, record.robot_id, now)
            record.last_seen_written = time.monotonic()

    async def _accept_login(self, ws: Any, msg: dict) -> Optional[Session]:
        if msg.get("v") != 1 or msg.get("type") != "login":
            await _send(ws, _error("unknown_type"))
            return None
        username = msg.get("username")
        if isinstance(username, str) and username != "":
            await self._accept_username_login(ws, msg, username)
            return None
        robot_id = msg.get("robot_id")
        if not isinstance(robot_id, str) or not valid_robot_id(robot_id):
            await _send(ws, _error("offline"))
            return None
        if robot_id in self.robots:
            await _send(ws, _error("legacy_login"))
            with contextlib.suppress(ConnectionClosed):
                await ws.close()
            return None
        return await self._accept_password_login(ws, msg, robot_id)

    async def _accept_password_login(
        self, ws: Any, msg: dict, robot_id: str
    ) -> Optional[Session]:
        session = self.sessions.get(robot_id)
        if session is None or session.robot_ws is None:
            await _send(ws, _error("offline"))
            return None
        password = msg.get("password")
        code = "auth"
        hostname = ""
        for _attempt in range(3):
            async with session.lock:
                if session.robot_ws is None:
                    code = "offline"
                    break
                stored = bytes(session.password_hash)
                hostname = session.hostname if isinstance(session.hostname, str) else ""
            self._db.commit()
            matched = await asyncio.to_thread(password_matches, stored, password)
            async with session.lock:
                if session.robot_ws is None:
                    code = "offline"
                    break
                if bytes(session.password_hash) != stored:
                    continue
                if not matched:
                    code = "auth"
                    break
                if session.operator_ws is not None:
                    code = "busy"
                    break
                session.operator_ws = ws
                hostname = session.hostname if isinstance(session.hostname, str) else ""
                code = ""
                break
        else:
            code = "auth"
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

    async def _accept_username_login(self, ws: Any, msg: dict, username: str) -> None:
        holder = self._operator_ws.get(username)
        if holder is not None:
            await _send(ws, _error("already_attached"))
            return
        row = read_operator(self._db, username)
        stored_hash = row[0] if row is not None else None
        seen = self._fail_hash.get(username, _UNSEEN)
        if seen is not _UNSEEN and seen != stored_hash:
            self._fail_count[username] = 0
        self._fail_hash[username] = stored_hash
        if self._fail_count.get(username, 0) >= _LOCKOUT_AFTER:
            self._fail_count[username] = self._fail_count.get(username, 0) + 1
            await _send(ws, _error("auth"))
            return
        if row is None:
            self._fail_count[username] = self._fail_count.get(username, 0) + 1
            await _send(ws, _error("auth"))
            return
        secret_hash, iterations, _disabled = row
        self._db.commit()
        matched = await asyncio.to_thread(
            password_matches, secret_hash, msg.get("password"), iterations
        )
        fresh = read_operator(self._db, username)
        if fresh is None or fresh[0] != secret_hash:
            self._fail_count[username] = 0
            self._fail_hash[username] = None if fresh is None else fresh[0]
            await _send(ws, _error("auth"))
            return
        if fresh[2] or not matched:
            self._fail_count[username] = self._fail_count.get(username, 0) + 1
            self._fail_hash[username] = secret_hash
            await _send(ws, _error("auth"))
            return
        self._fail_count.pop(username, None)
        self._fail_hash.pop(username, None)
        await _send(ws, {"v": 1, "type": "logged_in", "username": username})
        self._operator_ws[username] = ws
        self._ws_user[ws] = username
        await self._broadcast_fleet()
        self._cancel_fleet()

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

    async def _forward(self, session: Any, ws: Any, side: str, msg: dict) -> None:
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

    def _still(self, session: Any, ws: Any, side: str) -> bool:
        if getattr(session, "durable", False):
            current = self.robots.get(session.robot_id)
        else:
            current = self.sessions.get(session.robot_id)
        if current is not session:
            return False
        if side == "robot":
            return session.robot_ws is ws
        return session.operator_ws is ws

    async def _drop(self, ws: Any) -> None:
        username = self._ws_user.pop(ws, None)
        if username is not None:
            if self._operator_ws.get(username) is ws:
                self._operator_ws.pop(username, None)
            self._kick_fleet()
            return
        for record in list(self.robots.values()):
            if record.robot_ws is ws or record.operator_ws is ws:
                await self._drop_durable(record, ws)
                return
        found = None
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

    async def _drop_durable(self, record: RobotRecord, ws: Any) -> None:
        notify_operator: Any = None
        notify_robot: Any = None
        async with record.lock:
            if record.robot_ws is ws:
                record.robot_ws = None
                notify_operator = record.operator_ws
                record.operator_ws = None
                record.operator_name = None
                record.start_in_progress = False
            elif record.operator_ws is ws:
                record.operator_ws = None
                record.operator_name = None
                record.start_in_progress = False
                notify_robot = record.robot_ws
            else:
                return
        if notify_operator is not None and notify_operator is not ws:
            with contextlib.suppress(ConnectionClosed):
                await _send(notify_operator, _error("offline"))
        if notify_robot is not None and notify_robot is not ws:
            with contextlib.suppress(ConnectionClosed):
                await _send(notify_robot, {"v": 1, "type": "operator_detached"})
        self._kick_fleet()

    def _pending_count(self) -> int:
        return sum(1 for record in self.robots.values() if record.approval == "pending")

    def _kick_fleet(self) -> None:
        if not self._ws_user:
            return
        task = self._fleet_task
        if task is not None and not task.done():
            return
        if task is not None and task.done() and not task.cancelled():
            err = task.exception()
            if err is not None:
                log.error("fleet snapshot failed: %s", type(err).__name__)
        self._fleet_task = asyncio.get_running_loop().create_task(self._push_fleet_later())

    def _cancel_fleet(self) -> None:
        task = self._fleet_task
        self._fleet_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _push_fleet_later(self) -> None:
        try:
            await asyncio.sleep(FLEET_COALESCE_S)
        except asyncio.CancelledError:
            raise
        self._fleet_task = None
        await self._broadcast_fleet()

    async def _broadcast_fleet(self) -> None:
        self._note_scale()
        message = self.fleet_message()
        for ws in list(self._ws_user):
            with contextlib.suppress(ConnectionClosed):
                await _send(ws, message)

    def _note_scale(self) -> None:
        connected = sum(1 for record in self.robots.values() if record.robot_ws is not None)
        if connected > 200 and not self._scale_robots:
            log.warning("connected robots %s exceed 200", connected)
            self._scale_robots = True
        dashboards = len(self._ws_user)
        if dashboards > 10 and not self._scale_dashboards:
            log.warning("dashboard sockets %s exceed 10", dashboards)
            self._scale_dashboards = True

    def fleet_message(self) -> dict:
        now = datetime.now(timezone.utc)
        classes = []
        robots = []
        for record in sorted(self.robots.values(), key=lambda item: item.robot_id):
            online = record.robot_ws is not None
            held = record.operator_ws is not None or record.start_in_progress
            classes.append(
                classify(
                    record.approval,
                    online,
                    held,
                    record.mode,
                    record.last_seen,
                    now,
                )
            )
            shown_location = record.location if record.location else record.hostname
            operator = record.operator_name if held and record.operator_name else None
            robots.append(
                {
                    "robot_id": record.robot_id,
                    "location": shown_location,
                    "hostname": record.hostname,
                    "status": status_label(
                        record.approval,
                        online,
                        held,
                        record.phase,
                        record.start_in_progress,
                        record.last_seen,
                        now,
                    ),
                    "mode": record.mode,
                    "approval": record.approval,
                    "phase": record.phase,
                    "operator": operator,
                    "last_seen": record.last_seen,
                }
            )
        return {
            "v": 1,
            "type": "fleet",
            "counts": counts_from_classes(classes),
            "robots": robots,
        }


def _token_fields(msg: dict) -> Optional[tuple]:
    if "hostname" in msg:
        hostname = msg.get("hostname")
        if not field_text(hostname):
            return None
    else:
        hostname = ""
    if "location" in msg:
        location = msg.get("location")
        if not field_text(location):
            return None
    else:
        location = ""
    if "mode" in msg:
        mode = msg.get("mode")
        if mode not in _MODES:
            return None
    else:
        mode = "manual"
    return hostname, location, mode
