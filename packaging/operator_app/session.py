"""Hold the operator's registry socket.

A successful login leaves the socket open. The config page sends on it
and reads inventory and the review reply. Failed logins keep the socket
as well, so the form can try again. The password is not stored on this
object.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from websockets.asyncio.client import connect

from packaging.operator_app.config import parse_inbound
from packaging.operator_app.login import LoginResult, interpret_login, login_message


def _is_open(ws: Any) -> bool:
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is None:
        return True
    return getattr(state, "name", "") == "OPEN"


class OperatorSession:
    """One WebSocket to the registry. ``ws`` is the socket kept for later."""

    def __init__(self) -> None:
        self.ws: Any = None
        self.hostname = ""
        self._url = ""
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"OperatorSession(open={self.ws is not None}, hostname={self.hostname!r})"

    async def login(self, url: str, robot_id: str, password: str) -> LoginResult:
        async with self._lock:
            try:
                if self._url != url or not _is_open(self.ws):
                    await self._connect(url)
                assert self.ws is not None
                await self.ws.send(json.dumps(login_message(robot_id, password)))
                raw = await asyncio.wait_for(self.ws.recv(), 5)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.close()
                self.hostname = ""
                return LoginResult("unreachable")
            result = interpret_login(raw)
            if result.ok:
                self.hostname = result.hostname
            return result

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._lock:
            ws = self.ws
            if not _is_open(ws):
                raise ConnectionError("closed")
        await ws.send(json.dumps(message))

    async def next_message(self) -> dict[str, Any] | None:
        """Next ``v: 1`` object, or ``None`` when the socket is closed.

        A frame that is not that object is skipped. Send may run while
        this waits; the lock is not held across ``recv``.
        """
        ws = self.ws
        if not _is_open(ws):
            return None
        while True:
            try:
                raw = await ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
            msg = parse_inbound(raw)
            if msg is not None:
                return msg

    async def _connect(self, url: str) -> None:
        await self.close()
        self.ws = await connect(url, open_timeout=5)
        self._url = url

    async def close(self) -> None:
        ws = self.ws
        self.ws = None
        self._url = ""
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
