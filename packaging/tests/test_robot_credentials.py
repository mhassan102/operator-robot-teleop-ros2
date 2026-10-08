"""Robot identity file.

Nothing here opens a registry socket, starts mlink, Docker, the camera,
or the arm, or sends a gripper command.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from packaging.robot_app.credentials import load_or_create

_SENTENCE = "Robot credentials file cannot be read.\n"
_GOOD_ID = "012345678"
_STORED_TOKEN = "already-stored-token"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _rejected(path: Path, capsys: Any) -> None:
    with pytest.raises(SystemExit) as caught:
        load_or_create(path)
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _SENTENCE


def _snapshot(path: Path) -> tuple[bytes, int, int]:
    info = path.stat()
    return path.read_bytes(), info.st_ino, info.st_mtime_ns


def test_missing_file_mints_a_private_identity(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    calls = {"id": 0, "nbytes": 0}

    def fake_id() -> str:
        calls["id"] += 1
        return _GOOD_ID

    def fake_hex(nbytes: int) -> str:
        calls["nbytes"] = nbytes
        return "ab" * nbytes

    monkeypatch.setattr(
        "packaging.robot_app.credentials.new_id", fake_id
    )
    monkeypatch.setattr(
        "packaging.robot_app.credentials.secrets.token_hex", fake_hex
    )
    parent = tmp_path / "fresh"
    parent_mode = 0o755
    parent.mkdir()
    os.chmod(parent, parent_mode)
    path = parent / "nested" / "robot.json"
    previous = os.umask(0o777)
    try:
        robot_id, token = load_or_create(path)
    finally:
        os.umask(previous)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert token not in captured.out
    assert token not in captured.err
    assert robot_id == _GOOD_ID
    assert calls["id"] == 1
    assert calls["nbytes"] == 32
    assert token == "ab" * 32
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert _mode(parent) == parent_mode
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body == {
        "robot_id": _GOOD_ID,
        "token": token,
        "mode": "manual",
        "location": "",
    }
    assert "password" not in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    again_id, again_token = load_or_create(path)
    again = capsys.readouterr()
    assert (again_id, again_token) == (robot_id, token)
    assert again.out == ""
    assert again.err == ""
    assert token not in again.out
    assert token not in again.err
    assert path.read_bytes() == before
    assert calls["id"] == 1


def test_good_file_returns_the_same_pair_and_does_not_print_the_token(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    def fail_mint() -> str:
        raise AssertionError("minted over a good file")

    monkeypatch.setattr(
        "packaging.robot_app.credentials.new_id", fail_mint
    )
    path = tmp_path / "robot.json"
    path.write_text(
        json.dumps(
            {
                "robot_id": "123456789",
                "token": _STORED_TOKEN,
                "mode": "autonomous",
                "location": "dubai-lab",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o644)
    before = _snapshot(path)
    robot_id, token = load_or_create(path)
    captured = capsys.readouterr()
    assert (robot_id, token) == ("123456789", _STORED_TOKEN)
    assert captured.out == ""
    assert captured.err == ""
    assert _STORED_TOKEN not in captured.out
    assert _STORED_TOKEN not in captured.err
    assert _snapshot(path) == before
    assert _mode(path) == 0o644


def test_directory_exits_and_is_not_replaced(
    tmp_path: Path, capsys: Any
) -> None:
    directory = tmp_path / "credentials"
    directory.mkdir()
    marker = directory / "keep.txt"
    marker.write_text("stay", encoding="utf-8")
    before = sorted(path.name for path in directory.iterdir())
    inode = directory.stat().st_ino
    _rejected(directory, capsys)
    assert directory.is_dir()
    assert directory.stat().st_ino == inode
    assert sorted(path.name for path in directory.iterdir()) == before
    assert marker.read_text(encoding="utf-8") == "stay"


def test_bad_json_exits_and_is_not_replaced(
    tmp_path: Path, capsys: Any
) -> None:
    path = tmp_path / "robot.json"
    secret = "token-inside-bad-json"
    path.write_text('{"token": "%s"' % secret, encoding="utf-8")
    before = _snapshot(path)
    _rejected(path, capsys)
    assert _snapshot(path) == before


def test_bad_id_exits_and_is_not_replaced(
    tmp_path: Path, capsys: Any
) -> None:
    secret = "token-must-stay-in-the-file"
    samples: tuple[object, ...] = (
        "12345678",
        "1234567890",
        "12345678A",
        "abcdefghi",
        123456789,
        None,
    )
    for index, robot_id in enumerate(samples):
        path = tmp_path / f"bad-id-{index}.json"
        path.write_text(
            json.dumps({"robot_id": robot_id, "token": secret}),
            encoding="utf-8",
        )
        before = _snapshot(path)
        _rejected(path, capsys)
        assert _snapshot(path) == before


def test_empty_token_exits_and_is_not_replaced(
    tmp_path: Path, capsys: Any
) -> None:
    samples: tuple[dict[str, object], ...] = (
        {"robot_id": "123456789", "token": ""},
        {"robot_id": "123456789"},
        {"robot_id": "123456789", "token": None},
        {"robot_id": "123456789", "token": 0},
    )
    for index, body in enumerate(samples):
        path = tmp_path / f"empty-token-{index}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        before = _snapshot(path)
        _rejected(path, capsys)
        assert _snapshot(path) == before


def test_unreadable_file_exits_and_is_not_replaced(
    tmp_path: Path, capsys: Any
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read a mode 000 file")
    path = tmp_path / "robot.json"
    secret = "hidden-token"
    path.write_text(
        json.dumps({"robot_id": "123456789", "token": secret}),
        encoding="utf-8",
    )
    before = path.read_bytes()
    inode = path.stat().st_ino
    os.chmod(path, 0)
    try:
        _rejected(path, capsys)
        assert path.stat().st_ino == inode
        assert stat.S_IMODE(path.stat().st_mode) == 0
    finally:
        os.chmod(path, 0o600)
    assert path.read_bytes() == before
