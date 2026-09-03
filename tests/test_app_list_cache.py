"""Testy AppListCache — wspólny cache listy aplikacji audio."""
from __future__ import annotations

from unittest.mock import MagicMock

from simple_deck.core.app_list_cache import (AppListCache,
                                            get_app_list_cache,
                                            reset_app_list_cache)


class TestAppListCache:
    def test_refresh_calls_list_apps(self, qapp):
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = ["discord", "spotify"]
        cache = AppListCache(audio_backend=audio)
        cache.refresh()
        assert cache._last_apps == ["discord", "spotify"]
        audio.list_apps.assert_called()

    def test_emits_apps_changed(self, qapp):
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = ["discord"]
        cache = AppListCache(audio_backend=audio)
        received = []
        cache.apps_changed.connect(lambda apps: received.append(apps))
        # V7: emit tylko gdy lista się zmieni — wstaw nową listę by wywołać emit
        audio.list_apps.return_value = ["discord", "spotify"]
        cache.refresh()
        assert len(received) >= 1
        assert "spotify" in received[-1]

    def test_no_emit_when_unchanged(self, qapp):
        """V7: Refresh z identyczną listą nie emituje — eliminuje redundandate
        enumeracje PA w subskrybentach (dawniej emit zawsze → 5× redundantnych
        list_apps() w pickerach)."""
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = ["discord"]
        cache = AppListCache(audio_backend=audio)
        received = []
        cache.apps_changed.connect(lambda apps: received.append(apps))
        cache.refresh()  # identyczna lista
        assert received == []

    def test_cached_apps_returns_last_snapshot(self, qapp):
        """V7: cached_apps() daje dostęp do ostatniej listy bez RPC."""
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = ["discord", "spotify"]
        cache = AppListCache(audio_backend=audio)
        # constructor already refreshed
        assert cache.cached_apps() == ["discord", "spotify"]
        # mutacje zwracanej listy nie wpływają na cache
        snap = cache.cached_apps()
        snap.append("HACK")
        assert cache.cached_apps() == ["discord", "spotify"]

    def test_no_backend_no_crash(self, qapp):
        reset_app_list_cache()
        cache = AppListCache(audio_backend=None)
        cache.refresh()  # nie powinno crashować

    def test_list_apps_exception_handled(self, qapp):
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.side_effect = RuntimeError("PulseAudio down")
        cache = AppListCache(audio_backend=audio)
        cache.refresh()  # nie powinno crashować

    def test_singleton_get_cache(self, qapp):
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = []
        c1 = get_app_list_cache(audio)
        c2 = get_app_list_cache(audio)
        assert c1 is c2

    def test_reset_clears_singleton(self, qapp):
        reset_app_list_cache()
        audio = MagicMock()
        audio.list_apps.return_value = []
        c1 = get_app_list_cache(audio)
        reset_app_list_cache()
        c2 = get_app_list_cache(audio)
        assert c1 is not c2
