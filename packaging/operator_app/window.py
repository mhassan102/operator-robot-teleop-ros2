"""PyQt5 operator window.

Login shows the robot hostname and waits for inventory. Review sends
the choice. Start sends ``start`` and, once the robot reports ready,
runs the operator plan and loads the console. ``TELEOP_SUPERVISOR_DRY_RUN=1``
still sends ``start`` and shows the robot status, and it does not spawn
or load the console. Stop and Logout stay above the page. Stop brings
the operator stack down first, then asks the robot to stop, and returns
to the config page. Logout does that stop, detaches this operator, and
returns to login. Closing the window does the stop, then exits.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QEventLoop, QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from packaging.operator_app.config import local_default_nic, review_text
from packaging.operator_app.config_page import ConfigPage
from packaging.operator_app.login import LoginResult, message_for
from packaging.operator_app.session import OperatorSession
from packaging.supervisor.operator_commands import (
    OperatorDecision,
    camera_page_of,
    decide_operator,
    default_route_ipv4,
    dry_run_enabled,
    stop_operator_local,
)
from packaging.supervisor.robot_commands import PlanError

STOP_CONFIRM_MS = 30_000
CLOSE_CONFIRM_MS = 3_000
LOGOUT_CONFIRM_MS = 5_000
_UNCONFIRMED = "The robot did not confirm stop."
_CHOICE_KEYS = ("link", "iface1", "iface2", "arm", "video")


def _choice_tuple(message: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(message.get(key) for key in _CHOICE_KEYS)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def webengine_installed() -> bool:
    """True when the distro Qt WebEngine module can be imported.

    This does not construct a view and does not load a page.
    """
    return importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is not None


class _ResultBus(QObject):
    arrived = pyqtSignal(object)
    inbound = pyqtSignal(object)
    session = pyqtSignal(str)
    console = pyqtSignal(str)


class LoginWindow(QWidget):
    def __init__(
        self,
        registry_url: str,
        operator_nic: str | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        super().__init__()
        self.registry_url = registry_url
        self.logged_in = False
        self.hostname = ""
        self._operator_nic = operator_nic
        self._root_override = root
        self._client: OperatorSession | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._closing = False
        self._shut = False
        self._reader_started = False
        self._reader_task: asyncio.Task[None] | None = None
        self._start_thread: threading.Thread | None = None
        self._start_busy = False
        self._awaiting_ready = False
        self._operator_started = False
        self._allow_console = False
        self._inventory: dict[str, Any] | None = None
        self._accepted_config: dict[str, Any] | None = None
        self._stop_busy = False
        self._stop_sent = False
        self._stop_finished = False
        self._awaiting_stopped = False
        self._after_stop = ""
        self._close_requested = False
        self._logout_busy = False
        self._awaiting_logout = False
        self._returned_to_login = False
        self._robot_unconfirmed = False
        self._stop_wait_hook: Any = None
        self.config_page: ConfigPage | None = None
        self.console_view: QWidget | None = None

        self.setWindowTitle("Teleop operator")
        self.id_edit = QLineEdit()
        self.id_edit.setObjectName("robot_id")
        self.id_edit.setPlaceholderText("9-digit ID")
        self.password_edit = QLineEdit()
        self.password_edit.setObjectName("password")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Password")
        self.login_button = QPushButton("Log in")
        self.login_button.setObjectName("login")
        self.login_button.clicked.connect(self.submit)
        self.id_edit.returnPressed.connect(self.submit)
        self.password_edit.returnPressed.connect(self.submit)
        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        self.registry_label = QLabel(registry_url)
        self.registry_label.setTextFormat(Qt.PlainText)

        self.hostname_label = QLabel("")
        self.hostname_label.setObjectName("hostname")
        self.hostname_label.setTextFormat(Qt.PlainText)
        self.waiting_label = QLabel("")
        self.waiting_label.setObjectName("waiting")
        self.waiting_label.setTextFormat(Qt.PlainText)

        self.form_page = QWidget()
        form_layout = QVBoxLayout(self.form_page)
        fields = QFormLayout()
        fields.addRow("Robot ID", self.id_edit)
        fields.addRow("Password", self.password_edit)
        form_layout.addLayout(fields)
        form_layout.addWidget(self.login_button)
        form_layout.addWidget(self.status_label)
        form_layout.addWidget(self.registry_label)

        self.waiting_page = QWidget()
        waiting_layout = QVBoxLayout(self.waiting_page)
        caption = QLabel("Robot")
        caption.setTextFormat(Qt.PlainText)
        waiting_layout.addWidget(caption)
        waiting_layout.addWidget(self.hostname_label)
        waiting_layout.addWidget(self.waiting_label)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.form_page)
        self.stack.addWidget(self.waiting_page)

        self.session_bar = QWidget()
        self.session_bar.setObjectName("session_bar")
        bar_layout = QHBoxLayout(self.session_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.logout_button = QPushButton("Logout")
        self.logout_button.setObjectName("logout")
        self.bar_status = QLabel("")
        self.bar_status.setObjectName("bar_status")
        self.bar_status.setWordWrap(True)
        self.bar_status.setTextFormat(Qt.PlainText)
        self.stop_button.clicked.connect(self.stop_session)
        self.logout_button.clicked.connect(self.logout_session)
        bar_layout.addWidget(self.stop_button)
        bar_layout.addWidget(self.logout_button)
        bar_layout.addWidget(self.bar_status, 1)
        self.session_bar.hide()

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._on_stop_timeout)
        self._logout_timer = QTimer(self)
        self._logout_timer.setSingleShot(True)
        self._logout_timer.timeout.connect(self._on_logout_timeout)

        root = QVBoxLayout(self)
        root.addWidget(self.session_bar)
        root.addWidget(self.stack)
        self.resize(440, 240)

        self._bus = _ResultBus(self)
        self._bus.arrived.connect(self.present)
        self._bus.inbound.connect(self.present_inbound)
        self._bus.session.connect(self._set_session_status)
        self._bus.console.connect(self._on_console)

    @property
    def session(self) -> OperatorSession | None:
        """Open registry socket after login. The next page sends on it."""
        client = self._client
        if self.logged_in and client is not None and client.ws is not None:
            return client
        return None

    def submit(self) -> None:
        if self.logged_in or not self.login_button.isEnabled():
            return
        robot_id = self.id_edit.text().strip()
        password = self.password_edit.text()
        if robot_id == "" or password == "":
            self.status_label.setText(message_for("empty"))
            return
        if not self._ensure_client():
            self.status_label.setText(message_for("unreachable"))
            return
        self.login_button.setEnabled(False)
        self.status_label.setText("Logging in...")
        assert self._loop is not None and self._client is not None
        future = asyncio.run_coroutine_threadsafe(
            self._client.login(self.registry_url, robot_id, password),
            self._loop,
        )
        future.add_done_callback(self._on_future)

    def present(self, result: LoginResult) -> None:
        if self._closing:
            return
        if result.ok:
            self.logged_in = True
            self._returned_to_login = False
            self._logout_busy = False
            self.hostname = result.hostname
            self.hostname_label.setText(result.hostname)
            self.waiting_label.setText("Waiting for the robot.")
            self.password_edit.clear()
            self.session_bar.show()
            self.stack.setCurrentWidget(self.waiting_page)
            self._start_reader()
            return
        self.logged_in = False
        self.login_button.setEnabled(True)
        self.status_label.setText(message_for(result.code))
        self.stack.setCurrentWidget(self.form_page)

    def show_inventory(
        self, inventory: dict[str, Any], operator_nic: str | None = None
    ) -> None:
        """Show Link, the robot NICs, arm, and video. Nothing is spawned."""
        if not self.logged_in or self._closing or not isinstance(inventory, dict):
            return
        nic = self._operator_nic if operator_nic is None else operator_nic
        if nic is None:
            nic = local_default_nic()
        self._inventory = dict(inventory)
        if self.config_page is None:
            self.config_page = ConfigPage()
            self.config_page.review_button.clicked.connect(self.review)
            self.config_page.start_button.clicked.connect(self.start_session)
            self.stack.addWidget(self.config_page)
        self.config_page.start_button.setEnabled(True)
        self.config_page.apply(inventory, nic, self.hostname)
        self.session_bar.show()
        self.stack.setCurrentWidget(self.config_page)
        self.resize(760, 520)

    def present_inbound(self, msg: object) -> None:
        if self._closing or not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "logged_out":
            self._return_to_login()
            return
        if kind == "error" and msg.get("code") == "offline" and self._awaiting_stopped:
            self._robot_unconfirmed = True
            self._awaiting_stopped = False
            self._stop_timer.stop()
            self._set_session_status(_UNCONFIRMED)
            if self._close_requested:
                self._release_stop_wait(False)
                return
            self._finish_after_stop()
            return
        if kind == "inventory":
            self.show_inventory(msg)
            return
        if kind == "status":
            self._on_status(msg)
            return
        page = self.config_page
        if page is None:
            return
        if kind == "config_ok" or (
            kind == "error" and msg.get("code") in {"bad_config", "unreachable"}
        ):
            page.set_status(review_text(msg))
            if kind == "config_ok":
                self._accepted_config = page.current_config()
            elif msg.get("code") == "bad_config":
                self._accepted_config = None

    def review(self) -> None:
        """Send the current choice. This does not start a process."""
        page = self.config_page
        if page is None or self._closing:
            return
        message = page.current_config()
        client = self._client
        loop = self._loop
        if client is None or loop is None or not loop.is_running():
            page.set_status("Cannot reach the registry.")
            return
        page.set_status("Sending...")
        future = asyncio.run_coroutine_threadsafe(client.send_json(message), loop)
        future.add_done_callback(self._on_review_sent)

    def start_session(self) -> None:
        """Send ``start``. The operator half waits for the robot's ready."""
        page = self.config_page
        if (
            page is None
            or self._closing
            or self._close_requested
            or self._start_busy
            or self._stop_busy
            or self._logout_busy
            or self._operator_started
        ):
            return
        accepted = self._accepted_config
        if accepted is not None and _choice_tuple(page.current_config()) != _choice_tuple(
            accepted
        ):
            self._set_session_status("Review the changed config before Start.")
            return
        try:
            camera_page_of(self._inventory)
        except PlanError as exc:
            self._set_session_status(exc.detail)
            return
        if not dry_run_enabled() and not webengine_installed():
            self._set_session_status("Qt WebEngine is not installed.")
            return
        if not self._try_send({"v": 1, "type": "start"}):
            self._set_session_status("Cannot reach the registry.")
            return
        self._awaiting_ready = True
        self._start_busy = True
        page.start_button.setEnabled(False)
        self._set_session_status("starting")

    def stop_session(self) -> None:
        """Stop the operator stack, then the robot, and show the config page."""
        if (
            self._closing
            or self._close_requested
            or self._logout_busy
            or not self.logged_in
        ):
            return
        if self._stop_busy:
            self._after_stop = "config"
            return
        self._begin_stop("config")

    def logout_session(self) -> None:
        """Stop both sides, detach this operator, and return to login."""
        if self._closing or self._close_requested or not self.logged_in:
            return
        if self._logout_busy:
            return
        if self._stop_busy:
            self._after_stop = "logout"
            return
        self._begin_stop("logout")

    def _begin_stop(self, after: str) -> None:
        self._stop_busy = True
        self._stop_finished = False
        self._after_stop = after
        self._robot_unconfirmed = False
        self._awaiting_ready = False
        self._allow_console = False
        page = self.config_page
        if page is not None:
            page.start_button.setEnabled(False)
        self._set_session_status("stopping")
        try:
            self._request_local_stop()
        except Exception as exc:
            print(f"operator stop failed: {exc}", file=sys.stderr)
        if self._signal_robot_stop():
            self._stop_timer.start(STOP_CONFIRM_MS)
            return
        self._finish_after_stop()

    def _signal_robot_stop(self) -> bool:
        """Send ``stop`` once. The operator stack has already been stopped."""
        if self._stop_sent:
            return self._awaiting_stopped
        if self._try_send({"v": 1, "type": "stop"}):
            self._stop_sent = True
            self._awaiting_stopped = True
            return True
        self._set_session_status("Cannot reach the registry.")
        return False

    def _on_stop_timeout(self) -> None:
        if self._close_requested or not self._awaiting_stopped:
            return
        self._awaiting_stopped = False
        self._robot_unconfirmed = True
        self._set_session_status(_UNCONFIRMED)
        self._finish_after_stop()

    def _finish_after_stop(self) -> None:
        if self._stop_finished or self._close_requested:
            return
        self._stop_finished = True
        self._stop_busy = False
        self._awaiting_stopped = False
        self._stop_timer.stop()
        self._stop_sent = False
        after = self._after_stop
        self._after_stop = ""
        if after == "logout":
            self._begin_logout()
            return
        self._show_config_again()

    def _show_config_again(self) -> None:
        """Hide the console. Start can use the config already accepted."""
        self._operator_started = False
        self._start_busy = False
        self._awaiting_ready = False
        self._allow_console = False
        page = self.config_page
        if page is not None:
            page.start_button.setEnabled(True)
            self.stack.setCurrentWidget(page)
        if self.logged_in:
            self.session_bar.show()

    def _begin_logout(self) -> None:
        self._logout_busy = True
        if not self._try_send({"v": 1, "type": "logout"}):
            self._return_to_login()
            return
        self._awaiting_logout = True
        self._logout_timer.start(LOGOUT_CONFIRM_MS)

    def _on_logout_timeout(self) -> None:
        if self._awaiting_logout and not self._close_requested:
            self._return_to_login()

    def _return_to_login(self) -> None:
        if self._returned_to_login or self._close_requested:
            return
        self._returned_to_login = True
        self._awaiting_logout = False
        self._logout_busy = False
        self._stop_busy = False
        self._logout_timer.stop()
        self._stop_timer.stop()
        self._close_session_socket()
        self.logged_in = False
        self._accepted_config = None
        self._inventory = None
        self._operator_started = False
        self._start_busy = False
        self._awaiting_ready = False
        self._allow_console = False
        self._reader_started = False
        self.session_bar.hide()
        page = self.config_page
        if page is not None:
            page.start_button.setEnabled(False)
        self.login_button.setEnabled(True)
        self.stack.setCurrentWidget(self.form_page)
        if self._robot_unconfirmed:
            self.status_label.setText("Logged out. " + _UNCONFIRMED)
        else:
            self.status_label.setText("Logged out.")
        self.resize(440, 240)

    def _close_session_socket(self) -> None:
        loop = self._loop
        client = self._client
        if loop is None or client is None or not loop.is_running():
            self._cancel_reader()
            return
        loop.call_soon_threadsafe(self._cancel_reader)
        closing = asyncio.run_coroutine_threadsafe(client.close(), loop)
        try:
            closing.result(timeout=2)
        except Exception:
            pass

    def _loop_is_running(self) -> bool:
        loop = self._loop
        return loop is not None and loop.is_running()

    def _release_stop_wait(self, ok: bool) -> None:
        hook = self._stop_wait_hook
        if hook is not None:
            hook(ok)

    def _wait_for_stopped(self, timeout_s: float) -> bool:
        """Pump Qt events until the robot reports stopped, or the brief wait ends."""
        if not self._loop_is_running():
            return False
        waiter = QEventLoop()
        result = {"ok": False}

        def finish(ok: bool) -> None:
            result["ok"] = ok
            if waiter.isRunning():
                waiter.quit()

        self._stop_wait_hook = finish
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: finish(False))
        timer.start(int(timeout_s * 1000))
        waiter.exec_()
        timer.stop()
        self._stop_wait_hook = None
        return bool(result["ok"])

    def _on_status(self, msg: dict[str, Any]) -> None:
        phase = msg.get("phase")
        detail = msg.get("detail")
        phase_text = phase if isinstance(phase, str) else ""
        detail_text = detail if isinstance(detail, str) else ""
        shown = detail_text if detail_text else phase_text
        if shown:
            self._set_session_status(shown)
        if phase_text == "stopped" and self._awaiting_stopped:
            self._awaiting_stopped = False
            self._stop_timer.stop()
            self._start_busy = False
            if self._close_requested:
                self._release_stop_wait(True)
                return
            self._finish_after_stop()
            return
        page = self.config_page
        if phase_text in {"error", "stopped", "ready"}:
            self._start_busy = False
            if page is not None:
                page.start_button.setEnabled(True)
        if phase_text in {"error", "stopped"}:
            self._awaiting_ready = False
        if phase_text != "ready" or dry_run_enabled() or not self._awaiting_ready:
            if phase_text == "ready":
                self._awaiting_ready = False
            return
        self._awaiting_ready = False
        self._begin_operator()

    def _begin_operator(self) -> None:
        if self._operator_started or self._closing or dry_run_enabled():
            return
        page = self.config_page
        config = self._accepted_config
        if config is None and page is not None:
            config = page.current_config()
        decision = decide_operator(
            config,
            self._inventory or {},
            self._root(),
            "ready",
            dry_run=False,
            bind_ip=default_route_ipv4(),
        )
        if decision.commands is None:
            if decision.detail:
                self._set_session_status(decision.detail)
            return
        self._allow_console = True
        self._operator_started = True
        self._start_thread = threading.Thread(
            target=self._run_commands,
            args=(decision,),
            name="operator-start",
            daemon=True,
        )
        self._start_thread.start()

    def _run_commands(self, decision: OperatorDecision) -> None:
        if (
            dry_run_enabled()
            or self._closing
            or not self._allow_console
            or decision.commands is None
        ):
            return
        try:
            from packaging.supervisor import operator_exec

            root = self._root()
            for command in decision.commands:
                operator_exec.execute_command(command, root)
        except Exception:
            if self._closing:
                return
            try:
                self._bus.session.emit("operator start failed")
            except RuntimeError:
                return
            return
        if dry_run_enabled() or self._closing or not self._allow_console:
            return
        try:
            self._bus.console.emit(decision.console_url)
        except RuntimeError:
            return

    def _on_console(self, url: str) -> None:
        if self._closing or dry_run_enabled() or not self._allow_console:
            return
        self._show_console(url)

    def _show_console(self, url: str) -> None:
        """Load the local console. Dry-run returns before WebEngine is imported."""
        if dry_run_enabled() or self._closing:
            return
        if not url.startswith("http://127.0.0.1:8090/"):
            return
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self._set_session_status("Qt WebEngine is not installed.")
            return
        view = self.console_view
        if view is None:
            view = QWebEngineView()
            view.setObjectName("console")
            self.console_view = view
            self.stack.addWidget(view)
        view.setUrl(QUrl(url))
        self.stack.setCurrentWidget(view)
        self.resize(1100, 720)

    def _request_local_stop(self) -> None:
        self._awaiting_ready = False
        self._allow_console = False
        live = self._operator_started
        try:
            stop_operator_local(self._root(), live=live)
        finally:
            if live:
                self._operator_started = False

    def _set_session_status(self, text: str) -> None:
        if self._closing:
            return
        self.bar_status.setText(text)
        page = self.config_page
        if page is not None:
            page.set_session_status(text)

    def _root(self) -> Path:
        if self._root_override is not None:
            return self._root_override
        return repo_root()

    def _try_send(self, message: dict[str, Any]) -> bool:
        client = self._client
        loop = self._loop
        if client is None or loop is None or not loop.is_running():
            return False
        future = asyncio.run_coroutine_threadsafe(client.send_json(message), loop)
        future.add_done_callback(self._on_send_done)
        return True

    def _on_send_done(self, future: asyncio.Future[Any]) -> None:
        if self._closing:
            return
        try:
            future.result()
        except Exception:
            if self._closing:
                return
            try:
                self._bus.session.emit("Cannot reach the registry.")
            except RuntimeError:
                return

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        self._closing = True
        self._stop_timer.stop()
        self._logout_timer.stop()
        loop = self._loop
        client = self._client
        thread = self._thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._cancel_reader)
            if client is not None:
                closing = asyncio.run_coroutine_threadsafe(client.close(), loop)
                try:
                    closing.result(timeout=2)
                except Exception:
                    pass
            loop.call_soon_threadsafe(loop.stop)
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=2)

    def closeEvent(self, event: Any) -> None:
        """Stop the operator stack, then the robot, then exit. Not a logout."""
        if not self._close_requested:
            self._close_requested = True
            try:
                self._request_local_stop()
            except Exception as exc:
                print(f"operator stop failed: {exc}", file=sys.stderr)
            self._signal_robot_stop()
            if self._awaiting_stopped and self._loop_is_running():
                self._wait_for_stopped(CLOSE_CONFIRM_MS / 1000.0)
            self._awaiting_stopped = False
        self.shutdown()
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        super().closeEvent(event)

    def _ensure_client(self) -> bool:
        if self._thread is not None and self._loop is not None:
            return True
        self._client = OperatorSession()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name="operator-registry",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(5) or self._loop is None:
            return False
        return True

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def _start_reader(self) -> None:
        if self._reader_started:
            return
        loop = self._loop
        if loop is None or self._client is None or not loop.is_running():
            return
        self._reader_started = True
        asyncio.run_coroutine_threadsafe(self._reader(), loop)

    def _cancel_reader(self) -> None:
        task = self._reader_task
        if task is not None and not task.done():
            task.cancel()

    async def _reader(self) -> None:
        client = self._client
        if client is None:
            return
        self._reader_task = asyncio.current_task()
        try:
            while not self._closing:
                msg = await client.next_message()
                if msg is None or self._closing:
                    return
                try:
                    self._bus.inbound.emit(msg)
                except RuntimeError:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        finally:
            self._reader_task = None

    def _on_review_sent(self, future: asyncio.Future[Any]) -> None:
        if self._closing:
            return
        try:
            future.result()
        except Exception:
            if self._closing:
                return
            try:
                self._bus.inbound.emit(
                    {
                        "v": 1,
                        "type": "error",
                        "code": "unreachable",
                        "detail": "Cannot reach the registry.",
                    }
                )
            except RuntimeError:
                return

    def _on_future(self, future: asyncio.Future[LoginResult]) -> None:
        if self._closing:
            return
        try:
            result = future.result()
        except Exception:
            result = LoginResult("unreachable")
        if self._closing:
            return
        try:
            self._bus.arrived.emit(result)
        except RuntimeError:
            return
