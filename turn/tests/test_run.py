"""T4: two loopback agents, local signalling, host punch + echo. No EC2, no TURN."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aioice import Candidate, Connection

from agent.ice import (
    HELLO_ACK,
    IceAgent,
    candidate_to_dict,
    dict_to_candidate,
    format_path,
    nominated_path,
)
from agent.session import run_session
from signalling.server import listening_uri, start_server

HOST = Candidate(
    foundation="1",
    component=1,
    transport="udp",
    priority=2130706431,
    host="192.0.2.10",
    port=40000,
    type="host",
)
SRFLX = Candidate(
    foundation="2",
    component=1,
    transport="udp",
    priority=1694498815,
    host="198.51.100.8",
    port=40000,
    type="srflx",
    related_address="192.0.2.10",
    related_port=40000,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "example.yaml"


def _loopback_cfg(role: str, url: str, room: str) -> dict[str, Any]:
    return {
        "role": role,
        "bind_ip": "127.0.0.1",
        "ifname": None,
        "stun_server": None,
        "stun_port": 3478,
        "signalling_url": url,
        "room": room,
    }


async def _with_server(coro):
    server = await start_server("127.0.0.1", 0)
    try:
        return await coro(listening_uri(server))
    finally:
        server.close()
        await server.wait_closed()


def test_dict_to_candidate_roundtrip() -> None:
    for src in (HOST, SRFLX):
        d = candidate_to_dict(src)
        rebuilt = dict_to_candidate(d)
        assert rebuilt.host == src.host
        assert rebuilt.port == src.port
        assert rebuilt.type == src.type
        assert rebuilt.foundation == src.foundation
        assert rebuilt.component == src.component
        assert rebuilt.priority == src.priority
        assert rebuilt.related_address == src.related_address
        assert rebuilt.related_port == src.related_port


def test_format_path_direct() -> None:
    info = {
        "kind": "direct",
        "local_type": "host",
        "remote_type": "srflx",
        "local": "192.0.2.10:40000",
        "remote": "198.51.100.8:40000",
    }
    line = format_path(info)
    assert "path=direct" in line
    assert "host:192.0.2.10:40000" in line
    assert "srflx:198.51.100.8:40000" in line


def test_nominated_path_none_before_connect() -> None:
    conn = Connection(ice_controlling=True, use_ipv4=True, use_ipv6=False)
    try:
        assert nominated_path(conn)["kind"] == "none"
    finally:
        asyncio.run(conn.close())


async def _two_agents_echo(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    room = "t4-echo"
    controlling = run_session(_loopback_cfg("controlling", url, room))
    controlled = run_session(_loopback_cfg("controlled", url, room))
    return await asyncio.gather(controlling, controlled)


def test_two_agents_loopback_echo_host() -> None:
    """Sheet 7/8/11: punch + ICE-PWD checks + nominate host + hello/hello-ack."""
    a, b = asyncio.run(asyncio.wait_for(_with_server(_two_agents_echo), 30))
    assert a["ok"] and b["ok"], (a, b)
    assert a["path"] == "direct"
    assert b["path"] == "direct"
    assert a["local_type"] == "host"
    assert b["local_type"] == "host"
    assert a["remote_type"] == "host"
    assert b["remote_type"] == "host"
    assert a["echo"] == HELLO_ACK
    assert b["echo"] == HELLO_ACK


async def _wrong_password() -> None:
    cfg_a = _loopback_cfg("controlling", "ws://127.0.0.1:0", "unused")
    cfg_b = _loopback_cfg("controlled", "ws://127.0.0.1:0", "unused")
    a = IceAgent(cfg_a)
    b = IceAgent(cfg_b)
    try:
        ca = await a.gather()
        cb = await b.gather()
        assert a.connection is not None and b.connection is not None
        await a.set_remote(
            {
                "username": b.connection.local_username,
                "password": "xxxxxxxxxxxxxxxxxxxxxx",
                "candidates": cb,
            }
        )
        await b.set_remote(
            {
                "username": a.connection.local_username,
                "password": "yyyyyyyyyyyyyyyyyyyyyy",
                "candidates": ca,
            }
        )
        results = await asyncio.wait_for(
            asyncio.gather(a.connect(), b.connect(), return_exceptions=True),
            20,
        )
        assert all(isinstance(r, ConnectionError) for r in results), results
    finally:
        await a.close()
        await b.close()


def test_wrong_ice_password_does_not_nominate() -> None:
    """Sheet 8: connectivity checks must use ICE-PWD; garbage pwd does not punch."""
    asyncio.run(_wrong_password())


def test_cli_run_prints_path_and_ack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def fake_run(cfg: dict[str, Any], hold: bool = False) -> dict[str, Any]:
        del cfg
        assert hold is True
        print("path=direct local=host:127.0.0.1:1 remote=host:127.0.0.1:2")
        print("hello-ack")
        return {"ok": True, "path": "direct", "echo": HELLO_ACK}

    monkeypatch.setattr("agent.__main__.run_session", fake_run)
    from agent.__main__ import main

    cfg_path = tmp_path / "local.yaml"
    cfg_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["run", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "path=direct" in out
    assert "hello-ack" in out


def test_run_session_requires_signalling() -> None:
    cfg = _loopback_cfg("controlling", "", "hello-world")
    cfg["signalling_url"] = ""
    with pytest.raises(ValueError, match="signalling_url"):
        asyncio.run(run_session(cfg))
