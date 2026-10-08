"""Sqlite file for operator accounts and robot token rows.

Importing this module does not create a file. ``connect`` and the admin
CLI open the database. Plaintext passwords and device tokens are not
stored here.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional

FLEET_ITERATIONS = 200_000
PENDING_CAP = 500
STALE_AFTER_S = 30.0
STALE_STOP_WAIT_S = 30.0
MAINTAIN_INTERVAL_S = 1.0
PENDING_KEEP_S = 7 * 24 * 60 * 60
LAST_SEEN_INTERVAL_S = 30.0
FLEET_COALESCE_S = 0.05

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,31}$")
_FIELD_MAX = 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operators (
    username TEXT PRIMARY KEY,
    secret_hash BLOB NOT NULL,
    iterations INTEGER NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS robots (
    robot_id TEXT PRIMARY KEY,
    secret_hash BLOB NOT NULL,
    iterations INTEGER NOT NULL,
    hostname TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'manual',
    approval TEXT NOT NULL DEFAULT 'pending',
    phase TEXT NOT NULL DEFAULT 'idle',
    last_seen TEXT,
    created_at TEXT NOT NULL
);
"""


class RobotRow(NamedTuple):
    robot_id: str
    secret_hash: bytes
    iterations: int
    hostname: str
    location: str
    mode: str
    approval: str
    phase: str
    last_seen: Optional[str]
    created_at: str


def default_db_path() -> str:
    """``TELEOP_REGISTRY_DB`` or ``~/.local/share/teleop/registry.sqlite``."""
    chosen = os.environ.get("TELEOP_REGISTRY_DB", "").strip()
    if chosen:
        return chosen
    return str(Path.home() / ".local" / "share" / "teleop" / "registry.sqlite")


def valid_username(value: object) -> bool:
    return isinstance(value, str) and _USERNAME.fullmatch(value) is not None


def valid_robot_id(value: object) -> bool:
    return isinstance(value, str) and len(value) == 9 and value.isdigit()


def field_text(value: object) -> bool:
    """Hostname or location: at most 64 characters, no newline and no NUL."""
    if not isinstance(value, str) or len(value) > _FIELD_MAX:
        return False
    return "\n" not in value and "\r" not in value and "\x00" not in value


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_seconds(
    last_seen: object, now: Optional[datetime] = None
) -> Optional[float]:
    """Seconds since ``last_seen``. Missing or unparseable is not stale."""
    if not isinstance(last_seen, str) or last_seen == "":
        return None
    try:
        stamp = datetime.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - stamp).total_seconds()


def classify(
    approval: str,
    online: bool,
    held: bool,
    mode: str,
    last_seen: object,
    now: Optional[datetime] = None,
) -> str:
    """One class. Pending is outside the four dashboard counts."""
    if approval != "approved":
        return "pending"
    if not online:
        return "offline"
    if held:
        return "in_teleop"
    age = age_seconds(last_seen, now)
    if age is not None and age >= STALE_AFTER_S:
        return "offline"
    if mode == "autonomous":
        return "autonomous"
    return "needs_manual"


def status_label(
    approval: str,
    online: bool,
    held: bool,
    phase: str,
    start_in_progress: bool,
    last_seen: object,
    now: Optional[datetime] = None,
) -> str:
    if approval != "approved":
        return "pending"
    age = age_seconds(last_seen, now)
    stale = age is not None and age >= STALE_AFTER_S
    if not online or (not held and stale):
        return "offline"
    if held and stale:
        return "stale"
    if not held:
        return "online"
    if phase == "error":
        return "error"
    if phase == "ready":
        return "running"
    if start_in_progress or phase == "starting":
        return "starting"
    return "attached"


def counts_from_classes(classes: list) -> dict:
    autonomous = classes.count("autonomous")
    needs_manual = classes.count("needs_manual")
    in_teleop = classes.count("in_teleop")
    offline = classes.count("offline")
    return {
        "registered": autonomous + needs_manual + in_teleop + offline,
        "autonomous": autonomous,
        "needs_manual": needs_manual,
        "in_teleop": in_teleop,
    }


