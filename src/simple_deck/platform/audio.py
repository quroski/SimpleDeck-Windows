"""Audio backend - kontrola głośności systemowej i per-proces.

Backend (Windows-only build):
  - Windows: pycaw (WASAPI)

Każdy backend eksponuje:
  - list_apps()              -> list[str]      nazwy sesji (do UI pickera)
  - get_volume(target=None)  -> float [0..1]
  - set_volume(v, target=None)
  - toggle_mute(target=None)
  - get_mute(target=None)

V9: sesje danego procesu są enumerowane po WSZYSTKICH aktywnych
endpointach render (nie tylko domyślnym urządzeniu), z fallbackiem
do AudioUtilities.GetAllSessions(). Aplikacja audio może mieć kilka
sesji naraz (np. strumień główny + powiadomienia) — odczyt bierze
pierwszą sesję, zapis/mute trafia do wszystkich; pary sesji są
deduplikowane po (nazwa procesu, PID). list_apps() zwraca zdedupe
listę znormalizowanych nazw procesów (bez końcówki ``.exe``),
zgodną z dopasowaniem celu w _get_sessions i podpowiedziami AppPicker.
"""
from __future__ import annotations

import logging
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

log = logging.getLogger(__name__)

try:  # pycaw/comtypes — dostępne tylko na Windows build
    import comtypes
    from ctypes import POINTER, cast
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.endpointvolume import IAudioEndpointVolume, IAudioMeterInformation
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioSession as _AudioSession, AudioUtilities

    PYCAW_AVAILABLE = True
except ImportError:  # pragma: no cover - środowiska bez pycaw
    PYCAW_AVAILABLE = False


def normalize_proc_name(name: str) -> str:
    """Znormalizuj nazwę procesu: trim, lowercase, bez końcówki ``.exe``.

    Ten sam klucz jest używany przez dopasowanie sesji (_get_sessions),
    dedupe list_apps i podpowiedzi AppPicker — dzięki temu ``spotify``,
    ``Spotify.exe`` i ``SPOTIFY.EXE`` to jeden proces.
    """
    n = (name or "").strip().lower()
    if n.endswith(".exe"):
        n = n[: -len(".exe")].rstrip()
    return n


# Częstotliwość (sekundy) powtarzania ostrzeżenia "cel nie ma sesji" per target.
_MISSING_WARN_INTERVAL_S = 30.0


class AudioBackend(ABC):
    """Abstrakcyjny backend audio."""

    @abstractmethod
    def list_apps(self) -> list[str]:
        """Lista aktywnych sesji audio (nazwy procesów, np. 'discord.exe')."""

    @abstractmethod
    def get_volume(self, target: Optional[str] = None) -> float:
        """Głośność 0..1 (None = główny system, str = konkretny proces)."""

    @abstractmethod
    def set_volume(self, value: float, target: Optional[str] = None) -> None:
        """Ustaw głośność 0..1."""

    @abstractmethod
    def toggle_mute(self, target: Optional[str] = None) -> None:
        """Wycisz/odmutuj."""

    @abstractmethod
    def get_mute(self, target: Optional[str] = None) -> bool:
        """Czy wyciszone."""

    def set_mute(self, muted: bool, target: Optional[str] = None) -> None:
        """Ustaw stan wyciszenia (True = mute, False = unmute).

        Domyślnie zaimplementowane przez get_mute + toggle_mute (dla
        backendów bez bezpośredniego SetMute). Backendy WASAPI nadpisują
        bezpośrednim SetMute — unika przełączenia stanu ustawionego ręcznie
        przez użytkownika między get a toggle.
        """
        if self.get_mute(target) != bool(muted):
            self.toggle_mute(target)

    def list_output_devices(self) -> list[tuple[str, str]]:
        """Lista urządzeń wyjściowych jako (nazwa_wewnętrzna, opis).

        Domyślnie pusta - backendy nadpisują jeśli potrafią wyliczyć.
        """
        return []

    def set_default_output(self, name: str) -> bool:
        """Ustaw domyślne urządzenie wyjściowe. Zwraca True jeśli się udało.

        Domyślnie brak obsługi - backendy nadpisują.
        """
        return False

    def set_output_device(self, name: str) -> None:
        """Ustaw urządzenie wyjściowe do sterowania głośnością systemową.

        Domyślnie no-op - backendy nadpisują jeśli obsługują wybór urządzenia.
        """

    def backend_name(self) -> str:
        """Nazwa backendu (do logów). Domyślnie nazwa klasy."""
        return type(self).__name__

    def get_peak(self, target: Optional[str] = None) -> float:
        """Szczytowy poziom audio 0..1 dla VU meter (C10).

        Domyślnie 0.0 — backendy nadpisują jeśli obsługują metering.
        """
        return 0.0


