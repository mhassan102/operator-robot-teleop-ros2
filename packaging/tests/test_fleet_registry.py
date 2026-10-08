"""Sqlite operators and robot tokens. A password register closes.

The server binds 127.0.0.1 on a free port. It must not bind 8765, 8766,
3479, 8090, or anything in 50000–50100.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

_TURN = Path(__file__).resolve().parents[2] / "turn"
if str(_TURN) not in sys.path:
    sys.path.insert(0, str(_TURN))

import getpass

from packaging.registry import session as registry_session
from packaging.registry import store as registry_store
from packaging.registry.admin import main as admin_main
from packaging.registry.session import (
    Registry,
    hash_password,
    password_matches,
)
from packaging.registry.store import (
    FLEET_ITERATIONS,
    MAINTAIN_INTERVAL_S,
    PENDING_KEEP_S,
    STALE_STOP_WAIT_S,
    RobotRow,
    add_operator,
    classify,
    clear_robot,
    connect as store_connect,
    counts_from_classes,
    default_db_path,
    insert_robot,
    set_operator_disabled,
    set_operator_password,
    status_label,
    utc_now,
)
from signalling.server import listening_uri, registry_db_path, start_server
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)

_TOKEN = "device-token-0123456789abcdef0123456789abcdef"
_OPERATOR_PASSWORD = "correct-horse"
_ROBOT_PASSWORD = "AB23CD45"


class FakeWS:
    def __init__(self) -> None:
        self.sent: list = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True


def _modes(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _run(coro: Any) -> None:
    asyncio.run(asyncio.wait_for(coro, 20))


def _no_secret(msg: dict, *secrets: str) -> None:
    raw = json.dumps(msg)
    for secret in secrets:
        assert secret not in raw


async def _send(ws: Any, msg: dict) -> None:
    await ws.send(json.dumps(msg))


async def _recv(ws: Any) -> dict:
    raw = await asyncio.wait_for(ws.recv(), 2)
    msg = json.loads(raw)
    assert isinstance(msg, dict)
    return msg


@contextlib.asynccontextmanager
async def _listening(**kwargs: Any):
    server = None
    for _ in range(16):
        candidate = await start_server("127.0.0.1", 0, **kwargs)
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


def test_password_matches_keeps_the_old_iteration_default() -> None:
    fleet = hash_password(_TOKEN, FLEET_ITERATIONS)
    legacy = hash_password(_ROBOT_PASSWORD)
    assert password_matches(legacy, _ROBOT_PASSWORD)
    assert password_matches(fleet, _TOKEN, FLEET_ITERATIONS)
    assert not password_matches(fleet, _TOKEN)
    assert not password_matches(fleet, _TOKEN, 20_000)
    assert FLEET_ITERATIONS == 200_000


def test_import_does_not_create_the_database(tmp_path: Path, monkeypatch: Any) -> None:
    target = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(target))
    import packaging.registry
    import packaging.registry.admin
    import packaging.registry.store

    assert packaging.registry.Registry is Registry
    assert not target.exists()
    assert packaging.registry.admin.main is admin_main
    assert packaging.registry.store.connect is store_connect


def test_registry_paths_follow_the_env_and_the_home_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(tmp_path / "chosen.sqlite"))
    assert registry_db_path() == str(tmp_path / "chosen.sqlite")
    assert default_db_path() == registry_db_path()
    monkeypatch.delenv("TELEOP_REGISTRY_DB")
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = str(tmp_path / ".local" / "share" / "teleop" / "registry.sqlite")
    assert registry_db_path() == expected
    assert default_db_path() == expected
    assert not Path(expected).exists()


def test_memory_database_skips_wal() -> None:
    reg = Registry()
    try:
        mode = reg._db.execute("PRAGMA journal_mode").fetchone()[0]
        busy = reg._db.execute("PRAGMA busy_timeout").fetchone()[0]
        assert str(mode).lower() != "wal"
        assert busy == 5000
    finally:
        reg.close()


def test_durable_file_modes_and_second_open(tmp_path: Path) -> None:
    path = tmp_path / "private" / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            robot = FakeWS()
            created = await reg._accept_register(
                robot,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                    "mode": "autonomous",
                    "location": "dubai-lab",
                },
            )
            assert created is not None
            assert robot.sent[-1] == {
                "v": 1,
                "type": "registered",
                "approval": "pending",
            }
            _no_secret(robot.sent[-1], _TOKEN)
        finally:
            reg.close()

    _run(_body())
    assert _modes(path.parent) == 0o700
    assert _modes(path) == 0o600
    assert _TOKEN.encode() not in path.read_bytes()

    side = sqlite3.connect(path)
    side.execute(
        "UPDATE robots SET approval='approved', phase='ready' WHERE robot_id='222222222'"
    )
    side.commit()
    row = side.execute(
        "SELECT iterations, phase, approval, length(secret_hash) FROM robots"
    ).fetchone()
    side.close()
    assert row == (200_000, "ready", "approved", 48)

    reg = Registry(str(path))
    try:
        record = reg.robots["222222222"]
        assert record.approval == "approved"
        assert record.phase == "idle"
        assert record.robot_ws is None
        assert password_matches(record.secret_hash, _TOKEN, record.iterations)
    finally:
        reg.close()

    async def _reopen() -> None:
        again = Registry(str(path))
        try:
            bad = FakeWS()
            result = await again._accept_register(
                bad,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": "wrong-device-token",
                    "hostname": "other",
                },
            )
            assert result is None
            assert bad.closed is False
            assert bad.sent[-1] == {"v": 1, "type": "error", "code": "auth"}
            assert again.robots["222222222"].robot_ws is None
            assert again.robots["222222222"].hostname == "AUTOOS-DEV-MUHAMMADOSAMA"
            first_hash = bytes(again.robots["222222222"].secret_hash)
            good = FakeWS()
            held = FakeWS()
            again.robots["222222222"].operator_ws = held
            again.robots["222222222"].operator_name = "lab"
            accepted = await again._accept_register(
                good,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                    "mode": "manual",
                },
            )
            assert accepted is again.robots["222222222"]
            assert good.sent[0] == {
                "v": 1,
                "type": "registered",
                "approval": "approved",
            }
            assert good.sent[1] == {"v": 1, "type": "operator_attached"}
            assert bytes(again.robots["222222222"].secret_hash) == first_hash
            assert again.robots["222222222"].operator_ws is held
            old = FakeWS()
            old.closed = False
            # The previous socket in this reopen was None, so close was not
            # required. A second good register must not clear the hold.
            previous = good
            third = FakeWS()
            again.robots["222222222"].operator_ws = held
            accepted = await again._accept_register(
                third,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                },
            )
            assert previous.closed is True
            assert again.robots["222222222"].robot_ws is third
            await again._drop(previous)
            assert "222222222" in again.robots
            assert again.robots["222222222"].operator_ws is held
            assert again.robots["222222222"].operator_name == "lab"
            _no_secret(third.sent[0], _TOKEN)
        finally:
            again.close()

    _run(_reopen())
    reg = Registry(str(path))
    try:
        assert reg.robots["222222222"].phase == "idle"
        assert reg.robots["222222222"].approval == "approved"
        assert password_matches(
            reg.robots["222222222"].secret_hash, _TOKEN, FLEET_ITERATIONS
        )
    finally:
        reg.close()


def test_password_register_does_not_write_sqlite_or_grow_the_reply(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            long_host = "h" * 80
            ws = FakeWS()
            session = await reg._accept_register(
                ws,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "111111111",
                    "password": _ROBOT_PASSWORD,
                    "hostname": long_host,
                },
            )
            assert session is None
            assert ws.sent == [
                {"v": 1, "type": "error", "code": "legacy_robot"}
            ]
            assert ws.closed is True
            assert reg._db.execute("SELECT COUNT(*) FROM robots").fetchone()[0] == 0
            assert reg._db.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 0
            assert "111111111" not in reg.robots
            _no_secret(ws.sent[0], _ROBOT_PASSWORD)
        finally:
            reg.close()

    _run(_body())
    assert _ROBOT_PASSWORD.encode() not in path.read_bytes()


def test_token_register_rejects_a_bad_field_and_a_wrong_token(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            bad = FakeWS()
            result = await reg._accept_register(
                bad,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "h" * 65,
                    "mode": "manual",
                },
            )
            assert result is None
            assert bad.sent[-1]["code"] == "bad_field"
            assert bad.closed is False
            assert reg.robots == {}
            newline = FakeWS()
            result = await reg._accept_register(
                newline,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "location": "dubai\nlab",
                },
            )
            assert result is None
            assert newline.sent[-1]["code"] == "bad_field"
            mode = FakeWS()
            result = await reg._accept_register(
                mode,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "mode": "fly",
                },
            )
            assert result is None
            assert mode.sent[-1]["code"] == "bad_field"
            assert reg._db.execute("SELECT COUNT(*) FROM robots").fetchone()[0] == 0
            good = FakeWS()
            await reg._accept_register(
                good,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "password": _ROBOT_PASSWORD,
                    "hostname": "host-a",
                },
            )
            assert good.sent[-1]["approval"] == "pending"
            assert reg.robots["222222222"].mode == "manual"
            assert reg.robots["222222222"].location == ""
            stored = bytes(reg.robots["222222222"].secret_hash)
            wrong = FakeWS()
            result = await reg._accept_register(
                wrong,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": "other-device-token",
                    "hostname": "host-b",
                },
            )
            assert result is None
            assert wrong.closed is False
            assert wrong.sent[-1]["code"] == "auth"
            assert reg.robots["222222222"].robot_ws is good
            assert reg.robots["222222222"].hostname == "host-a"
            assert bytes(reg.robots["222222222"].secret_hash) == stored
        finally:
            reg.close()

    _run(_body())


def test_pending_cap_is_500_and_does_not_hash_a_refused_insert(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"
    conn = store_connect(str(path))
    created = utc_now()
    for index in range(499):
        insert_robot(
            conn,
            "%09d" % index,
            b"\x11" * 48,
            1,
            "",
            "",
            "manual",
            "pending",
            "idle",
            created,
            created,
        )
    insert_robot(
        conn,
        "000000499",
        b"\x11" * 48,
        1,
        "",
        "",
        "manual",
        "approved",
        "idle",
        created,
        created,
    )
    conn.close()

    async def _one_more() -> None:
        reg = Registry(str(path))
        try:
            assert reg._pending_count() == 499
            ws = FakeWS()
            calls = {"n": 0}
            real = asyncio.to_thread

            async def _spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
                calls["n"] += 1
                return await real(fn, *args, **kwargs)

            asyncio.get_running_loop()
            original = asyncio.to_thread
            asyncio.to_thread = _spy  # type: ignore[assignment]
            try:
                created_row = await reg._accept_register(
                    ws,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "900000000",
                        "token": _TOKEN,
                    },
                )
            finally:
                asyncio.to_thread = original  # type: ignore[assignment]
            assert created_row is not None
            assert calls["n"] == 1
            assert reg._pending_count() == 500
        finally:
            reg.close()

    _run(_one_more())

    async def _full() -> None:
        reg = Registry(str(path))
        try:
            ws = FakeWS()
            calls = {"n": 0}

            async def _spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
                calls["n"] += 1
                raise AssertionError("full must not hash")

            original = asyncio.to_thread
            asyncio.to_thread = _spy  # type: ignore[assignment]
            try:
                result = await reg._accept_register(
                    ws,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "900000001",
                        "token": _TOKEN,
                    },
                )
            finally:
                asyncio.to_thread = original  # type: ignore[assignment]
            assert result is None
            assert calls["n"] == 0
            assert ws.closed is False
            assert ws.sent[-1] == {"v": 1, "type": "error", "code": "full"}
            assert "900000001" not in reg.robots
            known = FakeWS()
            result = await reg._accept_register(
                known,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "000000001",
                    "token": "nope",
                },
            )
            assert result is None
            assert known.sent[-1]["code"] == "auth"
            assert known.closed is False
        finally:
            reg.close()

    _run(_full())


def test_presence_updates_memory_and_throttles_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            robot = FakeWS()
            await reg._accept_register(
                robot,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "host-a",
                    "location": "dubai-lab",
                    "mode": "manual",
                },
            )
            side = sqlite3.connect(path)
            side.execute(
                "UPDATE robots SET last_seen='2000-01-01T00:00:00Z' WHERE robot_id='222222222'"
            )
            side.commit()
            side.close()
            record = reg.robots["222222222"]
            await reg._presence(
                record,
                robot,
                {"v": 1, "type": "presence", "phase": "flying", "mode": "manual"},
            )
            assert robot.sent[-1]["code"] == "bad_field"
            assert record.mode == "manual"
            assert record.location == "dubai-lab"
            same = FakeWS()
            # Presence is only applied from the current socket. A different
            # socket does not update the row.
            await reg._presence(
                record,
                same,
                {"v": 1, "type": "presence", "phase": "ready", "mode": "autonomous"},
            )
            assert record.phase == "idle"
            await reg._presence(
                record,
                robot,
                {
                    "v": 1,
                    "type": "presence",
                    "phase": "idle",
                    "mode": "manual",
                    "location": "dubai-lab",
                    "hostname": "host-a",
                },
            )
            side = sqlite3.connect(path)
            stored = side.execute(
                "SELECT last_seen, phase, mode FROM robots WHERE robot_id='222222222'"
            ).fetchone()
            side.close()
            assert stored[0] == "2000-01-01T00:00:00Z"
            assert record.last_seen != "2000-01-01T00:00:00Z"
            dashboard = FakeWS()
            reg._ws_user[dashboard] = "lab"
            reg._operator_ws["lab"] = dashboard
            await reg._presence(
                record,
                robot,
                {
                    "v": 1,
                    "type": "presence",
                    "phase": "ready",
                    "mode": "autonomous",
                    "location": "sharjah",
                },
            )
            side = sqlite3.connect(path)
            stored = side.execute(
                "SELECT phase, mode, location, hostname FROM robots WHERE robot_id='222222222'"
            ).fetchone()
            side.close()
            assert stored == ("ready", "autonomous", "sharjah", "host-a")
            assert record.phase == "ready"
            deadline = time.monotonic() + 1
            while not dashboard.sent and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert dashboard.sent
            assert dashboard.sent[-1]["type"] == "fleet"
            assert dashboard.sent[-1]["robots"][0]["phase"] == "ready"
            assert dashboard.sent[-1]["robots"][0]["location"] == "sharjah"
            _no_secret(dashboard.sent[-1], _TOKEN)
            before = len(dashboard.sent)
            await reg._presence(
                record,
                robot,
                {
                    "v": 1,
                    "type": "presence",
                    "phase": "ready",
                    "mode": "autonomous",
                    "location": "sharjah",
                    "hostname": "host-a",
                },
            )
            await asyncio.sleep(0.12)
            assert len(dashboard.sent) == before
            record.last_seen_written = time.monotonic() - 31
            side = sqlite3.connect(path)
            side.execute(
                "UPDATE robots SET last_seen='2000-01-01T00:00:00Z' WHERE robot_id='222222222'"
            )
            side.commit()
            side.close()
            await reg._presence(
                record,
                robot,
                {
                    "v": 1,
                    "type": "presence",
                    "phase": "ready",
                    "mode": "autonomous",
                    "location": "sharjah",
                    "hostname": "host-a",
                },
            )
            side = sqlite3.connect(path)
            stored_seen = side.execute(
                "SELECT last_seen FROM robots WHERE robot_id='222222222'"
            ).fetchone()[0]
            side.close()
            assert stored_seen != "2000-01-01T00:00:00Z"
            assert len(dashboard.sent) == before
        finally:
            reg.close()

    _run(_body())


def test_fleet_counts_do_not_double_count() -> None:
    now = datetime.now(timezone.utc)
    fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = Registry()
    try:
        def _add(
            robot_id: str,
            approval: str,
            mode: str,
            online: bool,
            held: bool,
            last_seen: str | None,
            phase: str = "idle",
            start: bool = False,
        ) -> None:
            record = registry_session.RobotRecord(
                RobotRow(
                    robot_id=robot_id,
                    secret_hash=b"\x22" * 48,
                    iterations=FLEET_ITERATIONS,
                    hostname="host-" + robot_id[-1],
                    location="",
                    mode=mode,
                    approval=approval,
                    phase=phase,
                    last_seen=last_seen,
                    created_at=fresh,
                )
            )
            if online:
                record.robot_ws = object()
            if held:
                record.operator_ws = object()
                record.operator_name = "lab"
            record.start_in_progress = start
            reg.robots[robot_id] = record

        _add("100000001", "pending", "manual", True, False, fresh)
        _add("100000002", "approved", "manual", True, False, fresh)
        _add("100000003", "approved", "autonomous", True, False, fresh)
        _add("100000004", "approved", "manual", False, False, fresh)
        _add("100000005", "approved", "autonomous", True, True, fresh, "ready")
        _add("100000006", "approved", "manual", True, False, stale)
        _add("100000007", "approved", "manual", True, True, stale, "ready")
        _add("100000008", "approved", "manual", True, False, None)
        message = reg.fleet_message()
        classes = []
        for robot in message["robots"]:
            record = reg.robots[robot["robot_id"]]
            classes.append(
                classify(
                    record.approval,
                    record.robot_ws is not None,
                    record.operator_ws is not None or record.start_in_progress,
                    record.mode,
                    record.last_seen,
                    now,
                )
            )
        assert counts_from_classes(classes) == message["counts"]
        assert message["counts"] == {
            "registered": 7,
            "autonomous": 1,
            "needs_manual": 2,
            "in_teleop": 2,
        }
        by_id = {robot["robot_id"]: robot for robot in message["robots"]}
        assert list(by_id) == sorted(by_id)
        assert by_id["100000001"]["status"] == "pending"
        assert by_id["100000001"]["approval"] == "pending"
        assert by_id["100000002"]["status"] == "online"
        assert by_id["100000002"]["location"] == "host-2"
        assert by_id["100000003"]["mode"] == "autonomous"
        assert by_id["100000004"]["status"] == "offline"
        assert by_id["100000005"]["status"] == "running"
        assert by_id["100000005"]["operator"] == "lab"
        assert by_id["100000006"]["status"] == "offline"
        assert by_id["100000007"]["status"] == "stale"
        assert by_id["100000008"]["status"] == "online"
        raw = json.dumps(message)
        assert "secret_hash" not in raw
        assert _TOKEN not in raw
        assert status_label("approved", True, False, "idle", False, None, now) == "online"
        assert (
            status_label("approved", True, True, "ready", False, stale, now) == "stale"
        )
    finally:
        reg.close()


def test_username_lockout_skips_the_fifth_password_check(
    tmp_path: Path, monkeypatch: Any
) -> None:
    path = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": _OPERATOR_PASSWORD)
    assert admin_main(["add-operator", "lab"]) == 0

    calls = {"n": 0}
    real = password_matches

    def _wrapped(stored: bytes, password: object, iterations: int = 20_000) -> bool:
        calls["n"] += 1
        return real(stored, password, iterations)

    monkeypatch.setattr(registry_session, "password_matches", _wrapped)

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            for _ in range(4):
                ws = FakeWS()
                await reg._accept_login(
                    ws,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": "wrong-password",
                    },
                )
                assert ws.closed is False
                assert ws.sent[-1] == {"v": 1, "type": "error", "code": "auth"}
            assert calls["n"] == 4
            locked = FakeWS()
            await reg._accept_login(
                locked,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": _OPERATOR_PASSWORD,
                },
            )
            assert calls["n"] == 4
            assert locked.closed is False
            assert locked.sent[-1]["code"] == "auth"
            other = FakeWS()
            await reg._accept_login(
                other,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": _OPERATOR_PASSWORD,
                },
            )
            assert calls["n"] == 4
            assert other.sent[-1]["code"] == "auth"
        finally:
            reg.close()

    _run(_body())
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "new-password-value")
    assert admin_main(["set-password", "lab"]) == 0

    async def _after_reset() -> None:
        reg = Registry(str(path))
        try:
            # A new Registry is a process restart: the count is gone.
            ws = FakeWS()
            await reg._accept_login(
                ws,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": "new-password-value",
                },
            )
            assert ws.sent[0] == {"v": 1, "type": "logged_in", "username": "lab"}
            assert ws.sent[1]["type"] == "fleet"
            assert "hostname" not in ws.sent[0]
            _no_secret(ws.sent[0], "new-password-value")
            _no_secret(ws.sent[1], "new-password-value")
        finally:
            reg.close()

    before = calls["n"]
    _run(_after_reset())
    assert calls["n"] == before + 1

    async def _same_process_reset() -> None:
        reg = Registry(str(path))
        try:
            for _ in range(4):
                ws = FakeWS()
                await reg._accept_login(
                    ws,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": "wrong-password",
                    },
                )
            held = calls["n"]
            blocked = FakeWS()
            await reg._accept_login(
                blocked,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": "new-password-value",
                },
            )
            assert calls["n"] == held
            assert blocked.sent[-1]["code"] == "auth"
        finally:
            reg.close()
        monkeypatch.setattr(getpass, "getpass", lambda prompt="": "reset-password-ok")
        assert admin_main(["set-password", "lab"]) == 0
        reg = Registry(str(path))
        # Hash change is noticed by the same process only when that process
        # reads the new row. Use one Registry across the admin write.
        reg.close()
        reg = Registry(str(path))
        try:
            # Rebuild the lock on this object, then change the hash underneath.
            for _ in range(4):
                await reg._accept_login(
                    FakeWS(),
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": "wrong-password",
                    },
                )
            monkeypatch.setattr(
                getpass, "getpass", lambda prompt="": "after-lock-password"
            )
            assert admin_main(["set-password", "lab"]) == 0
            seen = calls["n"]
            ok = FakeWS()
            await reg._accept_login(
                ok,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": "after-lock-password",
                },
            )
            assert calls["n"] == seen + 1
            assert ok.sent[0] == {
                "v": 1,
                "type": "logged_in",
                "username": "lab",
            }
        finally:
            reg.close()

    _run(_same_process_reset())


def test_hashing_runs_through_to_thread(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": _OPERATOR_PASSWORD)
    assert admin_main(["add-operator", "lab"]) == 0
    seen: list = []
    real = asyncio.to_thread

    async def _spy(fn: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(fn.__name__)
        return await real(fn, *args, **kwargs)

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            monkeypatch.setattr(asyncio, "to_thread", _spy)
            robot = FakeWS()
            await reg._accept_register(
                robot,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "111111111",
                    "password": _ROBOT_PASSWORD,
                    "hostname": "host",
                },
            )
            assert robot.sent == [
                {"v": 1, "type": "error", "code": "legacy_robot"}
            ]
            assert robot.closed is True
            assert "hash_password" not in seen
            assert "111111111" not in reg.robots
            token = FakeWS()
            await reg._accept_register(
                token,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "222222222",
                    "token": _TOKEN,
                    "hostname": "host",
                    "mode": "manual",
                },
            )
            assert token.sent[-1] == {
                "v": 1,
                "type": "registered",
                "approval": "pending",
            }
            assert "hash_password" in seen
            operator = FakeWS()
            await reg._accept_login(
                operator,
                {
                    "v": 1,
                    "type": "login",
                    "username": "lab",
                    "password": _OPERATOR_PASSWORD,
                },
            )
            assert operator.sent[0] == {
                "v": 1,
                "type": "logged_in",
                "username": "lab",
            }
            assert operator.closed is False
            assert "password_matches" in seen
            legacy = FakeWS()
            before = len(seen)
            await reg._accept_login(
                legacy,
                {
                    "v": 1,
                    "type": "login",
                    "robot_id": "222222222",
                    "password": _ROBOT_PASSWORD,
                },
            )
            assert legacy.sent == [
                {"v": 1, "type": "error", "code": "legacy_login"}
            ]
            assert legacy.closed is True
            assert seen[before:] == []
            await reg._username_frame(operator, {"v": 1, "type": "logout"})
            assert operator.sent[-1] == {
                "v": 1,
                "type": "error",
                "code": "unknown_type",
            }
            assert operator.closed is False
            assert reg._ws_user[operator] == "lab"
        finally:
            reg.close()

    _run(_body())
    assert "hash_password" in seen
    assert "password_matches" in seen


def test_admin_prints_names_only(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    path = tmp_path / "nested" / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    prompts = {"n": 0, "value": _OPERATOR_PASSWORD}

    def _prompt(prompt: str = "") -> str:
        prompts["n"] += 1
        return prompts["value"]

    monkeypatch.setattr(getpass, "getpass", _prompt)
    assert admin_main(["add-operator", "lab"]) == 0
    out = capsys.readouterr().out
    assert out == "lab\n"
    assert _OPERATOR_PASSWORD not in out
    assert prompts["n"] == 1
    assert admin_main(["add-operator", "lab"]) == 1
    again = capsys.readouterr().out
    assert again == "operator exists\n"
    assert prompts["n"] == 1
    assert admin_main(["add-operator", "123456789"]) == 1
    assert capsys.readouterr().out == "bad username\n"
    prompts["value"] = "abc"
    assert admin_main(["add-operator", "other"]) == 1
    short_out = capsys.readouterr().out
    assert short_out == "password too short\n"
    assert "abc" not in short_out
    prompts["value"] = "another-password"
    assert admin_main(["set-password", "missing"]) == 1
    missing = capsys.readouterr().out
    assert missing == "unknown operator\n"
    assert prompts["n"] == 2
    assert admin_main(["set-password", "lab"]) == 0
    renamed = capsys.readouterr().out
    assert renamed == "lab\n"
    assert "another-password" not in renamed
    assert admin_main(["disable-operator", "lab"]) == 0
    assert capsys.readouterr().out == "lab\n"
    assert admin_main(["list-operators"]) == 0
    assert capsys.readouterr().out == "lab disabled=1\n"
    assert admin_main(["enable-operator", "lab"]) == 0
    assert capsys.readouterr().out == "lab\n"
    assert admin_main(["disable-operator", "nosuch"]) == 1
    assert capsys.readouterr().out == "unknown operator\n"
    secret_id = "t" * 40
    assert admin_main(["clear-robot", secret_id]) == 1
    cleared = capsys.readouterr().out
    assert cleared == "bad id\n"
    assert secret_id not in cleared
    assert admin_main(["clear-robot", "222222222"]) == 1
    assert capsys.readouterr().out == "unknown robot\n"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO robots (
            robot_id, secret_hash, iterations, hostname, location, mode,
            approval, phase, last_seen, created_at
        ) VALUES ('222222222', ?, 200000, '', '', 'manual', 'pending', 'idle', NULL, ?)
        """,
        (b"\x33" * 48, utc_now()),
    )
    conn.commit()
    password_row = conn.execute("SELECT secret_hash FROM operators").fetchone()
    conn.close()
    assert admin_main(["clear-robot", "222222222"]) == 0
    assert capsys.readouterr().out == "222222222\n"
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM robots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 1
    conn.close()
    text = path.read_bytes()
    assert _OPERATOR_PASSWORD.encode() not in text
    assert b"another-password" not in text
    assert isinstance(password_row[0], bytes) and len(password_row[0]) == 48
    assert _modes(path) == 0o600
    assert _modes(path.parent) == 0o700
    busy = sqlite3.connect(path).execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy == 5000


