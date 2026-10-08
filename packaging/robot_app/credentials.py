"""9-digit robot ID and the on-disk identity file.

The person at the SSH session types the password. This module does not
generate a password and does not store one. ``load_or_create`` stores
the id and the device token. It does not print the token.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import NoReturn

ID_LENGTH = 9
_CANNOT_READ = "Robot credentials file cannot be read."


def new_id() -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(ID_LENGTH))


def load_or_create(path: str | os.PathLike[str]) -> tuple[str, str]:
    """Return ``(robot_id, token)`` from ``path``.

    A missing path is minted with ``new_id`` and ``secrets.token_hex(32)``
    at mode ``0600``. A missing parent directory is created at mode
    ``0700``. An existing good file is returned unchanged and is not
    printed. A directory, an unreadable path, bad JSON, a bad id, or an
    empty token prints one sentence and exits non-zero. That path is
    left in place.
    """
    target = Path(path)
    if _exists(target):
        return _load(target)
    _make_parent(target)
    return _mint(target)


def _exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        _reject()
    return True


def _load(path: Path) -> tuple[str, str]:
    if path.is_dir():
        _reject()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _reject()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _reject()
    pair = _pair(data)
    if pair is None:
        _reject()
    return pair


def _pair(data: object) -> tuple[str, str] | None:
    if not isinstance(data, dict):
        return None
    robot_id = data.get("robot_id")
    token = data.get("token")
    if (
        not isinstance(robot_id, str)
        or len(robot_id) != ID_LENGTH
        or not robot_id.isdigit()
    ):
        return None
    if not isinstance(token, str) or token == "":
        return None
    return robot_id, token


def _make_parent(path: Path) -> None:
    parent = path.parent
    if parent == Path(""):
        return
    missing: list[Path] = []
    cursor = parent
    while True:
        if _exists(cursor):
            if not cursor.is_dir():
                _reject()
            break
        missing.append(cursor)
        above = cursor.parent
        if above == cursor:
            break
        cursor = above
    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            if not directory.is_dir():
                _reject()
            continue
        except OSError:
            _reject()
        try:
            os.chmod(directory, 0o700)
        except OSError:
            _reject()


def _mint(path: Path) -> tuple[str, str]:
    robot_id = new_id()
    token = secrets.token_hex(32)
    text = json.dumps(
        {
            "robot_id": robot_id,
            "token": token,
            "mode": "manual",
            "location": "",
        }
    )
    payload = (text + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(os.fspath(path), flags, 0o600)
    except FileExistsError:
        return _load(path)
    except OSError:
        _reject()
    try:
        _write_all(fd, payload)
    except OSError:
        os.close(fd)
        _discard_new(path)
        _reject()
    try:
        os.close(fd)
    except OSError:
        _discard_new(path)
        _reject()
    try:
        os.chmod(path, 0o600)
    except OSError:
        _reject()
    return robot_id, token


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _discard_new(path: Path) -> None:
    # Only a file this call created. An existing bad file stays.
    try:
        os.unlink(path)
    except OSError:
        pass


def _reject() -> NoReturn:
    print(_CANNOT_READ, file=sys.stderr, flush=True)
    raise SystemExit(1) from None