class NullAudioBackend(AudioBackend):
    """No-op - gdy audio nie jest dostępny (np. brak dźwięku w środowisku CI)."""
    def list_apps(self) -> list[str]: return []
    def get_volume(self, target=None) -> float: return 0.0
    def set_volume(self, value, target=None) -> None: pass
    def toggle_mute(self, target=None) -> None: pass
    def get_mute(self, target=None) -> bool: return False
    def set_mute(self, muted: bool, target=None) -> None: pass


# ============================================================
# Windows WASAPI (pycaw)
# ============================================================
class WindowsAudioBackend(AudioBackend):
    """Kontrola głośności przez WASAPI (pycaw + comtypes).

    Używa publicznego API pycaw:
      - AudioUtilities.GetSpeakers() + IMMDevice.Activate(IID, CLSCTX, None)
        → IAudioEndpointVolume (głośność systemowa)
      - AudioUtilities.GetSession(processname) + session.SimpleAudioVolume
        → ISimpleAudioVolume (głośność per-proces)
    """

    def __init__(self) -> None:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        self._AudioUtilities = AudioUtilities
        self._CLSCTX_ALL = CLSCTX_ALL
        self._volume_iid = IAudioEndpointVolume._iid_
        # pycaw/comtypes nie mają stubów — sesje trzymamy jako Any
        # (dynamiczne obiekty COM; statyczna analiza nie zna ich atrybutów).
        self._session_cache: dict[str, Any] = {}
        self._session_cache_at: dict[str, float] = {}
        self._session_ttl = 10.0  # V8: Increased from 5.0 - sessions don't change that fast
        # V8: Volume cache with short TTL to reduce WASAPI calls
        self._volume_cache: dict[str, tuple[float, float]] = {}  # (volume, timestamp)
        self._volume_ttl = 0.1  # 100ms cache for volume readings
        # Wybrane urządzenie wyjściowe (puste = domyślne systemowe)
        self._device_name: str = ""
        # V9: throttle powtórzeń ostrzeżenia "cel nie ma sesji audio" per target
        self._missing_warned_at: dict[str, float] = {}

    def set_output_device(self, name: str) -> None:
        """Ustaw urządzenie wyjściowe do sterowania głośnością systemową."""
        self._device_name = name or ""

    def list_apps(self) -> list[str]:
        """Zdedupe lista znormalizowanych nazw procesów z aktywnymi sesjami.

        V9: te same nazwy przechodzą przez normalize_proc_name, więc picker
        i _get_sessions porównują spójny klucz (np. 'spotify' zamiast
        raz 'spotify', raz 'Spotify.exe').
        """
        try:
            sessions = self._enumerate_all_sessions()
            names = set()
            for s in sessions:
                proc = getattr(s, "Process", None)
                if proc is None:
                    continue
                try:
                    raw = proc.name()
                except Exception:
                    continue
                norm = normalize_proc_name(raw)
                if norm:
                    names.add(norm)
            return sorted(names)
        except Exception:
            log.exception("list_apps failed")
            return []

    def _device_friendly_name(self, dev) -> str:
        """V9: czytelna nazwa endpointu (pycaw 20251023: AudioDevice.name nie istnieje)."""
        try:
            fn = dev.FriendlyName
            if fn:
                return str(fn)
        except Exception:
            pass
        return str(dev)

    def _render_devices(self) -> list:
        """V9: aktywne endpointy render (wszystkie urządzenia, nie tylko domyślne).

        Zwraca [] w razie błędu COM — callery muszą obsłużyć pustą listę.
        """
        try:
            return list(self._AudioUtilities.GetAllDevices(
                data_flow=EDataFlow.eRender.value,
                device_state=DEVICE_STATE.ACTIVE.value,
            ) or [])
        except Exception:
            log.exception("_render_devices failed")
            return []

    def _enumerate_all_sessions(self) -> list:
        """Sesje audio po WSZYSTKICH aktywnych endpointach render, dedupe.

        V9: aplikacja może odtwarzać audio na urządzeniu innym niż domyślne —
        dawne AudioUtilities.GetAllSessions() widziało tylko domyślny endpoint.
        Pary (znormalizowana nazwa, PID) deduplikowane first-wins (ten sam
        proces na wielu urządzeniach = jeden wpis). W razie błędu COM albo
        braku sesji po enumeracji ręcznej fallback do GetAllSessions().
        """
        seen: set[tuple[str, int]] = set()
        out: list = []
        try:
            for dev in self._render_devices():
                try:
                    mgr = dev.AudioSessionManager
                    enum = mgr.GetSessionEnumerator()
                    if enum is None:
                        continue
                    count = enum.GetCount()
                    for i in range(count):
                        try:
                            ctl = enum.GetSession(i)
                            if ctl is None:
                                continue
                            ctl2 = ctl.QueryInterface(IAudioSessionControl2)
                            session = _AudioSession(ctl2)
                        except Exception:
                            continue
                        try:
                            pid = int(session.ProcessId)
                        except Exception:
                            pid = 0
                        try:
                            raw = session.Process.name() if session.Process else ""
                        except Exception:
                            raw = ""
                        key = (normalize_proc_name(raw), pid)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(session)
                except Exception:
                    continue
        except Exception:
            pass
        if out:
            return out
        # Fallback: sesje domyślnego endpointu (stara ścieżka / COM partial failure)
        try:
            sessions = self._AudioUtilities.GetAllSessions()
            return list(sessions or [])
        except Exception:
            return []

    def _get_sessions(self, target: str) -> list:
        """Zwraca WSZYSTKIE sesje audio procesu `target` (V9) lub [].

        Proces może mieć kilka sesji (strumień główny + dźwięki UI). Cache
        TTL 10 s — enumeracja COM jest kosztowna, a poty wigglują do 30 Hz.
        """
        if not target:
            return []
        key = normalize_proc_name(target)
        if not key:
            return []
        now = time.monotonic()
        cached = self._session_cache.get(key)
        if cached is not None and (now - self._session_cache_at.get(key, 0.0)) < self._session_ttl:
            return cached
        sessions = [s for s in self._enumerate_all_sessions()
                    if self._session_proc_name(s) == key]
        if sessions:
            self._session_cache[key] = sessions
            self._session_cache_at[key] = now
            return sessions
        # Miss — ewentualnie wygaś stary wpis
        self._session_cache.pop(key, None)
        self._session_cache_at.pop(key, None)
        return []

    def _session_proc_name(self, session) -> str:
        """Znormalizowana nazwa procesu sesji ('' gdy nie do ustalenia)."""
        try:
            proc = session.Process
            if proc is None:
                return ""
            return normalize_proc_name(proc.name())
        except Exception:
            return ""

    def _warn_missing_target(self, target: str) -> None:
        """Ostrzegaj o braku sesji dla celu, max raz na 30 s per target (V9)."""
        try:
            key = normalize_proc_name(target)
            now = time.monotonic()
            last = self._missing_warned_at.get(key)
            if last is not None and (now - last) < _MISSING_WARN_INTERVAL_S:
                return
            self._missing_warned_at[key] = now
            log.warning("audio target '%s' nie ma aktywnych sesji audio", target)
        except Exception:
            pass

    def _get_master(self):
        """Zwraca IAudioEndpointVolume dla wybranego (lub domyślnego) urządzenia.

        V9: dopasowanie urządzenia po FriendlyName (pycaw 20251023 usunął
        atrybuty DataFlow/.name z AudioDevice; enumeracja używa GetAllDevices
        z filtrem eRender/ACTIVE, więc str DataFlow zniknął z warunków).
        """
        if self._device_name:
            wanted = normalize_proc_name(self._device_name)
            for d in self._render_devices():
                if normalize_proc_name(self._device_friendly_name(d)) == wanted:
                    return d.EndpointVolume
        devices = self._AudioUtilities.GetSpeakers()
        if devices is None:
            # Brak endpointu (rzadkie) — caller loguje i zwraca bezpieczną wartość.
            raise RuntimeError("no default audio endpoint")
        return devices.EndpointVolume

    def get_volume(self, target: Optional[str] = None) -> float:
        try:
            cache_key = target or "__system__"
            # V8: Check volume cache first (100ms TTL)
            now = time.monotonic()
            if cache_key in self._volume_cache:
                vol, ts = self._volume_cache[cache_key]
                if now - ts < self._volume_ttl:
                    return vol
            
            # Fetch and cache
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    self._warn_missing_target(target)
                    return 0.0
                # Publiczne API: session.SimpleAudioVolume.GetMasterVolume()
                vol = float(sessions[0].SimpleAudioVolume.GetMasterVolume())
            else:
                master = self._get_master()
                vol = float(master.GetMasterVolumeLevelScalar())
            
            self._volume_cache[cache_key] = (vol, now)
            return vol
        except Exception:
            log.exception("get_volume failed")
            return 0.0

    def set_volume(self, value: float, target: Optional[str] = None) -> None:
        value = max(0.0, min(1.0, value))
        try:
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    self._warn_missing_target(target)
                    return
                # V9: proces może mieć wiele sesji — ustaw głośność we wszystkich
                for s in sessions:
                    s.SimpleAudioVolume.SetMasterVolume(value, None)
                return
            master = self._get_master()
            master.SetMasterVolumeLevelScalar(value, None)
        except Exception:
            log.exception("set_volume failed")

    def toggle_mute(self, target: Optional[str] = None) -> None:
        try:
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    self._warn_missing_target(target)
                    return
                # V9: semantics "wycisz wszystko": gdy JAKAKOLWIEK sesja gra
                # dźwięk (nie wyciszona), wycisz wszystkie; inaczej odmutuj.
                any_unmuted = any(
                    not bool(s.SimpleAudioVolume.GetMute()) for s in sessions
                )
                new_state = any_unmuted
                for s in sessions:
                    s.SimpleAudioVolume.SetMute(new_state, None)
                return
            master = self._get_master()
            cur = master.GetMute()
            master.SetMute(not cur, None)
        except Exception:
            log.exception("toggle_mute failed")

    def set_mute(self, muted: bool, target: Optional[str] = None) -> None:
        """Ustaw stan wyciszenia bezpośrednio (SetMute(bool)).

        Nadpisuje ABC default (get+toggle) — bezpośrednie SetMute nie zależy
        od bieżącego stanu, więc nie ma race gdy użytkownik ręcznie zdmucha
        dźwięk między odczytem a przełączeniem.

        V9: stan trafia do WSZYSTKICH sesji celu (strumień główny + dźwięki UI).
        """
        try:
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    self._warn_missing_target(target)
                    return
                for s in sessions:
                    s.SimpleAudioVolume.SetMute(bool(muted), None)
                return
            master = self._get_master()
            master.SetMute(bool(muted), None)
        except Exception:
            log.exception("set_mute failed")

    def get_mute(self, target: Optional[str] = None) -> bool:
        try:
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    self._warn_missing_target(target)
                    return False
                # V9: cel jest "wyciszony" gdy wszystkie sesje wyciszone?
                # Semantyka UX: gdy JAKAKOLWIEK sesja gra dźwięk, odczyt mute=False.
                return all(bool(s.SimpleAudioVolume.GetMute()) for s in sessions)
            master = self._get_master()
            return bool(master.GetMute())
        except Exception:
            log.exception("get_mute failed")
            return False

    def get_peak(self, target: Optional[str] = None) -> float:
        """C10: VU metering przez IAudioMeterInformation (WASAPI).

        V9: dla celu zwraca MAX poziom ze wszystkich sesji procesu — VU
        nie gaśnie, gdy jedna z sesji (np. powiadomienia) gra w danej chwili.
        """
        try:
            if target:
                sessions = self._get_sessions(target)
                if not sessions:
                    return 0.0
                peak = 0.0
                for s in sessions:
                    try:
                        meter = s._ctl.QueryInterface(IAudioMeterInformation)
                        peak = max(peak, float(getattr(meter, "GetPeakValue")()))
                    except Exception:
                        continue
                return peak
            devices = self._AudioUtilities.GetSpeakers()
            if devices is None:
                return 0.0
            meter = devices._dev.Activate(IAudioMeterInformation._iid_,
                                          self._CLSCTX_ALL, None)
            meter = cast(meter, POINTER(IAudioMeterInformation))
            # Metody interfejsu COM generuje comtypes w runtime — getattr zamiast
            # bezpośredniego dostępu (statycznie niewidoczne).
            return float(getattr(meter, "GetPeakValue")())
        except Exception:
            return 0.0

    def list_output_devices(self) -> list[tuple[str, str]]:
        """Enumeracja endpointów odtwarzania przez pycaw. Best-effort.

        V9: filtr eRender/ACTIVE na poziomie GetAllDevices + FriendlyName
        (pycaw 20251023: AudioDevice nie ma już atrybutów DataFlow/.name).
        """
        try:
            out = []
            for d in self._render_devices():
                name = self._device_friendly_name(d)
                out.append((name, name))
            return out
        except Exception:
            log.exception("list_output_devices failed")
            return []

    def set_default_output(self, name: str) -> bool:
        """Ustawienie domyślnego urządzenia na Windows wymaga IPolicyConfig
        (nie jest w publicznym API pycaw). Zwracamy False - nieobsługiwane."""
        log.warning("set_default_output not supported on Windows (needs IPolicyConfig)")
        return False


# ============================================================
# Fabryka
# ============================================================
def make_audio_backend() -> AudioBackend:
    """Tworzy odpowiedni backend dla platformy. Zwraca Null jeśli błąd."""
    try:
        if sys.platform.startswith("win"):
            return WindowsAudioBackend()
        else:
            log.warning("audio backend: unsupported platform %s", sys.platform)
            return NullAudioBackend()
    except Exception:
        log.warning("audio backend unavailable - using Null")
        return NullAudioBackend()
