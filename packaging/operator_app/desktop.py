"""Desktop window for the operator console.

The pages stay on http://127.0.0.1:8090/. This process only shows them
and returns when the window closes. It does not start mlink.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

_WINDOW_TITLE = "Teleop operator"
_CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def backend_name() -> str:
    """``qt`` when WebEngine can be imported, else ``chrome``, else empty."""
    if _qt_available():
        return "qt"
    if _chrome_bin():
        return "chrome"
    return ""


def show_console(url: str, until: threading.Event | None = None) -> None:
    """Block until the desktop window closes or ``until`` is set."""
    if _qt_available():
        try:
            _show_qt(url, until)
            return
        except ImportError:
            pass
    chrome = _chrome_bin()
    if chrome:
        _show_chrome(url, chrome, until)
        return
    raise RuntimeError("the operator window needs PyQt5 WebEngine or Google Chrome")


def chrome_command(binary: str, url: str, profile: str) -> list[str]:
    """App window with its own profile, so an open Chrome is left alone."""
    return [
        binary,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1100,720",
    ]


def _qt_available() -> bool:
    import importlib.util

    return (
        importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is not None
        and importlib.util.find_spec("PyQt5.QtWidgets") is not None
    )


def _chrome_bin() -> str | None:
    for name in _CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _show_qt(url: str, until: threading.Event | None) -> None:
    from PyQt5.QtCore import Qt, QTimer, QUrl
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    from PyQt5.QtWidgets import QApplication, QMainWindow

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance()
    if app is None:
        app = QApplication(["teleop-operator"])
    app.setApplicationName(_WINDOW_TITLE)
    app.setApplicationDisplayName(_WINDOW_TITLE)
    window = QMainWindow()
    window.setWindowTitle(_WINDOW_TITLE)
    view = QWebEngineView(window)
    view.load(QUrl(url))
    window.setCentralWidget(view)
    window.resize(1100, 720)
    window.show()
    window.raise_()
    window.activateWindow()

    def _pulse() -> None:
        if until is not None and until.is_set():
            app.quit()

    timer = QTimer()
    timer.timeout.connect(_pulse)
    timer.start(200)
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
    signal.signal(signal.SIGTERM, lambda *_args: app.quit())
    app.exec_()


def _show_chrome(url: str, binary: str, until: threading.Event | None) -> None:
    profile = tempfile.mkdtemp(prefix="teleop-operator-")
    proc = subprocess.Popen(
        chrome_command(binary, url, profile),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        while proc.poll() is None:
            if until is not None and until.is_set():
                _stop_process(proc)
                break
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            _stop_process(proc)
        shutil.rmtree(profile, ignore_errors=True)


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait(timeout=3)
