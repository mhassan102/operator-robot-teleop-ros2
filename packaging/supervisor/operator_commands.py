"""Build the operator start commands after the robot reports ready.

``operator_plan`` does not spawn processes and does not load a console.
TURN writes ``packaging/run/ice-op.yaml`` from a template. ``bind_ip`` is
this PC's default-route IPv4, injected by tests. A missing address or any
address that starts with ``100.`` is refused. The file is a runtime copy.
This module does not print it.

An empty inventory ``camera_page`` is an error. No camera URL is invented.
``TELEOP_SUPERVISOR_DRY_RUN=1`` is handled by ``decide_operator``: no
commands and no console URL, and the yaml is not written.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.operator_app.config import local_default_nic
from packaging.robot_app.inventory import interface_ipv4
from packaging.supervisor.robot_commands import (
    Command,
    PlanError,
    _replace_bind_ip,
    _write_private,
)

_NO_IPV4 = "this PC has no IPv4 for bind_ip"
_NO_CAMERA = "camera page is not set"
_NOT_READY = "robot is not ready"
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
CONSOLE_ORIGIN = "http://127.0.0.1:8090/"

_STOP_PID = (
    "import os, signal, sys\n"
    "path = sys.argv[1]\n"
    "try:\n"
    "    pid = int(open(path, encoding='utf-8').read().strip())\n"
    "except (OSError, ValueError):\n"
    "    raise SystemExit(0)\n"
    "try:\n"
    "    os.kill(pid, signal.SIGTERM)\n"
    "except OSError:\n"
    "    pass\n"
    "try:\n"
    "    os.remove(path)\n"
    "except OSError:\n"
    "    pass\n"
)


@dataclass(frozen=True)
class OperatorPlan:
    """Commands to spawn, then the console URL the window may load."""

    commands: tuple[Command, ...]
    console_url: str


@dataclass(frozen=True)
class OperatorDecision:
    """``commands`` is None when the caller must not spawn or load a page."""

    commands: tuple[Command, ...] | None
    console_url: str
    detail: str


def dry_run_enabled() -> bool:
    return os.environ.get("TELEOP_SUPERVISOR_DRY_RUN") == "1"


def console_url(camera_page: str) -> str:
    """Same URL ``start_operator_mlink.sh --remote-laptop`` prints."""
    return f"{CONSOLE_ORIGIN}?cam={camera_page}"


def camera_page_of(inventory: Mapping[str, Any] | None) -> str:
    """Inventory camera page. Empty, blank, or unsafe text is an error."""
    if not isinstance(inventory, Mapping):
        raise PlanError(_NO_CAMERA)
    page = inventory.get("camera_page")
    if not isinstance(page, str):
        raise PlanError(_NO_CAMERA)
    text = page.strip()
    if text == "" or text.startswith("-") or any(ch.isspace() for ch in text):
        raise PlanError(_NO_CAMERA)
    return text


def default_route_ipv4(
    route_text: str | None = None,
    ipv4_of: Callable[[str], str] | None = None,
) -> str:
    """IPv4 of this PC's default-route NIC, or ``\"\"`` when it has none.

    ``ipv4_of`` injects the address in tests. The live lookup reads the
    interface address and does not run a process.
    """
    nic = local_default_nic(route_text)
    if not nic:
        return ""
    lookup = interface_ipv4 if ipv4_of is None else ipv4_of
    try:
        found = lookup(nic)
    except OSError:
        return ""
    if not isinstance(found, str):
        return ""
    return found.strip()


def _script(root: Path, *parts: str) -> str:
    return str(root.joinpath(*parts))


def _checked_bind(bind_ip: str | None) -> str:
    raw = default_route_ipv4() if bind_ip is None else bind_ip
    if not isinstance(raw, str):
        raise PlanError(_NO_IPV4)
    text = raw.strip()
    if not _IPV4.match(text):
        raise PlanError(_NO_IPV4)
    octets = [int(part) for part in text.split(".")]
    if any(part > 255 for part in octets):
        raise PlanError(_NO_IPV4)
    if text.startswith("100."):
        raise PlanError(f"refusing bind_ip {text}")
    return text


def _ice_text(root: Path, template: str | None) -> str:
    if template is not None:
        return template
    source = root / "turn" / "config" / "local_op.yaml"
    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError("missing turn/config/local_op.yaml") from exc


def operator_plan(
    config: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    repo_root: str | Path,
    phase: str,
    bind_ip: str | None = None,
    template: str | None = None,
) -> OperatorPlan:
    """mlink-op, then the gripper-only operator script.

    ``phase`` must be ``ready``. Otherwise no command is built and no yaml
    is written. ``template`` is the TURN yaml text. When it is omitted the
    live caller reads ``turn/config/local_op.yaml``. Tests pass a fixture
    and ``bind_ip``.
    """
    if phase != "ready":
        raise PlanError(_NOT_READY)
    if not isinstance(config, Mapping):
        raise PlanError("no config")
    link = config.get("link")
    if link not in {"tailscale", "turn"}:
        raise PlanError("link must be tailscale or turn")
    page = camera_page_of(inventory)
    root = Path(repo_root)
    daemon = _script(root, "mlink-transport", "scripts", "start_daemon.sh")
    operator_script = _script(
        root, "teleoperation-prototype", "scripts", "start_operator_mlink.sh"
    )
    if link == "tailscale":
        daemon_command = Command(
            argv=(daemon, "op", "--remote-laptop"),
            label="mlink-op",
        )
    else:
        ipv4 = _checked_bind(bind_ip)
        text = _replace_bind_ip(_ice_text(root, template), ipv4)
        dest = root / "packaging" / "run" / "ice-op.yaml"
        _write_private(dest, text)
        daemon_command = Command(
            argv=(daemon, "op", "--ice", "--ice-config", str(dest)),
            label="mlink-op",
        )
    operator_command = Command(
        argv=(operator_script, "--remote-laptop"),
        env=(
            ("REMOTE_LAPTOP_CAM", page),
            ("TELEOP_GRIPPER_ONLY", "1"),
        ),
        label="operator",
    )
    return OperatorPlan(
        (daemon_command, operator_command),
        console_url(page),
    )


def decide_operator(
    config: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    repo_root: str | Path,
    phase: str,
    *,
    dry_run: bool,
    bind_ip: str | None = None,
    template: str | None = None,
) -> OperatorDecision:
    """Commands and console URL only after ``ready``, and never on dry-run."""
    if phase != "ready" or dry_run:
        return OperatorDecision(None, "", "")
    try:
        plan = operator_plan(
            config,
            inventory,
            repo_root,
            phase,
            bind_ip=bind_ip,
            template=template,
        )
    except PlanError as exc:
        return OperatorDecision(None, "", exc.detail)
    return OperatorDecision(plan.commands, plan.console_url, "")


def operator_stop_plan(repo_root: str | Path) -> list[Command]:
    """Operator compose down, then the mlink-op process. Nothing is spawned."""
    root = Path(repo_root)
    project = root / "teleoperation-prototype"
    compose = project / "compose.operator-mlink.yaml"
    pidfile = root / "packaging" / "run" / "mlink-op.pid"
    return [
        Command(
            argv=(
                "docker",
                "compose",
                "--project-directory",
                str(project),
                "-f",
                str(compose),
                "down",
                "--remove-orphans",
            ),
            label="operator-compose",
        ),
        Command(
            argv=(sys.executable, "-c", _STOP_PID, str(pidfile)),
            label="mlink-op",
        ),
    ]


def stop_operator_local(repo_root: str | Path, *, live: bool = False) -> None:
    """Run the local operator stop when a live start has spawned it.

    Dry-run and a window that never started return before any process is
    created. The exec helper is imported only on the live path.
    """
    if not live or dry_run_enabled():
        return
    from packaging.supervisor import operator_exec

    for command in operator_stop_plan(repo_root):
        operator_exec.execute_command(command, repo_root)
