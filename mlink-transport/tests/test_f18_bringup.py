"""F18 Stage 4 bring-up glue: YAML still control-only; start_daemon defaults."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from proto.config import MlinkConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "scripts" / "start_daemon.sh"


def _parse(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MLINK_PARSE_ONLY"] = "1"
    return subprocess.run(
        [str(START), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_allow_tailscale_default_remains_reject() -> None:
    cfg = MlinkConfig(session_id=1, paths=())
    assert cfg.allow_tailscale is False
    orin = load_config(ROOT / "config" / "lab-op.yaml")
    assert orin.allow_tailscale is False


def test_remote_laptop_yaml_stays_control_only() -> None:
    op = load_config(ROOT / "config" / "lab-op-remote-laptop.yaml")
    edge = load_config(ROOT / "config" / "lab-edge-remote-laptop.yaml")
    assert op.listen_media == op.send_media == ""
    assert edge.listen_media == edge.send_media == ""
    assert op.listen_app == "127.0.0.1:5501"
    assert op.send_app == "127.0.0.1:5502"
    assert edge.listen_app == "127.0.0.1:5503"
    assert edge.send_app == "127.0.0.1:5504"
    assert op.allow_tailscale is True
    assert edge.allow_tailscale is True


def test_start_daemon_default_is_orin_yaml() -> None:
    op = _parse("op")
    assert op.returncode == 0, op.stderr
    assert "config=config/lab-op.yaml" in op.stdout
    assert "path=orin-eth-wifi" in op.stdout
    assert "reflect=no" in op.stdout
    assert "control=no" in op.stdout
    assert "remote-laptop" not in op.stdout

    edge = _parse("edge")
    assert edge.returncode == 0, edge.stderr
    assert "config=config/lab-edge.yaml" in edge.stdout
    assert "reflect=no" in edge.stdout


def test_start_daemon_remote_laptop_yaml() -> None:
    op = _parse("op", "--remote-laptop")
    assert op.returncode == 0, op.stderr
    assert "config=config/lab-op-remote-laptop.yaml" in op.stdout
    assert "path=tailscale0" in op.stdout
    assert "reflect=no" in op.stdout
    assert "control=no" in op.stdout

    edge = _parse("edge", "--remote-laptop")
    assert edge.returncode == 0, edge.stderr
    assert "config=config/lab-edge-remote-laptop.yaml" in edge.stdout
    assert "reflect=no" in edge.stdout


def test_start_daemon_rejects_reflect() -> None:
    result = _parse("edge", "--reflect")
    assert result.returncode == 2
    assert "will not drive the robot" in result.stderr


def test_start_daemon_rejects_control() -> None:
    result = _parse("op", "--control")
    assert result.returncode == 2
    assert "mlink-ping" in result.stderr


def test_start_daemon_requires_role() -> None:
    result = _parse("--remote-laptop")
    assert result.returncode == 2
    assert "Usage:" in result.stderr


def test_start_daemon_script_is_executable() -> None:
    assert START.is_file()
    assert os.access(START, os.X_OK)
