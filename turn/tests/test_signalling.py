"""In-process signalling: two clients in one room exchange ICE blobs. No STUN."""

from __future__ import annotations

import asyncio
from typing import Any

from agent.signalling_client import SignallingClient, SignallingError
from signalling.server import listening_uri, start_server

PAYLOAD_A = {
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

PAYLOAD_B = {
    "username": "ufragB",
    "password": "passwordB_is_22charsX",
    "candidates": [
        {
            "ip": "192.0.2.20",
            "port": 40001,
            "type": "srflx",
            "foundation": "2",
            "component": 1,
            "priority": 1694498815,
            "related": {"ip": "10.0.0.20", "port": 40001},
        }
    ],
}


async def _with_server(coro):
    server = await start_server("127.0.0.1", 0)
    try:
        return await coro(listening_uri(server))
    finally:
        server.close()
        await server.wait_closed()


def _blob_fields(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": msg["username"],
        "password": msg["password"],
        "candidates": msg["candidates"],
    }


async def _two_clients(url: str) -> None:
    async with SignallingClient(url) as a, SignallingClient(url) as b:
        await a.join("hello-world", "controlling")
        await b.join("hello-world", "controlled")
        await a.send(**PAYLOAD_A)
        await b.send(**PAYLOAD_B)
        peer_a = await a.wait_for_peer()
        peer_b = await b.wait_for_peer()
        assert _blob_fields(peer_a) == PAYLOAD_B
        assert _blob_fields(peer_b) == PAYLOAD_A
        assert peer_a["role"] == "controlled"
        assert peer_b["role"] == "controlling"
        assert peer_a.get("type") == "candidates"
        assert "hello" not in peer_a
        assert "hello" not in peer_b


async def _late_joiner(url: str) -> None:
    async with SignallingClient(url) as a, SignallingClient(url) as b:
        await a.join("hello-world", "controlling")
        await a.send(**PAYLOAD_A)
        await b.join("hello-world", "controlled")
        await b.send(**PAYLOAD_B)
        peer_b = await b.wait_for_peer()
        peer_a = await a.wait_for_peer()
        assert _blob_fields(peer_b) == PAYLOAD_A
        assert _blob_fields(peer_a) == PAYLOAD_B


async def _room_full(url: str) -> None:
    async with (
        SignallingClient(url) as a,
        SignallingClient(url) as b,
        SignallingClient(url) as c,
    ):
        await a.join("hello-world", "controlling")
        await b.join("hello-world", "controlled")
        try:
            await c.join("hello-world", "controlling")
        except SignallingError as exc:
            assert exc.error == "room_full"
        else:
            raise AssertionError("third member should be rejected")


def test_two_clients_exchange_candidates() -> None:
    asyncio.run(asyncio.wait_for(_with_server(_two_clients), 10))


def test_peer_blob_replayed_to_late_joiner() -> None:
    asyncio.run(asyncio.wait_for(_with_server(_late_joiner), 10))


def test_room_rejects_third_member() -> None:
    asyncio.run(asyncio.wait_for(_with_server(_room_full), 10))
