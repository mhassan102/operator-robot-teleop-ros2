"""WebSocket signalling: two members per room exchange ICE credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from websockets.asyncio.server import Server, serve
from websockets.exceptions import ConnectionClosed

ROLES = frozenset({"controlling", "controlled"})
CANDIDATE_KEYS = ("ip", "port", "type", "foundation", "component", "priority")


def _send_json(ws: Any, msg: dict[str, Any]) -> Any:
    return ws.send(json.dumps(msg))


def _validate_candidates_msg(msg: dict[str, Any]) -> str | None:
    if not isinstance(msg.get("username"), str) or not msg["username"]:
        return "bad_candidates"
    if not isinstance(msg.get("password"), str) or not msg["password"]:
        return "bad_candidates"
    cands = msg.get("candidates")
    if not isinstance(cands, list):
        return "bad_candidates"
    for c in cands:
        if not isinstance(c, dict):
            return "bad_candidates"
        if any(k not in c for k in CANDIDATE_KEYS):
            return "bad_candidates"
        related = c.get("related")
        if related is not None:
            if (
                not isinstance(related, dict)
                or "ip" not in related
                or "port" not in related
            ):
                return "bad_candidates"
    return None


class Member:
    def __init__(self, ws: Any, role: str) -> None:
        self.ws = ws
        self.role = role
        self.blob: dict[str, Any] | None = None


class Room:
    def __init__(self, name: str) -> None:
        self.name = name
        self.members: dict[Any, Member] = {}

    def other(self, ws: Any) -> Member | None:
        for other_ws, member in self.members.items():
            if other_ws is not ws:
                return member
        return None


class Hub:
    """Rooms of exactly two members (controlling + controlled)."""

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    async def handle(self, websocket: Any) -> None:
        room: Room | None = None
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await _send_json(websocket, {"type": "error", "error": "bad_json"})
                    continue
                if not isinstance(msg, dict):
                    await _send_json(websocket, {"type": "error", "error": "bad_json"})
                    continue
                typ = msg.get("type")
                if typ == "join":
                    room, err = self._join(websocket, msg, room)
                    if err is not None:
                        await _send_json(websocket, {"type": "error", "error": err})
                        continue
                    assert room is not None
                    await _send_json(
                        websocket,
                        {"type": "joined", "room": room.name, "role": msg["role"]},
                    )
                    peer = room.other(websocket)
                    if peer is not None and peer.blob is not None:
                        await _send_json(websocket, peer.blob)
                elif typ == "candidates":
                    if room is None or websocket not in room.members:
                        await _send_json(
                            websocket, {"type": "error", "error": "not_joined"}
                        )
                        continue
                    err = _validate_candidates_msg(msg)
                    if err is not None:
                        await _send_json(websocket, {"type": "error", "error": err})
                        continue
                    blob = {
                        "type": "candidates",
                        "username": msg["username"],
                        "password": msg["password"],
                        "candidates": msg["candidates"],
                        "role": room.members[websocket].role,
                    }
                    room.members[websocket].blob = blob
                    peer = room.other(websocket)
                    if peer is not None:
                        await _send_json(peer.ws, blob)
                else:
                    await _send_json(
                        websocket, {"type": "error", "error": "unknown_type"}
                    )
        except ConnectionClosed:
            pass
        finally:
            self._leave(websocket, room)

    def _join(
        self, websocket: Any, msg: dict[str, Any], existing: Room | None
    ) -> tuple[Room | None, str | None]:
        if existing is not None:
            return existing, "already_joined"
        room_name = msg.get("room")
        role = msg.get("role")
        if not isinstance(room_name, str) or not room_name:
            return None, "bad_join"
        if role not in ROLES:
            return None, "bad_join"
        room = self.rooms.get(room_name)
        if room is None:
            room = Room(room_name)
            self.rooms[room_name] = room
        if len(room.members) >= 2:
            return None, "room_full"
        if any(m.role == role for m in room.members.values()):
            return None, "duplicate_role"
        room.members[websocket] = Member(websocket, role)
        return room, None

    def _leave(self, websocket: Any, room: Room | None) -> None:
        if room is None:
            return
        room.members.pop(websocket, None)
        if not room.members:
            self.rooms.pop(room.name, None)


async def start_server(host: str = "127.0.0.1", port: int = 0) -> Server:
    """Bind a signalling server. port=0 picks an ephemeral port (tests)."""
    return await serve(Hub().handle, host, port)


def listening_uri(server: Server) -> str:
    sock = server.sockets[0]
    host, port = sock.getsockname()[:2]
    return f"ws://{host}:{port}"


async def _amain(host: str, port: int) -> None:
    async with serve(Hub().handle, host, port) as server:
        print(f"signalling {listening_uri(server)}", flush=True)
        await server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ICE signalling WebSocket server")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        asyncio.run(_amain(args.bind, args.port))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
