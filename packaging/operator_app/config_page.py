"""Config page: Link, two robot NICs, arm, video, and Review.

Review is the only action. Interface 2 may be chosen. This page does
not start mlink, the camera, or the arm, and it does not bond Interface 2.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from packaging.operator_app.config import (
    LINK_HELP,
    Choice,
    arm_choices,
    config_message,
    interface1_choices,
    interface2_choices,
    operator_network_text,
    preferred_arm,
    preferred_video,
    video_choices,
)


class ConfigPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("config_page")
        self._applied = False

        self.hostname_label = QLabel("")
        self.hostname_label.setObjectName("config_hostname")
        self.hostname_label.setTextFormat(Qt.PlainText)

        self.link_tailscale = QRadioButton("Tailscale")
        self.link_tailscale.setObjectName("link_tailscale")
        self.link_turn = QRadioButton("TURN")
        self.link_turn.setObjectName("link_turn")
        self.link_tailscale.setChecked(True)

        self.link_help = QLabel(LINK_HELP)
        self.link_help.setObjectName("link_help")
        self.link_help.setWordWrap(True)
        self.link_help.setTextFormat(Qt.PlainText)

        self.iface1 = QComboBox()
        self.iface1.setObjectName("iface1")
        self.iface2 = QComboBox()
        self.iface2.setObjectName("iface2")
        self.arm = QComboBox()
        self.arm.setObjectName("arm")
        self.video = QComboBox()
        self.video.setObjectName("video")
        for combo in (self.iface1, self.iface2, self.arm, self.video):
            combo.setMinimumWidth(420)

        self.operator_network = QLabel(operator_network_text(""))
        self.operator_network.setObjectName("operator_network")
        self.operator_network.setTextFormat(Qt.PlainText)

        self.review_button = QPushButton("Review")
        self.review_button.setObjectName("review")
        self.review_status = QLabel("")
        self.review_status.setObjectName("review_status")
        self.review_status.setWordWrap(True)
        self.review_status.setTextFormat(Qt.PlainText)

        link_box = QWidget()
        link_layout = QVBoxLayout(link_box)
        link_layout.setContentsMargins(0, 0, 0, 0)
        radios = QHBoxLayout()
        radios.addWidget(self.link_tailscale)
        radios.addWidget(self.link_turn)
        radios.addStretch(1)
        link_layout.addLayout(radios)
        link_layout.addWidget(self.link_help)

        form = QFormLayout()
        form.addRow("Link", link_box)
        form.addRow("Interface 1", self.iface1)
        form.addRow("Interface 2", self.iface2)
        form.addRow("Arm", self.arm)
        form.addRow("Video", self.video)

        root = QVBoxLayout(self)
        caption = QLabel("Robot")
        caption.setTextFormat(Qt.PlainText)
        root.addWidget(caption)
        root.addWidget(self.hostname_label)
        root.addLayout(form)
        root.addWidget(self.operator_network)
        root.addWidget(self.review_button)
        root.addWidget(self.review_status)
        root.addStretch(1)

    def set_status(self, text: str) -> None:
        self.review_status.setText(text)

    def apply(
        self, inventory: Mapping[str, Any], operator_nic: str, hostname: str
    ) -> None:
        """Fill the dropdowns. A later inventory keeps a choice that is still listed."""
        keep = self._applied
        self.hostname_label.setText(hostname)
        self.operator_network.setText(operator_network_text(operator_nic))
        if not keep:
            self.link_tailscale.setChecked(True)
        iface1 = interface1_choices(inventory)
        preferred_iface = iface1[0].value if iface1 else None
        self._fill_strings(
            self.iface1,
            iface1,
            preferred_iface if isinstance(preferred_iface, str) else None,
            keep,
            fallback_first=True,
        )
        self._fill_iface2(interface2_choices(inventory), keep)
        self._fill_strings(
            self.arm,
            arm_choices(inventory),
            preferred_arm(inventory),
            keep,
            fallback_first=False,
        )
        self._fill_strings(
            self.video,
            video_choices(inventory),
            preferred_video(inventory),
            keep,
            fallback_first=False,
        )
        self._applied = True

    def current_config(self) -> dict[str, Any]:
        link = "turn" if self.link_turn.isChecked() else "tailscale"
        iface1 = self.iface1.currentData()
        if self.iface2.currentIndex() <= 0:
            iface2: str | None = None
        else:
            raw = self.iface2.currentData()
            iface2 = raw if isinstance(raw, str) else None
        arm = self.arm.currentData()
        video = self.video.currentData()
        return config_message(
            link,
            iface1 if isinstance(iface1, str) else "",
            iface2,
            arm if isinstance(arm, str) else "",
            video if isinstance(video, str) else "",
        )

    def _fill_strings(
        self,
        combo: QComboBox,
        choices: list[Choice],
        preferred: str | None,
        keep: bool,
        *,
        fallback_first: bool,
    ) -> None:
        previous = None
        if keep and combo.currentIndex() >= 0:
            data = combo.currentData()
            if isinstance(data, str):
                previous = data
        combo.blockSignals(True)
        combo.clear()
        for choice in choices:
            if isinstance(choice.value, str):
                combo.addItem(choice.label, choice.value)
        idx = -1
        if previous is not None:
            idx = combo.findData(previous)
        if idx < 0 and preferred is not None:
            idx = combo.findData(preferred)
        if idx < 0 and fallback_first and combo.count():
            idx = 0
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _fill_iface2(self, choices: list[Choice], keep: bool) -> None:
        # Index 0 is the None row and stores no item data. Qt uses a
        # null QVariant for "no data", so None cannot be stored itself.
        previous = None
        if keep and self.iface2.currentIndex() > 0:
            data = self.iface2.currentData()
            if isinstance(data, str):
                previous = data
        self.iface2.blockSignals(True)
        self.iface2.clear()
        for choice in choices:
            if choice.value is None:
                self.iface2.addItem(choice.label)
            else:
                self.iface2.addItem(choice.label, choice.value)
        idx = 0
        if previous is not None:
            found = self.iface2.findData(previous)
            if found >= 0:
                idx = found
        if self.iface2.count() == 0:
            idx = -1
        self.iface2.setCurrentIndex(idx)
        self.iface2.blockSignals(False)
