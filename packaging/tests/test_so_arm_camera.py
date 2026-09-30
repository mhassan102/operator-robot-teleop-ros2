"""SO-ARM camera launcher arguments.

Parse-only runs resolve the capture node and exit. They do not start
MediaMTX, open a video device, or move the gripper.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_START = _REPO / "video" / "so-arm" / "start.sh"
_GST = _REPO / "video" / "so-arm" / "gst-publish.sh"
_ORIN_GST = _REPO / "video" / "gst-publish.sh"
_ORIN_START = _REPO / "video" / "start.sh"


def _sysfs(tmp: Path, names: dict[str, str]) -> Path:
    root = tmp / "v4l"
    for node, name in names.items():
        directory = root / node
        directory.mkdir(parents=True)
        (directory / "name").write_text(name + "\n", encoding="utf-8")
    return root


def _run(
    tmp: Path,
    names: dict[str, str],
    device: str | None,
) -> subprocess.CompletedProcess[str]:
    sysfs = _sysfs(tmp, names)
    sentinel = tmp / "mediamtx-started"
    fake = tmp / "mediamtx"
    fake.write_text(
        "#!/bin/sh\nprintf started > "
        + shlex.quote(str(sentinel))
        + "\nexit 1\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["SO_ARM_CAMERA_PARSE_ONLY"] = "1"
    env["SO_ARM_CAMERA_FORBID_START"] = "1"
    env["SO_ARM_VIDEO_SYSFS"] = str(sysfs)
    env["SO_ARM_MEDIAMTX_BIN"] = str(fake)
    env.pop("DEVICE", None)
    if device is not None:
        env["DEVICE"] = device
    result = subprocess.run(
        [str(_START)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert not sentinel.exists()
    assert not (_REPO / "video" / "so-arm" / "run" / "mediamtx.pid").exists()
    return result


def test_default_device_requires_usb_cam(tmp_path: Path) -> None:
    result = _run(tmp_path, {"video2": "USB2.0_CAM1"}, device=None)
    assert result.returncode == 0, result.stderr
    assert "SO-ARM camera /dev/video2 name USB2.0_CAM1" in result.stdout


def test_explicit_default_device_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, {"video2": "USB2.0_CAM1"}, device="/dev/video2")
    assert result.returncode == 0, result.stderr
    assert "/dev/video2" in result.stdout
    assert "USB2.0_CAM1" in result.stdout


def test_default_device_refuses_other_camera(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {"video2": "Integrated Camera"},
        device="/dev/video2",
    )
    assert result.returncode == 2
    assert "not USB2.0_CAM1" in result.stderr
    assert "integrated camera is /dev/video0" in result.stderr
    assert "Integrated Camera" in result.stderr


def test_other_device_prints_its_name(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {"video0": "Integrated Camera"},
        device="/dev/video0",
    )
    assert result.returncode == 0, result.stderr
    assert "SO-ARM camera /dev/video0 name Integrated Camera" in result.stdout


def test_other_device_refuses_metadata(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {"video3": "USB2.0_CAM1 Metadata"},
        device="/dev/video3",
    )
    assert result.returncode == 2
    assert "metadata" in result.stderr


def test_other_device_must_exist(tmp_path: Path) -> None:
    result = _run(tmp_path, {}, device="/dev/video9")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_encoder_is_software_x264() -> None:
    text = _GST.read_text(encoding="utf-8")
    assert "x264enc" in text
    assert "nvv4l2h264enc" not in text
    assert "127.0.0.1" in text
    assert "5004" in text
    orin = _ORIN_GST.read_text(encoding="utf-8")
    orin_start = _ORIN_START.read_text(encoding="utf-8")
    assert "nvv4l2h264enc" in orin
    assert "nvv4l2h264enc" in orin_start


def test_mediamtx_config_shape() -> None:
    text = _START.read_text(encoding="utf-8")
    assert "webrtcAddress: :8889" in text
    assert "webrtcIPsFromInterfacesList: [tailscale0]" in text
    assert "udp+rtp://127.0.0.1:5004" in text
    assert "tailscale ip -4" in text
    parse_at = text.index('SO_ARM_CAMERA_PARSE_ONLY:-}" == "1"')
    publish_at = text.index("gst-publish.sh")
    assert parse_at < publish_at
