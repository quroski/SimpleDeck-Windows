"""Profile - model danych mapowania przycisków/potencjometrów/LEDów.

Profile są serializowane do JSON w ``~/.config/simple-deck/profiles/``.

Profil = kompletna konfiguracja urządzenia dla danego kontekstu, np:
  - "Discord"  → potencjometr 0 = głośność Discorca, przycisk 0 = Push-To-Talk
  - "Spotify"  → potencjometr 0 = głośność Spotify, przycisk 1 = Play/Pause
  - "Desktop"  → przyciski = media keys (vol up/down/mute, play/pause)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path


# --- Typy akcji przypisywanych do kontrolek ---

class PotAction(str, Enum):
    """Co robi potencjometr."""
    NONE = "none"
    SYSTEM_VOLUME = "system_volume"     # główny mikser systemu
    APP_VOLUME = "app_volume"           # konkretny proces (nazwa w `target`)
    GAME_VOLUME = "game_volume"         # auto-wykrywanie gry z listy (settings.game_apps)
    NONE_ALT = "disabled"


class ButtonAction(str, Enum):
    """Co robi przycisk."""
    NONE = "none"
    HOTKEY = "hotkey"                   # symulacja sekwencji klawiszy
    TOGGLE_MUTE = "toggle_mute"         # mute audio system/app
    RUN_COMMAND = "run_command"
    PASTE_TEXT = "paste_text"           # wklej tekst (schowek + Ctrl+V)


class LedMode(int, Enum):
    """Tryb LED — V3: wielomodowa linijka (3 aktywne LED).

    Wartości muszą być identyczne z firmware/include/leds.h.
    Legacy tryby (0..7) są zachowane dla kompatybilności wstecznej.
    """
    OFF = 0
    ON = 1
    BLINK = 2
    DIM = 3
    PULSE = 4
    BREATHE = 5
    STROBE = 6
    HEARTBEAT = 7
    LEGACY = 8
    VU_BAR = 9             # wskaźnik głośności
    # V3: nowe globalne tryby linijki
    SOLID = 10             # wszystkie LED ciągle
    BREATHING = 11         # wszystkie LED oddychają
    CHASE = 12             # pościg
    KNIGHT_RIDER = 13      # scanner KITT
    STROBE_BAR = 14        # stroboskop linijki
    BUTTONS = 15           # wskaźnik przycisków
    MANUAL = 16            # ręczna jasność per-LED


# --- Konfiguracje per kontrolka ---

@dataclass(slots=True)
class PotConfig:
    idx: int = 0
    enabled: bool = True
    action: PotAction = PotAction.SYSTEM_VOLUME
    target: str = ""           # nazwa procesu dla APP_VOLUME (puste = system)
    sensitivity: float = 1.0   # mnożnik czułości
    smooth_ui: bool = True
    # V1.0.3: nazwa własna kontrolki (puste = domyślna "POT N"). Edytowalna
    # inline w Overview (DeckMap) — czysto kosmetyczna, nie idzie na MCU.
    label: str = ""
    # --- Ustawienia zaawansowane ---
    curve: str = "linear"       # "linear" | "log" | "exp"
    min_volume: float = 0.0     # dolna granica mapowania (0..1)
    max_volume: float = 1.0     # górna granica mapowania (0..1)
    invert: bool = False        # odwróć kierunek


POT_CURVES = ("linear", "log", "exp", "gamma", "s-curve")


@dataclass(slots=True)
class ButtonConfig:
    idx: int = 0
    action: ButtonAction = ButtonAction.HOTKEY
    hotkey: str = ""           # np. "Ctrl+Shift+D", "MediaPlay"
    target: str = ""           # dla APP_VOLUME / TOGGLE_MUTE / PASTE_TEXT
    on_press: bool = True      # True = reaguj na wciśnięcie, False = na puszczenie
    paste_enter: bool = False  # PASTE_TEXT: naciśnij Enter po wklejeniu
    # V1.0.3: nazwa własna kontrolki (puste = domyślna "BTN N").
    label: str = ""


# --- Cały profil ---

SCHEMA_VERSION = 5    # V5: label; V4: pot_display_order; V3: LED modes; V2: VU bar


def _identity_order() -> list[int]:
    """Domyślna kolejność wyświetlania potencjometrów [0,1,2,3,4]."""
    from ..transport.protocol import POT_COUNT
    return list(range(POT_COUNT))


def _validate_order(order: list[int]) -> list[int]:
    """Zweryfikuj i napraw listę kolejności wyświetlania potencjometrów.

    Poprawna lista to permutacja ``[0..POT_COUNT-1]``. Jeśli jest niepoprawna
    (np. brakujące/zduplikowane/wpisy spoza zakresu), zwróć identyczność.
    """
    from ..transport.protocol import POT_COUNT
    if (isinstance(order, list) and len(order) == POT_COUNT
            and sorted(order) == list(range(POT_COUNT))):
        return list(order)
    return _identity_order()


@dataclass(slots=True)
class Profile:
    """Kompletny profil mapowania urządzenia."""
    name: str = "Default"
    description: str = ""
    pots: list[PotConfig] = field(default_factory=list)
    buttons: list[ButtonConfig] = field(default_factory=list)
    # V2: wskaźnik głośności (8-LED VU bar) zamiast per-LED config
    vu_bar_enabled: bool = True
    # V3: konfiguracja trybu linijki LED
    led_mode: int = LedMode.VU_BAR.value           # domyślnie VU bar
    led_brightness: int = 255                        # globalna jasność 0..255
    led_speed_ms: int = 1000                         # okres animacji (ms)
    led_per_led: list[int] = field(default_factory=lambda: [0] * 3)  # per-LED MANUAL
    # V4: kolejność wyświetlania kart potencjometrów na PotsPage.
    # Lista indeksów FIZYCZNYCH kanałów (cfg.idx) w kolejności wyświetlania.
    # np. [0,2,4,1,3] = pokazuj pot 1,3,5,2,4. Nie wpływa na mapowanie kanałów.
    pot_display_order: list[int] = field(default_factory=_identity_order)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Zapewnij poprawną długość list (zgodnie ze sprzętem)
        from ..transport.protocol import POT_COUNT, BUTTON_COUNT
        if len(self.pots) < POT_COUNT:
            self.pots.extend(PotConfig(idx=i) for i in range(len(self.pots), POT_COUNT))
        if len(self.buttons) < BUTTON_COUNT:
            self.buttons.extend(ButtonConfig(idx=i) for i in range(len(self.buttons), BUTTON_COUNT))
        # V4: walidacja pot_display_order (napraw jeśli uszkodzona)
        self.pot_display_order = _validate_order(self.pot_display_order)

    # --- Serializacja ---
    def to_dict(self) -> dict:
        d = asdict(self)
        # Enum → str dla JSON
        d["pots"] = [{**p, "action": p["action"].value if hasattr(p["action"], "value") else p["action"]} for p in d["pots"]]
        d["buttons"] = [{**b, "action": b["action"].value if hasattr(b["action"], "value") else b["action"]} for b in d["buttons"]]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        pots = [PotConfig(
            idx=p.get("idx", i),
            enabled=p.get("enabled", True),
            action=PotAction(p.get("action", "system_volume")),
            target=p.get("target", ""),
            sensitivity=p.get("sensitivity", 1.0),
            smooth_ui=p.get("smooth_ui", True),
            # V5: label (stare profile schema<5 nie mają pola → "").
            label=str(p.get("label", "")),
            curve=p.get("curve", "linear") if p.get("curve", "linear") in POT_CURVES else "linear",
            min_volume=max(0.0, min(1.0, float(p.get("min_volume", 0.0)))),
            max_volume=max(0.0, min(1.0, float(p.get("max_volume", 1.0)))),
            invert=bool(p.get("invert", False)),
        ) for i, p in enumerate(d.get("pots", []))]
        buttons = [ButtonConfig(
            idx=b.get("idx", i),
            action=ButtonAction(b.get("action", "hotkey")),
            hotkey=b.get("hotkey", ""),
            target=b.get("target", ""),
            on_press=b.get("on_press", True),
            paste_enter=b.get("paste_enter", False),
            label=str(b.get("label", "")),  # V5
        ) for i, b in enumerate(d.get("buttons", []))]
        # V2: stare profile (schema < 2) miały pole "leds" — ignorujemy je (cicha migracja).
        # V3: nowe pola led_mode/led_brightness/led_speed_ms/led_per_led.
        #     Profile v2 bez tych pól dostają sensowne wartości domyślne.
        vu_bar_enabled = bool(d.get("vu_bar_enabled", True))
        led_mode = int(d.get("led_mode", LedMode.VU_BAR.value if vu_bar_enabled else LedMode.OFF.value))
        led_brightness = max(0, min(255, int(d.get("led_brightness", 255))))
        led_speed_ms = max(0, min(65535, int(d.get("led_speed_ms", 1000))))
        led_per_led_raw = d.get("led_per_led", [0, 0, 0])
        led_per_led = [max(0, min(255, int(v))) for v in led_per_led_raw][:3]
        while len(led_per_led) < 3:
            led_per_led.append(0)
        # V4: kolejność wyświetlania potencjometrów. Stare profile (schema<4)
        # nie mają tego pola → _validate_order zwróci identyczność.
        pot_display_order = _validate_order(list(d.get("pot_display_order", _identity_order())))

        return cls(
            name=d.get("name", "Default"),
            description=d.get("description", ""),
            pots=pots, buttons=buttons,
            vu_bar_enabled=vu_bar_enabled,
            led_mode=led_mode,
            led_brightness=led_brightness,
            led_speed_ms=led_speed_ms,
            led_per_led=led_per_led,
            pot_display_order=pot_display_order,
            schema_version=SCHEMA_VERSION,
        )

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "Profile":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(d)
