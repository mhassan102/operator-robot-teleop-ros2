"""Pair a robot with one operator and relay the session JSON.

Operator accounts and robot token rows live in sqlite. A password
``register`` is ``legacy_robot`` and the socket closes. A ``login``
with ``robot_id`` and no username is ``legacy_login`` and the socket
closes. ``logout`` is ``unknown_type``. The plaintext password and the
device token are never logged and never copied into an outbound message.

A username login includes ``username`` and then one ``fleet`` snapshot.
That login can approve a robot and attach to one online approved robot.
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

from packaging.registry import store as registry_store
from packaging.registry.store import (
    FLEET_COALESCE_S,
    FLEET_ITERATIONS,
    LAST_SEEN_INTERVAL_S,
    PENDING_CAP,
    RobotRow,
    age_seconds,
    classify,
    counts_from_classes,
    delete_old_pending,
    field_text,
    insert_robot,
    load_robots,
    operator_rows,
    read_operator,
    robot_ids,
    set_robot_approval,
    status_label,
    connect as connect_db,
    update_robot_last_seen,
    update_robot_phase,
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
        self.sweep_mark: Optional[str] = None
        self.stale_deadline: Optional[float] = None
        self.lock = asyncio.Lock()


class Registry:
    """Sqlite operators and robot tokens. Live sockets stay in memory."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._db = connect_db(db_path)
        delete_old_pending(self._db)
        self.robots: dict = {}
        for row in load_robots(self._db):
            self.robots[row.robot_id] = RobotRecord(row)
        self._operator_ws: dict = {}
        self._ws_user: dict = {}
        self._login_hash: dict = {}
        self._fail_count: dict = {}
        self._fail_hash: dict = {}
        self._fleet_task: Optional[asyncio.Task] = None
        self._maintain_task: Optional[asyncio.Task] = None
        self._scale_robots = False
        self._scale_dashboards = False

    def close(self) -> None:
        for name in ("_fleet_task", "_maintain_task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None and not task.done():
                task.cancel()
        self._db.close()

    def _ensure_maintain(self) -> None:
        task = self._maintain_task
        if task is not None and not task.done():
            return
        self._maintain_task = asyncio.get_running_loop().create_task(self._maintain())

    async def handle(self, ws: Any, first: dict) -> None:
        self._ensure_maintain()
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
        msg: Optional[dict] = first
        while True:
            if msg is None:
                await _send(ws, _error("bad_json"))
            elif ws in self._ws_user:
                await self._username_frame(ws, msg)
            elif msg.get("type") == "login":
                await self._accept_login(ws, msg)
            else:
                await _send(ws, _error("unknown_type"))
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
        robot_id = msg.get("robot_id")
        if not valid_robot_id(robot_id):
            await _send(ws, _error("bad_id"))
            return None
        token = msg.get("token")
        if isinstance(token, str) and token != "":
            return await self._accept_token_register(ws, msg, token)
        password = msg.get("password")
        if isinstance(password, str) and password != "":
            await _send(ws, _error("legacy_robot"))
            with contextlib.suppress(ConnectionClosed):
                await ws.close()
            return None
        await _send(ws, _error("auth"))
        return None

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
            record.sweep_mark = None
            record.stale_deadline = None
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
            self._clear_sweep_if_fresh(record)
            self._kick_fleet()
            return
        if time.monotonic() - record.last_seen_written >= LAST_SEEN_INTERVAL_S:
            update_robot_last_seen(self._db, record.robot_id, now)
            record.last_seen_written = time.monotonic()
        self._clear_sweep_if_fresh(record)

    async def _accept_login(self, ws: Any, msg: dict) -> None:
        if msg.get("v") != 1 or msg.get("type") != "login":
            await _send(ws, _error("unknown_type"))
            return
        username = msg.get("username")
        if isinstance(username, str) and username != "":
            await self._accept_username_login(ws, msg, username)
            return
        await _send(ws, _error("legacy_login"))
        with contextlib.suppress(ConnectionClosed):
            await ws.close()

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
        self._login_hash[username] = bytes(secret_hash)
        await _send(ws, {"v": 1, "type": "logged_in", "username": username})
        self._operator_ws[username] = ws
        self._ws_user[ws] = username
        await self._broadcast_fleet()
        self._cancel_fleet()

    async def _forward(self, session: Any, ws: Any, side: str, msg: dict) -> None:
        allowed = _FROM_ROBOT if side == "robot" else _FROM_OPERATOR
        if msg.get("v") != 1 or msg.get("type") not in allowed:
            await _send(ws, _error("unknown_type"))
            return
        status_phase: Any = None
        started = False
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
            if getattr(session, "durable", False):
                if side == "robot" and msg.get("type") == "status":
                    status_phase = msg.get("phase")
                if side == "operator" and msg.get("type") == "start":
                    if not session.start_in_progress:
                        session.start_in_progress = True
                        started = True
            await _send(peer, msg)
        if status_phase is not None and session.robot_ws is ws:
            self._apply_status(session, status_phase)
        if started:
            self._kick_fleet()

    def _still(self, session: Any, ws: Any, side: str) -> bool:
        current = self.robots.get(session.robot_id)
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
            if self._login_hash.get(username) is not None and (
                self._operator_ws.get(username) is None
            ):
                self._login_hash.pop(username, None)
            await self._release_hold(ws, reply=False)
            self._kick_fleet()
            return
        for record in list(self.robots.values()):
            if record.robot_ws is ws or record.operator_ws is ws:
                await self._drop_durable(record, ws)
                return

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
                record.stale_deadline = None
                record.sweep_mark = None
            elif record.operator_ws is ws:
                record.operator_ws = None
                record.operator_name = None
                record.start_in_progress = False
                record.stale_deadline = None
                self._mark_after_detach(record)
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

    async def _username_frame(self, ws: Any, msg: dict) -> None:
        if msg.get("v") != 1:
            await _send(ws, _error("unknown_type"))
            return
        kind = msg.get("type")
        if kind == "approve":
            await self._approve(ws, msg)
        elif kind == "attach":
            await self._attach(ws, msg)
        elif kind == "release":
            await self._release(ws)
        elif kind == "sign_out":
            await self._sign_out(ws)
        elif kind == "login":
            await _send(ws, _error("already_attached"))
        elif kind in _FROM_OPERATOR:
            record = self._held_record(ws)
            if record is None:
                await _send(ws, _error("offline"))
                return
            await self._forward(record, ws, "operator", msg)
        else:
            await _send(ws, _error("unknown_type"))

    async def _approve(self, ws: Any, msg: dict) -> None:
        robot_id = msg.get("robot_id")
        record = self.robots.get(robot_id) if valid_robot_id(robot_id) else None
        if record is None:
            await _send(ws, _error("not_found"))
            log.info("approve code=not_found")
            return
        record.approval = "approved"
        set_robot_approval(self._db, record.robot_id, "approved")
        await _send(
            ws, {"v": 1, "type": "approved", "robot_id": record.robot_id}
        )
        log.info("approve robot_id=%s code=approved", record.robot_id)
        self._kick_fleet()

    async def _attach(self, ws: Any, msg: dict) -> None:
        username = self._ws_user.get(ws)
        robot_id = msg.get("robot_id")
        record = self.robots.get(robot_id) if valid_robot_id(robot_id) else None
        if record is None:
            await _send(ws, _error("not_found"))
            log.info("attach code=not_found")
            return
        if record.approval != "approved":
            await _send(ws, _error("pending"))
            log.info("attach robot_id=%s code=pending", record.robot_id)
            return
        if not self._online_for_attach(record):
            await _send(ws, _error("offline"))
            log.info("attach robot_id=%s code=offline", record.robot_id)
            return
        if record.operator_ws is not None and record.operator_ws is not ws:
            await _send(ws, _error("busy"))
            log.info("attach robot_id=%s code=busy", record.robot_id)
            return
        if self._held_record(ws) is not None:
            await _send(ws, _error("already_attached"))
            log.info(
                "attach robot_id=%s code=already_attached", record.robot_id
            )
            return
        robot: Any = None
        hostname = ""
        location = ""
        mode = "manual"
        async with record.lock:
            if record.approval != "approved":
                code = "pending"
            elif not self._online_for_attach(record):
                code = "offline"
            elif record.operator_ws is not None and record.operator_ws is not ws:
                code = "busy"
            elif self._held_record(ws) is not None:
                code = "already_attached"
            else:
                code = ""
                record.operator_ws = ws
                record.operator_name = username if isinstance(username, str) else None
                robot = record.robot_ws
                hostname = record.hostname
                location = record.location if record.location else record.hostname
                mode = record.mode
                robot_id = record.robot_id
        if code:
            await _send(ws, _error(code))
            log.info("attach robot_id=%s code=%s", record.robot_id, code)
            return
        await _send(
            ws,
            {
                "v": 1,
                "type": "attached",
                "robot_id": robot_id,
                "hostname": hostname,
                "location": location,
                "mode": mode,
            },
        )
        if robot is not None:
            with contextlib.suppress(ConnectionClosed):
                await _send(robot, {"v": 1, "type": "operator_attached"})
        log.info(
            "attach robot_id=%s username=%s code=attached", robot_id, username
        )
        self._kick_fleet()

    async def _release(self, ws: Any) -> None:
        if ws not in self._ws_user:
            await _send(ws, _error("unknown_type"))
            return
        await self._release_hold(ws, reply=True)

    async def _sign_out(self, ws: Any) -> None:
        username = self._ws_user.get(ws)
        if self._held_record(ws) is not None:
            await self._release_hold(ws, reply=False)
        if username is not None and self._operator_ws.get(username) is ws:
            self._operator_ws.pop(username, None)
        self._ws_user.pop(ws, None)
        if username is not None and self._operator_ws.get(username) is None:
            self._login_hash.pop(username, None)
        await _send(ws, {"v": 1, "type": "signed_out"})
        log.info("sign_out username=%s code=signed_out", username or "")

    async def _release_hold(self, ws: Any, reply: bool) -> bool:
        """Clear this socket's hold. A logged-in socket with no hold still gets ``released``."""
        record = self._held_record(ws)
        if record is None:
            if reply and ws in self._ws_user:
                await _send(ws, {"v": 1, "type": "released"})
            return False
        robot: Any = None
        async with record.lock:
            if record.operator_ws is not ws:
                robot = None
                cleared = False
            else:
                record.operator_ws = None
                record.operator_name = None
                record.start_in_progress = False
                record.stale_deadline = None
                robot = record.robot_ws
                cleared = True
        if not cleared:
            if reply and ws in self._ws_user:
                await _send(ws, {"v": 1, "type": "released"})
            return False
        self._mark_after_detach(record)
        if robot is not None and robot is not ws:
            with contextlib.suppress(ConnectionClosed):
                await _send(robot, {"v": 1, "type": "operator_detached"})
        if reply:
            await _send(ws, {"v": 1, "type": "released"})
            log.info("release robot_id=%s code=released", record.robot_id)
        self._kick_fleet()
        return True

    def _held_record(self, ws: Any) -> Optional[RobotRecord]:
        for record in self.robots.values():
            if record.operator_ws is ws:
                return record
        return None

    def _is_stale(self, record: RobotRecord) -> bool:
        """Open socket and a ``last_seen`` older than 30s. Missing is not stale."""
        if record.robot_ws is None:
            return False
        age = age_seconds(record.last_seen)
        if age is None:
            return False
        return age >= registry_store.STALE_AFTER_S

    def _online_for_attach(self, record: RobotRecord) -> bool:
        if record.robot_ws is None:
            return False
        if record.operator_ws is not None:
            return True
        return not self._is_stale(record)

    def _mark_after_detach(self, record: RobotRecord) -> None:
        record.stale_deadline = None
        if self._is_stale(record):
            record.sweep_mark = "offline"
        else:
            record.sweep_mark = None

    def _clear_sweep_if_fresh(self, record: RobotRecord) -> None:
        if self._is_stale(record):
            return
        if record.sweep_mark is None and record.stale_deadline is None:
            return
        record.sweep_mark = None
        record.stale_deadline = None
        self._kick_fleet()

    def _apply_status(self, record: RobotRecord, phase: object) -> None:
        if phase not in _PHASES:
            return
        if phase == "stopped":
            record.stale_deadline = None
        changed = phase != record.phase
        if changed:
            record.phase = str(phase)
            update_robot_phase(self._db, record.robot_id, record.phase)
        if phase in ("ready", "stopped", "error") and record.start_in_progress:
            record.start_in_progress = False
            changed = True
        if changed:
            self._kick_fleet()

    async def _maintain(self) -> None:
        while True:
            try:
                await asyncio.sleep(registry_store.MAINTAIN_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            try:
                await self._maintain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("registry maintain failed: %s", type(exc).__name__)

    async def _maintain_once(self) -> None:
        await self._reload_operators()
        await self._reload_robots()

    async def _reload_operators(self) -> None:
        for username, secret_hash, disabled in operator_rows(self._db):
            seen = self._fail_hash.get(username, _UNSEEN)
            if seen is not _UNSEEN and seen != secret_hash:
                self._fail_count.pop(username, None)
                self._fail_hash[username] = secret_hash
            ws = self._operator_ws.get(username)
            if ws is None:
                continue
            logged = self._login_hash.get(username)
            if disabled or (logged is not None and logged != secret_hash):
                await self._disconnect_username(ws)

    async def _disconnect_username(self, ws: Any) -> None:
        await self._drop(ws)
        with contextlib.suppress(ConnectionClosed):
            await ws.close()

    async def _reload_robots(self) -> None:
        ids = robot_ids(self._db)
        for robot_id in list(self.robots):
            record = self.robots.get(robot_id)
            if record is None:
                continue
            if robot_id not in ids:
                await self._forget_robot(record)
                continue
            await self._sweep_robot(record)

    async def _forget_robot(self, record: RobotRecord) -> None:
        if self.robots.get(record.robot_id) is not record:
            return
        if record.robot_id in robot_ids(self._db):
            return
        self.robots.pop(record.robot_id, None)
        robot = record.robot_ws
        operator = record.operator_ws
        record.robot_ws = None
        record.operator_ws = None
        record.operator_name = None
        record.start_in_progress = False
        record.stale_deadline = None
        record.sweep_mark = None
        if operator is not None and operator is not robot:
            with contextlib.suppress(ConnectionClosed):
                await _send(operator, _error("offline"))
        if robot is not None:
            with contextlib.suppress(ConnectionClosed):
                await robot.close()
        log.info("clear-robot robot_id=%s code=offline", record.robot_id)
        self._kick_fleet()

    async def _sweep_robot(self, record: RobotRecord) -> None:
        # Stale is an open socket with an old stamp. A gone socket is ``offline``
        # from ``_drop`` and does not get ``stop``.
        if record.robot_ws is None:
            record.stale_deadline = None
            return
        if not self._is_stale(record):
            if record.sweep_mark is not None or record.stale_deadline is not None:
                record.sweep_mark = None
                record.stale_deadline = None
                self._kick_fleet()
            return
        if record.operator_ws is None:
            record.stale_deadline = None
            if record.approval != "approved":
                return
            if record.sweep_mark != "offline":
                record.sweep_mark = "offline"
                self._kick_fleet()
            return
        if record.sweep_mark != "stale":
            record.sweep_mark = "stale"
            record.stale_deadline = (
                time.monotonic() + registry_store.STALE_STOP_WAIT_S
            )
            operator = record.operator_ws
            if operator is not None:
                with contextlib.suppress(ConnectionClosed):
                    await _send(operator, _error("stale"))
            self._kick_fleet()
            return
        if record.stale_deadline is None:
            return
        if time.monotonic() < record.stale_deadline:
            return
        await self._stale_stop(record)

    async def _stale_stop(self, record: RobotRecord) -> None:
        robot = record.robot_ws
        operator = record.operator_ws
        async with record.lock:
            if record.robot_ws is not robot or record.operator_ws is not operator:
                return
            if robot is None or operator is None:
                return
            record.operator_ws = None
            record.operator_name = None
            record.start_in_progress = False
            record.stale_deadline = None
        record.sweep_mark = "offline"
        with contextlib.suppress(ConnectionClosed):
            await _send(robot, {"v": 1, "type": "stop"})
        with contextlib.suppress(ConnectionClosed):
            await _send(operator, {"v": 1, "type": "operator_detached"})
        log.info("stale robot_id=%s code=stop", record.robot_id)
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
