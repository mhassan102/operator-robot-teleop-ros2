"""Build the robot start commands for a config that already passed review.

``robot_plan`` does not spawn processes. A set Interface 2 is an error.
TURN writes ``packaging/run/ice-edge.yaml`` from a template and refuses a
missing IPv4 or any address that starts with ``100.``. The file is a
runtime copy. This module does not print it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_IFACE2 = (
    "second interface is not used in this version; set Interface 2 to None"
)
_NO_IPV4 = "Interface 1 has no IPv4 for bind_ip"
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_PHASES = frozenset({"starting", "ready", "stopped", "error"})


class PlanError(Exception):
    """The start plan was refused. ``detail`` is safe to put on the status line."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Command:
    """One process the live path may spawn. ``env`` is added to that process only."""

    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()
    label: str = ""


@dataclass(frozen=True)
class StartDecision:
    """``commands`` is None when the caller must not spawn."""

    status: dict[str, Any] | None
    commands: tuple[Command, ...] | None


def status_message(phase: str, detail: str) -> dict[str, Any]:
    if phase not in _PHASES:
        raise ValueError(phase)
    return {"v": 1, "type": "status", "phase": phase, "detail": detail}


def _script(root: Path, *parts: str) -> str:
    return str(root.joinpath(*parts))


def _require_path(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or value == "" or value.startswith("-"):
        raise PlanError(f"{key} is not set")
    if "\n" in value or "\x00" in value:
        raise PlanError(f"{key} is not set")
    return value


def _iface_ipv4(inventory: Mapping[str, Any], name: object) -> str | None:
    if not isinstance(name, str) or name == "":
        return None
    items = inventory.get("interfaces")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, Mapping) or item.get("name") != name:
            continue
        ipv4 = item.get("ipv4")
        if isinstance(ipv4, str) and ipv4:
            return ipv4
        return None
    return None


def _bind_ip(inventory: Mapping[str, Any], iface1: object) -> str:
    raw = _iface_ipv4(inventory, iface1)
    if raw is None or not _IPV4.match(raw):
        raise PlanError(_NO_IPV4)
    octets = [int(part) for part in raw.split(".")]
    if any(part > 255 for part in octets):
        raise PlanError(_NO_IPV4)
    if raw.startswith("100."):
        raise PlanError(f"refusing bind_ip {raw}")
    return raw


def _replace_bind_ip(template: str, ipv4: str) -> str:
    found = False
    out: list[str] = []
    for line in template.splitlines(keepends=True):
        newline = ""
        body = line
        if body.endswith("\r\n"):
            newline = "\r\n"
            body = body[:-2]
        elif body.endswith("\n"):
            newline = "\n"
            body = body[:-1]
        stripped = body.lstrip(" ")
        if stripped.startswith("#") or not stripped.startswith("bind_ip:"):
            out.append(line)
            continue
        indent = body[: len(body) - len(stripped)]
        rest = stripped[len("bind_ip:") :].strip()
        comment = ""
        marker = " #"
        if marker in rest:
            _value, _sep, tail = rest.partition(marker)
            comment = marker + tail
        out.append(f"{indent}bind_ip: {ipv4}{comment}{newline}")
        found = True
    if not found:
        raise PlanError("ICE template has no bind_ip")
    return "".join(out)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _ice_text(root: Path, template: str | None) -> str:
    if template is not None:
        return template
    source = root / "turn" / "config" / "local_edge.yaml"
    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError("missing turn/config/local_edge.yaml") from exc


def robot_plan(
    config: Mapping[str, Any],
    inventory: Mapping[str, Any],
    repo_root: str | Path,
    template: str | None = None,
) -> list[Command]:
    """Return the edge, camera, and arm commands, in that order.

    ``template`` is the TURN yaml text. When it is omitted the live caller
    reads ``turn/config/local_edge.yaml``. Tests pass a fixture string.
    """
    if config.get("iface2") is not None:
        raise PlanError(_IFACE2)
    link = config.get("link")
    if link not in {"tailscale", "turn"}:
        raise PlanError("link must be tailscale or turn")
    arm = _require_path(config, "arm")
    video = _require_path(config, "video")
    root = Path(repo_root)
    daemon = _script(root, "mlink-transport", "scripts", "start_daemon.sh")
    camera = _script(root, "video", "so-arm", "start.sh")
    arm_script = _script(
        root, "teleoperation-prototype", "scripts", "start_robot_mlink.sh"
    )
    if link == "tailscale":
        edge = Command(
            argv=(daemon, "edge", "--remote-laptop"),
            label="mlink-edge",
        )
    else:
        ipv4 = _bind_ip(inventory, config.get("iface1"))
        text = _replace_bind_ip(_ice_text(root, template), ipv4)
        dest = root / "packaging" / "run" / "ice-edge.yaml"
        _write_private(dest, text)
        edge = Command(
            argv=(daemon, "edge", "--ice", "--ice-config", str(dest)),
            label="mlink-edge",
        )
    return [
        edge,
        Command(argv=(camera,), env=(("DEVICE", video),), label="camera"),
        Command(
            argv=(arm_script, "--real-arm", "--serial-port", arm),
            label="arm",
        ),
    ]


def decide_start(
    config: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    repo_root: str | Path,
    *,
    dry_run: bool,
    template: str | None = None,
) -> StartDecision:
    """Status to send now. ``commands`` is set only when the live path may spawn."""
    if not isinstance(config, Mapping):
        return StartDecision(status_message("error", "no config"), None)
    try:
        commands = robot_plan(
            config,
            inventory if isinstance(inventory, Mapping) else {},
            repo_root,
            template=template,
        )
    except PlanError as exc:
        return StartDecision(status_message("error", exc.detail), None)
    if dry_run:
        return StartDecision(status_message("ready", "dry-run"), None)
    return StartDecision(None, tuple(commands))
