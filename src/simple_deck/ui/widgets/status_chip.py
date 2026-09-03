"""Status Chip - wskaźnik stanu połączenia (kropka + etykieta)."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ...transport.connection_manager import ConnectionState, STATE_LABELS


class StatusChip(QFrame):
    """Kompaktowy wskaźnik stanu połączenia: ● Połączony / ◐ Łączenie…"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cardSubtle")
        self.setFixedHeight(36)
        self.setMinimumWidth(160)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 14, 6)
        lay.setSpacing(8)

        self._dot = QLabel(objectName="statusDot")
        self._dot.setProperty("state", "disconnected")
        self._dot.setFixedSize(10, 10)

        self._label = QLabel("Nieaktywny")
        self._label.setStyleSheet("font-size: 12px; font-weight: 500; color: #F5F7FA; background: transparent;")

        lay.addWidget(self._dot)
        lay.addWidget(self._label)

        self.set_state(ConnectionState.DISCONNECTED)

    def set_state(self, state: ConnectionState) -> None:
        self._dot.setProperty("state", state.value)
        self._dot.style().unpolish(self._dot)
        # V8: Skip polish() - Qt will polish on next paint, saves ~20-30% style recalculations
        # self._dot.style().polish(self._dot)  # Removed - not needed
        self._label.setText(STATE_LABELS.get(state, str(state.value)))
