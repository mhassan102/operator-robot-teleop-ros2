"""Build the operator commands. Nothing in this module is spawned.

Launch brings the operator container up in UI-only mode. Start, after the
robot reports ready, runs mlink-op only. The container is already up, so
Start does not call ``start_operator_mlink.sh``. Stop signals that mlink
process and leaves the container up. Quit is the compose down that follows
that stop.

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
CONSOLE_ORIGIN = "http://127.0.0.1:8090"
_IFACE2 = (
    "second interface is not used in this version; set Interface 2 to None"
)

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
    """Drive page. The container is already serving it."""
    return f"{CONSOLE_ORIGIN}/operate?cam={camera_page}"


def operate_path(camera_page: str) -> str:
    """Path the browser loads after mlink-op is up."""
    return f"/operate?cam={camera_page}"


def start_refused(
    config: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
) -> str:
    """Why Start must not send, or ``\"\"`` when the choice may be sent."""
    if not isinstance(config, Mapping):
        return "no config"
    if config.get("iface2") is not None:
        return _IFACE2
    try:
        camera_page_of(inventory)
    except PlanError as exc:
        return exc.detail
    return ""


def packaging_mounts(repo_root: str | Path) -> tuple[str, str]:
    """Host paths mounted read-only at ``/opt/teleop/packaging`` and ``vendor``.

    A repo checkout vendors websockets under ``packaging/debian/vendor``.
    An installed tree vendors them at ``/opt/teleop/vendor``.
    """
    root = Path(repo_root)
    packaging_dir = root / "packaging"
    bundled = packaging_dir / "debian" / "vendor"
    if (bundled / "websockets" / "__init__.py").is_file():
        vendor = bundled
    else:
        vendor = root / "vendor"
    return str(packaging_dir), str(vendor)


def ui_container_plan(
    repo_root: str | Path,
    registry_url: str,
    helper_url: str = "http://127.0.0.1:8091",
) -> Command:
    """Compose up for the UI-only container. No mlink and no operator script."""
    root = Path(repo_root)
    project = root / "teleoperation-prototype"
    base = project / "compose.operator-mlink.yaml"
    overlay = project / "compose.operator-ui.yaml"
    packaging_dir, vendor = packaging_mounts(root)
    return Command(
        argv=(
            "docker",
            "compose",
            "--project-directory",
            str(project),
            "-f",
            str(base),
            "-f",
            str(overlay),
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "operator",
        ),
        env=(
            ("TELEOP_UI_ONLY", "1"),
            ("TELEOP_REGISTRY", registry_url),
            ("TELEOP_HELPER_URL", helper_url),
            ("TELEOP_GRIPPER_ONLY", "1"),
            ("TELEOP_PACKAGING_MOUNT", packaging_dir),
            ("TELEOP_VENDOR_MOUNT", vendor),
        ),
        label="operator-ui",
    )


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
    """mlink-op only. The operator container is already up.

    ``phase`` must be ``ready``. Otherwise no command is built and no yaml
    is written. ``template`` is the TURN yaml text. When it is omitted the
    live caller reads ``turn/config/local_op.yaml``. Tests pass a fixture
    and ``bind_ip``. This does not call ``start_operator_mlink.sh``.
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
    return OperatorPlan((daemon_command,), console_url(page))


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
    """Stop mlink-op only. The operator container stays up. Nothing is spawned."""
    root = Path(repo_root)
    pidfile = root / "packaging" / "run" / "mlink-op.pid"
    return [
        Command(
            argv=(sys.executable, "-c", _STOP_PID, str(pidfile)),
            label="mlink-op",
        ),
    ]


def operator_quit_plan(repo_root: str | Path) -> list[Command]:
    """Compose down for the operator container. Nothing is spawned.

    Quit runs this after mlink-op is already stopped. Stop does not.
    """
    root = Path(repo_root)
    project = root / "teleoperation-prototype"
    base = project / "compose.operator-mlink.yaml"
    overlay = project / "compose.operator-ui.yaml"
    return [
        Command(
            argv=(
                "docker",
                "compose",
                "--project-directory",
                str(project),
                "-f",
                str(base),
                "-f",
                str(overlay),
                "down",
                "--remove-orphans",
            ),
            label="operator-compose",
        ),
    ]


def stop_operator_local(repo_root: str | Path, *, live: bool = False) -> None:
    """Stop mlink-op when a live Start has spawned it.

    The container stays up. Dry-run and a session that never started mlink
    return before any process is created. The exec helper is imported only
    on the live path.
    """
    if not live or dry_run_enabled():
        return
    from packaging.supervisor import operator_exec

    for command in operator_stop_plan(repo_root):
        operator_exec.execute_command(command, repo_root)
