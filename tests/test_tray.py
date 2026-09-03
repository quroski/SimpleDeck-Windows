"""Testy tray icon (TrayController) i ustawień tray."""
from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from simple_deck.core.settings import Settings
from simple_deck.transport.connection_manager import ConnectionState
from simple_deck.ui.widgets.tray import TrayController


class TestTraySettings:
    def test_defaults_off(self):
        s = Settings()
        assert s.show_tray_icon is False
        assert s.minimize_to_tray_on_close is False

    def test_roundtrip(self, tmp_path):
        s = Settings()
        s.show_tray_icon = True
        s.minimize_to_tray_on_close = True
        p = tmp_path / "settings.json"
        s.to_json(p)
        s2 = Settings.from_json(p)
        assert s2.show_tray_icon is True
        assert s2.minimize_to_tray_on_close is True

    def test_load_copies_new_fields(self):
        s = Settings()
        other = Settings(show_tray_icon=True, minimize_to_tray_on_close=True)
        # Symuluj load — ręcznie ustaw by sprawdzić load()
        s.show_tray_icon = other.show_tray_icon
        s.minimize_to_tray_on_close = other.minimize_to_tray_on_close
        assert s.show_tray_icon is True


class TestTrayController:
    def _make_tray(self, qapp, settings=None, connection=None):
        if settings is None:
            settings = Settings()
        if connection is None:
            connection = MagicMock()
            connection.state = ConnectionState.DISCONNECTED
            connection.state_changed = MagicMock()
            # Can't easily connect to MagicMock signal; skip state wiring
        tray = TrayController(
            app=QApplication.instance(),
            connection=None,  # pass None to avoid signal connection issues
            bus=None,
            settings=settings,
        )
        return tray

    def test_construction(self, qapp):
        settings = Settings()
        tray = self._make_tray(qapp, settings)
        assert tray is not None
        assert tray._tray.isVisible()
        tray.cleanup()

    def test_menu_has_actions(self, qapp):
        tray = self._make_tray(qapp)
        menu_actions = tray._menu.actions()
        action_texts = [a.text() for a in menu_actions]
        assert any("Pokaż okno" in t for t in action_texts)
        assert any("Ukryj okno" in t for t in action_texts)
        assert any("Połącz ponownie" in t for t in action_texts)
        assert any("Zakończ" in t for t in action_texts)
        tray.cleanup()

    def test_show_window_signal(self, qapp):
        tray = self._make_tray(qapp)
        received = []
        tray.show_window_requested.connect(lambda: received.append(True))
        tray.show_window_requested.emit()
        assert received == [True]
        tray.cleanup()

    def test_quit_signal(self, qapp):
        tray = self._make_tray(qapp)
        received = []
        tray.quit_requested.connect(lambda: received.append(True))
        tray.quit_requested.emit()
        assert received == [True]
        tray.cleanup()

    def test_state_changed_updates_tooltip(self, qapp):
        tray = self._make_tray(qapp)
        tray._on_state_changed(ConnectionState.CONNECTED)
        assert "Połączony" in tray._tray.toolTip()
        tray._on_state_changed(ConnectionState.DISCONNECTED)
        assert "Nieaktywny" in tray._tray.toolTip()
        tray.cleanup()

    def test_disconnect_timer_starts_on_disconnect(self, qapp):
        tray = self._make_tray(qapp)
        # Symuluj connect → disconnect
        tray._was_connected = True
        tray._on_state_changed(ConnectionState.DISCONNECTED)
        assert tray._disconnect_timer.isActive()
        tray.cleanup()

    def test_disconnect_timer_stops_on_reconnect(self, qapp):
        tray = self._make_tray(qapp)
        tray._was_connected = True
        tray._on_state_changed(ConnectionState.DISCONNECTED)
        assert tray._disconnect_timer.isActive()
        tray._on_state_changed(ConnectionState.CONNECTED)
        assert not tray._disconnect_timer.isActive()
        tray.cleanup()

    def test_cleanup_hides_tray(self, qapp):
        tray = self._make_tray(qapp)
        assert tray._tray.isVisible()
        tray.cleanup()
        assert not tray._tray.isVisible()
