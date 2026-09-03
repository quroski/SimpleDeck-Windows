"""Testy AppPicker - edytowalne combo, free-text, brak reset'u."""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.ui.widgets.app_picker import AppPicker


def _backend(apps):
    b = MagicMock()
    b.list_apps.return_value = list(apps)
    return b


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
