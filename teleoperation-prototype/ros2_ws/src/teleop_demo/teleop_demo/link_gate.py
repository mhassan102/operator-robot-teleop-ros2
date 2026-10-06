"""When the operator process may open mlink and arm the heartbeat.

``TELEOP_UI_ONLY=1`` serves the console first. The UDP socket and the
heartbeat timer stay down until Start enables them. Nothing here binds
a port.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Optional

_ON = frozenset({"1", "true", "yes", "on"})


def startup_link(env: Mapping[str, str]) -> tuple[bool, bool]:
    """Return ``(open_socket, arm_heartbeat)`` for process start.

    UI-only skips both, including when ``TELEOP_MLINK`` is set. Any other
    start arms the heartbeat timer, and opens the socket only when mlink
    is enabled. That is the behavior from before the console pages moved.
    """
    if str(env.get("TELEOP_UI_ONLY", "")).strip() == "1":
        return False, False
    enabled = str(env.get("TELEOP_MLINK", "")).strip().lower() in _ON
    return enabled, True


class LinkGate:
    """Records the socket and the timers Start and Stop turn on and off."""

    def __init__(self) -> None:
        self.socket: Any = None
        self.heartbeat: Any = None
        self.poll: Any = None

    def apply_startup(
        self,
        env: Mapping[str, str],
        open_socket: Callable[[], Any],
        arm_heartbeat: Callable[[], Any],
        arm_poll: Optional[Callable[[], Any]] = None,
    ) -> None:
        open_now, arm_now = startup_link(env)
        if open_now:
            self.socket = open_socket()
            if arm_poll is not None:
                self.poll = arm_poll()
        if arm_now:
            self.heartbeat = arm_heartbeat()

    def enable(
        self,
        open_socket: Callable[[], Any],
        arm_heartbeat: Callable[[], Any],
        arm_poll: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Open the socket and arm the heartbeat. A second call is a no-op."""
        if self.socket is None:
            self.socket = open_socket()
        if self.poll is None and arm_poll is not None:
            self.poll = arm_poll()
        if self.heartbeat is None:
            self.heartbeat = arm_heartbeat()

    def disable(
        self,
        close_socket: Callable[[Any], None],
        cancel: Callable[[Any], None],
    ) -> None:
        """Stop the heartbeat first, then close the socket."""
        if self.heartbeat is not None:
            cancel(self.heartbeat)
            self.heartbeat = None
        if self.poll is not None:
            cancel(self.poll)
            self.poll = None
        if self.socket is not None:
            close_socket(self.socket)
            self.socket = None
