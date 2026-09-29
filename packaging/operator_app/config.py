"""Config-page values and the config message.

The operator NIC is discovered from ``/proc/net/route`` and shown as a
read-only line. It is not a field of the config message.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from packaging.robot_app.inventory import default_arm, default_video, hidden_interface

LINK_HELP = "TURN changes gripper control. Video stays on the robot camera page."


class Choice:
    """One dropdown row. ``value`` is what the config message sends."""

    def __init__(self, label: str, value: str | None) -> None:
        self.label = label
        self.value = value

    def __repr__(self) -> str:
        return f"Choice({self.label!r}, {self.value!r})"


def _items(inventory: Mapping[str, Any], key: str) -> list[Any]:
    if not isinstance(inventory, Mapping):
        return []
    items = inventory.get(key) or []
    return items if isinstance(items, list) else []


def interface1_choices(inventory: Mapping[str, Any]) -> list[Choice]:
    """NICs that own the default route, in inventory order."""
    choices: list[Choice] = []
    for item in _items(inventory, "interfaces"):
        if not isinstance(item, dict) or item.get("default_route") is not True:
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            choices.append(Choice(name, name))
    return choices


def interface2_choices(inventory: Mapping[str, Any]) -> list[Choice]:
    """``None`` plus every robot NIC. Non-default NICs are marked local-only."""
    choices = [Choice("None", None)]
    for item in _items(inventory, "interfaces"):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if item.get("default_route") is True:
            label = name
        else:
            label = f"{name} (local-only)"
        choices.append(Choice(label, name))
    return choices


def arm_choices(inventory: Mapping[str, Any]) -> list[Choice]:
    choices: list[Choice] = []
    for arm in _items(inventory, "arms"):
        if not isinstance(arm, dict):
            continue
        path = arm.get("path")
        if not isinstance(path, str) or not path:
            continue
        label = arm.get("label") if isinstance(arm.get("label"), str) else ""
        tty = arm.get("tty") if isinstance(arm.get("tty"), str) else ""
        choices.append(Choice(f"{label}  {path}  {tty}".strip(), path))
    return choices


def video_choices(inventory: Mapping[str, Any]) -> list[Choice]:
    choices: list[Choice] = []
    for video in _items(inventory, "videos"):
        if not isinstance(video, dict):
            continue
        path = video.get("path")
        if not isinstance(path, str) or not path:
            continue
        name = video.get("name") if isinstance(video.get("name"), str) else ""
        choices.append(Choice(f"{path}  {name}".strip(), path))
    return choices


def preferred_arm(inventory: Mapping[str, Any]) -> str | None:
    return default_arm(_items(inventory, "arms"))


def preferred_video(inventory: Mapping[str, Any]) -> str | None:
    return default_video(_items(inventory, "videos"))


def config_message(
    link: str,
    iface1: str,
    iface2: str | None,
    arm: str,
    video: str,
) -> dict[str, Any]:
    """The operator → robot config object. ``iface2`` is null when unset."""
    return {
        "v": 1,
        "type": "config",
        "link": link,
        "iface1": iface1,
        "iface2": iface2,
        "arm": arm,
        "video": video,
    }


def operator_network_text(nic: str) -> str:
    text = nic.strip()
    if not text:
        return "Operator network: (none)"
    return f"Operator network: {text}"


def local_default_nic(route_text: str | None = None) -> str:
    """Physical NIC of the default route, lowest metric first.

    Loopback, Tailscale, docker bridges, and veth are skipped. This
    reads ``/proc/net/route`` when ``route_text`` is omitted. It does
    not run a process.
    """
    if route_text is None:
        try:
            route_text = Path("/proc/net/route").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return ""
    best_name = ""
    best_metric: int | None = None
    for raw in route_text.splitlines():
        parts = raw.split()
        if len(parts) < 7 or parts[1] != "00000000":
            continue
        name = parts[0]
        if hidden_interface(name, ""):
            continue
        try:
            metric = int(parts[6])
        except ValueError:
            continue
        if best_metric is None or metric < best_metric:
            best_metric = metric
            best_name = name
    return best_name


def parse_inbound(raw: Any) -> dict[str, Any] | None:
    """One ``v: 1`` object, or ``None`` when the frame is not that."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return None
    if isinstance(raw, dict) and raw.get("v") == 1:
        return raw
    return None


def review_text(msg: Mapping[str, Any]) -> str:
    """``config_ok``, or the ``detail`` string from ``bad_config``."""
    if msg.get("v") != 1:
        return "The registry sent an unexpected reply."
    if msg.get("type") == "config_ok":
        return "config_ok"
    if msg.get("type") == "error":
        detail = msg.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        code = msg.get("code")
        if isinstance(code, str) and code:
            return code
    return "The registry sent an unexpected reply."
