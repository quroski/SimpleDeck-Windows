"""PotDispatcher - wykonuje akcje potencjometrów (regulacja głośności).

To brakujące ogniwo między zdarzeniem ``pot_event`` z MCU a backendem audio.
Bez tego dispatcher'a fizyczne potencjometry tylko animują pasek w UI
(``deck_map``) ale NIE sterują głośnością.

Mapowanie ADC (0..4095) → głośność (0..1):
  1. norm = adc / 4095
  2. odwrócenie jeśli ``invert``
  3. krzywa: linear / log (sqrt - rozszerza dół) / exp (kwadrat - rozszerza górę)
  4. liniowe mapowanie na przedział [min_volume, max_volume]
  5. mnożnik czułości + clamp 0..1

Throttle: maks. ~30 Hz wywołań ``set_volume`` na pot (PulseAudio/WASAPI nie
lubią tysięcy wywołań/s). Wartości w trakcie throttle'owania są koaleskowane
i wysyłane jako ostatnia znana - dzięki temu końcowa pozycja pota zawsze
trafia do backendu.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Slot

from .event_bus import EventBus
from .profile import PotAction, Profile

log = logging.getLogger(__name__)

# Maksymalna częstotliwość wywołań set_volume na jeden potencjometr.
MAX_HZ = 30.0
MIN_INTERVAL_S = 1.0 / MAX_HZ
ADC_MAX = 4095.0


def _apply_curve(norm: float, curve: str) -> float:
    """Krzywa odpowiedzi dla znormalizowanej wartości 0..1.

    Dostępne krzywe:
      linear  — liniowa (1:1)
      log     — sqrt (większa rozdzielczość przy cichych poziomach)
      exp     — kwadrat (większa rozdzielczość przy głośnych poziomach)
      gamma   — n^2.2 (percepcyjnie liniowa, jak sRGB)
      s-curve — smoothstep: 3n²-2n³ (płynne przejścia na obu końcach)
    """
    n = max(0.0, min(1.0, norm))
    if curve == "log":
        return n ** 0.5
    if curve == "exp":
        return n * n
    if curve == "gamma":
        return n ** 2.2
    if curve == "s-curve":
        return n * n * (3.0 - 2.0 * n)
    return n


class PotDispatcher(QObject):
    """Słucha ``pot_event`` i steruje głośnością przez ``audio_backend``."""

    def __init__(self, bus: EventBus, audio_backend=None,
                 settings=None, window_backend=None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._bus = bus
        self._audio = audio_backend
        self._settings = settings
        self._window_backend = window_backend
        self._profile: Optional[Profile] = None

        # Koalescencja: idx -> ostatnia wyliczona głośność (jeszcze niewysłana)
        self._pending: dict[int, float] = {}
        self._last_flush = 0.0
        # Pojedynczy timer koalescujący - flush'uje pending po MIN_INTERVAL_S
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(int(MIN_INTERVAL_S * 1000))
        self._coalesce.timeout.connect(self._flush)

        # Debounced persistence pot values do settings.json (dla UI restore).
        # Coalescencja 2 s — ruch pota potrafi trwać długo, nie chcemy
        # spamować dysku. Flush na closeEvent też woła _flush_persist.
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(2000)
        self._persist_timer.timeout.connect(self._flush_persist)

        bus.pot_event.connect(self._on_pot)

        # Cache foreground process name (TTL 1 s) — unikaj Win32 RPC na każdym
        # flush (30 Hz). Przetrzymywany między wywołaniami _flush.
        self._cached_fg_proc: str = ""
        self._cached_fg_at: float = 0.0

    def set_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._sync_volumes_from_cache()

    def _sync_volumes_from_cache(self) -> None:
        """Ustaw głośności w mikserze na podstawie ostatnich znanych wartości ADC.

        Bez tego po starcie / zmianie profilu mikser zachowuje głośność z
        poprzedniej sesji — dispatcher wysyła set_volume dopiero gdy MCU
        wyśle POT_EVT (czyli gdy user poruszy potencjometrem).
        """
        if self._profile is None or self._audio is None or self._settings is None:
            return
        cached = getattr(self._settings, "last_pot_values", [])
        for idx, cfg in enumerate(self._profile.pots):
            if idx >= len(cached) or cached[idx] < 0:
                continue
            if cfg.action not in (PotAction.SYSTEM_VOLUME, PotAction.APP_VOLUME,
                                  PotAction.GAME_VOLUME):
                continue
            if not cfg.enabled:
                continue
            vol = self._map_volume(cfg, cached[idx])
            target = cfg.target if cfg.action == PotAction.APP_VOLUME else None
            try:
                self._audio.set_volume(vol, target=target)
            except Exception:
                log.debug("sync volume failed for pot %d", idx)

    def set_audio_backend(self, audio_backend) -> None:
        self._audio = audio_backend

    def set_settings(self, settings) -> None:
        """Wstrzyknij obiekt Settings (dla global_invert_all_pots)."""
        self._settings = settings

    # ============================================================
    # Slot: zdarzenie potencjometru z MCU
    # ============================================================
    @Slot(int, int)
    def _on_pot(self, idx: int, adc: int) -> None:
        # Persist last-known value (cache dla UI przy następnym starcie).
        # Wszystkie pota zapisujemy niezależnie czy są przypisane - bo UI
        # pokazuje wszystkie 5 pasków. Debounced write przez _persist_timer.
        if self._settings is not None and 0 <= idx < len(self._settings.last_pot_values):
            self._settings.last_pot_values[idx] = int(adc)
            if not self._persist_timer.isActive():
                self._persist_timer.start()

        if self._profile is None:
            return
        if idx < 0 or idx >= len(self._profile.pots):
            return
        cfg = self._profile.pots[idx]
        if not cfg.enabled:
            return

        # V4: LED indicator (pot_level) jest DECOUPLED od akcji potencjometru.
        # Każdy włączony potencjometr steruje linijką VU, niezależnie czy
        # reguluje głośność (SYSTEM_VOLUME / APP_VOLUME) czy ma action=NONE.
        # Set_volume flush natomiast odpala się tylko dla potów głośnościowych.
        is_volume = cfg.action in (PotAction.SYSTEM_VOLUME, PotAction.APP_VOLUME,
                                    PotAction.GAME_VOLUME)
        level = self._map_volume(cfg, adc) if is_volume else self._raw_level(cfg, adc)
        self._bus.pot_level.emit(idx, level)

        # Volume dispatch — tylko dla potencjometrów głośnościowych z backendem.
        if self._audio is None or not is_volume:
            return

        self._pending[idx] = level
        now = time.monotonic()
        if now - self._last_flush >= MIN_INTERVAL_S:
            # Możemy wysłać natychmiast
            self._flush()
        elif not self._coalesce.isActive():
            # Zaplanuj flush reszty - końcowa pozycja zawsze trafia do backendu
            self._coalesce.start()

    def _raw_level(self, cfg, adc: int) -> float:
        """Surowy poziom ADC (0..1) z zastosowaniem invert — dla potów
        z action=NONE, które nie przechodzą przez _map_volume ale nadal
        sterują wskaźnikiem LED."""
        norm = max(0, min(int(adc), 4095)) / ADC_MAX
        global_invert = bool(getattr(self._settings, "invert_all_pots", False))
        if bool(getattr(cfg, "invert", False)) ^ global_invert:
            norm = 1.0 - norm
        return norm

    def _map_volume(self, cfg, adc: int) -> float:
        """ADC → głośność 0..1 wg konfiguracji potencjometru."""
        norm = max(0, min(int(adc), 4095)) / ADC_MAX
        # Globalne odwrócenie (XOR z per-pot invert) — komponują się
        global_invert = bool(getattr(self._settings, "invert_all_pots", False))
        if (bool(getattr(cfg, "invert", False)) ^ global_invert):
            norm = 1.0 - norm
        norm = _apply_curve(norm, getattr(cfg, "curve", "linear"))
        lo = max(0.0, min(1.0, float(getattr(cfg, "min_volume", 0.0))))
        hi = max(lo, min(1.0, float(getattr(cfg, "max_volume", 1.0))))
        vol = lo + norm * (hi - lo)
        sens = float(getattr(cfg, "sensitivity", 1.0))
        if sens != 1.0:
            vol *= sens
        return max(0.0, min(1.0, vol))

    def _get_foreground_proc(self) -> str:
        """Zwraca nazwę procesu aktywnej aplikacji (cached, TTL 1 s)."""
        now = time.monotonic()
        if self._cached_fg_proc and (now - self._cached_fg_at) < 1.0:
            return self._cached_fg_proc
        if self._window_backend is not None:
            try:
                proc = self._window_backend.active_process_name() or ""
            except Exception:
                proc = ""
            self._cached_fg_proc = proc
            self._cached_fg_at = now
            return proc
        return ""

    def _flush(self) -> None:
        """Wyślij wszystkie oczekujące wartości głośności do backendu audio."""
        if not self._pending or self._audio is None or self._profile is None:
            return
        # Cache foreground proc raz per flush — wspólne dla wszystkich potów GAME_VOLUME
        fg_proc = ""
        has_game_pot = any(
            idx < len(self._profile.pots)
            and self._profile.pots[idx].action == PotAction.GAME_VOLUME
            for idx in self._pending
        )
        if has_game_pot:
            fg_proc = self._get_foreground_proc()
            game_apps = [a.lower() for a in getattr(self._settings, "game_apps", [])] \
                if self._settings else []
        for idx, vol in list(self._pending.items()):
            try:
                target = None
                if idx < len(self._profile.pots):
                    cfg = self._profile.pots[idx]
                    if cfg.action == PotAction.APP_VOLUME:
                        target = cfg.target or None
                    elif cfg.action == PotAction.GAME_VOLUME:
                        # Sprawdź czy foreground proc jest na liście gier.
                        if fg_proc and fg_proc in game_apps:
                            target = fg_proc
                        else:
                            continue  # brak gry → nic nie rób
                self._audio.set_volume(vol, target=target)
            except Exception:
                log.exception("set_volume failed for pot %d", idx)
        self._pending.clear()
        self._last_flush = time.monotonic()

    def _flush_persist(self) -> None:
        """Zapisz cache potencjometrów do settings.json.

        Wywoływane z debounce przez _persist_timer (po 2 s bezczynności)
        lub explicit przy closeEvent aplikacji.
        """
        if self._settings is None:
            return
        try:
            from .settings import settings_path
            self._settings.to_json(settings_path())
        except Exception:
            log.exception("pot values persist failed")
