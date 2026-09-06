# Simple Deck — Aplikacja desktopowa

Aplikacja kontrolna dla urządzenia **GREJEM Stream Deck**. Python 3.10+,
**PySide6** (Qt 6) z interfejsem w stylu **Glassmorphism / Glossy**. Auto-reconnect
USB HID, wykrywanie aktywnej aplikacji, kontrola głośności (WASAPI), symulacja skrótów klawiszowych.

---

## 1. Struktura

```
simple-deck-desktop/
├── pyproject.toml
├── run.bat                  ← skrypt uruchomieniowy (Windows)
├── LICENSE
├── src/simple_deck/
│   ├── __main__.py          ← entrypoint (python -m simple_deck)
│   ├── app.py               ← wire_application: kompozycja wszystkich warstw
│   ├── transport/           ← HID + protokół binarny + auto-reconnect
│   │   ├── protocol.py      ← lustro firmware/protocol.h (CRC16-CCITT)
│   │   ├── hid_device.py    ← wrapper hidapi + reader thread
│   │   ├── watchdog.py      ← heartbeat timeout
│   │   └── connection_manager.py ← FSM DISCONNECTED→CONNECTING→CONNECTED→RECONNECTING
│   ├── core/                ← logika aplikacji
│   │   ├── event_bus.py     ← dystrybucja ramek (Qt signals)
│   │   ├── profile.py       ← Profile / PotConfig / ButtonConfig / vu_bar_enabled (JSON)
│   │   ├── profile_manager.py ← load/save, auto-switch wg aktywnej aplikacji
│   │   └── hotkey_dispatcher.py ← wykonuje akcje przycisków
│   ├── platform/            ← abstrakcje platformowe (Windows)
│   │   ├── window_detector.py ← GetForegroundWindow + QueryFullProcessImageNameW
│   │   ├── audio.py         ← WASAPI przez pycaw (per-process volume)
│   │   └── hotkey.py        ← SendInput przez ctypes
│   └── ui/                  ← interfejs użytkownika
│       ├── main_window.py   ← frameless window + header Simple Deck
│       ├── widgets/         ← status_chip, nav_sidebar, deck_map, config_rows, app_picker, hotkey_field
│       └── pages/           ← overview, pots, buttons, leds, settings
├── assets/
│   ├── themes/
│   │   ├── glossy.qss       ← główny arkusz Glassmorphism (~430 linii)
│   │   └── palette.py       ← paleta kolorów
│   ├── icons/               ← ikony SVG
│   └── fonts/               ← fonty (Inter)
├── installer/windows/       ← skrypty budujące instalatory Windows
│   ├── build.ps1            ← główny skrypt build (.exe)
│   ├── build.bat            ← wrapper PowerShell
│   ├── simple_deck.iss      ← Inno Setup script
│   ├── simple_deck.wxs      ← WiX script
│   ├── simple_deck.spec     ← PyInstaller spec
│   └── launch.py            ← entry point dla PyInstaller
├── docs/
│   ├── ARCHITECTURE.md      ← architektura aplikacji
│   ├── PROTOCOL.md          ← protokół komunikacji HID
│   └── WIRING.md            ← schemat połączeń
├── scripts/
│   └── release.py           ← automatyzacja release (build + tag + GitHub release)
├── tests/
│   ├── test_protocol.py     ← CRC + framing (parowanie z firmware C)
│   └── test_filters.py      ← adaptacyjny EMA + deadband
└── .github/workflows/
    └── release.yml          ← CI/CD dla GitHub Releases
```

---

## 2. Instalacja i uruchomienie (NAJSZYBCIEJ)

### 2.1. Windows

```powershell
cd "C:\...\simple-deck-desktop"
.\run.bat            # stworzy .venv, zainstaluje deps, uruchomi aplikację
.\run.bat --demo
```

### 2.2. Instalacja na stałe (do menu aplikacji)

Jeśli chcesz mieć Simple Deck w menu aplikacji na stałe, użyj instalatora:

