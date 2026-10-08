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
        self.pending: list[dict[str, Any]] = []
        self._url = ""
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"OperatorSession(open={self.ws is not None}, hostname={self.hostname!r})"

    def take_pending(self) -> list[dict[str, Any]]:
        """Fleet frames read while waiting for ``logged_in``."""
        found = self.pending
        self.pending = []
        return found

    async def login(self, url: str, username: str, password: str) -> LoginResult:
        """Read until ``logged_in`` or ``error``. A ``fleet`` frame is stored."""
        async with self._lock:
            try:
                if self._url != url or not _is_open(self.ws):
                    await self._connect(url)
                assert self.ws is not None
                await self.ws.send(json.dumps(login_message(username, password)))
                while True:
                    raw = await asyncio.wait_for(self.ws.recv(), 5)
                    parsed = parse_inbound(raw)
                    if isinstance(parsed, dict) and parsed.get("type") == "fleet":
                        self.pending.append(parsed)
                        continue
                    result = interpret_login(parsed if parsed is not None else raw)
                    if result.ok:
                        self.hostname = result.hostname
                    else:
                        self.pending.clear()
                    return result
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.close()
                self.hostname = ""
                self.pending.clear()
                return LoginResult("unreachable")

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
        self.pending.clear()
        ws = self.ws
        self.ws = None
        self._url = ""
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