def test_scale_log_fires_once(caplog: Any) -> None:
    reg = Registry()
    try:
        caplog.set_level(logging.WARNING)
        for index in range(11):
            reg._ws_user[object()] = "user%s" % index
        for index in range(201):
            robot_id = "%09d" % index
            record = registry_session.RobotRecord(
                RobotRow(
                    robot_id=robot_id,
                    secret_hash=b"\x44" * 48,
                    iterations=FLEET_ITERATIONS,
                    hostname="",
                    location="",
                    mode="manual",
                    approval="approved",
                    phase="idle",
                    last_seen=None,
                    created_at=utc_now(),
                )
            )
            record.robot_ws = object()
            reg.robots[robot_id] = record
        reg._note_scale()
        reg._note_scale()
        text = caplog.text
        assert "exceed 200" in text
        assert "exceed 10" in text
        assert text.count("exceed 200") == 1
        assert text.count("exceed 10") == 1
    finally:
        reg.close()


def test_start_server_stays_in_memory_unless_db_path_is_passed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    target = tmp_path / "should-not.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(target))

    async def _body() -> None:
        async with _listening() as uri:
            async with connect(uri) as robot, connect(uri) as legacy:
                await _send(
                    robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "222222222",
                        "token": _TOKEN,
                        "hostname": "host",
                        "mode": "manual",
                    },
                )
                assert await _recv(robot) == {
                    "v": 1,
                    "type": "registered",
                    "approval": "pending",
                }
                await _send(
                    legacy,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                        "hostname": "host",
                    },
                )
                assert await _recv(legacy) == {
                    "v": 1,
                    "type": "error",
                    "code": "legacy_robot",
                }
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(legacy.recv(), 2)

    _run(_body())
    assert not target.exists()


