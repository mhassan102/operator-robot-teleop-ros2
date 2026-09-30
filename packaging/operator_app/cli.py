"""Operator window entry point. ``--registry`` selects the signalling URL."""

from __future__ import annotations

import argparse
import sys

from packaging.operator_app.login import DEFAULT_REGISTRY


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m packaging.operator_app",
        description="Operator login, config, and console",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"registry WebSocket URL (default {DEFAULT_REGISTRY})",
    )
    return parser.parse_args(argv)


def prepare_qt() -> None:
    """Import WebEngine before a ``QApplication`` exists.

    Qt raises ``ImportError`` if ``QtWebEngineWidgets`` is imported after
    the application object. ``AA_ShareOpenGLContexts`` has to be set first.
    A missing WebEngine package is left for the Start button to report.
    """
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    try:
        import PyQt5.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        return


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        prepare_qt()
    except ImportError:
        print(
            "PyQt5 is not installed for this Python.\n"
            "Install it with: sudo apt install python3-pyqt5",
            file=sys.stderr,
        )
        return 1
    from PyQt5.QtWidgets import QApplication

    from packaging.operator_app.window import LoginWindow

    app = QApplication(["teleop-operator"])
    window = LoginWindow(args.registry)
    window.show()
    return app.exec_()
