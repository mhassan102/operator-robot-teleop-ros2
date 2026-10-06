"""Launch the operator console. ``--registry`` selects the signalling URL.

This starts one host Python process, opens ``http://127.0.0.1:8090/``
in the desktop window, and does not start mlink. That process serves
the pages, holds the registry socket, maps the keyboard, and, after
Start, sends mlink UDP. Closing the window stops mlink-op and then
stops this process. There is no Docker compose step.
``TELEOP_SUPERVISOR_DRY_RUN=1`` returns before the process binds a port
and before mlink is executed.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from packaging.operator_app.login import DEFAULT_REGISTRY
from packaging.supervisor.operator_commands import dry_run_enabled

CONSOLE_URL = "http://127.0.0.1:8090/"
_QUIT_WAIT_S = 45.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m packaging.operator_app",
        description="Operator desktop window",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"registry WebSocket URL (default {DEFAULT_REGISTRY})",
    )
    return parser.parse_args(argv)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def open_desktop(url: str, until: object = None) -> None:
    """Show the console and return when that window closes."""
    from packaging.operator_app.desktop import show_console

    show_console(url, until=until)


def _post_json(url: str, timeout: float = 15.0) -> bool:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def request_shutdown(console: str) -> bool:
    """Quit through the operator process so mlink-op stops first."""
    return _post_json(console.rstrip("/") + "/api/quit")


def shutdown_after_close(
    service: Any,
    *,
    console: str = CONSOLE_URL,
    wait_s: float = _QUIT_WAIT_S,
) -> None:
    """Stop mlink-op, then let this process exit."""
    if service.quit_requested():
        return
    request_shutdown(console)
    if service.wait_for_quit(wait_s):
        return
    service.request_quit()
    service.wait_for_quit(wait_s)


def wait_until_ready(url: str, timeout: float = 60.0) -> bool:
    """Poll ``/api/health``. This does not start mlink."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def launch(
    registry_url: str,
    *,
    root: Path | None = None,
    port: int = 8090,
    service: Any = None,
    browser: Callable[[str], None] | None = None,
    wait_ready: Callable[[], None] | None = None,
) -> int:
    """Serve the pages in this process and open the window. Dry-run does neither."""
    if dry_run_enabled():
        return 0
    from packaging.operator_app.host import OperatorHost

    repo = repo_root() if root is None else Path(root)
    host = service if service is not None else OperatorHost(repo, registry_url)
    try:
        bound = host.serve("127.0.0.1", port)
    except OSError as exc:
        print(f"operator failed: {exc}", file=sys.stderr)
        return 1
    origin = f"http://127.0.0.1:{bound}/"
    page = CONSOLE_URL if bound == 8090 else origin
    if wait_ready is None:
        if not wait_until_ready(origin + "api/health"):
            print("operator did not become ready", file=sys.stderr)
            host.shutdown()
            return 1
    else:
        wait_ready()
    code = 0
    try:
        if browser is None:
            try:
                open_desktop(page, until=host.quit_event())
            except Exception as exc:
                print(f"operator window failed: {exc}", file=sys.stderr)
                code = 1
            shutdown_after_close(host, console=page)
        else:
            browser(page)
            host.wait_for_quit()
    finally:
        host.shutdown()
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return launch(args.registry)
