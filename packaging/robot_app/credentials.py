"""9-digit robot ID.

The person at the SSH session types the password. This module does not
generate one, and it does not store one.
"""

from __future__ import annotations

import secrets

ID_LENGTH = 9


def new_id() -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(ID_LENGTH))
