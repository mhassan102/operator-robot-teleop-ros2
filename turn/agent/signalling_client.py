"""Signalling client: connect, join a room, send ICE blob, wait for peer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


class SignallingError(Exception):
    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


class SignallingClient:
    """WebSocket client for ICE username / password / candidate exchange."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._ws: Any = None
        self._incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._recv_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self._ws = await connect(self.url)
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def join(self, room: str, role: str, timeout: float = 5.0) -> dict[str, Any]:
        await self._send({"type": "join", "room": room, "role": role})
        return await self._expect("joined", timeout)

    async def send(
        self,
        username: str,
        password: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        await self._send(
            {
                "type": "candidates",
                "username": username,
                "password": password,
                "candidates": candidates,
            }
        )

    async def wait_for_peer(self, timeout: float = 5.0) -> dict[str, Any]:
        return await self._expect("candidates", timeout)

    async def close(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            self._recv_task = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __aenter__(self) -> SignallingClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._ws is None:
            raise SignallingError("not_connected")
        await self._ws.send(json.dumps(msg))

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(msg, dict):
                    await self._incoming.put(msg)
        except ConnectionClosed:
            pass
        finally:
            await self._incoming.put(None)

    async def _expect(self, typ: str, timeout: float) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"timed out waiting for {typ}")
            msg = await asyncio.wait_for(self._incoming.get(), remaining)
            if msg is None:
                raise SignallingError("disconnected")
            if msg.get("type") == "error":
                raise SignallingError(str(msg.get("error", "error")))
            if msg.get("type") == typ:
                return msg
