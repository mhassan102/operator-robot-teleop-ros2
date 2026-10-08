"""Operator accounts and robot-row removal for the fleet registry.

``python3 -m packaging.registry.admin``. Passwords come from ``getpass``
and are never printed. The database is ``TELEOP_REGISTRY_DB`` or
``~/.local/share/teleop/registry.sqlite``.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

from packaging.registry.session import hash_password
from packaging.registry.store import (
    FLEET_ITERATIONS,
    add_operator,
    clear_robot,
    connect,
    default_db_path,
    list_operators,
    set_operator_disabled,
    set_operator_password,
    utc_now,
    valid_robot_id,
    valid_username,
)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m packaging.registry.admin"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add-operator")
    add.add_argument("username")
    password = sub.add_parser("set-password")
    password.add_argument("username")
    disable = sub.add_parser("disable-operator")
    disable.add_argument("username")
    enable = sub.add_parser("enable-operator")
    enable.add_argument("username")
    sub.add_parser("list-operators")
    clear = sub.add_parser("clear-robot")
    clear.add_argument("robot_id")
    args = parser.parse_args(argv)
    try:
        conn = connect(default_db_path())
    except (OSError, sqlite3.Error):
        print("database error")
        return 1
    try:
        if args.command == "add-operator":
            return _add_operator(conn, args.username)
        if args.command == "set-password":
            return _set_password(conn, args.username)
        if args.command == "disable-operator":
            return _set_disabled(conn, args.username, 1)
        if args.command == "enable-operator":
            return _set_disabled(conn, args.username, 0)
        if args.command == "list-operators":
            return _list_operators(conn)
        if args.command == "clear-robot":
            return _clear_robot(conn, args.robot_id)
    finally:
        conn.close()
    print("database error")
    return 1


def _add_operator(conn: sqlite3.Connection, username: object) -> int:
    if not valid_username(username):
        print("bad username")
        return 1
    existing = list_operators(conn)
    if any(name == username for name, _disabled in existing):
        print("operator exists")
        return 1
    secret = _prompt_password()
    if secret is None:
        return 1
    digest = hash_password(secret, FLEET_ITERATIONS)
    if not add_operator(conn, username, digest, FLEET_ITERATIONS, utc_now()):
        print("operator exists")
        return 1
    print(username)
    return 0


def _set_password(conn: sqlite3.Connection, username: object) -> int:
    if not valid_username(username):
        print("bad username")
        return 1
    known = {name for name, _disabled in list_operators(conn)}
    if username not in known:
        print("unknown operator")
        return 1
    secret = _prompt_password()
    if secret is None:
        return 1
    digest = hash_password(secret, FLEET_ITERATIONS)
    if not set_operator_password(conn, username, digest, FLEET_ITERATIONS):
        print("unknown operator")
        return 1
    print(username)
    return 0


def _set_disabled(conn: sqlite3.Connection, username: object, disabled: int) -> int:
    if not valid_username(username):
        print("bad username")
        return 1
    if not set_operator_disabled(conn, username, disabled):
        print("unknown operator")
        return 1
    print(username)
    return 0


def _list_operators(conn: sqlite3.Connection) -> int:
    for username, disabled in list_operators(conn):
        print("%s disabled=%s" % (username, disabled))
    return 0


def _clear_robot(conn: sqlite3.Connection, robot_id: object) -> int:
    if not valid_robot_id(robot_id):
        print("bad id")
        return 1
    if not clear_robot(conn, robot_id):
        print("unknown robot")
        return 1
    print(robot_id)
    return 0


def _prompt_password() -> str | None:
    try:
        secret = getpass.getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        print("no password")
        return None
    if not isinstance(secret, str) or len(secret) < 8:
        print("password too short")
        return None
    return secret


if __name__ == "__main__":
    sys.exit(main())
