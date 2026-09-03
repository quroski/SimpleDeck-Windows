"""Globalne ustawienia aplikacji (poza profilami).

Profile (~/.config/simple-deck/profiles/*.json) trzymają mapowanie kontrolek,
zaś ten moduł trzymia ustawienia *aplikacji* niezależne od profilu:
akcent kolorystyczny, autostart, urządzenie wyjściowe audio, tuning filtra
MCU (CFG_CMD), reguły auto-przełączania profili oraz lista ostatnio
używanych aplikacji audio.

Zapisywane jako JSON w ``~/.config/simple-deck/settings.json``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def settings_dir() -> Path:
    """Katalog konfiguracji aplikacji (tworzony jeśli nie istnieje)."""
    p = Path.home() / ".config" / "simple-deck"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return settings_dir() / "settings.json"


# Domyślne wartości tuning'u filtra MCU - zgodne z firmware/include/config.h:
#   CFG_DEADBAND=8, CFG_ALPHA_SLOW=13, CFG_ALPHA_FAST=205, CFG_SEND_THR=16
CFG_DEFAULT_DEADBAND = 8
CFG_DEFAULT_ALPHA_SLOW = 13
CFG_DEFAULT_ALPHA_FAST = 205
CFG_DEFAULT_SEND_THR = 16


# Akcenty dostępne w UI - klucz → kolor z palette.py
ACCENTS: dict[str, str] = {
    "cyan": "#2DD4FF",
    "magenta": "#FF2EC4",
    "green": "#3CFFB0",
    "amber": "#FFB13C",
    "purple": "#9B5CFF",
}
DEFAULT_ACCENT = "cyan"


@dataclass
class CfgTuning:
    """Parametry filtra ADC wysyłane do MCU przez CFG_CMD.

    Wszystkie wartości to surowe bajty firmware'u (Q8 dla alpha).
    """
    deadband: int = CFG_DEFAULT_DEADBAND
    alpha_slow: int = CFG_DEFAULT_ALPHA_SLOW
    alpha_fast: int = CFG_DEFAULT_ALPHA_FAST
    send_thr: int = CFG_DEFAULT_SEND_THR

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "CfgTuning":
        if not isinstance(d, dict):
            return cls()
        return cls(
            deadband=cls._clamp(d.get("deadband", CFG_DEFAULT_DEADBAND), 0, 255),
            alpha_slow=cls._clamp(d.get("alpha_slow", CFG_DEFAULT_ALPHA_SLOW), 0, 255),
            alpha_fast=cls._clamp(d.get("alpha_fast", CFG_DEFAULT_ALPHA_FAST), 0, 255),
            send_thr=cls._clamp(d.get("send_thr", CFG_DEFAULT_SEND_THR), 0, 255),
        )

    @staticmethod
    def _clamp(v, lo, hi) -> int:
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return lo


@dataclass
class Settings:
    """Kompletny stan ustawień aplikacji."""
    accent: str = DEFAULT_ACCENT
    autostart: bool = False
    audio_output_device: str = ""        # "" = urządzenie domyślne
    cfg_tuning: CfgTuning = field(default_factory=CfgTuning)
    # process_name (lower) -> profile_name
    auto_switch_rules: dict[str, str] = field(default_factory=dict)
    # ostatnio wybrane aplikacje audio (do sugestii w AppPicker)
    recent_apps: list[str] = field(default_factory=list)
    # Globalne odwrócenie kierunku wszystkich potencjometrów (hw wiring fix)
    invert_all_pots: bool = False
    # Ostatnie znane wartości potencjometrów (cache dla UI przy starcie)
    # 5 wartości ADC 0..4095; -1 = nieznane (zostanie nadpisane przez POT_EVT)
    last_pot_values: list[int] = field(default_factory=lambda: [-1] * 5)
    # V6: Tray icon — opt-in (domyślnie wyłączone wg preferencji usera)
    show_tray_icon: bool = False
    minimize_to_tray_on_close: bool = False
    # Powiadomienia toast (info/success/warning/error)
    notifications_enabled: bool = True
    # Ostatnio używany profil (ładowany przy starcie)
    active_profile: str = "Default"
    # Lista procesów oznaczonych jako gry (dla PotAction.GAME_VOLUME).
    # Nazwy lowercase, np. ["cs2.exe", "witcher3.exe"].
    game_apps: list[str] = field(default_factory=list)
    # Rozmiar okna głównego [w, h] — zapisywany przy zamknięciu, przywracany
    # przy starcie (clamped do availableGeometry ekranu w MainWindow).
    window_size: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "accent": self.accent if self.accent in ACCENTS else DEFAULT_ACCENT,
            "autostart": bool(self.autostart),
            "audio_output_device": self.audio_output_device,
            "cfg_tuning": self.cfg_tuning.to_dict(),
            "auto_switch_rules": {str(k): str(v) for k, v in self.auto_switch_rules.items()},
            "recent_apps": [str(a) for a in self.recent_apps],
            "invert_all_pots": bool(self.invert_all_pots),
            "last_pot_values": [int(v) for v in self.last_pot_values],
            "show_tray_icon": bool(self.show_tray_icon),
            "minimize_to_tray_on_close": bool(self.minimize_to_tray_on_close),
            "notifications_enabled": bool(self.notifications_enabled),
            "active_profile": str(self.active_profile),
            "game_apps": [str(a).lower() for a in self.game_apps],
            "window_size": self._sanitized_window_size(),
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Settings":
        if not isinstance(d, dict):
            return cls()
        accent = d.get("accent", DEFAULT_ACCENT)
        if accent not in ACCENTS:
            accent = DEFAULT_ACCENT
        # last_pot_values: list[5], z domyślnymi -1 (nieznane)
        lpv_raw = d.get("last_pot_values") or []
        lpv = []
        for i in range(5):
            if i < len(lpv_raw):
                try:
                    v = int(lpv_raw[i])
                    lpv.append(max(-1, min(4095, v)))
                except (TypeError, ValueError):
                    lpv.append(-1)
            else:
                lpv.append(-1)
        return cls(
            accent=accent,
            autostart=bool(d.get("autostart", False)),
            audio_output_device=str(d.get("audio_output_device", "")),
            cfg_tuning=CfgTuning.from_dict(d.get("cfg_tuning")),
            auto_switch_rules={
                str(k).lower(): str(v)
                for k, v in (d.get("auto_switch_rules") or {}).items()
            },
            recent_apps=[str(a) for a in (d.get("recent_apps") or [])][:50],
            invert_all_pots=bool(d.get("invert_all_pots", False)),
            last_pot_values=lpv,
            show_tray_icon=bool(d.get("show_tray_icon", False)),
            minimize_to_tray_on_close=bool(d.get("minimize_to_tray_on_close", False)),
            notifications_enabled=bool(d.get("notifications_enabled", True)),
            active_profile=str(d.get("active_profile", "Default")),
            game_apps=[str(a).lower() for a in (d.get("game_apps") or [])],
            window_size=cls._parse_window_size(d.get("window_size")),
        )

    @staticmethod
    def _parse_window_size(raw) -> list[int]:
        """[w, h] z JSON — odporne na śmieci (zwraca [] przy błędnych)."""
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return []
        try:
            w, h = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return []
        if w < 200 or h < 200 or w > 16384 or h > 16384:
            return []
        return [w, h]

    def _sanitized_window_size(self) -> list[int]:
        return self._parse_window_size(self.window_size)

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "Settings":
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(d)
        except FileNotFoundError:
            return cls()
        except Exception:
            log.exception("failed to load settings from %s - using defaults", path)
            return cls()

    # --- Akcent kolorystyczny ---
    @property
    def accent_color(self) -> str:
        return ACCENTS.get(self.accent, ACCENTS[DEFAULT_ACCENT])

    # --- Reguły auto-przełączania ---
    def set_rule(self, process_name: str, profile_name: str) -> None:
        if profile_name:
            self.auto_switch_rules[process_name.lower()] = profile_name
        else:
            self.auto_switch_rules.pop(process_name.lower(), None)

    def profile_for_process(self, process_name: str) -> Optional[str]:
        return self.auto_switch_rules.get(process_name.lower())

    # --- Ostatnie aplikacje ---
    def remember_app(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        if name in self.recent_apps:
            self.recent_apps.remove(name)
        self.recent_apps.insert(0, name)
        del self.recent_apps[50:]

    def load(self, path: Optional[Path] = None) -> None:
        """Wczytaj z dysku (in-place)."""
        path = path or settings_path()
        other = Settings.from_json(path)
        self.accent = other.accent
        self.autostart = other.autostart
        self.audio_output_device = other.audio_output_device
        self.cfg_tuning = other.cfg_tuning
        self.auto_switch_rules = other.auto_switch_rules
        self.recent_apps = other.recent_apps
        self.invert_all_pots = other.invert_all_pots
        self.last_pot_values = other.last_pot_values
        self.show_tray_icon = other.show_tray_icon
        self.minimize_to_tray_on_close = other.minimize_to_tray_on_close
        self.notifications_enabled = other.notifications_enabled
        self.active_profile = other.active_profile
        self.game_apps = other.game_apps
        self.window_size = other.window_size
