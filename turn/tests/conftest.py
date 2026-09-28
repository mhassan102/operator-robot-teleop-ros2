"""Let `python3 -m pytest` from the repo root import `signalling` and `agent`."""

from __future__ import annotations

import sys
from pathlib import Path

_TURN_ROOT = str(Path(__file__).resolve().parents[1])
if _TURN_ROOT not in sys.path:
    sys.path.insert(0, _TURN_ROOT)
