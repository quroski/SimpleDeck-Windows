"""Testy AppPicker - edytowalne combo, free-text, brak reset'u."""
from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal

import simple_deck.ui.widgets.app_picker as app_picker_mod
from simple_deck.ui.widgets.app_picker import AppPicker


def _backend(apps):
    b = MagicMock()
    b.list_apps.return_value = list(apps)
    return b


class _StubCache(QObject):
    """Zastępuje globalny AppListCache (bez timera / singletona)."""
    apps_changed = Signal(list)

    def __init__(self, apps: list[str]) -> None:
        super().__init__()
        self._apps = list(apps)

    def cached_apps(self) -> list[str]:
        return list(self._apps)


def _picker_with_cache(monkeypatch, cached_apps: list[str]) -> AppPicker:
    """Picker z podmienionym get_app_list_cache — hint testy nie dotykają
    singletona cache (který ignoruje późniejsze backendy)."""
    stub = _StubCache(cached_apps)
    monkeypatch.setattr(app_picker_mod, "get_app_list_cache",
                        lambda backend=None: stub)
    return AppPicker(audio_backend=_backend([]))


class TestAppPicker:
    def test_system_target_roundtrip(self, qapp):
        ap = AppPicker(audio_backend=_backend(["discord"]))
        ap.set_target("")
        assert ap.get_target() == ""

    def test_free_text_not_running_persists(self, qapp):
        """Skrócona wersja wymogu 'dowolna aplikacja': cel wpisany ręcznie,
        nieobecny w uruchomionych, jest zachowany przez refresh."""
        ap = AppPicker(audio_backend=_backend(["discord", "firefox"]),
                       recent_apps=[])
        ap.set_target("foobar2000")     # nie działa, nie w recent
        assert ap.get_target() == "foobar2000"
        ap.refresh()                    # simula 5s refresh
        assert ap.get_target() == "foobar2000"   # NIE zresetowany!

    def test_running_app_selected(self, qapp):
        ap = AppPicker(audio_backend=_backend(["discord", "firefox"]))
        ap.set_target("discord")
        assert ap.get_target() == "discord"

    def test_recent_app_as_suggestion(self, qapp):
        ap = AppPicker(audio_backend=_backend(["discord"]),
                       recent_apps=["vlc", "discord"])
        # vlc nie działa ale jest w recent → sugestia
        ap.set_target("vlc")
        assert ap.get_target() == "vlc"

    def test_target_lost_when_set_to_system(self, qapp):
        ap = AppPicker(audio_backend=_backend(["discord"]))
        ap.set_target("discord")
        ap.set_target("")
        assert ap.get_target() == ""

    def test_set_recent_apps_refreshes(self, qapp):
        ap = AppPicker(audio_backend=_backend([]))
        ap.set_recent_apps(["spotify"])
        ap.set_target("spotify")
        assert ap.get_target() == "spotify"


class TestAppPickerHint:
    """V9: dopasowanie hintu przez normalize_proc_name (obie strony)."""

    def test_hint_hidden_for_ext_variant_of_running_app(self, qapp, monkeypatch):
        ap = _picker_with_cache(monkeypatch, ["spotify"])
        ap.set_target("spotify.exe")
        assert ap.get_target() == "spotify.exe"
        assert ap._hint.isHidden()

    def test_hint_hidden_for_bare_name_when_cached_has_ext(self, qapp, monkeypatch):
        ap = _picker_with_cache(monkeypatch, ["Spotify.exe"])
        ap.set_target("spotify")
        assert ap._hint.isHidden()

    def test_hint_shown_for_unknown_app(self, qapp, monkeypatch):
        ap = _picker_with_cache(monkeypatch, ["spotify"])
        ap.set_target("foobar2000")
        assert not ap._hint.isHidden()

    def test_hint_hidden_after_target_starts_running(self, qapp, monkeypatch):
        ap = _picker_with_cache(monkeypatch, [])
        ap.set_target("discord")
        assert not ap._hint.isHidden()
        # Aplikacja wystartowała — cache emituje nową listę
        ap._on_apps_changed(["discord.exe"])
        assert ap._hint.isHidden()
