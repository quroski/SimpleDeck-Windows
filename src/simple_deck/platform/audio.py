"""Audio backend - kontrola głośności systemowej i per-proces.

Backendi:
  - Windows: pycaw (WASAPI)
  - Linux:   pulsectl (działa z PulseAudio i PipeWire-Pulse)

Każdy backend eksponuje:
  - list_apps()              -> list[str]      nazwy sesji (do UI pickera)
  - get_volume(target=None)  -> float [0..1]
  - set_volume(v, target=None)
  - toggle_mute(target=None)
  - get_mute(target=None)
"""
from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


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
        self._session_cache: dict[str, object] = {}
        self._session_cache_at: dict[str, float] = {}
        self._session_ttl = 10.0  # V8: Increased from 5.0 - sessions don't change that fast
        # V8: Volume cache with short TTL to reduce WASAPI calls
        self._volume_cache: dict[str, tuple[float, float]] = {}  # (volume, timestamp)
        self._volume_ttl = 0.1  # 100ms cache for volume readings
        # Wybrane urządzenie wyjściowe (puste = domyślne systemowe)
        self._device_name: str = ""

    def set_output_device(self, name: str) -> None:
        """Ustaw urządzenie wyjściowe do sterowania głośnością systemową."""
        self._device_name = name or ""

    def list_apps(self) -> list[str]:
        try:
            sessions = self._AudioUtilities.GetAllSessions()
            return [s.Process.name() if s.Process else "unknown"
                    for s in sessions if s.Process]
        except Exception:
            log.exception("list_apps failed")
            return []

    def _get_session(self, target: str):
        """Zwraca sesję audio dla procesu `target` lub None.

        Używa pycaw public API: AudioUtilities.GetSession(processname).
        Zwraca obiekt AudioSession z `.SimpleAudioVolume' (public).

        Cache TTL 5 s — eliminuje GetAllSessions() RPC na każdym set_volume
        (pot wiggle 30 Hz → dawniej 30 RPC/s, teraz max 1 / 5 s).
        """
        if not target:
            return None
        import time
        target_l = target.lower()
        now = time.monotonic()
        cached = self._session_cache.get(target_l)
        if cached is not None and (now - self._session_cache_at.get(target_l, 0.0)) < self._session_ttl:
            return cached
        # GetSession filtruje case-insensitive po nazwie procesu
        for s in self._AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name().lower() == target_l:
                self._session_cache[target_l] = s
                self._session_cache_at[target_l] = now
                return s
        # Miss — ewentualnie wygaś stary wpis
        self._session_cache.pop(target_l, None)
        self._session_cache_at.pop(target_l, None)
        return None

    def _get_master(self):
        """Zwraca IAudioEndpointVolume dla wybranego (lub domyślnego) urządzenia."""
        if self._device_name:
            for d in self._AudioUtilities.GetAllDevices():
                if str(getattr(d, "DataFlow", "")).lower().startswith("render"):
                    if d.name == self._device_name:
                        return d.EndpointVolume
        devices = self._AudioUtilities.GetSpeakers()
        return devices.EndpointVolume

    def get_volume(self, target: Optional[str] = None) -> float:
        try:
            cache_key = target or "__system__"
            # V8: Check volume cache first (100ms TTL)
            import time
            now = time.monotonic()
            if cache_key in self._volume_cache:
                vol, ts = self._volume_cache[cache_key]
                if now - ts < self._volume_ttl:
                    return vol
            
            # Fetch and cache
            if target:
                s = self._get_session(target)
                if s is None:
                    return 0.0
                # Publiczne API: session.SimpleAudioVolume.GetMasterVolume()
                vol = float(s.SimpleAudioVolume.GetMasterVolume())
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
                s = self._get_session(target)
                if s is not None:
                    s.SimpleAudioVolume.SetMasterVolume(value, None)
                return
            master = self._get_master()
            master.SetMasterVolumeLevelScalar(value, None)
        except Exception:
            log.exception("set_volume failed")

    def toggle_mute(self, target: Optional[str] = None) -> None:
        try:
            if target:
                s = self._get_session(target)
                if s is not None:
                    cur = s.SimpleAudioVolume.GetMute()
                    s.SimpleAudioVolume.SetMute(not cur, None)
                return
            master = self._get_master()
            cur = master.GetMute()
            master.SetMute(not cur, None)
        except Exception:
            log.exception("toggle_mute failed")

    def get_mute(self, target: Optional[str] = None) -> bool:
        try:
            if target:
                s = self._get_session(target)
                if s is None:
                    return False
                return bool(s.SimpleAudioVolume.GetMute())
            master = self._get_master()
            return bool(master.GetMute())
        except Exception:
            log.exception("get_mute failed")
            return False

    def get_peak(self, target: Optional[str] = None) -> float:
        """C10: VU metering przez IAudioMeterInformation (WASAPI)."""
        try:
            from pycaw.pycaw import IAudioMeterInformation
            if target:
                s = self._get_session(target)
                if s is None:
                    return 0.0
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
            else:
                from ctypes import cast, POINTER
                devices = self._AudioUtilities.GetSpeakers()
                meter = devices._dev.Activate(IAudioMeterInformation._iid_,
                                              self._CLSCTX_ALL, None)
                meter = cast(meter, POINTER(IAudioMeterInformation))
            return float(meter.GetPeakValue())
        except Exception:
            return 0.0

    def list_output_devices(self) -> list[tuple[str, str]]:
        """Enumeracja endpointów odtwarzania przez pycaw. Best-effort."""
        try:
            devs = self._AudioUtilities.GetAllDevices()
            out = []
            for d in devs:
                # Tylko endpointy odtwarzania (Render), nie Capture
                if str(getattr(d, "DataFlow", "")).lower().startswith("render"):
                    name = d.name if hasattr(d, "name") else str(d)
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
# Linux PulseAudio/PipeWire (pulsectl)
# ============================================================
class LinuxPulseAudioBackend(AudioBackend):
    """Kontrola głośności przez PulseAudio lub PipeWire-Pulse (pulsectl).

    B4 fix: ``volume_set_all_chans`` przyjmuje OBIEKT encji (sink_input/sink),
    nie sam CVolume. Wcześniej ``si.volume`` modyfikowało lokalną kopię
    CVolume bez pushowania do serwera PulseAudio.
    """

    def __init__(self) -> None:
        # V7: ``import pulsectl`` odroczone do _ensure_pulse — oszczędność
        # ~50-100 ms cold-start + ~2-3 MB RSS do pierwszego użycia audio.
        self._pulsectl = None
        self._pulse = None  # lazy — połączenie do serwera przy pierwszym użyciu
        # Cache sink_input per-target (key=target_lower → sink_input index).
        # Inwalidowany po TTL (5 s) lub przy miss. Eliminuje sink_input_list()
        # RPC na każdym set_volume (pot wiggle 30 Hz → dawniej 30 RPC/s).
        self._si_cache: dict[str, int] = {}
        self._si_cache_at: dict[str, float] = {}
        self._si_ttl = 5.0

    def _ensure_pulse(self):
        """Leniwa inicjalizacja połączenia PulseAudio — dopiero gdy potrzebne.

        V7: Również lazy ``import pulsectl`` — moduł ładuje się dopiero gdy
        pierwszy raz potrzebujemy serwera PA. Aplikacja bez regulacji głośności
        (np. user tylko mapuje hotkeye) nie płaci ~50-100 ms cold-start ani
        ~2-3 MB RSS za pulsectl + ctypes.
        """
        if self._pulsectl is None:
            import pulsectl
            self._pulsectl = pulsectl
        if self._pulse is None:
            self._pulse = self._pulsectl.Pulse("simple-deck")
        return self._pulse

    def list_apps(self) -> list[str]:
        try:
            names = set()
            for sink_input in self._ensure_pulse().sink_input_list():
                name = sink_input.proplist.get("application.process.binary", "")
                if name:
                    names.add(name)
            return sorted(names)
        except Exception:
            log.exception("list_apps failed")
            return []

    def _find_sink_input(self, target: str):
        """Znajdź sink_input dla `target` z cache TTL 5 s.

        Eliminuje sink_input_list() RPC na każdym set_volume (30 Hz pot wiggle
        → dawniej 30 pełnych enumeracji sesji / s).
        """
        import time
        target_l = target.lower()
        now = time.monotonic()
        # Sprawdź cache — indeks może być nieaktualny jeśli sesja zniknęła,
        # dlatego `_lookup_sink_input` weryfikuje po pobraniu.
        cached_idx = self._si_cache.get(target_l)
        pulse = self._ensure_pulse()
        if cached_idx is not None and (now - self._si_cache_at.get(target_l, 0.0)) < self._si_ttl:
            try:
                return pulse.sink_input_info(cached_idx)
            except Exception:
                self._si_cache.pop(target_l, None)
                self._si_cache_at.pop(target_l, None)
        # Full enumeration — cache miss
        for si in pulse.sink_input_list():
            name = si.proplist.get("application.process.binary", "").lower()
            if name == target_l:
                self._si_cache[target_l] = si.index
                self._si_cache_at[target_l] = now
                return si
        self._si_cache.pop(target_l, None)
        self._si_cache_at.pop(target_l, None)
        return None

    def _find_default_sink(self):
        """Zwraca domyślny sink (urządzenie wyjściowe) lub None."""
        pulse = self._ensure_pulse()
        server = pulse.server_info()
        for s in pulse.sink_list():
            if s.name == server.default_sink_name:
                return s
        sinks = pulse.sink_list()
        return sinks[0] if sinks else None

    def get_volume(self, target: Optional[str] = None) -> float:
        try:
            if target:
                si = self._find_sink_input(target)
                if si is None:
                    return 0.0
                return float(si.volume.value_flat)
            s = self._find_default_sink()
            return float(s.volume.value_flat) if s else 0.0
        except Exception:
            log.exception("get_volume failed")
            return 0.0

    def set_volume(self, value: float, target: Optional[str] = None) -> None:
        value = max(0.0, min(1.0, value))
        try:
            pulse = self._ensure_pulse()
            if target:
                si = self._find_sink_input(target)
                if si is not None:
                    # B4 fix: przekaż encję (si), nie CVolume (si.volume).
                    # pulsectl zaktualizuje volume w serwerze PulseAudio.
                    pulse.volume_set_all_chans(si, value)
                return
            s = self._find_default_sink()
            if s is not None:
                pulse.volume_set_all_chans(s, value)
        except Exception:
            log.exception("set_volume failed")

    def toggle_mute(self, target: Optional[str] = None) -> None:
        try:
            pulse = self._ensure_pulse()
            if target:
                si = self._find_sink_input(target)
                if si is not None:
                    pulse.sink_input_mute(si.index, not si.mute)
                return
            s = self._find_default_sink()
            if s is not None:
                pulse.sink_mute(s.index, not s.mute)
        except Exception:
            log.exception("toggle_mute failed")

    def get_mute(self, target: Optional[str] = None) -> bool:
        try:
            if target:
                si = self._find_sink_input(target)
                return bool(si.mute) if si is not None else False
            s = self._find_default_sink()
            return bool(s.mute) if s is not None else False
        except Exception:
            log.exception("get_mute failed")
            return False

    def list_output_devices(self) -> list[tuple[str, str]]:
        """Lista sinków PulseAudio jako (name, description)."""
        try:
            return [(s.name, s.description)
                    for s in self._ensure_pulse().sink_list()]
        except Exception:
            log.exception("list_output_devices failed")
            return []

    def set_default_output(self, name: str) -> bool:
        """Ustaw domyślny sink w PulseAudio."""
        if not name:
            return False
        try:
            self._ensure_pulse().sink_default_set(name)
            return True
        except Exception:
            log.exception("set_default_output failed: %s", name)
            return False


# ============================================================
# Fabryka
# ============================================================
def make_audio_backend() -> AudioBackend:
    """Tworzy odpowiedni backend dla platformy. Zwraca Null jeśli błąd."""
    try:
        if sys.platform.startswith("win"):
            return WindowsAudioBackend()
        elif sys.platform.startswith("linux"):
            return LinuxPulseAudioBackend()
        else:
            log.warning("audio backend: unsupported platform %s", sys.platform)
            return NullAudioBackend()
    except Exception:
        log.warning("audio backend unavailable - using Null")
        return NullAudioBackend()
