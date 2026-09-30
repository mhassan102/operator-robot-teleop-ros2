"""Spawn a robot plan. Not used when ``TELEOP_SUPERVISOR_DRY_RUN=1``.

The dry-run guard runs before any process is created. Callers that are
planning only should not import this module.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from packaging.supervisor.robot_commands import Command


def _refuse_dry_run() -> None:
    if os.environ.get("TELEOP_SUPERVISOR_DRY_RUN") == "1":
        raise RuntimeError("TELEOP_SUPERVISOR_DRY_RUN=1 must not exec")


def _is_edge_daemon(command: Command) -> bool:
    return bool(command.argv) and command.argv[0].endswith("start_daemon.sh")


def _spawn_edge(argv: list[str], env: dict[str, str], repo_root: Path) -> None:
    proc = subprocess.Popen(argv, env=env)
    pidfile = repo_root / "packaging" / "run" / "mlink-edge.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{proc.pid}\n", encoding="utf-8")
    os.chmod(pidfile, 0o600)
    time.sleep(0.2)
    code = proc.poll()
    if code is not None:
        raise RuntimeError(f"mlink-edge exited {code}")


def execute_command(command: Command, repo_root: str | Path) -> None:
    """Run one planned command. Refuses outright when dry-run is set."""
    _refuse_dry_run()
    root = Path(repo_root)
    env = os.environ.copy()
    for key, value in command.env:
        env[key] = value
    argv = list(command.argv)
    if _is_edge_daemon(command):
        _spawn_edge(argv, env, root)
        return
    result = subprocess.run(argv, env=env, check=False)
    if result.returncode != 0:
        label = command.label or "command"
        raise RuntimeError(f"{label} exited {result.returncode}")