def test_unopenable_database_keeps_ice_join(tmp_path: Path, caplog: Any) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    db_path = blocked / "registry.sqlite"
    caplog.set_level(logging.ERROR)

    async def _body() -> None:
        async with _listening(db_path=str(db_path)) as uri:
            async with connect(uri) as ice, connect(uri) as robot:
                await _send(
                    ice, {"type": "join", "room": "hello-world", "role": "controlling"}
                )
                joined = await _recv(ice)
                assert joined["type"] == "joined"
                assert joined["room"] == "hello-world"
                assert "v" not in joined
                await _send(
                    robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                        "hostname": "host",
                    },
                )
                assert await _recv(robot) == {"type": "error", "error": "unknown_type"}
                await _send(
                    robot,
                    {
                        "v": 1,
                        "type": "login",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                    },
                )
                assert await _recv(robot) == {"type": "error", "error": "unknown_type"}

    _run(_body())
    assert str(db_path) in caplog.text
    assert "registry database cannot be opened" in caplog.text
    assert not db_path.exists()


def test_password_and_token_logins_on_one_server(
    tmp_path: Path, monkeypatch: Any
) -> None:
    path = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": _OPERATOR_PASSWORD)
    assert admin_main(["add-operator", "lab"]) == 0
    monkeypatch.setattr(registry_session, "FLEET_COALESCE_S", 0.2)

    async def _body() -> None:
        async with _listening(db_path=str(path)) as uri:
            async with (
                connect(uri) as password_robot,
                connect(uri) as token_robot,
                connect(uri) as operator,
                connect(uri) as dashboard,
            ):
                await _send(
                    password_robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                        "hostname": "password-host",
                    },
                )
                assert await _recv(password_robot) == {
                    "v": 1,
                    "type": "error",
                    "code": "legacy_robot",
                }
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(password_robot.recv(), 2)
                await _send(
                    operator,
                    {
                        "v": 1,
                        "type": "login",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                    },
                )
                assert await _recv(operator) == {
                    "v": 1,
                    "type": "error",
                    "code": "legacy_login",
                }
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(operator.recv(), 2)
                await _send(
                    token_robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "222222222",
                        "token": _TOKEN,
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                        "mode": "manual",
                    },
                )
                token_reply = await _recv(token_robot)
                assert token_reply == {
                    "v": 1,
                    "type": "registered",
                    "approval": "pending",
                }
                _no_secret(token_reply, _TOKEN, _ROBOT_PASSWORD, _OPERATOR_PASSWORD)
                async with connect(uri) as legacy:
                    await _send(
                        legacy,
                        {
                            "v": 1,
                            "type": "login",
                            "robot_id": "222222222",
                            "password": _ROBOT_PASSWORD,
                        },
                    )
                    assert await _recv(legacy) == {
                        "v": 1,
                        "type": "error",
                        "code": "legacy_login",
                    }
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(legacy.recv(), 2)
                async with connect(uri) as wrong:
                    await _send(
                        wrong,
                        {
                            "v": 1,
                            "type": "register",
                            "robot_id": "222222222",
                            "token": "not-the-device-token",
                            "hostname": "intruder",
                        },
                    )
                    assert await _recv(wrong) == {
                        "v": 1,
                        "type": "error",
                        "code": "auth",
                    }
                    await _send(
                        wrong,
                        {
                            "v": 1,
                            "type": "register",
                            "robot_id": "12",
                            "token": "x",
                        },
                    )
                    assert await _recv(wrong) == {
                        "v": 1,
                        "type": "error",
                        "code": "bad_id",
                    }
                await _send(
                    dashboard,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert await _recv(dashboard) == {
                    "v": 1,
                    "type": "logged_in",
                    "username": "lab",
                }
                fleet = await _recv(dashboard)
                assert fleet["type"] == "fleet"
                assert [robot["robot_id"] for robot in fleet["robots"]] == ["222222222"]
                assert fleet["robots"][0]["status"] == "pending"
                assert fleet["robots"][0]["operator"] is None
                assert fleet["counts"]["registered"] == 0
                _no_secret(fleet, _TOKEN, _ROBOT_PASSWORD, _OPERATOR_PASSWORD)
                await _send(dashboard, {"v": 1, "type": "logout"})
                assert await _recv(dashboard) == {
                    "v": 1,
                    "type": "error",
                    "code": "unknown_type",
                }
                async with connect(uri) as second:
                    await _send(
                        second,
                        {
                            "v": 1,
                            "type": "login",
                            "username": "lab",
                            "password": "wrong-password",
                        },
                    )
                    assert await _recv(second) == {
                        "v": 1,
                        "type": "error",
                        "code": "already_attached",
                    }
                    await _send(second, {"v": 1, "type": "approve", "robot_id": "222222222"})
                    assert await _recv(second) == {
                        "v": 1,
                        "type": "error",
                        "code": "unknown_type",
                    }
                async with connect(uri) as again:
                    await _send(
                        again,
                        {
                            "v": 1,
                            "type": "login",
                            "robot_id": "111111111",
                            "password": _ROBOT_PASSWORD,
                        },
                    )
                    assert await _recv(again) == {
                        "v": 1,
                        "type": "error",
                        "code": "legacy_login",
                    }
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(again.recv(), 2)
                await _send(
                    token_robot,
                    {
                        "v": 1,
                        "type": "presence",
                        "mode": "manual",
                        "location": "dubai-lab",
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                        "phase": "ready",
                    },
                )
                updated = await _recv(dashboard)
                assert updated["type"] == "fleet"
                assert updated["robots"][0]["location"] == "dubai-lab"
                assert updated["robots"][0]["phase"] == "ready"
                _no_secret(updated, _TOKEN)
                await _send(
                    token_robot,
                    {
                        "v": 1,
                        "type": "presence",
                        "mode": "autonomous",
                        "location": "dubai-lab",
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                        "phase": "idle",
                    },
                )
                await _send(
                    token_robot,
                    {
                        "v": 1,
                        "type": "presence",
                        "mode": "autonomous",
                        "location": "sharjah-lab",
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                        "phase": "stopped",
                    },
                )
                coalesced = await asyncio.wait_for(dashboard.recv(), 1)
                coalesced_msg = json.loads(coalesced)
                assert coalesced_msg["type"] == "fleet"
                assert coalesced_msg["robots"][0]["location"] == "sharjah-lab"
                assert coalesced_msg["robots"][0]["phase"] == "stopped"
                extra = asyncio.create_task(dashboard.recv())
                done, _pending = await asyncio.wait({extra}, timeout=0.3)
                assert not done
                extra.cancel()
                with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                    await extra

    _run(_body())
    blob = path.read_bytes()
    assert _TOKEN.encode() not in blob
    assert _ROBOT_PASSWORD.encode() not in blob
    assert _OPERATOR_PASSWORD.encode() not in blob
    assert b"111111111" not in blob
    conn = sqlite3.connect(path)
    robots = [row[0] for row in conn.execute("SELECT robot_id FROM robots")]
    iterations = conn.execute("SELECT iterations FROM robots").fetchone()[0]
    conn.close()
    assert robots == ["222222222"]
    assert iterations == 200_000


def test_disabled_username_is_auth_and_the_socket_stays_up(
    tmp_path: Path, monkeypatch: Any
) -> None:
    path = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": _OPERATOR_PASSWORD)
    assert admin_main(["add-operator", "lab"]) == 0
    assert admin_main(["disable-operator", "lab"]) == 0

    async def _body() -> None:
        async with _listening(db_path=str(path)) as uri:
            async with connect(uri) as operator:
                await _send(
                    operator,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert await _recv(operator) == {"v": 1, "type": "error", "code": "auth"}
                await _send(
                    operator,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "nobody",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert await _recv(operator) == {"v": 1, "type": "error", "code": "auth"}
                await _send(
                    operator,
                    {"v": 1, "type": "login", "username": "123456789", "password": "x"},
                )
                assert await _recv(operator) == {"v": 1, "type": "error", "code": "auth"}

    _run(_body())


def test_classify_treats_a_missing_last_seen_as_not_stale() -> None:
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert classify("approved", True, False, "manual", None, now) == "needs_manual"
    assert classify("approved", True, False, "autonomous", None, now) == "autonomous"
    old = (now - timedelta(seconds=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert classify("approved", True, False, "manual", old, now) == "offline"
    assert classify("approved", True, True, "manual", old, now) == "in_teleop"
    assert classify("pending", True, False, "autonomous", None, now) == "pending"
    fresh = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert (
        counts_from_classes(["pending", "autonomous", "needs_manual", "offline", "in_teleop"])
        == {
            "registered": 4,
            "autonomous": 1,
            "needs_manual": 1,
            "in_teleop": 1,
        }
    )
    assert fresh
    assert status_label("pending", True, True, "ready", True, fresh, now) == "pending"


def _ago(seconds: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_user(reg: Registry, username: str, password: str) -> None:
    assert add_operator(
        reg._db,
        username,
        hash_password(password, FLEET_ITERATIONS),
        FLEET_ITERATIONS,
        utc_now(),
    )


async def _login_user(reg: Registry, username: str, password: str) -> FakeWS:
    ws = FakeWS()
    await reg._accept_username_login(
        ws,
        {"v": 1, "type": "login", "username": username, "password": password},
        username,
    )
    return ws


async def _register_token(
    reg: Registry,
    robot_id: str,
    *,
    mode: str = "manual",
    location: str = "dubai-lab",
    hostname: str = "AUTOOS-DEV-MUHAMMADOSAMA",
) -> FakeWS:
    robot = FakeWS()
    created = await reg._accept_register(
        robot,
        {
            "v": 1,
            "type": "register",
            "robot_id": robot_id,
            "token": _TOKEN,
            "hostname": hostname,
            "mode": mode,
            "location": location,
        },
    )
    assert created is reg.robots[robot_id]
    return robot


async def _fleets(ws: FakeWS) -> list:
    await asyncio.sleep(0.12)
    return [msg for msg in ws.sent if msg.get("type") == "fleet"]


def test_old_pending_rows_are_deleted_on_open(tmp_path: Path) -> None:
    assert PENDING_KEEP_S == 7 * 24 * 60 * 60
    path = tmp_path / "registry.sqlite"
    conn = store_connect(str(path))
    created = utc_now()
    old = _ago(PENDING_KEEP_S + 120)
    young = _ago(PENDING_KEEP_S - 120)

    def _row(robot_id: str, approval: str, seen: str) -> None:
        insert_robot(
            conn,
            robot_id,
            b"\x33" * 48,
            FLEET_ITERATIONS,
            "host",
            "",
            "manual",
            approval,
            "idle",
            seen,
            created,
        )

    _row("333333333", "pending", old)
    _row("444444444", "pending", young)
    _row("555555555", "pending", young)
    _row("666666666", "approved", old)
    conn.execute("UPDATE robots SET last_seen=NULL WHERE robot_id='555555555'")
    conn.commit()
    conn.close()

    reg = Registry(str(path))
    try:
        assert "333333333" not in reg.robots
        assert set(reg.robots) == {"444444444", "555555555", "666666666"}
        assert reg.robots["666666666"].approval == "approved"
        assert reg.robots["444444444"].approval == "pending"
    finally:
        reg.close()


def test_approve_attach_release_sign_out_and_stale(tmp_path: Path) -> None:
    assert STALE_STOP_WAIT_S == 30.0
    assert MAINTAIN_INTERVAL_S == 1.0
    path = tmp_path / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            _add_user(reg, "lab", _OPERATOR_PASSWORD)
            _add_user(reg, "other", "other-password")
            robot = await _register_token(reg, "222222222")
            offline = await _register_token(reg, "333333333", location="")
            await reg._drop(offline)
            op = await _login_user(reg, "lab", _OPERATOR_PASSWORD)
            assert op.sent[0] == {"v": 1, "type": "logged_in", "username": "lab"}
            other = await _login_user(reg, "other", "other-password")
            password_robot = FakeWS()
            created = await reg._accept_register(
                password_robot,
                {
                    "v": 1,
                    "type": "register",
                    "robot_id": "111111111",
                    "password": _ROBOT_PASSWORD,
                    "hostname": "password-host",
                },
            )
            assert created is None
            assert password_robot.sent == [
                {"v": 1, "type": "error", "code": "legacy_robot"}
            ]
            assert password_robot.closed is True
            assert "111111111" not in reg.robots
            password_op = FakeWS()
            await reg._accept_login(
                password_op,
                {
                    "v": 1,
                    "type": "login",
                    "robot_id": "111111111",
                    "password": _ROBOT_PASSWORD,
                },
            )
            assert password_op.sent == [
                {"v": 1, "type": "error", "code": "legacy_login"}
            ]
            assert password_op.closed is True

            await reg._username_frame(
                op, {"v": 1, "type": "approve", "robot_id": "999999999"}
            )
            assert op.sent[-1] == {"v": 1, "type": "error", "code": "not_found"}
            assert op.closed is False
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "999999999"}
            )
            assert op.sent[-1]["code"] == "not_found"
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert op.sent[-1] == {"v": 1, "type": "error", "code": "pending"}
            assert robot.sent[-1]["type"] == "registered"
            await reg._username_frame(
                op, {"v": 1, "type": "approve", "robot_id": "333333333"}
            )
            assert op.sent[-1] == {
                "v": 1,
                "type": "approved",
                "robot_id": "333333333",
            }
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "333333333"}
            )
            assert op.sent[-1]["code"] == "offline"
            await reg._username_frame(
                op, {"v": 1, "type": "approve", "robot_id": "222222222"}
            )
            assert op.sent[-1] == {
                "v": 1,
                "type": "approved",
                "robot_id": "222222222",
            }
            reg.robots["222222222"].last_seen = _ago(31)
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert op.sent[-1]["code"] == "offline"
            reg.robots["222222222"].last_seen = utc_now()
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            attached = op.sent[-1]
            assert attached["type"] == "attached"
            assert attached["robot_id"] == "222222222"
            assert attached["hostname"] == "AUTOOS-DEV-MUHAMMADOSAMA"
            assert attached["location"] == "dubai-lab"
            assert attached["mode"] == "manual"
            assert robot.sent[-1] == {"v": 1, "type": "operator_attached"}
            _no_secret(attached, _TOKEN, _OPERATOR_PASSWORD)
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert op.sent[-1]["code"] == "already_attached"
            second = await _register_token(reg, "444444444")
            await reg._username_frame(
                op, {"v": 1, "type": "approve", "robot_id": "444444444"}
            )
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "444444444"}
            )
            assert op.sent[-1]["code"] == "already_attached"
            assert second.sent[-1]["type"] == "registered"
            assert reg.robots["222222222"].operator_name == "lab"
            await reg._username_frame(
                other, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert other.sent[-1]["code"] == "busy"

            await reg._forward(
                reg.robots["222222222"],
                robot,
                "robot",
                {"v": 1, "type": "inventory", "marker": "INV-MARKER-9"},
            )
            assert op.sent[-1]["marker"] == "INV-MARKER-9"
            await reg._forward(
                reg.robots["222222222"],
                op,
                "operator",
                {"v": 1, "type": "config", "marker": "CFG-MARKER-9"},
            )
            assert robot.sent[-1]["marker"] == "CFG-MARKER-9"
            await reg._forward(
                reg.robots["222222222"],
                op,
                "operator",
                {"v": 1, "type": "start"},
            )
            assert robot.sent[-1] == {"v": 1, "type": "start"}
            assert reg.robots["222222222"].start_in_progress is True
            await reg._forward(
                reg.robots["222222222"],
                robot,
                "robot",
                {"v": 1, "type": "status", "phase": "ready", "detail": ""},
            )
            assert op.sent[-1]["phase"] == "ready"
            record = reg.robots["222222222"]
            assert record.phase == "ready"
            assert record.start_in_progress is False
            running = (await _fleets(op))[-1]
            assert running["robots"][0]["status"] == "running"
            assert running["counts"]["in_teleop"] == 1
            assert running["robots"][0]["operator"] == "lab"
            _no_secret(running, _TOKEN)
            side = sqlite3.connect(path)
            assert (
                side.execute(
                    "SELECT approval, phase FROM robots WHERE robot_id='222222222'"
                ).fetchone()
                == ("approved", "ready")
            )
            side.close()

            await reg._username_frame(op, {"v": 1, "type": "release"})
            assert robot.sent[-1] == {"v": 1, "type": "operator_detached"}
            assert op.sent[-1] == {"v": 1, "type": "released"}
            assert reg._ws_user[op] == "lab"
            assert record.operator_ws is None
            released = (await _fleets(op))[-1]
            row = next(
                item for item in released["robots"] if item["robot_id"] == "222222222"
            )
            assert row["operator"] is None
            assert row["status"] == "online"
            assert released["counts"]["in_teleop"] == 0
            await reg._username_frame(
                op, {"v": 1, "type": "config", "marker": "CFG-MARKER-9"}
            )
            assert op.sent[-1]["code"] == "offline"
            await reg._username_frame(op, {"v": 1, "type": "release"})
            assert op.sent[-1] == {"v": 1, "type": "released"}

            record.last_seen = _ago(31)
            record.sweep_mark = None
            before = len(op.sent)
            await reg._maintain_once()
            stale_fleet = (await _fleets(op))[-1]
            shown = next(
                item
                for item in stale_fleet["robots"]
                if item["robot_id"] == "222222222"
            )
            assert shown["status"] == "offline"
            assert len([m for m in robot.sent if m.get("type") == "stop"]) == 0
            assert robot.closed is False
            assert password_robot.sent == [
                {"v": 1, "type": "error", "code": "legacy_robot"}
            ]
            assert password_op.sent == [
                {"v": 1, "type": "error", "code": "legacy_login"}
            ]
            assert "111111111" not in reg.robots
            assert not hasattr(reg, "sessions")
            marked = len(op.sent)
            await reg._maintain_once()
            await asyncio.sleep(0.12)
            assert len(op.sent) == marked
            assert len(op.sent) > before
            record.last_seen = None
            await reg._maintain_once()
            missing = (await _fleets(op))[-1]
            missing_row = next(
                item for item in missing["robots"] if item["robot_id"] == "222222222"
            )
            assert missing_row["status"] == "online"

            record.last_seen = utc_now()
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert op.sent[-1]["type"] == "attached"
            record.last_seen = _ago(31)
            stops = len([m for m in robot.sent if m.get("type") == "stop"])
            await reg._maintain_once()
            assert any(m.get("code") == "stale" for m in op.sent)
            assert record.operator_ws is op
            assert record.sweep_mark == "stale"
            assert record.stale_deadline is not None
            assert record.stale_deadline >= time.monotonic() + STALE_STOP_WAIT_S - 1
            await reg._forward(
                record, op, "operator", {"v": 1, "type": "stop"}
            )
            assert robot.sent[-1] == {"v": 1, "type": "stop"}
            await reg._forward(
                record,
                robot,
                "robot",
                {"v": 1, "type": "status", "phase": "stopped", "detail": ""},
            )
            assert record.stale_deadline is None
            await reg._maintain_once()
            assert len([m for m in robot.sent if m.get("type") == "stop"]) == stops + 1
            await reg._username_frame(op, {"v": 1, "type": "release"})
            assert op.sent[-1]["type"] == "released"

            record.last_seen = utc_now()
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            record.last_seen = _ago(31)
            await reg._maintain_once()
            assert any(m.get("code") == "stale" for m in op.sent)
            record.stale_deadline = time.monotonic() - 1
            sent_before = len(op.sent)
            await reg._maintain_once()
            assert robot.sent[-1] == {"v": 1, "type": "stop"}
            fresh_notes = op.sent[sent_before:]
            assert {"v": 1, "type": "operator_detached"} in fresh_notes
            assert not any(m.get("code") == "offline" for m in fresh_notes)
            assert record.operator_ws is None
            assert reg._ws_user[op] == "lab"
            await reg._username_frame(op, {"v": 1, "type": "release"})
            assert op.sent[-1] == {"v": 1, "type": "released"}

            record.last_seen = utc_now()
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            stops = len([m for m in robot.sent if m.get("type") == "stop"])
            await reg._drop(robot)
            assert record.robot_ws is None
            assert record.operator_ws is None
            assert op.sent[-1] == {"v": 1, "type": "error", "code": "offline"}
            assert len([m for m in robot.sent if m.get("type") == "stop"]) == stops

            fresh_robot = await _register_token(reg, "222222222")
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert fresh_robot.sent[-1] == {"v": 1, "type": "operator_attached"}
            await reg._username_frame(op, {"v": 1, "type": "sign_out"})
            assert fresh_robot.sent[-1] == {"v": 1, "type": "operator_detached"}
            assert op.sent[-1] == {"v": 1, "type": "signed_out"}
            assert op not in reg._ws_user
            names = {
                row[0]
                for row in reg._db.execute(
                    "SELECT username FROM operators ORDER BY username"
                )
            }
            assert names == {"lab", "other"}
            again = await _login_user(reg, "lab", _OPERATOR_PASSWORD)
            assert again.sent[0]["type"] == "logged_in"
        finally:
            reg.close()

    _run(_body())
    again = Registry(str(path))
    try:
        assert again.robots["222222222"].approval == "approved"
        assert again.robots["333333333"].approval == "approved"
        assert again.robots["222222222"].phase == "idle"
        assert b"INV-MARKER-9" not in path.read_bytes()
        assert b"CFG-MARKER-9" not in path.read_bytes()
    finally:
        again.close()


def test_maintain_applies_disable_password_and_clear(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"

    async def _body() -> None:
        reg = Registry(str(path))
        try:
            _add_user(reg, "lab", _OPERATOR_PASSWORD)
            for _ in range(4):
                bad = FakeWS()
                await reg._accept_username_login(
                    bad,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": "wrong-password",
                    },
                    "lab",
                )
                assert bad.sent[-1]["code"] == "auth"
            assert reg._fail_count["lab"] >= 4
            set_operator_password(
                reg._db,
                "lab",
                hash_password("new-password", FLEET_ITERATIONS),
                FLEET_ITERATIONS,
            )
            await reg._maintain_once()
            assert reg._fail_count.get("lab", 0) == 0
            op = await _login_user(reg, "lab", "new-password")
            assert op.sent[0]["type"] == "logged_in"
            robot = await _register_token(reg, "222222222")
            await reg._username_frame(
                op, {"v": 1, "type": "approve", "robot_id": "222222222"}
            )
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert robot.sent[-1]["type"] == "operator_attached"
            set_operator_password(
                reg._db,
                "lab",
                hash_password("newer-password", FLEET_ITERATIONS),
                FLEET_ITERATIONS,
            )
            await reg._maintain_once()
            assert op.closed is True
            assert robot.sent[-1] == {"v": 1, "type": "operator_detached"}
            assert reg.robots["222222222"].operator_ws is None
            op = await _login_user(reg, "lab", "newer-password")
            assert op.sent[0]["type"] == "logged_in"
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert robot.sent[-1]["type"] == "operator_attached"
            set_operator_disabled(reg._db, "lab", 1)
            await reg._maintain_once()
            assert op.closed is True
            assert "lab" not in reg._operator_ws
            assert reg.robots["222222222"].operator_ws is None
            assert robot.sent[-1] == {"v": 1, "type": "operator_detached"}
            blocked = await _login_user(reg, "lab", "newer-password")
            assert blocked.sent[-1]["code"] == "auth"
            assert blocked.closed is False
            set_operator_disabled(reg._db, "lab", 0)
            op = await _login_user(reg, "lab", "newer-password")
            await reg._username_frame(
                op, {"v": 1, "type": "attach", "robot_id": "222222222"}
            )
            assert robot.sent[-1]["type"] == "operator_attached"
            clear_robot(reg._db, "222222222")
            await reg._maintain_once()
            assert robot.closed is True
            assert "222222222" not in reg.robots
            assert any(m.get("code") == "offline" for m in op.sent)
            assert not any(m.get("type") == "stop" for m in robot.sent)
            assert reg._db.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 1
            assert op.closed is False
            assert reg._ws_user[op] == "lab"
        finally:
            reg.close()

    _run(_body())


def test_live_maintain_and_relay(tmp_path: Path, monkeypatch: Any) -> None:
    assert MAINTAIN_INTERVAL_S == 1.0
    path = tmp_path / "registry.sqlite"
    monkeypatch.setenv("TELEOP_REGISTRY_DB", str(path))
    monkeypatch.setattr(registry_store, "MAINTAIN_INTERVAL_S", 0.05)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": _OPERATOR_PASSWORD)
    assert admin_main(["add-operator", "lab"]) == 0

    async def _until_closed(ws: Any) -> list:
        found = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), deadline - time.monotonic())
            except ConnectionClosed:
                return found
            found.append(json.loads(raw))
        raise AssertionError("socket stayed open")

    async def _body() -> None:
        async with _listening(db_path=str(path)) as uri:
            async with connect(uri) as password_robot, connect(uri) as operator:
                await _send(
                    password_robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                        "hostname": "password-host",
                    },
                )
                assert await _recv(password_robot) == {
                    "v": 1,
                    "type": "error",
                    "code": "legacy_robot",
                }
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(password_robot.recv(), 2)
                await _send(
                    operator,
                    {
                        "v": 1,
                        "type": "login",
                        "robot_id": "111111111",
                        "password": _ROBOT_PASSWORD,
                    },
                )
                assert await _recv(operator) == {
                    "v": 1,
                    "type": "error",
                    "code": "legacy_login",
                }
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(operator.recv(), 2)
            async with connect(uri) as still:
                await _send(still, {"v": 1, "type": "logout"})
                assert await _recv(still) == {
                    "type": "error",
                    "error": "unknown_type",
                }
                await _send(still, {"v": 1, "type": "logout"})
                assert await _recv(still) == {
                    "type": "error",
                    "error": "unknown_type",
                }
            async with (
                connect(uri) as robot,
                connect(uri) as dashboard,
            ):
                await _send(
                    robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "222222222",
                        "token": _TOKEN,
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                        "mode": "manual",
                        "location": "dubai-lab",
                    },
                )
                assert (await _recv(robot))["approval"] == "pending"
                await _send(
                    dashboard,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert (await _recv(dashboard))["username"] == "lab"
                assert (await _recv(dashboard))["type"] == "fleet"
                await _send(
                    dashboard, {"v": 1, "type": "approve", "robot_id": "222222222"}
                )
                assert await _recv(dashboard) == {
                    "v": 1,
                    "type": "approved",
                    "robot_id": "222222222",
                }
                approved = await _recv(dashboard)
                assert approved["type"] == "fleet"
                assert approved["robots"][0]["status"] == "online"
                await _send(
                    dashboard, {"v": 1, "type": "attach", "robot_id": "222222222"}
                )
                attached = await _recv(dashboard)
                assert attached["type"] == "attached"
                assert attached["location"] == "dubai-lab"
                assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
                fleet = await _recv(dashboard)
                assert fleet["counts"]["in_teleop"] == 1
                assert fleet["robots"][0]["operator"] == "lab"
                await _send(
                    robot, {"v": 1, "type": "inventory", "marker": "INV-MARKER-9"}
                )
                assert (await _recv(dashboard))["marker"] == "INV-MARKER-9"
                await _send(
                    dashboard, {"v": 1, "type": "config", "marker": "CFG-MARKER-9"}
                )
                assert (await _recv(robot))["marker"] == "CFG-MARKER-9"
                await _send(dashboard, {"v": 1, "type": "start"})
                assert await _recv(robot) == {"v": 1, "type": "start"}
                started = await _recv(dashboard)
                assert started["type"] == "fleet"
                assert started["robots"][0]["status"] == "starting"
                await _send(
                    robot,
                    {"v": 1, "type": "status", "phase": "ready", "detail": ""},
                )
                assert (await _recv(dashboard))["phase"] == "ready"
                running = await _recv(dashboard)
                assert running["robots"][0]["status"] == "running"
                await _send(dashboard, {"v": 1, "type": "stop"})
                assert await _recv(robot) == {"v": 1, "type": "stop"}
                await _send(
                    robot,
                    {"v": 1, "type": "status", "phase": "stopped", "detail": ""},
                )
                assert (await _recv(dashboard))["phase"] == "stopped"
                stopped = await _recv(dashboard)
                assert stopped["type"] == "fleet"
                assert stopped["robots"][0]["phase"] == "stopped"
                await _send(dashboard, {"v": 1, "type": "release"})
                assert await _recv(robot) == {"v": 1, "type": "operator_detached"}
                assert await _recv(dashboard) == {"v": 1, "type": "released"}
                freed = await _recv(dashboard)
                assert freed["type"] == "fleet"
                assert freed["robots"][0]["operator"] is None
                await _send(dashboard, {"v": 1, "type": "sign_out"})
                assert await _recv(dashboard) == {"v": 1, "type": "signed_out"}
                await _send(
                    dashboard,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert (await _recv(dashboard))["type"] == "logged_in"
                assert admin_main(["disable-operator", "lab"]) == 0
                await _until_closed(dashboard)
            async with connect(uri) as blocked:
                await _send(
                    blocked,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert (await _recv(blocked))["code"] == "auth"
                await _send(
                    blocked,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": _OPERATOR_PASSWORD,
                    },
                )
                assert (await _recv(blocked))["code"] == "auth"
            assert admin_main(["enable-operator", "lab"]) == 0
            monkeypatch.setattr(getpass, "getpass", lambda prompt="": "new-password")
            assert admin_main(["set-password", "lab"]) == 0
            async with connect(uri) as dashboard, connect(uri) as robot:
                await _send(
                    dashboard,
                    {
                        "v": 1,
                        "type": "login",
                        "username": "lab",
                        "password": "new-password",
                    },
                )
                assert (await _recv(dashboard))["type"] == "logged_in"
                await _recv(dashboard)
                await _send(
                    robot,
                    {
                        "v": 1,
                        "type": "register",
                        "robot_id": "222222222",
                        "token": _TOKEN,
                        "hostname": "AUTOOS-DEV-MUHAMMADOSAMA",
                    },
                )
                registered = await _recv(robot)
                assert registered["approval"] == "approved"
                await _send(
                    dashboard, {"v": 1, "type": "attach", "robot_id": "222222222"}
                )
                attached = None
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and attached is None:
                    msg = await _recv(dashboard)
                    if msg.get("type") == "attached":
                        attached = msg
                assert attached is not None
                assert attached["type"] == "attached"
                assert await _recv(robot) == {"v": 1, "type": "operator_attached"}
                assert admin_main(["clear-robot", "222222222"]) == 0
                robot_frames = await _until_closed(robot)
                assert not any(frame.get("type") == "stop" for frame in robot_frames)
                deadline = time.monotonic() + 2
                saw_offline = False
                while time.monotonic() < deadline and not saw_offline:
                    msg = await _recv(dashboard)
                    saw_offline = msg.get("code") == "offline"
                assert saw_offline
                await _send(dashboard, {"v": 1, "type": "sign_out"})
                signed = None
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and signed is None:
                    msg = await _recv(dashboard)
                    if msg.get("type") == "signed_out":
                        signed = msg
                assert signed == {"v": 1, "type": "signed_out"}

    _run(_body())
    conn = sqlite3.connect(path)
    try:
        assert [row[0] for row in conn.execute("SELECT username FROM operators")] == [
            "lab"
        ]
        assert (
            conn.execute("SELECT COUNT(*) FROM robots").fetchone()[0] == 0
        )
        assert b"INV-MARKER-9" not in path.read_bytes()
    finally:
        conn.close()
