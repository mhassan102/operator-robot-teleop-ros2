"""Login, config, and session pages for the operator process.

The registry socket stays in this process. A browser refresh reads
``/api/session`` and does not send ``login`` again. The password is not
stored and is not logged. Stop returns to ``/config`` and leaves this
process up. Release returns to ``/fleet`` and keeps the login. Sign out
returns to ``/``. Quit stops mlink, then the robot, then asks this
process to exit. It does not send ``sign_out`` and it does not run
docker compose. Nothing here binds 8090 or the mlink ports; the host
server calls ``dispatch``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from packaging.operator_app.config import (
    LINK_HELP,
    arm_choices,
    config_message,
    interface1_choices,
    interface2_choices,
    local_default_nic,
    operator_network_text,
    parse_inbound,
    preferred_arm,
    preferred_video,
    review_text,
    video_choices,
)
from packaging.operator_app.login import dashboard_message, message_for
from packaging.operator_app.session import OperatorSession
from packaging.supervisor.operator_commands import (
    console_url,
    dry_run_enabled,
    operate_path,
    start_refused,
)

_MAX_BODY = 65536
_CHOICE_KEYS = ("link", "iface1", "iface2", "arm", "video")
_INPUT_CHOICES = (
    {"label": "Keyboard", "value": "keyboard"},
    {"label": "Leader arm", "value": "leader"},
)
_UNCONFIRMED = "The robot did not confirm stop."
_REVIEW_FIRST = "Review the changed config before Start."
_SIGNED_OUT = "Signed out."
_STATIC = {
    "/operate.css": "text/css; charset=utf-8",
    "/operate.js": "text/javascript; charset=utf-8",
    "/reader.js": "text/javascript; charset=utf-8",
    "/login.js": "text/javascript; charset=utf-8",
    "/config.js": "text/javascript; charset=utf-8",
    "/fleet.js": "text/javascript; charset=utf-8",
    "/session_bar.js": "text/javascript; charset=utf-8",
}
HelperPost = Callable[[str, dict[str, Any]], dict[str, Any]]
LinkHook = Callable[[], None]


class HttpResult:
    def __init__(self, status: int, content_type: str, body: bytes) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body


class ConsoleApp:
    """One operator session. ``login_sends`` counts registry ``login`` frames."""

    def __init__(
        self,
        registry_url: str,
        *,
        helper_url: str = "http://127.0.0.1:8091",
        web_root: str | Path,
        operator_nic: str | None = None,
        on_enable_link: LinkHook | None = None,
        on_disable_link: LinkHook | None = None,
        helper_post: HelperPost | None = None,
        session: Any = None,
        stop_timeout: float = 30.0,
        reply_timeout: float = 5.0,
    ) -> None:
        self.registry_url = registry_url
        self.helper_url = helper_url.rstrip("/")
        self.web_root = Path(web_root)
        self._on_enable_link = on_enable_link
        self._on_disable_link = on_disable_link
        self._helper_post = helper_post
        self._session = session if session is not None else OperatorSession()
        self._stop_timeout = stop_timeout
        self._reply_timeout = reply_timeout
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False
        self._httpd: ThreadingHTTPServer | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._reader_started = False
        self._stop_handle: asyncio.TimerHandle | None = None
        self._reply_handle: asyncio.TimerHandle | None = None
        self.login_sends = 0
        nic = local_default_nic() if operator_nic is None else operator_nic
        self._operator_network = operator_network_text(nic)
        self._reset_session_locked(keep_id="")

    def __repr__(self) -> str:
        return f"ConsoleApp(view={self._view!r}, logged_in={self._logged_in})"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._thread_main,
            name="operator-console",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(5) or self._loop is None:
            raise RuntimeError("operator console loop did not start")

    def serve(self, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
        """Bind a test server. Production uses the host server and ``dispatch``."""
        if host != "127.0.0.1":
            raise ValueError("console test server binds 127.0.0.1 only")
        self.start()
        httpd = ThreadingHTTPServer((host, port), _handler(self))
        self._httpd = httpd
        threading.Thread(
            target=httpd.serve_forever,
            name="operator-console-http",
            daemon=True,
        ).start()
        return httpd

    def close(self) -> None:
        """Release the registry socket. This does not stop the arm."""
        if self._closed:
            return
        self._closed = True
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_timers)
            closing = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
            try:
                closing.result(timeout=2)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=2)

    def dispatch(self, method: str, target: str, body: bytes = b"") -> HttpResult | None:
        path = urlparse(target).path
        if method == "GET":
            return self._get(path)
        if method == "POST":
            return self._post(path, body)
        return None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "view": self._view,
                "logged_in": self._logged_in,
                "username": self._username,
                "robot_id": self._robot_id,
                "hostname": self._hostname,
                "status": self._status,
                "holding": self._holding,
                "fleet_seen": self._fleet_seen,
                "counts": dict(self._counts),
                "robots": [dict(row) for row in self._robots],
                "review_status": self._review_status,
                "start_enabled": self._start_enabled and self._view == "config",
                "operate_url": self._operate_url,
                "operator_network": self._operator_network,
                "link_help": LINK_HELP,
                "form_revision": self._form_revision,
                "link": self._link,
                "selected": {
                    "link": self._link,
                    "iface1": self._iface1,
                    "iface2": self._iface2,
                    "arm": self._arm,
                    "video": self._video,
                    "input": self._input,
                },
                "choices": {
                    "iface1": _choice_json(self._iface1_choices),
                    "iface2": _choice_json(self._iface2_choices),
                    "arm": _choice_json(self._arm_choices),
                    "video": _choice_json(self._video_choices),
                    "input": _INPUT_CHOICES,
                },
            }

    def _get(self, path: str) -> HttpResult | None:
        if path == "/api/session":
            return _json(200, self.snapshot())
        if path == "/":
            return _page(self.web_root / "login.html")
        if path == "/fleet":
            return _page(self.web_root / "fleet.html")
        if path == "/config":
            return _page(self.web_root / "config.html")
        if path == "/operate":
            if self.snapshot()["view"] != "operate":
                return HttpResult(403, "text/plain; charset=utf-8", b"not ready")
            page = _page(self.web_root / "index.html")
            if page.status != 200 or self.accepted_input() != "leader":
                return page
            body = page.body.replace(
                b'id="leader-joints-block" hidden',
                b'id="leader-joints-block"',
                1,
            ).replace(
                b'id="follower-joints-block" hidden',
                b'id="follower-joints-block"',
                1,
            ).replace(
                b'id="keys-block"',
                b'id="keys-block" hidden',
                1,
            )
            return HttpResult(page.status, page.content_type, body)
        if path == "/index.html":
            return HttpResult(404, "text/plain; charset=utf-8", b"not found")
        content_type = _STATIC.get(path)
        if content_type is None:
            return None
        return _file(self.web_root / path.lstrip("/"), content_type)

    def _post(self, path: str, body: bytes) -> HttpResult | None:
        routes = {
            "/api/login": self._login,
            "/api/approve": self._approve,
            "/api/attach": self._attach,
            "/api/review": self._review,
            "/api/start": self._start,
            "/api/stop": self._stop,
            "/api/release": self._release,
            "/api/sign-out": self._sign_out,
            "/api/quit": self._quit,
        }
        action = routes.get(path)
        if action is None:
            return None
        if len(body) > _MAX_BODY:
            return _json(400, {"ok": False, "message": "bad body"})
        raw = _object(body)
        if raw is None:
            return _json(400, {"ok": False, "message": "bad body"})
        try:
            payload = self._wait(action(raw))
        except Exception:
            payload = {"ok": False, "message": "Cannot reach the registry."}
        return _json(200, payload)

    async def _login(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._logged_in:
                return self._public_locked() | {
                    "ok": True,
                    "message": self._status,
                }
        username = raw.get("username")
        password = raw.get("password")
        username_text = username.strip() if isinstance(username, str) else ""
        password_text = password if isinstance(password, str) else ""
        if username_text == "" or password_text == "":
            self._assign(status=message_for("empty"), view="login")
            return self.snapshot() | {"ok": False, "message": message_for("empty")}
        self.login_sends += 1
        result = await self._session.login(
            self.registry_url, username_text, password_text
        )
        password_text = ""
        if not result.ok:
            self._assign(logged_in=False, status=message_for(result.code), view="login")
            return self.snapshot() | {"ok": False, "message": message_for(result.code)}
        with self._lock:
            self._logged_in = True
            self._username = username_text
            self._robot_id = ""
            self._hostname = ""
            self._view = "fleet"
            self._status = "Waiting for the fleet."
            self._returned = False
        for frame in self._session.take_pending():
            self._apply_fleet(frame)
        self._start_reader()
        message = self.snapshot()["status"] or "Waiting for the fleet."
        return self.snapshot() | {"ok": True, "message": message}

    async def _approve(self, raw: dict[str, Any]) -> dict[str, Any]:
        return await self._fleet_command("approve", raw)

    async def _attach(self, raw: dict[str, Any]) -> dict[str, Any]:
        return await self._fleet_command("attach", raw)

    async def _fleet_command(self, kind: str, raw: dict[str, Any]) -> dict[str, Any]:
        if not self._open():
            self._assign(status="Cannot reach the registry.")
            return self.snapshot() | {
                "ok": False,
                "message": "Cannot reach the registry.",
            }
        robot_id = raw.get("robot_id")
        if not isinstance(robot_id, str) or len(robot_id) != 9 or not robot_id.isdigit():
            text = dashboard_message("not_found")
            self._assign(status=text)
            return self.snapshot() | {"ok": False, "message": text}
        try:
            await self._session.send_json(
                {"v": 1, "type": kind, "robot_id": robot_id}
            )
        except Exception:
            self._assign(status="Cannot reach the registry.")
            return self.snapshot() | {
                "ok": False,
                "message": "Cannot reach the registry.",
            }
        return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}

    async def _review(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not self._open():
            self._assign(review_status="Cannot reach the registry.")
            return self.snapshot() | {"ok": False, "message": "Cannot reach the registry."}
        message = self._message_from(raw)
        self._remember_choice(message)
        self._pending = message
        self._assign(review_status="Sending...")
        try:
            await self._session.send_json(message)
        except Exception:
            self._assign(review_status="Cannot reach the registry.")
            return self.snapshot() | {"ok": False, "message": "Cannot reach the registry."}
        return self.snapshot() | {"ok": True, "message": "Sending..."}

    async def _start(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not self._open() or self._stop_busy or self._view == "operate":
            return self.snapshot() | {"ok": False, "message": self.snapshot()["status"]}
        message = self._message_from(raw)
        accepted = self._accepted
        if accepted is None or _choice_tuple(message) != _choice_tuple(accepted):
            self._assign(status=_REVIEW_FIRST)
            return self.snapshot() | {"ok": False, "message": _REVIEW_FIRST}
        refused = start_refused(accepted, self._inventory)
        if refused:
            self._assign(status=refused)
            return self.snapshot() | {"ok": False, "message": refused}
        chosen = _normalize_input(raw.get("input"))
        self._assign(accepted_input=chosen, input=chosen)
        try:
            await self._session.send_json(dict(accepted))
            await self._session.send_json({"v": 1, "type": "start"})
        except Exception:
            self._assign(status="Cannot reach the registry.")
            return self.snapshot() | {"ok": False, "message": "Cannot reach the registry."}
        self._assign(status="starting", start_enabled=False, awaiting_ready=True)
        return self.snapshot() | {"ok": True, "message": "starting"}

    async def _stop(self, raw: dict[str, Any]) -> dict[str, Any]:
        return await self._begin_stop("config")

    async def _release(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self._logged_in:
                return self._public_locked() | {"ok": False, "message": self._status}
            drive = self._mlink_up or self._awaiting_ready
        if drive:
            return await self._begin_stop("release")
        await self._begin_release()
        return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}

    async def _sign_out(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            logged_in = self._logged_in
            view = self._view
            holding = self._holding
            drive = self._mlink_up or self._awaiting_ready
        if not logged_in and view == "login":
            return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}
        if holding and drive:
            return await self._begin_stop("sign_out")
        await self._begin_sign_out()
        return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}

    async def _quit(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            holding = self._holding
            drive = self._mlink_up or self._awaiting_ready
        if holding or drive:
            return await self._begin_stop("quit")
        await self._begin_quit()
        return self.snapshot() | {"ok": True, "message": "quit"}

    async def _begin_stop(self, after: str) -> dict[str, Any]:
        with self._lock:
            if self._stop_busy:
                self._after = after
                return self._public_locked() | {"ok": True, "message": "stopping"}
            if after != "quit" and not self._logged_in:
                return self._public_locked() | {"ok": False, "message": self._status}
            self._stop_busy = True
            self._after = after
            self._awaiting_ready = False
            self._start_enabled = False
            self._status = dashboard_message("stale") if self._stale_stop else "stopping"
            self._unconfirmed = False
        if self._on_disable_link is not None:
            await asyncio.to_thread(self._on_disable_link)
        if self._mlink_up and not dry_run_enabled():
            self._mlink_up = False
            await asyncio.to_thread(self._post_helper, "/mlink/stop", {})
        else:
            self._mlink_up = False
        with self._lock:
            if self._finished_stop or self._after != after:
                return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}
        sent = False
        if self._open():
            try:
                await self._session.send_json({"v": 1, "type": "stop"})
                sent = True
            except Exception:
                self._assign(status="Cannot reach the registry.")
        with self._lock:
            if self._finished_stop or self._after != after:
                return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}
        if sent:
            self._assign(awaiting_stopped=True)
            self._arm_stop_timer()
            return self.snapshot() | {"ok": True, "message": "stopping"}
        await self._finish_after_stop()
        return self.snapshot() | {"ok": True, "message": self.snapshot()["status"]}

    async def _on_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "released" and self._awaiting_reply == "released":
            self._show_fleet()
            return
        if kind == "signed_out" and self._awaiting_reply == "signed_out":
            await self._return_to_login()
            return
        if kind == "fleet":
            self._apply_fleet(msg)
            return
        if kind == "attached":
            self._note_attached(msg)
            return
        if kind == "inventory":
            self._apply_inventory(msg)
            return
        if kind == "status":
            await self._on_status(msg)
            return
        if kind == "config_ok":
            self._accepted = dict(self._pending) if isinstance(self._pending, dict) else None
            self._assign(review_status=review_text(msg))
            return
        if kind == "error" and msg.get("code") in {"bad_config", "unreachable"}:
            if msg.get("code") == "bad_config":
                self._accepted = None
            self._assign(review_status=review_text(msg))
            return
        if kind == "error" and msg.get("code") == "stale":
            await self._note_stale()
            return
        if kind == "error" and msg.get("code") == "offline":
            await self._on_offline()
            return
        if kind == "error" and self._awaiting_reply == "signed_out":
            await self._return_to_login()
            return
        if kind == "error" and self._awaiting_reply == "released":
            self._show_fleet()
            return
        if kind == "error" and self._view == "fleet" and not self._awaiting_stopped:
            code = msg.get("code")
            text = dashboard_message(code if isinstance(code, str) else "")
            if text:
                self._assign(status=text)

    async def _on_status(self, msg: dict[str, Any]) -> None:
        phase = msg.get("phase")
        detail = msg.get("detail")
        phase_text = phase if isinstance(phase, str) else ""
        detail_text = detail if isinstance(detail, str) else ""
        shown = detail_text if detail_text else phase_text
        if shown:
            self._assign(status=shown)
        if phase_text == "stopped" and self._awaiting_stopped:
            self._assign(awaiting_stopped=False)
            self._cancel_stop_timer()
            await self._finish_after_stop()
            return
        if phase_text in {"error", "stopped", "ready"} and not self._stop_busy:
            self._assign(start_enabled=True)
        if phase_text in {"error", "stopped"}:
            self._assign(awaiting_ready=False)
        if phase_text != "ready" or not self._awaiting_ready:
            if phase_text == "ready":
                self._assign(awaiting_ready=False)
            return
        self._assign(awaiting_ready=False)
        await self._begin_mlink()

    async def _begin_mlink(self) -> None:
        if self._mlink_up or self._closed or dry_run_enabled():
            return
        page = ""
        try:
            from packaging.supervisor.operator_commands import camera_page_of

            page = camera_page_of(self._inventory if isinstance(self._inventory, dict) else None)
        except Exception as exc:
            detail = getattr(exc, "detail", "") or "camera page is not set"
            self._assign(status=str(detail), start_enabled=True)
            return
        reply = await asyncio.to_thread(
            self._post_helper,
            "/mlink/start",
            {
                "config": dict(self._accepted) if isinstance(self._accepted, dict) else {},
                "inventory": dict(self._inventory) if isinstance(self._inventory, dict) else {},
                "phase": "ready",
            },
        )
        if not reply.get("ok"):
            detail = reply.get("detail")
            self._assign(
                status=detail if isinstance(detail, str) and detail else "operator start failed",
                start_enabled=True,
            )
            return
        if self._on_enable_link is not None:
            try:
                await asyncio.to_thread(self._on_enable_link)
            except Exception:
                self._assign(status="operator start failed", start_enabled=True)
                return
        self._mlink_up = True
        target = reply.get("console_url")
        if not isinstance(target, str) or not target:
            target = console_url(page)
        self._assign(view="operate", operate_url=operate_path(page), start_enabled=True)
        self._operate_url_full = target

    async def _finish_after_stop(self) -> None:
        with self._lock:
            if self._finished_stop:
                return
            self._finished_stop = True
            after = self._after
            self._stop_busy = False
            self._awaiting_stopped = False
            self._after = ""
        self._cancel_stop_timer()
        if after == "release":
            await self._begin_release()
            return
        if after == "sign_out":
            await self._begin_sign_out()
            return
        if after == "quit":
            await self._begin_quit()
            return
        self._show_config()

    def _show_config(self) -> None:
        with self._lock:
            self._finished_stop = False
            self._mlink_up = False
            self._stale_stop = False
            self._start_enabled = True
            if self._logged_in:
                self._view = "config"

    async def _begin_release(self) -> None:
        """Detach this robot and stay logged in. The registry socket stays open."""
        self._assign(awaiting_reply="released")
        if self._open():
            try:
                await self._session.send_json({"v": 1, "type": "release"})
            except Exception:
                self._show_fleet()
                return
            self._arm_reply_timer()
            return
        self._show_fleet()

    async def _begin_sign_out(self) -> None:
        """Leave the account. The registry closes this login, not the robot row."""
        self._assign(awaiting_reply="signed_out")
        if self._open():
            try:
                await self._session.send_json({"v": 1, "type": "sign_out"})
            except Exception:
                await self._return_to_login()
                return
            self._arm_reply_timer()
            return
        await self._return_to_login()

    def _reply_timeout_cb(self) -> None:
        asyncio.create_task(self._reply_timed_out())

    async def _reply_timed_out(self) -> None:
        kind = self._awaiting_reply
        if kind == "released":
            self._show_fleet()
        elif kind == "signed_out":
            await self._return_to_login()

    async def _note_stale(self) -> None:
        """The registry socket is still open, so stop is still forwarded."""
        with self._lock:
            attached = self._logged_in and (
                self._holding or self._view in {"config", "operate"}
            )
            busy = self._stop_busy
            after = self._after
            if not attached:
                return
            self._stale_stop = True
            self._status = dashboard_message("stale")
        if busy:
            if after not in {"quit", "sign_out"}:
                self._assign(after="release")
            return
        await self._begin_stop("release")

    async def _on_offline(self) -> None:
        """The robot socket is gone. Stop local mlink only."""
        with self._lock:
            after = self._after
            in_drive = (
                self._holding
                or self._view in {"config", "operate"}
                or self._mlink_up
            )
        if after == "quit":
            self._assign(awaiting_stopped=False, stop_busy=False, finished_stop=True)
            self._cancel_stop_timer()
            await self._begin_quit()
            return
        if after == "sign_out":
            self._assign(awaiting_stopped=False, stop_busy=False, finished_stop=True)
            self._cancel_stop_timer()
            await self._begin_sign_out()
            return
        if not in_drive:
            text = dashboard_message("offline")
            if text:
                self._assign(status=text)
            return
        await self._offline_to_fleet()

    async def _offline_to_fleet(self) -> None:
        with self._lock:
            mlink = self._mlink_up
            self._awaiting_stopped = False
            self._stop_busy = False
            self._after = ""
            self._finished_stop = True
            self._awaiting_reply = ""
            self._stale_stop = False
        self._cancel_timers()
        if mlink:
            if self._on_disable_link is not None:
                await asyncio.to_thread(self._on_disable_link)
            self._mlink_up = False
            if not dry_run_enabled():
                await asyncio.to_thread(self._post_helper, "/mlink/stop", {})
        self._show_fleet(dashboard_message("offline"))

    def _show_fleet(self, status: str | None = None) -> None:
        """Back to the robot list. The login and the registry socket stay."""
        self._cancel_timers()
        with self._lock:
            stale = self._stale_stop
            logged_in = self._logged_in
            self._stale_stop = False
            self._finished_stop = False
            self._stop_busy = False
            self._awaiting_stopped = False
            self._awaiting_ready = False
            self._awaiting_reply = ""
            self._after = ""
            self._mlink_up = False
            self._holding = False
            self._robot_id = ""
            self._hostname = ""
            self._accepted = None
            self._pending = None
            self._inventory = None
            self._applied = False
            self._operate_url = ""
            self._operate_url_full = ""
            self._start_enabled = False
            self._review_status = ""
            self._unconfirmed = False
            self._link = "tailscale"
            self._iface1 = ""
            self._iface2 = None
            self._arm = ""
            self._video = ""
            self._input = "keyboard"
            self._accepted_input = "keyboard"
            self._iface1_choices = []
            self._iface2_choices = []
            self._arm_choices = []
            self._video_choices = []
            if not logged_in:
                return
            self._view = "fleet"
            if status is None:
                self._status = dashboard_message("stale") if stale else ""
            else:
                self._status = status

    async def _begin_quit(self) -> None:
        """mlink and the robot are already stopped. This exits the process."""
        await asyncio.to_thread(self._post_helper, "/quit", {})
        self._assign(view="quit", status="quit", start_enabled=False)

    async def _return_to_login(self) -> None:
        with self._lock:
            if self._returned:
                return
            self._returned = True
            robot_id = self._robot_id
        self._cancel_timers()
        close = getattr(self._session, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass
        with self._lock:
            alive = self._reader_task is not None and not self._reader_task.done()
            self._reset_session_locked(keep_id=robot_id)
            self._view = "login"
            self._logged_in = False
            self._returned = True
            self._status = _SIGNED_OUT
            if alive:
                self._reader_started = True

    def _note_attached(self, msg: Mapping[str, Any]) -> None:
        robot_id = msg.get("robot_id")
        hostname = msg.get("hostname")
        self._assign(
            holding=True,
            robot_id=robot_id if isinstance(robot_id, str) else "",
            hostname=hostname if isinstance(hostname, str) else "",
            status="Waiting for the robot.",
            view="fleet",
        )

    def _apply_fleet(self, msg: Mapping[str, Any]) -> None:
        raw_counts = msg.get("counts")
        counts = raw_counts if isinstance(raw_counts, Mapping) else {}
        raw_robots = msg.get("robots")
        robots: list[dict[str, Any]] = []
        if isinstance(raw_robots, list):
            for row in raw_robots:
                if not isinstance(row, Mapping):
                    continue
                robot_id = row.get("robot_id")
                location = row.get("location")
                status = row.get("status")
                mode = row.get("mode")
                operator = row.get("operator")
                robots.append(
                    {
                        "robot_id": robot_id if isinstance(robot_id, str) else "",
                        "location": location if isinstance(location, str) else "",
                        "status": status if isinstance(status, str) else "",
                        "mode": mode if isinstance(mode, str) else "",
                        "operator": operator
                        if isinstance(operator, str) and operator
                        else None,
                    }
                )
        robots.sort(key=lambda item: item["robot_id"])
        numbers = {
            "registered": _nonneg(counts.get("registered")),
            "autonomous": _nonneg(counts.get("autonomous")),
            "needs_manual": _nonneg(counts.get("needs_manual")),
            "in_teleop": _nonneg(counts.get("in_teleop")),
        }
        with self._lock:
            waiting = self._status == "Waiting for the fleet."
        self._assign(counts=numbers, robots=robots, fleet_seen=True)
        if waiting:
            self._assign(status="")

    def _apply_inventory(self, inventory: Mapping[str, Any]) -> None:
        if not self._logged_in or not self._holding or not isinstance(inventory, Mapping):
            return
        iface1 = interface1_choices(inventory)
        iface2 = interface2_choices(inventory)
        arms = arm_choices(inventory)
        videos = video_choices(inventory)
        with self._lock:
            self._inventory = dict(inventory)
            first = not self._applied
            self._iface1_choices = iface1
            self._iface2_choices = iface2
            self._arm_choices = arms
            self._video_choices = videos
            if first:
                preferred_iface = iface1[0].value if iface1 else ""
                self._link = "tailscale"
                self._iface1 = preferred_iface if isinstance(preferred_iface, str) else ""
                self._iface2 = None
                arm = preferred_arm(inventory)
                video = preferred_video(inventory)
                self._arm = arm if isinstance(arm, str) else ""
                self._video = video if isinstance(video, str) else ""
            self._applied = True
            self._form_revision += 1
            self._view = "config"
            self._start_enabled = True
            if isinstance(inventory.get("hostname"), str) and inventory.get("hostname"):
                self._hostname = inventory["hostname"]

    def _message_from(self, raw: Mapping[str, Any] | None) -> dict[str, Any]:
        if not raw:
            with self._lock:
                return config_message(
                    self._link, self._iface1, self._iface2, self._arm, self._video
                )
        link = raw.get("link")
        if link not in {"tailscale", "turn"}:
            link = "tailscale"
        iface1 = raw.get("iface1") if isinstance(raw.get("iface1"), str) else ""
        iface2 = raw.get("iface2")
        if not isinstance(iface2, str) or iface2 == "":
            iface2 = None
        arm = raw.get("arm") if isinstance(raw.get("arm"), str) else ""
        video = raw.get("video") if isinstance(raw.get("video"), str) else ""
        return config_message(str(link), iface1, iface2, arm, video)

    def _remember_choice(self, message: Mapping[str, Any]) -> None:
        iface2 = message.get("iface2")
        with self._lock:
            self._link = message.get("link") if message.get("link") in {"tailscale", "turn"} else "tailscale"
            self._iface1 = message.get("iface1") if isinstance(message.get("iface1"), str) else ""
            self._iface2 = iface2 if isinstance(iface2, str) else None
            self._arm = message.get("arm") if isinstance(message.get("arm"), str) else ""
            self._video = message.get("video") if isinstance(message.get("video"), str) else ""

    def _post_helper(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        safe = {key: value for key, value in payload.items() if key != "password"}
        if self._helper_post is not None:
            reply = self._helper_post(path, safe)
            return reply if isinstance(reply, dict) else {"ok": False, "detail": "operator start failed"}
        url = self.helper_url + path
        data = json.dumps(safe).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError):
            return {"ok": False, "detail": "operator start failed"}
        if not isinstance(raw, dict):
            return {"ok": False, "detail": "operator start failed"}
        return raw

    def _open(self) -> bool:
        client = self._session
        ws = getattr(client, "ws", None)
        return bool(self._logged_in and ws is not None)

    def _start_reader(self) -> None:
        loop = self._loop
        if self._reader_started or loop is None:
            return
        self._reader_started = True
        self._reader_task = loop.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            while not self._closed:
                msg = await self._session.next_message()
                if msg is None or self._closed:
                    return
                if parse_inbound(msg) is None and not isinstance(msg, dict):
                    continue
                await self._on_message(msg)
        except asyncio.CancelledError:
            raise
        finally:
            self._reader_task = None
            self._reader_started = False

    def _arm_stop_timer(self) -> None:
        loop = self._loop
        if loop is None:
            return
        self._cancel_stop_timer()
        self._stop_handle = loop.call_later(self._stop_timeout, self._stop_timeout_cb)

    def _stop_timeout_cb(self) -> None:
        asyncio.create_task(self._on_stop_timeout())

    async def _on_stop_timeout(self) -> None:
        if not self._awaiting_stopped:
            return
        self._assign(awaiting_stopped=False, unconfirmed=True, status=_UNCONFIRMED)
        await self._finish_after_stop()

    def _cancel_stop_timer(self) -> None:
        handle = self._stop_handle
        self._stop_handle = None
        if handle is not None:
            handle.cancel()

    def _arm_reply_timer(self) -> None:
        loop = self._loop
        if loop is None:
            return
        handle = self._reply_handle
        self._reply_handle = None
        if handle is not None:
            handle.cancel()
        self._reply_handle = loop.call_later(self._reply_timeout, self._reply_timeout_cb)

    def _cancel_timers(self) -> None:
        self._cancel_stop_timer()
        handle = self._reply_handle
        self._reply_handle = None
        if handle is not None:
            handle.cancel()

    def _close_session(self) -> None:
        loop = self._loop
        task = self._reader_task
        if task is not None and loop is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)
        close = getattr(self._session, "close", None)
        if close is None or loop is None or not loop.is_running():
            return
        if asyncio.current_task(loop) is not None and threading.current_thread() is self._thread:
            return
        fut = asyncio.run_coroutine_threadsafe(close(), loop)
        try:
            fut.result(timeout=2)
        except Exception:
            pass

    async def _shutdown_async(self) -> None:
        self._cancel_timers()
        task = self._reader_task
        if task is not None and not task.done():
            task.cancel()
        close = getattr(self._session, "close", None)
        if close is not None:
            await close()

    def _wait(self, coro: Any) -> dict[str, Any]:
        self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        result = future.result(timeout=8)
        return result if isinstance(result, dict) else self.snapshot()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        bind = getattr(self._session, "bind", None)
        if bind is not None:
            bind(loop)
        self._ready.set()
        loop.run_forever()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def _assign(self, **names: Any) -> None:
        with self._lock:
            for key, value in names.items():
                setattr(self, f"_{key}", value)

    def _public_locked(self) -> dict[str, Any]:
        return {
            "view": self._view,
            "logged_in": self._logged_in,
            "username": self._username,
            "robot_id": self._robot_id,
            "hostname": self._hostname,
            "status": self._status,
            "holding": self._holding,
            "fleet_seen": self._fleet_seen,
            "counts": dict(self._counts),
            "robots": [dict(row) for row in self._robots],
            "review_status": self._review_status,
            "start_enabled": self._start_enabled and self._view == "config",
            "operate_url": self._operate_url,
            "operator_network": self._operator_network,
            "link_help": LINK_HELP,
            "form_revision": self._form_revision,
            "link": self._link,
            "selected": {
                "link": self._link,
                "iface1": self._iface1,
                "iface2": self._iface2,
                "arm": self._arm,
                "video": self._video,
                "input": self._input,
            },
            "choices": {
                "iface1": _choice_json(self._iface1_choices),
                "iface2": _choice_json(self._iface2_choices),
                "arm": _choice_json(self._arm_choices),
                "video": _choice_json(self._video_choices),
                "input": _INPUT_CHOICES,
            },
        }

    def _reset_session_locked(self, keep_id: str) -> None:
        self._view = "login"
        self._logged_in = False
        self._username = ""
        self._robot_id = keep_id
        self._hostname = ""
        self._holding = False
        self._fleet_seen = False
        self._counts = _empty_counts()
        self._robots: list[dict[str, Any]] = []
        self._status = ""
        self._review_status = ""
        self._start_enabled = False
        self._operate_url = ""
        self._operate_url_full = ""
        self._form_revision = 0
        self._link = "tailscale"
        self._iface1 = ""
        self._iface2 = None
        self._arm = ""
        self._video = ""
        self._input = "keyboard"
        self._accepted_input = "keyboard"
        self._iface1_choices: list[Any] = []
        self._iface2_choices: list[Any] = []
        self._arm_choices: list[Any] = []
        self._video_choices: list[Any] = []
        self._accepted: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._inventory: dict[str, Any] | None = None
        self._applied = False
        self._awaiting_ready = False
        self._awaiting_stopped = False
        self._awaiting_reply = ""
        self._stop_busy = False
        self._stale_stop = False
        self._after = ""
        self._finished_stop = False
        self._unconfirmed = False
        self._returned = False
        self._mlink_up = False

    def accepted_input(self) -> str:
        """Input committed by Start. Keyboard does not open the leader port."""
        with self._lock:
            mode = self._accepted_input
        if mode == "leader":
            return "leader"
        return "keyboard"


def _empty_counts() -> dict[str, int]:
    return {
        "registered": 0,
        "autonomous": 0,
        "needs_manual": 0,
        "in_teleop": 0,
    }


def _nonneg(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return value


def _normalize_input(value: Any) -> str:
    if value == "leader":
        return "leader"
    return "keyboard"


def _choice_tuple(message: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(message.get(key) for key in _CHOICE_KEYS)


def _choice_json(choices: list[Any]) -> list[dict[str, Any]]:
    return [{"label": choice.label, "value": choice.value} for choice in choices]


def _object(body: bytes) -> dict[str, Any] | None:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _json(status: int, payload: dict[str, Any]) -> HttpResult:
    return HttpResult(status, "application/json", json.dumps(payload).encode("utf-8"))


def _page(path: Path) -> HttpResult:
    return _file(path, "text/html; charset=utf-8")


def _file(path: Path, content_type: str) -> HttpResult:
    try:
        data = path.read_bytes()
    except OSError:
        return HttpResult(404, "text/plain; charset=utf-8", b"not found")
    return HttpResult(200, content_type, data)


def _handler(app: ConsoleApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            result = app.dispatch("GET", self.path, b"")
            if result is None:
                self.send_error(404, "not found")
                return
            self._send(result)

        def do_POST(self) -> None:
            length = _content_length(self.headers.get("Content-Length"))
            if length is None or length > _MAX_BODY:
                self._send(_json(400, {"ok": False, "message": "bad body"}))
                return
            body = self.rfile.read(length) if length else b""
            result = app.dispatch("POST", self.path, body)
            if result is None:
                self.send_error(404, "not found")
                return
            self._send(result)

        def _send(self, result: HttpResult) -> None:
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(result.body)

    return Handler


def _content_length(header: str | None) -> int | None:
    if header is None:
        return 0
    try:
        value = int(header)
    except ValueError:
        return None
    if value < 0:
        return None
    return value
