"""Testy single-instance guard (QLockFile + IPC raise signal)."""
from __future__ import annotations

from simple_deck.core.single_instance import (
    SingleInstanceCoordinator,
    SOCKET_NAME,
    acquire_single_instance,
    notify_existing_instance,
)


def _patch_settings_dir(monkeypatch, tmp_path):
    """Przekieruj settings_dir() na tmp_path (socket + lock w piaskownicy).

    Patchujemy OBA moduły: ``settings`` (gdzie funkcja jest zdefiniowana) oraz
    ``single_instance`` (który ją zaimportował przez ``from`` — posiada własne
    wiązanie, którego zwykły monkeypatch na module źródłowym NIE nadpisze).
    """
    import simple_deck.core.settings as settings_mod
    import simple_deck.core.single_instance as si_mod
    monkeypatch.setattr(settings_mod, "settings_dir", lambda: tmp_path)
    monkeypatch.setattr(si_mod, "settings_dir", lambda: tmp_path)


class TestSingleInstance:
    def test_first_instance_acquires_lock(self, tmp_path, monkeypatch):
        """Pierwsza instancja nabywa blokadę."""
        _patch_settings_dir(monkeypatch, tmp_path)
        lock = acquire_single_instance()
        assert lock is not None
        lock.unlock()

    def test_second_instance_fails(self, tmp_path, monkeypatch):
        """Druga instancja nie nabywa blokady (zwraca None)."""
        _patch_settings_dir(monkeypatch, tmp_path)
        lock1 = acquire_single_instance()
        assert lock1 is not None
        lock2 = acquire_single_instance()
        assert lock2 is None
        lock1.unlock()


class TestSingleInstanceIPC:
    """V8: IPC serwer + klient do przywoływania ukrytego okna."""

    def test_coordinator_starts_listening(self, qapp, tmp_path, monkeypatch):
        """Coordinator buduje działający serwer IPC."""
        _patch_settings_dir(monkeypatch, tmp_path)
        lock = acquire_single_instance()
        assert lock is not None
        coord = SingleInstanceCoordinator(lock=lock, parent=qapp)
        try:
            assert coord._server.isListening()
            assert coord._server.serverName() == str(tmp_path / SOCKET_NAME)
        finally:
            coord.cleanup()
            lock.unlock()

    def test_second_instance_emits_raise_requested(self, qapp, tmp_path,
                                                    monkeypatch, qtbot):
        """Druga instancja wysyła ping → coordinator emituje raise_requested."""
        _patch_settings_dir(monkeypatch, tmp_path)
        lock = acquire_single_instance()
        assert lock is not None
        coord = SingleInstanceCoordinator(lock=lock, parent=qapp)
        try:
            with qtbot.waitSignal(coord.raise_requested, timeout=2000):
                ok = notify_existing_instance()
            assert ok, "notify_existing_instance powinno połączyć się z serwerem"
        finally:
            coord.cleanup()
            lock.unlock()

    def test_notify_returns_false_when_no_server(self, qapp, tmp_path,
                                                  monkeypatch):
        """Bez działającego serwera notify_existing_instance zwraca False."""
        _patch_settings_dir(monkeypatch, tmp_path)
        # Żaden coordinator nie wystartował → nie ma serwera na tej ścieżce.
        ok = notify_existing_instance()
        assert ok is False

    def test_stale_socket_cleaned_up(self, qapp, tmp_path, monkeypatch):
        """Stary plik gniazda (po crashu) nie blokuje startu coordinatora."""
        _patch_settings_dir(monkeypatch, tmp_path)
        socket_file = tmp_path / SOCKET_NAME
        # Symuluj martwy plik gniazda (np. po SIGKILL poprzedniego procesu).
        socket_file.write_bytes(b"\x00stale")
        assert socket_file.exists()

        lock = acquire_single_instance()
        assert lock is not None
        coord = SingleInstanceCoordinator(lock=lock, parent=qapp)
        try:
            # removeServer powinien wyczyścić stary plik i listen() ma przejść.
            assert coord._server.isListening(), (
                "Coordinator powinien nasłuchiwać mimo pozostawionego pliku socketu")
        finally:
            coord.cleanup()
            lock.unlock()

    def test_multiple_pings_each_emit(self, qapp, tmp_path, monkeypatch, qtbot):
        """Każdy ping (np. user kliknął 2× w menu) emituje osobny raise_requested."""
        _patch_settings_dir(monkeypatch, tmp_path)
        lock = acquire_single_instance()
        assert lock is not None
        coord = SingleInstanceCoordinator(lock=lock, parent=qapp)
        emitted = []
        coord.raise_requested.connect(lambda: emitted.append(1))
        try:
            for _ in range(3):
                with qtbot.waitSignal(coord.raise_requested, timeout=2000):
                    assert notify_existing_instance()
            # Process any stragglers.
            qapp.processEvents()
            assert len(emitted) >= 3
        finally:
            coord.cleanup()
            lock.unlock()
