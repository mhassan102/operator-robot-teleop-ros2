"""T4 run: gather → signalling exchange → ICE connect → hello / hello-ack."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from .ice import HELLO, HELLO_ACK, IceAgent, format_candidate, format_path
from .signalling_client import SignallingClient, SignallingError

PEER_TIMEOUT = 60.0
CONNECT_TIMEOUT = 30.0
ECHO_TIMEOUT = 10.0


def _require_signalling(cfg: dict[str, Any]) -> tuple[str, str]:
    url = cfg.get("signalling_url")
    room = cfg.get("room")
    if not isinstance(url, str) or not url:
        raise ValueError("config missing signalling_url")
    if not isinstance(room, str) or not room:
        raise ValueError("config missing room")
    return url, room


def _print_gather(cfg: dict[str, Any], cands: list[dict[str, Any]], note: str | None) -> None:
    ifname = cfg.get("ifname") or "-"
    stun = cfg.get("stun_server") or "-"
    print(
        f"bind_ip={cfg['bind_ip']} ifname={ifname} role={cfg['role']} "
        f"room={cfg.get('room', '-')} signalling={cfg.get('signalling_url', '-')} "
        f"stun={stun}:{cfg.get('stun_port', '-')}",
        flush=True,
    )
    if note:
        print(f"note: {note}", file=sys.stderr, flush=True)
    for c in cands:
        print(format_candidate(c), flush=True)


async def _echo(agent: IceAgent, role: str) -> bytes:
    if role == "controlling":
        await agent.send(HELLO)
        print("sent hello", flush=True)
        data = await asyncio.wait_for(agent.recv(), ECHO_TIMEOUT)
        print(data.decode("ascii", errors="replace"), flush=True)
        if data != HELLO_ACK:
            raise RuntimeError(f"expected hello-ack, got {data!r}")
        return data
    data = await asyncio.wait_for(agent.recv(), ECHO_TIMEOUT)
    print(f"recv {data.decode('ascii', errors='replace')}", flush=True)
    if data != HELLO:
        raise RuntimeError(f"expected hello, got {data!r}")
    await agent.send(HELLO_ACK)
    print("hello-ack", flush=True)
    return HELLO_ACK


async def run_session(cfg: dict[str, Any], *, hold: bool = False) -> dict[str, Any]:
    """Gather, exchange ICE-PWD + candidates, punch, nominate, echo.

    TURN is not used (T5). aioice consent checks are the light keepalive.
    """
    url, room = _require_signalling(cfg)
    role = cfg["role"]
    agent = IceAgent(cfg)
    result: dict[str, Any] = {"ok": False, "path": "none"}
    try:
        cands = await agent.gather()
        _print_gather(cfg, cands, agent.bind_note)
        if not cands:
            print("no candidates", file=sys.stderr, flush=True)
            return result
        conn = agent.connection
        assert conn is not None
        async with SignallingClient(url) as sig:
            await sig.join(room, role)
            await sig.send(conn.local_username, conn.local_password, cands)
            peer = await sig.wait_for_peer(PEER_TIMEOUT)
        await agent.set_remote(peer)
        try:
            await asyncio.wait_for(agent.connect(), CONNECT_TIMEOUT)
        except ConnectionError as exc:
            print(
                f"ICE negotiation failed ({exc}); direct punch did not nominate. "
                "T5 TURN is not this milestone.",
                file=sys.stderr,
                flush=True,
            )
            result["path"] = "failed"
            return result
        except asyncio.TimeoutError:
            print(
                "ICE connect timed out; direct punch did not nominate. "
                "T5 TURN is not this milestone.",
                file=sys.stderr,
                flush=True,
            )
            result["path"] = "failed"
            return result
        info = agent.path()
        print(format_path(info), flush=True)
        result.update(
            {
                "path": info["kind"],
                "local_type": info.get("local_type"),
                "remote_type": info.get("remote_type"),
            }
        )
        echoed = await _echo(agent, role)
        result["ok"] = True
        result["echo"] = echoed
        if hold:
            print("holding path (ICE consent keepalive); Ctrl-C to stop", flush=True)
            await asyncio.Event().wait()
        return result
    except (SignallingError, asyncio.TimeoutError, RuntimeError) as exc:
        print(f"run failed: {exc}", file=sys.stderr, flush=True)
        return result
    finally:
        await agent.close()
