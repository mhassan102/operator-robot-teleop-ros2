"""Login messages for the operator window.

The registry URL defaults to the local signalling port. The plaintext
password is placed only in the outbound login message.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_REGISTRY = "ws://127.0.0.1:8765"

_KNOWN_ERRORS = frozenset({"auth", "offline", "busy"})

_MESSAGES = {
    "auth": "Wrong password.",
    "offline": "That robot is offline.",
    "busy": "Another operator is already connected.",
    "unreachable": "Cannot reach the registry.",
    "error": "The registry sent an unexpected reply.",
    "empty": "Enter the ID and password from the robot terminal.",
}


def message_for(code: str) -> str:
    return _MESSAGES.get(code, _MESSAGES["error"])


def login_message(robot_id: str, password: str) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "login",
        "robot_id": robot_id,
        "password": password,
    }


class LoginResult:
    """``code`` is ``ok``, ``auth``, ``offline``, ``busy``, or ``error``."""

    def __init__(self, code: str, hostname: str = "") -> None:
        self.code = code
        self.hostname = hostname

    @property
    def ok(self) -> bool:
        return self.code == "ok"

    def __repr__(self) -> str:
        return f"LoginResult({self.code!r}, hostname={self.hostname!r})"


def interpret_login(raw: Any) -> LoginResult:
    """Map one registry frame to a login result. A missing hostname is blank."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return LoginResult("error")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return LoginResult("error")
    if not isinstance(raw, dict) or raw.get("v") != 1:
        return LoginResult("error")
    kind = raw.get("type")
    if kind == "logged_in":
        hostname = raw.get("hostname", "")
        if not isinstance(hostname, str):
            hostname = ""
        return LoginResult("ok", hostname)
    if kind == "error":
        code = raw.get("code")
        if isinstance(code, str) and code in _KNOWN_ERRORS:
            return LoginResult(code)
    return LoginResult("error")
