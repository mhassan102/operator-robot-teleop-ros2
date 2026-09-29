"""Validate a config message against the last inventory.

Review answers ``config_ok`` or ``bad_config``. A set Interface 2 is
still accepted here. Nothing in this module starts a process, opens a
serial port, or bonds a second NIC.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_LINKS = frozenset({"tailscale", "turn"})


def _names(inventory: Mapping[str, Any], *, default_only: bool) -> set[str]:
    found: set[str] = set()
    items = inventory.get("interfaces") or []
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name == "":
            continue
        if default_only and item.get("default_route") is not True:
            continue
        found.add(name)
    return found


def _paths(inventory: Mapping[str, Any], key: str) -> set[str]:
    found: set[str] = set()
    items = inventory.get(key) or []
    if not isinstance(items, list):
        return found
    for item in items:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            found.add(path)
    return found


def _text(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    if value is None or value == "":
        return "-"
    return str(value)


def format_config_line(msg: Mapping[str, Any], result: str) -> str:
    """One terminal line: the fields that arrived, then the result."""
    if "iface2" not in msg:
        iface2_text = "-"
    elif msg.get("iface2") is None:
        iface2_text = "none"
    else:
        iface2_text = _text(msg.get("iface2"))
    return (
        "Config  "
        f"link {_text(msg.get('link'))}  "
        f"iface1 {_text(msg.get('iface1'))}  "
        f"iface2 {iface2_text}  "
        f"arm {_text(msg.get('arm'))}  "
        f"video {_text(msg.get('video'))}  "
        f"{result}"
    )


def _detail(msg: Mapping[str, Any], inventory: Mapping[str, Any]) -> str | None:
    if msg.get("link") not in _LINKS:
        return "link must be tailscale or turn"
    iface1 = msg.get("iface1")
    if not isinstance(iface1, str) or iface1 not in _names(inventory, default_only=True):
        return "iface1 is not a default-route interface"
    if "iface2" not in msg:
        return "unknown interface"
    iface2 = msg.get("iface2")
    known = _names(inventory, default_only=False)
    if iface2 is not None and (not isinstance(iface2, str) or iface2 not in known):
        return "unknown interface"
    arm = msg.get("arm")
    if not isinstance(arm, str) or arm not in _paths(inventory, "arms"):
        return "unknown arm"
    video = msg.get("video")
    if not isinstance(video, str) or video not in _paths(inventory, "videos"):
        return "unknown video"
    return None


def review_config(
    msg: object, inventory: Mapping[str, Any] | None
) -> tuple[dict[str, Any], str]:
    """Return ``(reply, terminal line)``.

    ``iface2`` may be a known NIC or null. Both are ``config_ok`` when
    the other fields match the inventory. The reply is a new object;
    extra fields on ``msg``, including a password, are not copied.
    """
    inv = inventory if isinstance(inventory, Mapping) else {}
    if not isinstance(msg, dict):
        reply = {
            "v": 1,
            "type": "error",
            "code": "bad_config",
            "detail": "bad config",
        }
        return reply, "Config  bad_config  bad config"
    detail = _detail(msg, inv)
    if detail is None:
        return {"v": 1, "type": "config_ok"}, format_config_line(msg, "config_ok")
    reply = {"v": 1, "type": "error", "code": "bad_config", "detail": detail}
    return reply, format_config_line(msg, f"bad_config  {detail}")
