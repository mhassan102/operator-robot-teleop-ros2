"""PyQt5 operator window.

Login shows the robot hostname and waits for inventory. The config
page then sends the choice when Review is pressed. A refused login
stays on the form. Review does not start mlink, the camera, or the arm.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
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


class _ResultBus(QObject):
    arrived = pyqtSignal(object)
    inbound = pyqtSignal(object)


class LoginWindow(QWidget):
    def __init__(self, registry_url: str, operator_nic: str | None = None) -> None:
        super().__init__()
        self.registry_url = registry_url
        self.logged_in = False
        self.hostname = ""
        self._operator_nic = operator_nic
        self._client: OperatorSession | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._closing = False
        self._shut = False
        self._reader_started = False
        self._reader_task: asyncio.Task[None] | None = None
        self.config_page: ConfigPage | None = None

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
        root = QVBoxLayout(self)
        root.addWidget(self.stack)
        self.resize(440, 240)

        self._bus = _ResultBus(self)
        self._bus.arrived.connect(self.present)
        self._bus.inbound.connect(self.present_inbound)

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
            self.hostname = result.hostname
            self.hostname_label.setText(result.hostname)
            self.waiting_label.setText("Waiting for the robot.")
            self.password_edit.clear()
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
        if self.config_page is None:
            self.config_page = ConfigPage()
            self.config_page.review_button.clicked.connect(self.review)
            self.stack.addWidget(self.config_page)
        self.config_page.apply(inventory, nic, self.hostname)
        self.stack.setCurrentWidget(self.config_page)
        self.resize(760, 520)

    def present_inbound(self, msg: object) -> None:
        if self._closing or not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "inventory":
            self.show_inventory(msg)
            return
        page = self.config_page
        if page is None:
            return
        if kind == "config_ok" or (
            kind == "error" and msg.get("code") in {"bad_config", "unreachable"}
        ):
            page.set_status(review_text(msg))

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

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        self._closing = True
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
