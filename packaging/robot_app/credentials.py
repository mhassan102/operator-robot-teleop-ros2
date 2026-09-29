"""ID and password generated when the robot terminal starts.

ID is 9 digits. Password is 8 characters from an alphabet that omits
I, O, 0, and 1. The plaintext password is shown in the window and sent
once at register. It is not written to a log.
"""

from __future__ import annotations

import secrets

PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ID_LENGTH = 9
PASSWORD_LENGTH = 8


def new_id() -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(ID_LENGTH))


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def new_credentials() -> tuple[str, str]:
    """Return ``(robot_id, password)``."""
    return new_id(), new_password()
