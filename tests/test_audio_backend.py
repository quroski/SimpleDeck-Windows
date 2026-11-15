"""Testy WindowsAudioBackend V9 na udawanym COM (bez prawdziwego pycaw).

Harness: moduł ``simple_deck.platform.audio`` monkeypatchuje globalne
atrybuty (comtypes, AudioUtilities, _AudioSession, IAudioSessionControl2,
EDataFlow, DEVICE_STATE) tak, że _enumerate_all_sessions / _render_devices /
_get_master działają w pełni offline. Signature real: pycaw AudioSession,
SetMasterVolume(level, ctx), SetMute(bool, ctx).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pytest

import simple_deck.platform.audio as audio_mod
from simple_deck.platform.audio import (
    WindowsAudioBackend,
    normalize_proc_name,
)


# ============================================================
# Fake COM harness
# ============================================================
class FakeProcess:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class FakeSimpleAudioVolume:
    def __init__(self, volume: float = 0.5, muted: bool = False) -> None:
        self.volume = volume
        self.muted = muted
        self.set_volume_calls: list[float] = []
        self.set_mute_calls: list[bool] = []

    def GetMasterVolume(self) -> float:
        return self.volume

    def SetMasterVolume(self, level: float, ctx: Any = None) -> None:
        self.set_volume_calls.append(level)
        self.volume = level

    def GetMute(self) -> bool:
        return self.muted

    def SetMute(self, muted: bool, ctx: Any = None) -> None:
        self.set_mute_calls.append(bool(muted))
        self.muted = bool(muted)


class FakeMeter:
    def __init__(self, peak: float = 0.0) -> None:
        self.peak = peak

    def GetPeakValue(self) -> float:
        return self.peak


# Tokeny rozróżniające QueryInterface po interfejsach (comtypes rozpoznaje
# po IID; fake rozpoznaje po tożsamości obiektu monkeypatchowanego modułu):
#   IAudioMeterInformation -> meter, ISimpleAudioVolume / IAudioSessionControl2
#   -> odpowiednio volume / sam ctl.
_IAUDIOMETER_TOKEN = object()
_ISIMPLEAUDIO_TOKEN = object()


class FakeSessionCtl:
    """IAudioSessionControl2 + IAudioMeterInformation + ISimpleAudioVolume."""

    def __init__(self, pid: int, process: Optional[FakeProcess],
                 volume: FakeSimpleAudioVolume, meter: FakeMeter) -> None:
        self._pid = pid
        self._process = process
        self._volume = volume
        self._meter = meter

    def GetProcessId(self) -> int:
        return self._pid

    def QueryInterface(self, iface: Any, *args: Any) -> Any:
        if iface is _IAUDIOMETER_TOKEN:
            return self._meter
        if iface is _ISIMPLEAUDIO_TOKEN:
            return self._volume
        return self  # IAudioSessionControl2 -> ctl (jak w COM: ten sam obiekt)


class FakeSession:
    """Mirror pycaw AudioSession: konstruktor z ctl2, właściwości pochodne."""

    def __init__(self, audio_session_control2) -> None:
        self._ctl = audio_session_control2

    @property
    def ProcessId(self) -> int:
        return int(self._ctl.GetProcessId())

    @property
    def Process(self):
        return self._ctl._process

    @property
    def SimpleAudioVolume(self):
        return self._ctl.QueryInterface(_ISIMPLEAUDIO_TOKEN)


def make_session(proc_name: str, pid: int = 100, volume: float = 0.5,
                 muted: bool = False, peak: float = 0.0) -> FakeSession:
    """Fabryka sesji — odpowiednik procesu z aktywną sesją audio."""
    process = FakeProcess(proc_name) if proc_name else None
    ctl = FakeSessionCtl(pid, process, FakeSimpleAudioVolume(volume, muted),
                         FakeMeter(peak))
    return FakeSession(ctl)


class FakeEnum:
    def __init__(self, sessions: list[FakeSession]) -> None:
        self._sessions = sessions

    def GetCount(self) -> int:
        return len(self._sessions)

    def GetSession(self, i: int) -> Any:
        return self._sessions[i]._ctl


class FakeSessionManager:
    def __init__(self, sessions: list[FakeSession]) -> None:
        self._sessions = sessions

    def GetSessionEnumerator(self) -> FakeEnum:
        return FakeEnum(self._sessions)


class FakeEndpointVolume:
    def __init__(self) -> None:
        self.volume = 0.8
        self.muted = False

    def GetMasterVolumeLevelScalar(self) -> float:
        return self.volume

    def SetMasterVolumeLevelScalar(self, value: float, ctx: Any = None) -> None:
        self.volume = value

    def GetMute(self) -> bool:
        return self.muted

    def SetMute(self, muted: bool, ctx: Any = None) -> None:
        self.muted = bool(muted)


class FakeDevice:
    def __init__(self, friendly_name: str,
                 sessions: Optional[list[FakeSession]] = None) -> None:
        self.FriendlyName = friendly_name
        self.EndpointVolume = FakeEndpointVolume()
        self._sessions = sessions or []

    @property
    def AudioSessionManager(self) -> FakeSessionManager:
        return FakeSessionManager(self._sessions)


class FakeAudioUtilities:
    """Zastępuje pycaw AudioUtilities: urządzenia + sesje default endpoint."""

    def __init__(self, devices: list[FakeDevice],
                 default_sessions: Optional[list[FakeSession]] = None,
                 speakers: Optional[FakeDevice] = None) -> None:
        self._devices = devices
        self._default_sessions = default_sessions or []
        self._speakers = speakers
        self.get_all_sessions_calls = 0

    @staticmethod
    def _match_flow(data_flow: Any, device_state: Any) -> bool:
        return data_flow == 0 and device_state == 1  # eRender + ACTIVE

    def GetAllDevices(self, data_flow: int = 2, device_state: int = 15) -> list:
        # Realne wywołanie backendu: GetAllDevices(data_flow=..., device_state=...)
        if data_flow != 0:  # eRender = 0
            return []
        if device_state != 1:  # ACTIVE = 1
            return []
        return list(self._devices)

    def GetAllSessions(self) -> list:
        self.get_all_sessions_calls += 1
        return list(self._default_sessions)

    def GetSpeakers(self) -> Optional[FakeDevice]:
        return self._speakers


# ============================================================
# Monkeypatch helper — podmienia modułowe globalne audio.py
# ============================================================
@pytest.fixture
def fake_audio(monkeypatch: pytest.MonkeyPatch):
    """Ustaw modułowe globalne V9 na fake'y; zwrot: (backend, utilities).

    Domyślne urządzenia:
      - "Głośniki (Realtek)" — sesje spotify (PID 101) + chrome x2 (PID 201/202)
      - "Słuchawki (BT)" — sesja spotify (PID 101, duplikat) + discord (PID 301)
    """
    spotify_main = make_session("spotify.exe", pid=101, volume=0.6, muted=False, peak=0.4)
    spotify_dup = make_session("Spotify", pid=101, volume=0.3, muted=False, peak=0.2)
    chrome_a = make_session("chrome.exe", pid=201, volume=0.5, muted=True, peak=0.9)
    chrome_b = make_session("chrome", pid=202, volume=0.2, muted=False, peak=0.1)
    discord = make_session("DISCORD.EXE", pid=301, volume=0.7, muted=False, peak=0.5)

    dev_speakers = FakeDevice("Głośniki (Realtek)", [spotify_main, chrome_a, chrome_b])
    dev_bt = FakeDevice("Słuchawki (BT)", [spotify_dup, discord])
    speakers = FakeDevice("Głośniki (Realtek) — default", [])
    speakers.EndpointVolume = dev_speakers.EndpointVolume

    utilities = FakeAudioUtilities([dev_speakers, dev_bt], speakers=speakers)

    class _ComtypesNS:
        CLSCTX_ALL = 23

    monkeypatch.setattr(audio_mod, "comtypes", _ComtypesNS)
    monkeypatch.setattr(audio_mod, "AudioUtilities", utilities)
    monkeypatch.setattr(audio_mod, "_AudioSession", FakeSession)
    monkeypatch.setattr(audio_mod, "IAudioSessionControl2", object)
    monkeypatch.setattr(audio_mod, "IAudioMeterInformation", _IAUDIOMETER_TOKEN)
    monkeypatch.setattr(audio_mod, "EDataFlow", type("E", (), {"eRender": type("V", (), {"value": 0})}))
    monkeypatch.setattr(audio_mod, "DEVICE_STATE", type("D", (), {"ACTIVE": type("V", (), {"value": 1})}))
    monkeypatch.setattr(audio_mod, "PYCAW_AVAILABLE", True)

    backend = WindowsAudioBackend()
    # WindowsAudioBackend.__init__ binduje _AudioUtilities bezpośrednio do
    # instancji (lokalny import) — podmień też atrybut instancji.
    backend._AudioUtilities = utilities
    backend._CLSCTX_ALL = _ComtypesNS.CLSCTX_ALL
    return backend, utilities


# ============================================================
# normalize_proc_name
# ============================================================
@pytest.mark.parametrize("raw,expected", [
    ("spotify", "spotify"),
    ("Spotify.EXE", "spotify"),
    ("  DISCORD.exe  ", "discord"),
    ("chrome", "chrome"),
    ("", ""),
    ("   ", ""),
    (".exe", ""),
    (None, ""),
])
def test_normalize_proc_name(raw, expected):
    assert normalize_proc_name(raw) == expected


# ============================================================
# list_apps — dedupe po znormalizowanej nazwie
# ============================================================
def test_list_apps_dedupes_normalized_names(fake_audio):
    backend, _ = fake_audio
    apps = backend.list_apps()
    assert apps == sorted({"spotify", "chrome", "discord"})
    # dedupe na poziomie sesji: każda nazwa występuje dokładnie raz
    assert len(apps) == len(set(apps))


# ============================================================
# _get_sessions — wszystkie sesje celu z wszystkich urządzeń
# ============================================================
def test_get_sessions_returns_all_sessions_across_devices(fake_audio):
    backend, _ = fake_audio
    sessions = backend._get_sessions("spotify.exe")
    assert len(sessions) == 1  # PID 101 duplikat odfiltrowany w enumeracji


def test_get_sessions_match_is_case_and_ext_insensitive(fake_audio):
    backend, _ = fake_audio
    assert len(backend._get_sessions("SPOTIFY")) == 1
    assert len(backend._get_sessions("discord")) == 1
    assert len(backend._get_sessions("Chrome")) == 2


def test_get_sessions_caches_within_ttl(fake_audio):
    backend, _ = fake_audio
    first = backend._get_sessions("chrome")
    second = backend._get_sessions("chrome")
    assert first is second  # cache hit (ten sam obiekt listy)


# ============================================================
# get/set volume
# ============================================================
def test_get_volume_reads_first_session(fake_audio):
    backend, _ = fake_audio
    assert backend.get_volume("spotify") == pytest.approx(0.6)


def test_get_volume_missing_target_warns_once_and_returns_zero(fake_audio, caplog):
    backend, _ = fake_audio
    with caplog.at_level(logging.WARNING):
        assert backend.get_volume("ghost") == 0.0
        assert backend.get_volume("ghost") == 0.0
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "ghost" in r.getMessage()]
    assert len(warnings) == 1  # throttled: max raz na 30 s per target


def test_get_volume_missing_target_warns_again_after_interval(fake_audio, caplog, monkeypatch):
    backend, _ = fake_audio
    times = [1000.0]

    def fake_monotonic() -> float:
        return times[0]

    monkeypatch.setattr(audio_mod.time, "monotonic", fake_monotonic)
    with caplog.at_level(logging.WARNING):
        backend.get_volume("ghost")
        times[0] += audio_mod._MISSING_WARN_INTERVAL_S + 1.0
        backend.get_volume("ghost")
    warnings = [r for r in caplog.records if "ghost" in r.getMessage()]
    assert len(warnings) == 2


def test_set_volume_clamps_and_applies_to_all_sessions(fake_audio):
    backend, _ = fake_audio
    backend._session_cache.clear()
    backend._session_cache_at.clear()
    backend.set_volume(1.5, "spotify")
    sessions = backend._get_sessions("spotify")
    for s in sessions:
        assert s.SimpleAudioVolume.volume == 1.0
        assert s.SimpleAudioVolume.set_volume_calls == [1.0]


def test_set_volume_master_path(fake_audio):
    backend, _ = fake_audio
    backend.set_volume(0.25)
    speakers = backend._AudioUtilities.GetSpeakers()
    assert speakers.EndpointVolume.volume == pytest.approx(0.25)


# ============================================================
# mute semantics (multi-session)
# ============================================================
def test_get_mute_any_unmuted_is_false(fake_audio):
    backend, _ = fake_audio
    # chrome: PID 201 muted, PID 202 unmuted -> nie wyciszony
    assert backend.get_mute("chrome") is False


def test_set_mute_applies_to_all_sessions(fake_audio):
    backend, _ = fake_audio
    backend._session_cache.clear()
    backend._session_cache_at.clear()
    backend.set_mute(True, "chrome")
    for s in backend._get_sessions("chrome"):
        assert s.SimpleAudioVolume.muted is True
    backend.set_mute(False, "chrome")
    for s in backend._get_sessions("chrome"):
        assert s.SimpleAudioVolume.muted is False


def test_toggle_mute_mutes_all_when_any_playing(fake_audio):
    backend, _ = fake_audio
    backend._session_cache.clear()
    backend._session_cache_at.clear()
    backend.toggle_mute("chrome")  # jedna gra -> wycisz wszystkie
    for s in backend._get_sessions("chrome"):
        assert s.SimpleAudioVolume.muted is True


def test_toggle_mute_unmutes_all_when_all_muted(fake_audio):
    backend, _ = fake_audio
    backend._session_cache.clear()
    backend._session_cache_at.clear()
    backend.set_mute(True, "spotify")
    backend._volume_cache.clear()
    backend.toggle_mute("spotify")  # wszystkie mute -> odmutuj wszystkie
    for s in backend._get_sessions("spotify"):
        assert s.SimpleAudioVolume.muted is False


def test_toggle_mute_master_path(fake_audio):
    backend, _ = fake_audio
    backend.toggle_mute()
    speakers = backend._AudioUtilities.GetSpeakers()
    assert speakers.EndpointVolume.muted is True


# ============================================================
# get_peak — max across sessions
# ============================================================
def test_get_peak_max_across_sessions(fake_audio):
    backend, _ = fake_audio
    # spotify: 0.4 + 0.2; chrome: 0.9 + 0.1
    assert backend.get_peak("spotify") == pytest.approx(0.4)
    assert backend.get_peak("chrome") == pytest.approx(0.9)


def test_get_peak_master_uses_default_endpoint(fake_audio):
    backend, _ = fake_audio
    # default endpoint meter nie jest w FakeDevice — master peak wraca 0.0
    # bez błędu (bare except).
    assert backend.get_peak() == 0.0


# ============================================================
# set_output_device / _get_master routing
# ============================================================
def test_get_master_routes_to_named_device(fake_audio):
    backend, _ = fake_audio
    backend.set_output_device("Słuchawki (BT)")
    master = backend._get_master()
    bt = next(d for d in backend._render_devices()
              if d.FriendlyName == "Słuchawki (BT)")
    assert master is bt.EndpointVolume


def test_get_master_case_insensitive_device_match(fake_audio):
    backend, _ = fake_audio
    backend.set_output_device("SŁUCHAWKI (BT)")
    master = backend._get_master()
    bt = next(d for d in backend._render_devices()
              if d.FriendlyName == "Słuchawki (BT)")
    assert master is bt.EndpointVolume


def test_get_master_falls_back_to_default_when_device_unknown(fake_audio):
    backend, _ = fake_audio
    backend.set_output_device("Nieistniejące Urządzenie")
    speakers = backend._AudioUtilities.GetSpeakers()
    assert backend._get_master() is speakers.EndpointVolume


def test_get_master_raises_without_endpoint(fake_audio):
    backend, _ = fake_audio
    backend._AudioUtilities._speakers = None
    backend.set_output_device("")
    with pytest.raises(RuntimeError, match="no default audio endpoint"):
        backend._get_master()


# ============================================================
# list_output_devices
# ============================================================
def test_list_output_devices_returns_friendly_names(fake_audio):
    backend, _ = fake_audio
    devices = backend.list_output_devices()
    assert ("Głośniki (Realtek)", "Głośniki (Realtek)") in devices
    assert ("Słuchawki (BT)", "Słuchawki (BT)") in devices
    assert all(isinstance(d, tuple) and len(d) == 2 for d in devices)


# ============================================================
# enumeracja + fallback
# ============================================================
def test_enumerate_all_sessions_dedupes_same_process_across_devices(fake_audio):
    """Spotify na 2 urządzeniach: PID 101 duplikat odfiltrowany → 1 sesja."""
    backend, _ = fake_audio
    sessions = backend._enumerate_all_sessions()
    spotify_sessions = [s for s in sessions
                        if normalize_proc_name(s.Process.name()) == "spotify"]
    assert len(spotify_sessions) == 1
    assert len(sessions) == 4  # spotify + 2x chrome + discord (bez duplikatu)


def test_enumerate_all_sessions_falls_back_when_com_fails(fake_audio, monkeypatch):
    backend, utilities = fake_audio

    def boom(dev):
        raise OSError("COM error")

    monkeypatch.setattr(backend, "_render_devices", boom)
    sessions = backend._enumerate_all_sessions()
    assert sessions == []  # brak sesji default w fake utilities
    assert utilities.get_all_sessions_calls == 1  # fallback użyty


def test_enumerate_all_sessions_falls_back_when_no_devices(fake_audio):
    backend, utilities = fake_audio
    backend._AudioUtilities._devices = []
    sessions = backend._enumerate_all_sessions()
    assert sessions == []
    assert utilities.get_all_sessions_calls == 1


def test_render_devices_only_render_active(fake_audio):
    backend, utilities = fake_audio
    # FakeAudioUtilities zwraca [] dla data_flow != eRender (0) lub state != ACTIVE (1)
    assert backend._render_devices()  # poprawne wywołanie daje urządzenia
    utilities._devices = []
    assert backend._render_devices() == []


# ============================================================
# exception safety
# ============================================================
def test_set_volume_exception_is_swallowed(fake_audio, monkeypatch):
    backend, _ = fake_audio
    monkeypatch.setattr(backend, "_get_master",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    backend.set_volume(0.5)  # nie podnosi


def test_list_apps_exception_returns_empty(fake_audio, monkeypatch):
    backend, _ = fake_audio
    monkeypatch.setattr(backend, "_enumerate_all_sessions",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert backend.list_apps() == []