def connect(path: str) -> sqlite3.Connection:
    """Open ``path``. ``:memory:`` skips WAL. Other paths are mode ``0600``."""
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
            _private_dir(parent)
        conn = sqlite3.connect(path, timeout=5.0)
        _harden_file(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _harden_file(path)
    else:
        conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    # A stored phase of ready must not come back as running.
    conn.execute("UPDATE robots SET phase='idle'")
    conn.commit()
    if path != ":memory:":
        _harden_file(path)
    return conn


def load_robots(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        """
        SELECT robot_id, secret_hash, iterations, hostname, location, mode,
               approval, phase, last_seen, created_at
        FROM robots
        ORDER BY robot_id
        """
    ).fetchall()
    conn.commit()
    loaded = []
    for row in rows:
        last_seen = row["last_seen"]
        loaded.append(
            RobotRow(
                robot_id=row["robot_id"],
                secret_hash=bytes(row["secret_hash"]),
                iterations=int(row["iterations"]),
                hostname=row["hostname"] or "",
                location=row["location"] or "",
                mode=row["mode"] or "manual",
                approval=row["approval"] or "pending",
                phase="idle",
                last_seen=last_seen if isinstance(last_seen, str) else None,
                created_at=row["created_at"] or "",
            )
        )
    return loaded


def insert_robot(
    conn: sqlite3.Connection,
    robot_id: str,
    secret_hash: bytes,
    iterations: int,
    hostname: str,
    location: str,
    mode: str,
    approval: str,
    phase: str,
    last_seen: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO robots (
            robot_id, secret_hash, iterations, hostname, location, mode,
            approval, phase, last_seen, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            robot_id,
            secret_hash,
            iterations,
            hostname,
            location,
            mode,
            approval,
            phase,
            last_seen,
            created_at,
        ),
    )
    conn.commit()
    _harden_conn(conn)


def update_robot_registration(
    conn: sqlite3.Connection,
    robot_id: str,
    hostname: str,
    location: str,
    mode: str,
    last_seen: str,
) -> None:
    """Hostname, mode, location, and last_seen. Approval and the hash stay."""
    conn.execute(
        """
        UPDATE robots
        SET hostname=?, location=?, mode=?, last_seen=?
        WHERE robot_id=?
        """,
        (hostname, location, mode, last_seen, robot_id),
    )
    conn.commit()
    _harden_conn(conn)


def update_robot_state(
    conn: sqlite3.Connection,
    robot_id: str,
    hostname: str,
    location: str,
    mode: str,
    phase: str,
    last_seen: str,
) -> None:
    conn.execute(
        """
        UPDATE robots
        SET hostname=?, location=?, mode=?, phase=?, last_seen=?
        WHERE robot_id=?
        """,
        (hostname, location, mode, phase, last_seen, robot_id),
    )
    conn.commit()
    _harden_conn(conn)


def update_robot_last_seen(
    conn: sqlite3.Connection, robot_id: str, last_seen: str
) -> None:
    conn.execute(
        "UPDATE robots SET last_seen=? WHERE robot_id=?",
        (last_seen, robot_id),
    )
    conn.commit()
    _harden_conn(conn)


def read_operator(
    conn: sqlite3.Connection, username: str
) -> Optional[tuple]:
    row = conn.execute(
        """
        SELECT secret_hash, iterations, disabled
        FROM operators WHERE username=?
        """,
        (username,),
    ).fetchone()
    if row is None:
        conn.commit()
        return None
    found = (bytes(row["secret_hash"]), int(row["iterations"]), int(row["disabled"]))
    conn.commit()
    return found


def add_operator(
    conn: sqlite3.Connection,
    username: str,
    secret_hash: bytes,
    iterations: int,
    created_at: str,
) -> bool:
    """Insert. Return False when the username is already there."""
    try:
        conn.execute(
            """
            INSERT INTO operators (
                username, secret_hash, iterations, disabled, created_at
            ) VALUES (?, ?, ?, 0, ?)
            """,
            (username, secret_hash, iterations, created_at),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    _harden_conn(conn)
    return True


def set_operator_password(
    conn: sqlite3.Connection,
    username: str,
    secret_hash: bytes,
    iterations: int,
) -> bool:
    cur = conn.execute(
        """
        UPDATE operators SET secret_hash=?, iterations=? WHERE username=?
        """,
        (secret_hash, iterations, username),
    )
    conn.commit()
    _harden_conn(conn)
    return cur.rowcount == 1


def set_operator_disabled(
    conn: sqlite3.Connection, username: str, disabled: int
) -> bool:
    cur = conn.execute(
        "UPDATE operators SET disabled=? WHERE username=?",
        (1 if disabled else 0, username),
    )
    conn.commit()
    _harden_conn(conn)
    return cur.rowcount == 1


def list_operators(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT username, disabled FROM operators ORDER BY username"
    ).fetchall()
    conn.commit()
    return [(row["username"], int(row["disabled"])) for row in rows]


def clear_robot(conn: sqlite3.Connection, robot_id: str) -> bool:
    cur = conn.execute("DELETE FROM robots WHERE robot_id=?", (robot_id,))
    conn.commit()
    _harden_conn(conn)
    return cur.rowcount == 1


def set_robot_approval(
    conn: sqlite3.Connection, robot_id: str, approval: str
) -> None:
    conn.execute(
        "UPDATE robots SET approval=? WHERE robot_id=?",
        (approval, robot_id),
    )
    conn.commit()
    _harden_conn(conn)


def update_robot_phase(conn: sqlite3.Connection, robot_id: str, phase: str) -> None:
    """Phase only. ``last_seen`` stays the register or presence stamp."""
    conn.execute(
        "UPDATE robots SET phase=? WHERE robot_id=?",
        (phase, robot_id),
    )
    conn.commit()
    _harden_conn(conn)


def delete_old_pending(
    conn: sqlite3.Connection, now: Optional[datetime] = None
) -> None:
    """Drop pending rows with no socket whose ``last_seen`` is older than 7 days.

    Called from ``Registry`` startup, before any socket exists. Approved rows
    stay. A missing ``last_seen`` is not old.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=PENDING_KEEP_S)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute(
        """
        DELETE FROM robots
        WHERE approval = 'pending'
          AND last_seen IS NOT NULL
          AND last_seen < ?
        """,
        (cutoff,),
    )
    conn.commit()
    _harden_conn(conn)


def robot_ids(conn: sqlite3.Connection) -> set:
    conn.commit()
    rows = conn.execute("SELECT robot_id FROM robots").fetchall()
    conn.commit()
    return {row["robot_id"] for row in rows}


def operator_rows(conn: sqlite3.Connection) -> list:
    conn.commit()
    rows = conn.execute(
        "SELECT username, secret_hash, disabled FROM operators"
    ).fetchall()
    conn.commit()
    return [
        (row["username"], bytes(row["secret_hash"]), int(row["disabled"]))
        for row in rows
    ]


def _private_dir(parent: str) -> None:
    leaf = os.path.realpath(parent)
    home = os.path.realpath(os.path.expanduser("~"))
    blocked = {
        "/",
        "/tmp",
        "/var",
        "/var/tmp",
        "/home",
        "/root",
        "/usr",
        "/opt",
        home,
        os.path.join(home, ".local"),
        os.path.join(home, ".local", "share"),
    }
    if leaf in blocked or not os.path.isdir(leaf):
        return
    os.chmod(leaf, 0o700)


def _harden_file(path: str) -> None:
    if not path or path == ":memory:":
        return
    real = os.path.realpath(path)
    if os.path.exists(real):
        os.chmod(real, 0o600)
    for suffix in ("-wal", "-shm"):
        side = real + suffix
        if os.path.exists(side):
            os.chmod(side, 0o600)


def _harden_conn(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return
    file_name = row[2]
    if isinstance(file_name, str) and file_name:
        _harden_file(file_name)


def db_file(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return None
    file_name = row[2]
    if not isinstance(file_name, str) or file_name == "":
        return None
    return file_name
