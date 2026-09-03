"""ProfileSwitcher - przełącznik profili w nagłówku okna.

Combo z listą profili + przyciski operacji CRUD (nowy/zmień nazwę/duplikuj/
usuń/import/eksport). Komponent kompaktny - pasuje do header'a obok status
chip'a. Operacje wołają ``ProfileManager`` i odświeżają listę.

Zanim ten widget istniał, w UI nie było w ogóle możliwości przełączenia
aktywnego profilu (MainWindow po prostu brał aktywny z ProfileManager'a).
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout,
                                QInputDialog, QMessageBox, QPushButton,
                                QWidget)

from ...core.profile_manager import ProfileManager
from .icon import IconLabel

log = logging.getLogger(__name__)


class ProfileSwitcher(QWidget):
    """Przełącznik profili z operacjami zarządzania."""

    active_changed = Signal(str)  # nazwa nowo aktywnego profilu

    def __init__(self, profile_mgr: ProfileManager, accent: str = "#2DD4FF",
                 parent=None):
        super().__init__(parent)
        self._mgr = profile_mgr
        self._accent = accent

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lbl = IconLabel("copy", color=self._accent, size=16)
        lay.addWidget(lbl)
        self._icon = lbl

        self._combo = QComboBox()
        self._combo.setMinimumWidth(160)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        lay.addWidget(self._combo)

        # Przyciski operacji (małe, ikonowe)
        for icon_name, tip, slot in [
            ("plus", "Nowy profil", self._on_new),
            ("pencil", "Zmień nazwę", self._on_rename),
            ("copy", "Duplikuj", self._on_duplicate),
            ("trash", "Usuń profil", self._on_delete),
            ("download", "Importuj", self._on_import),
            ("upload", "Eksportuj", self._on_export),
        ]:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setIcon(_pixmap_icon(icon_name))
            btn.setStyleSheet(
                "QPushButton { background: rgba(40,44,64,180); border: 1px solid "
                "rgba(255,255,255,18); border-radius: 8px; }"
                "QPushButton:hover { background: rgba(45,212,255,40); }"
            )
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        # Odśwież gdy lista profili się zmieni (create/delete/rename/import)
        self._mgr.profile_list_changed.connect(self._reload)
        self._mgr.active_profile_changed.connect(self._on_active_changed)
        self._reload()

    def set_accent(self, accent: str) -> None:
        self._accent = accent
        self._icon.set_color(accent)

    # ---- Odświeżanie combo ----
    def _reload(self) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        names = self._mgr.list_profiles()
        self._combo.addItems(names)
        active = self._mgr.active
        active_name = active.name if active is not None else (names[0] if names else "")
        if active_name:
            i = self._combo.findText(active_name)
            if i >= 0:
                self._combo.setCurrentIndex(i)
        self._combo.blockSignals(False)

    def _on_active_changed(self, profile) -> None:
        if profile is None:
            return
        self._combo.blockSignals(True)
        i = self._combo.findText(profile.name)
        if i >= 0:
            self._combo.setCurrentIndex(i)
        self._combo.blockSignals(False)

    def _on_combo_changed(self, idx: int) -> None:
        name = self._combo.itemText(idx)
        if name and (self._mgr.active is None or self._mgr.active.name != name):
            if self._mgr.set_active(name):
                self.active_changed.emit(name)

    # ---- Operacje CRUD ----
    def _current_name(self) -> str:
        return self._combo.currentText().strip()

    def _ask_name(self, title: str, default: str = "") -> Optional[str]:
        text, ok = QInputDialog.getText(self, title, "Nazwa profilu:", text=default)
        if not ok:
            return None
        text = text.strip()
        return text or None

    def _on_new(self) -> None:
        name = self._ask_name("Nowy profil")
        if not name:
            return
        if self._mgr.create(name) is None:
            QMessageBox.warning(self, "Simple Deck",
                                f"Profil '{name}' już istnieje.")

    def _on_rename(self) -> None:
        cur = self._current_name()
        if not cur:
            return
        name = self._ask_name("Zmień nazwę profilu", default=cur)
        if not name or name == cur:
            return
        if not self._mgr.rename(cur, name):
            QMessageBox.warning(self, "Simple Deck",
                                "Nie udało się zmienić nazwy (istnieje?)")

    def _on_duplicate(self) -> None:
        cur = self._current_name()
        if not cur:
            return
        if self._mgr.duplicate(cur) is None:
            QMessageBox.warning(self, "Simple Deck", "Nie udało się zduplikować profil.")

    def _on_delete(self) -> None:
        cur = self._current_name()
        if not cur:
            return
        if len(self._mgr.list_profiles()) <= 1:
            QMessageBox.information(self, "Simple Deck",
                                    "Nie można usunąć jedynego profilu.")
            return
        btn = QMessageBox.question(
            self, "Usuń profil",
            f"Czy na pewno usunąć profil '{cur}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if btn == QMessageBox.Yes:
            if not self._mgr.delete(cur):
                QMessageBox.warning(self, "Simple Deck", "Nie udało się usunąć profil.")

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Importuj profil", "", "Profile JSON (*.json)")
        if not path:
            return
        from pathlib import Path
        if self._mgr.import_profile(Path(path)) is None:
            QMessageBox.warning(self, "Simple Deck", "Import nie powiódł się.")

    def _on_export(self) -> None:
        cur = self._current_name()
        if not cur:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Eksportuj profil", f"{cur}.json", "Profile JSON (*.json)")
        if not path:
            return
        from pathlib import Path
        if not self._mgr.export_profile(cur, Path(path)):
            QMessageBox.warning(self, "Simple Deck", "Eksport nie powiódł się.")


def _pixmap_icon(name: str):
    """QIcon z ikony SVG (dla QPushButton.setIcon)."""
    from PySide6.QtGui import QIcon
    from .icon import icon_pixmap
    return QIcon(icon_pixmap(name, color="#A5ABC0", size=18))
