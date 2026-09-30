"""Stop commands for the robot stack.

Order is the robot container, then the mlink-edge process, then the
SO-ARM camera. Nothing in this module spawns those commands.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.supervisor.robot_commands import Command, status_message

_STOP_EDGE = (
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
class StopDecision:
    """``commands`` is None when the caller must not spawn."""

    status: dict[str, Any] | None
    commands: tuple[Command, ...] | None


def robot_stop_plan(repo_root: str | Path) -> list[Command]:
    """Container, then mlink-edge, then the SO-ARM camera launcher."""
    root = Path(repo_root)
    pidfile = root / "packaging" / "run" / "mlink-edge.pid"
    return [
        Command(
            argv=(
                str(
                    root
                    / "teleoperation-prototype"
                    / "scripts"
                    / "stop_mlink.sh"
                ),
            ),
            label="robot-container",
        ),
        Command(
            argv=(sys.executable, "-c", _STOP_EDGE, str(pidfile)),
            label="mlink-edge",
        ),
        Command(
            argv=(str(root / "video" / "so-arm" / "stop.sh"),),
            label="camera",
        ),
    ]


def decide_stop(repo_root: str | Path, *, dry_run: bool) -> StopDecision:
    if dry_run:
        return StopDecision(status_message("stopped", "dry-run"), None)
    return StopDecision(None, tuple(robot_stop_plan(repo_root)))
