"""T3 STUN gather: structured candidates without live NAT. Skip if no STUN."""

from __future__ import annotations

import asyncio
import errno
import socket
from pathlib import Path
from typing import Any

import pytest
from aioice import Candidate, Connection

from agent.ice import (
    IceAgent,
    bind_to_device,
    bound_device_name,
    candidate_to_dict,
    format_candidate,
    gather,
    load_config,
    validate_ice_endpoints,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "example.yaml"

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

CFG = {
    "role": "controlling",
    "bind_ip": "192.0.2.10",
    "ifname": None,
    "stun_server": "192.0.2.1",
    "stun_port": 3478,
}


def test_example_yaml_loads_for_gather() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg["bind_ip"]
    assert cfg["stun_server"]
    assert cfg["stun_port"]


def test_candidate_to_dict_host_and_srflx() -> None:
    d_host = candidate_to_dict(HOST)
    d_srflx = candidate_to_dict(SRFLX)
    assert d_host == {
        "ip": "192.0.2.10",
        "port": 40000,
        "type": "host",
        "foundation": "1",
        "component": 1,
        "priority": 2130706431,
    }
    assert "related" not in d_host
    assert d_srflx["type"] == "srflx"
    assert d_srflx["ip"] == "198.51.100.8"
    assert d_srflx["related"] == {"ip": "192.0.2.10", "port": 40000}
    assert format_candidate(d_srflx) == (
        "srflx 198.51.100.8:40000 related 192.0.2.10:40000"
    )


def test_build_connection_stun_only_no_turn() -> None:
    conn = IceAgent(CFG).build_connection()
    try:
        assert conn.stun_server == ("192.0.2.1", 3478)
        assert conn.turn_server is None
        assert conn.ice_controlling is True
    finally:
        asyncio.run(conn.close())


def test_controlled_role() -> None:
    cfg = dict(CFG, role="controlled")
    conn = IceAgent(cfg).build_connection()
    try:
        assert conn.ice_controlling is False
    finally:
        asyncio.run(conn.close())


def test_rejects_tailscale_endpoints() -> None:
    with pytest.raises(ValueError, match="100.x"):
        validate_ice_endpoints("100.64.0.1", None, "192.0.2.1")
    with pytest.raises(ValueError, match="tailscale0"):
        validate_ice_endpoints("192.0.2.10", "tailscale0", "192.0.2.1")
    with pytest.raises(ValueError, match="100.x"):
        validate_ice_endpoints("192.0.2.10", None, "100.67.1.1")


def test_gather_structured_from_mocked_aioice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_gather(self: Connection) -> None:
        self._local_candidates = [HOST, SRFLX]
        self._local_candidates_end = True

    async def fake_close(self: Connection) -> None:
        self._local_candidates = []
        self._protocols = []

    monkeypatch.setattr(Connection, "gather_candidates", fake_gather)
    monkeypatch.setattr(Connection, "close", fake_close)
    cands, note = asyncio.run(gather(CFG))
    assert [c["type"] for c in cands] == ["host", "srflx"]
    assert cands[1]["ip"] == "198.51.100.8"
    assert cands[1]["related"]["ip"] == "192.0.2.10"
    assert note is None


def test_gather_host_on_loopback_without_stun() -> None:
    """Real aioice bind on 127.0.0.1. No STUN, no TURN, no connect()."""
    cfg = {
        "role": "controlling",
        "bind_ip": "127.0.0.1",
        "ifname": None,
        "stun_server": None,
        "stun_port": 3478,
    }
    cands, _note = asyncio.run(asyncio.wait_for(gather(cfg), 10))
    host = [c for c in cands if c["type"] == "host"]
    assert host, cands
    assert all(c["ip"] == "127.0.0.1" for c in host)
    assert all(c["type"] != "relay" for c in cands)
    assert all(c["type"] != "srflx" for c in cands)


def test_bind_to_device_lo() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        try:
            bind_to_device(sock, "lo")
        except OSError as exc:
            if exc.errno in (
                errno.EPERM,
                errno.EACCES,
                errno.ENODEV,
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
                errno.ENOTSUP,
            ):
                pytest.skip(f"SO_BINDTODEVICE: {exc}")
            raise
        name = bound_device_name(sock)
        if name is not None:
            assert name == "lo"
    finally:
        sock.close()


def test_cli_gather_prints_srflx(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def fake_gather(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], None]:
        del cfg
        return (
            [
                candidate_to_dict(HOST),
                candidate_to_dict(SRFLX),
            ],
            None,
        )

    monkeypatch.setattr("agent.__main__.gather", fake_gather)
    from agent.__main__ import main

    cfg_path = tmp_path / "local.yaml"
    cfg_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["gather", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "srflx 198.51.100.8:40000" in out
    assert "host 192.0.2.10:40000" in out


def _stun_resolves(host: str, port: int) -> bool:
    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        return True
    except OSError:
        return False


def _first_local_ipv4() -> str | None:
    from aioice.ice import get_host_addresses

    for addr in get_host_addresses(use_ipv4=True, use_ipv6=False):
        if not addr.startswith("100."):
            return addr
    return None


@pytest.mark.network
def test_live_stun_google_srflx() -> None:
    host, port = "stun.l.google.com", 19302
    if not _stun_resolves(host, port):
        pytest.skip("stun.l.google.com not resolvable")
    bind_ip = _first_local_ipv4()
    if bind_ip is None:
        pytest.skip("no non-Tailscale IPv4")
    cfg = {
        "role": "controlling",
        "bind_ip": bind_ip,
        "ifname": None,
        "stun_server": host,
        "stun_port": port,
    }
    try:
        cands, _note = asyncio.run(asyncio.wait_for(gather(cfg), 20))
    except Exception as exc:  # noqa: BLE001 — skip live STUN on any gather failure
        pytest.skip(f"live STUN gather failed: {exc}")
    srflx = [c for c in cands if c["type"] == "srflx"]
    if not srflx:
        pytest.skip("no srflx (STUN UDP blocked)")
    assert srflx[0]["port"] > 0
    assert srflx[0]["related"]["ip"] == bind_ip
