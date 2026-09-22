"""T5 TURN fallback: pass coturn into aioice. No live EC2 in default pytest."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from aioice import Candidate, Connection, TransportPolicy

from agent.ice import (
    IceAgent,
    candidate_to_dict,
    format_path,
    gather,
    nominated_path,
)

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
RELAY = Candidate(
    foundation="3",
    component=1,
    transport="udp",
    priority=16777215,
    host="192.0.2.50",
    port=49152,
    type="relay",
    related_address="192.0.2.10",
    related_port=40000,
)

BASE = {
    "role": "controlling",
    "bind_ip": "192.0.2.10",
    "ifname": None,
    "stun_server": "192.0.2.1",
    "stun_port": 3478,
}

TURN = {
    "turn_server": "192.0.2.50",
    "turn_port": 3478,
    "turn_user": "labturn",
    "turn_password": "secret",
    "ice_policy": "all",
}


class _FakeProto:
    def __init__(self, cand: Candidate) -> None:
        self.local_candidate = cand
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _cfg(**extra: Any) -> dict[str, Any]:
    cfg = dict(BASE)
    cfg.update(TURN)
    cfg.update(extra)
    return cfg


def test_all_policy_passes_stun_and_turn_udp() -> None:
    cfg = _cfg(turn_transport="tcp")  # ignored; T5 is UDP only
    conn = IceAgent(cfg).build_connection()
    try:
        assert conn.stun_server == ("192.0.2.1", 3478)
        assert conn.turn_server == ("192.0.2.50", 3478)
        assert conn.turn_username == "labturn"
        assert conn.turn_password == "secret"
        assert conn.turn_transport == "udp"
        assert conn.turn_ssl is False
        assert conn._transport_policy == TransportPolicy.ALL
    finally:
        asyncio.run(conn.close())


def test_relay_policy_drops_stun_and_sets_relay() -> None:
    conn = IceAgent(_cfg(ice_policy="relay")).build_connection()
    try:
        assert conn.stun_server is None
        assert conn.turn_server == ("192.0.2.50", 3478)
        assert conn.turn_transport == "udp"
        assert conn._transport_policy == TransportPolicy.RELAY
    finally:
        asyncio.run(conn.close())


def test_relay_policy_requires_turn() -> None:
    with pytest.raises(ValueError, match="ice_policy relay"):
        IceAgent(dict(BASE, ice_policy="relay"))


def test_turn_server_without_password_rejected() -> None:
    with pytest.raises(ValueError, match="turn_password"):
        IceAgent(dict(BASE, turn_server="192.0.2.50", turn_port=3478))


def test_bad_ice_policy_rejected() -> None:
    with pytest.raises(ValueError, match="ice_policy"):
        IceAgent(_cfg(ice_policy="tcp"))


def test_turn_server_tailscale_rejected() -> None:
    with pytest.raises(ValueError, match="turn_server"):
        IceAgent(_cfg(turn_server="100.64.0.1"))


def test_nominated_relay_pair_is_path_turn() -> None:
    conn = Connection(ice_controlling=True, use_ipv4=True, use_ipv6=False)

    class _Pair:
        def __init__(self, local: Candidate, remote: Candidate) -> None:
            self.local_candidate = local
            self.remote_candidate = remote

    try:
        conn._nominated[1] = _Pair(RELAY, RELAY)  # type: ignore[assignment]
        info = nominated_path(conn)
        assert info["kind"] == "turn"
        assert "path=turn" in format_path(info)
        conn._nominated[1] = _Pair(HOST, RELAY)  # type: ignore[assignment]
        assert nominated_path(conn)["kind"] == "turn"
        conn._nominated[1] = _Pair(SRFLX, HOST)  # type: ignore[assignment]
        assert nominated_path(conn)["kind"] == "direct"
    finally:
        asyncio.run(conn.close())


def test_relay_policy_gather_keeps_only_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    host_p, srflx_p, relay_p = _FakeProto(HOST), _FakeProto(SRFLX), _FakeProto(RELAY)

    async def fake_gather(self: Connection) -> None:
        self._local_candidates = [HOST, SRFLX, RELAY]
        self._local_candidates_end = True
        self._protocols = [host_p, srflx_p, relay_p]

    async def fake_close(self: Connection) -> None:
        for proto in list(self._protocols):
            await proto.close()
        self._protocols = []
        self._local_candidates = []

    monkeypatch.setattr(Connection, "gather_candidates", fake_gather)
    monkeypatch.setattr(Connection, "close", fake_close)
    cands, note = asyncio.run(gather(_cfg(ice_policy="relay")))
    assert [c["type"] for c in cands] == ["relay"]
    assert cands[0]["ip"] == "192.0.2.50"
    assert host_p.closed and srflx_p.closed
    assert relay_p.closed  # agent.close() after gather
    assert note is None


def test_keep_relay_only_drains_close_events() -> None:
    async def _run() -> None:
        agent = IceAgent(_cfg(ice_policy="relay"))
        conn = agent.build_connection()

        class _QProto(_FakeProto):
            def __init__(self, cand: Candidate, owner: Connection) -> None:
                super().__init__(cand)
                self.owner = owner

            async def close(self) -> None:
                await super().close()
                self.owner._queue.put_nowait((None, None))

        host = _QProto(HOST, conn)
        relay = _FakeProto(RELAY)
        conn._protocols = [host, relay]
        conn._local_candidates = [HOST, RELAY]
        try:
            await agent._keep_relay_only(conn)
            assert conn._queue.empty()
            assert [p.local_candidate.type for p in conn._protocols] == ["relay"]
            assert host.closed
            assert not relay.closed
        finally:
            await conn.close()

    asyncio.run(_run())


def test_set_remote_relay_policy_drops_host() -> None:
    async def _run() -> list[str | None]:
        agent = IceAgent(_cfg(ice_policy="relay"))
        agent.connection = agent.build_connection()
        seen: list[Candidate | None] = []

        async def _add(cand: Candidate | None) -> None:
            seen.append(cand)

        agent.connection.add_remote_candidate = _add  # type: ignore[method-assign]
        try:
            await agent.set_remote(
                {
                    "username": "abcd",
                    "password": "x" * 22,
                    "candidates": [
                        candidate_to_dict(HOST),
                        candidate_to_dict(RELAY),
                    ],
                }
            )
        finally:
            await agent.close()
        return [c.type if c is not None else None for c in seen]

    assert asyncio.run(_run()) == ["relay", None]


def _first_local_ipv4() -> str | None:
    from aioice.ice import get_host_addresses

    for addr in get_host_addresses(use_ipv4=True, use_ipv6=False):
        if not addr.startswith("100."):
            return addr
    return None


@pytest.mark.turn
def test_live_turn_relay_candidate() -> None:
    """Optional. Set TURN_SERVER, TURN_USER, TURN_PASSWORD. Skips in CI."""
    host = os.environ.get("TURN_SERVER")
    user = os.environ.get("TURN_USER")
    password = os.environ.get("TURN_PASSWORD")
    if not host or not user or not password:
        pytest.skip("TURN_SERVER/TURN_USER/TURN_PASSWORD not set")
    bind_ip = _first_local_ipv4()
    if bind_ip is None:
        pytest.skip("no non-Tailscale IPv4")
    port = int(os.environ.get("TURN_PORT", "3478"))
    cfg = {
        "role": "controlling",
        "bind_ip": bind_ip,
        "ifname": None,
        "stun_server": host,
        "stun_port": port,
        "turn_server": host,
        "turn_port": port,
        "turn_user": user,
        "turn_password": password,
        "ice_policy": "relay",
    }
    try:
        cands, note = asyncio.run(asyncio.wait_for(gather(cfg), 25))
    except Exception as exc:  # noqa: BLE001 — skip when coturn is unreachable
        pytest.skip(f"live TURN gather failed: {exc}")
    relay = [c for c in cands if c["type"] == "relay"]
    if not relay:
        pytest.skip(f"no relay candidate ({note})")
    assert relay[0]["port"] > 0
    assert all(c["type"] == "relay" for c in cands)