```powershell
cd "installer\windows"
.\build.ps1          # zbuduje .exe i .msi
```

Następnie uruchom `output\Simple-Deck-Setup-X.Y.Z.exe`.

---

## 3. Ręczna konfiguracja (dla zaawansowanych)

Skrypt `run.bat` automatyzuje poniższe kroki. Jeśli wolisz ręcznie:

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[windows]"  # Windows: pycaw + pywin32

python -m simple_deck        # uruchom
```

Wszystkie zależności są deklarowane w [`pyproject.toml`](pyproject.toml) (PEP 621).

---

## 4. Funkcjonalności

### Transport (USB HID)
- **Custom HID** (VID 0x1209, PID 0xDE10), EP1 IN/OUT, 64-bajtowe raporty
- **Auto-reconnect**: przy rozłączeniu kabla lub utracie heartbeatu (>4.5 s) cyklicznie
  próbuje połączyć ponownie w tle - UI się **NIE zawiesza**
- Heartbeat watchdog: 3 × 1.5 s = 4.5 s timeout
- Reader w osobnym wątku daemon → Qt sygnały automatycznie kolejkowane do głównego wątku

### Profile (JSON w `~/.config/simple-deck/profiles/`)
- 5 potencjometrów → głośność systemowa / głośność aplikacji / wyłączony
- 4 przyciski → skrót klawiszowy / toggle-mute / uruchom komendę
- 8 LED VU bar → wskaźnik głośności (auto-focus na pot, timeout 3 s, fade 300 ms)
- Auto-switch wg aktywnej aplikacji (np. `discord` → profil Discord)
- **V1.0.3**: własne nazwy kontrolek — edycja inline (✎) w Overview, zapis w
  profilu; nazwy pokazują się też na kartach POTENCJOMETRY / PRZYCISKI
- **V1.0.3**: żywy wskaźnik aktywności potencjometrów na stronie POTENCJOMETRY
  (kropka + % podświetla się przy ruchu fizycznej kontrolki, gaśnie po 1 s)
- **V1.0.4**: karty przycisków mają numerowany badge (spójnie z kartami
  potencjometrów) zamiast glifu „◻"; usunięte martwe backendy Linux
  (xdotool/wtype/ydotool, pulsectl, python-xlib, xclip, autostart .desktop) —
  aplikacja jest Windows-only; poprawiony toast błędu skrótu i schowek
  akcji „Wklej tekst" (czysty WinAPI).

### Audio
- **Windows**: WASAPI przez `pycaw` (per-process volume)
- Fallback `NullAudioBackend` jeśli backend niedostępny

### Hotkeys
- **Windows**: `SendInput` przez ctypes (bez zewnętrznych zależności)
- Fallback `NullHotkeyBackend`

### Window detection
- **Windows**: `GetForegroundWindow` + `QueryFullProcessImageNameW` (ctypes)

### UI / Glossy
- Ciemnoszafirnowe tło z subtelnym gradientem
- Karty frosted glass (rgba 78% + 1px biała obwódka + radius 18)
- Gradient świetlny na górze każdej karty (efekt glossy)
- Neonowe akcenty: cyan `#2DD4FF` (primary), magenta `#FF2EC4`, purple `#9B5CFF`
- Drop shadows przez `QGraphicsDropShadowEffect`
- Animacja kropki statusu (4 kolory wg stanu połączenia)

---

## 5. Testy

```bash
pytest tests/
```

Powinno przejść wszystkie testy:
- `test_protocol.py` — weryfikacja CRC16-CCITT (znane wektory) + round-trip encode/decode
- `test_filters.py` — adaptacyjny EMA + deadband (parowanie z firmware/src/adc.c)

---

## 6. Zobacz też

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architektura aplikacji
- [`docs/PROTOCOL.md`](docs/PROTOCOL.md) — protokół komunikacji HID
- Repozytorium firmware: [KacperL-i2c/simpleDeck_V3](https://github.com/KacperL-i2c/simpleDeck_V3)
