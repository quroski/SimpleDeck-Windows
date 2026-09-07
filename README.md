# Simple Deck — Aplikacja desktopowa dla Windows 10/11

Aplikacja kontrolna dla urządzenia **GREJEM Stream Deck**. Python 3.10+,
**PySide6** (Qt 6) z GUI. Auto-detect USB HID, auto-reconnect, wykrywanie aktywnej aplikacji, kontrola głośności (WASAPI), symulacja skrótów klawiszowych.

Repozytorium-matka: [KacperL-i2c/simpleDeck_V3](https://github.com/KacperL-i2c/simpleDeck_V3)


---


---
---

## 1. Funkcjonalności

### Transport (USB HID)
- **Custom HID** (VID 0x1209, PID 0xDE10), EP1 IN/OUT, 64-bajtowe raporty
- **Auto-reconnect**: przy rozłączeniu kabla lub utracie heartbeatu (>4.5 s) cyklicznie
  próbuje połączyć ponownie w tle - UI się **NIE zawiesza**
- Heartbeat watchdog: 3 × 1.5 s = 4.5 s timeout
- Reader w osobnym wątku daemon → Qt sygnały automatycznie kolejkowane do głównego wątku

### Profile (JSON w `~/.config/simple-deck/profiles/`)
- 5 potencjometrów → głośność systemowa / głośność aplikacji / gra - auto wykrywanie / wyłączony
- 4 przyciski → skrót klawiszowy / klawisz funkcyjny F13-F24 / toggle-mute / uruchom komendę / wklej tekst / brak
- 3 LED VU bar → wskaźnik głośności (auto-focus na pot, timeout 3 s, fade 300 ms)
- Auto-switch wg aktywnej aplikacji (np. `discord` → profil Discord)


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
## 2. Instalacja

Pobierz najnowszą wersję [instalatora .exe](https://github.com/quroski/SimpleDeck-Windows/releases) i uruchom na swoim PC


Instalator i pliki aplikacji są cyfrowo podpisane (**GREJEM INDUSTRIES**, Azure Artifact Signing — Microsoft Trusted Signing). Windows Defender/SmartScreen nie powinny ostrzegać przed instalatorem; SmartScreen buduje reputację pliku automatycznie i przy pierwszych wydaniach może jeszcze wyświetlić "Więcej informacji → Uruchom mimo to".

Licencja: MIT © 2026 GREJEM INDUSTRIES.

<details>
<summary>Podpis w lokalnym buildzie (opcjonalnie)</summary>

`build.ps1` podpisuje artefakty automatycznie, gdy ustawione są zmienne środowiskowe:

| Zmienna | Znaczenie |
|---|---|
| `SIGN_ENDPOINT` | regionalny endpoint konta, np. `https://eus.codesigning.azure.net` |
| `SIGN_ACCOUNT` | nazwa Artifact Signing account |
| `SIGN_PROFILE` | nazwa certificate profile (Public Trust) |
| `AZURE_TENANT_ID` | Entra tenant (service principal) |
| `AZURE_CLIENT_ID` | app (client) ID |
| `AZURE_CLIENT_SECRET` | secret service principal |

Bez nich build działa normalnie, ale artefakty są niepodpisane.

</details>

### Zawartość instalacji

#### Pliki
| Co | Gdzie |
|---|---|
| Pakiet aplikacji (PyInstaller one-folder: `Simple-Deck.exe`, `Qt6*.dll`, `python3*.dll`, `hidapi.dll`) | `C:\Program Files\Simple Deck\` |
| Pluginy Qt (platforms, styles, imageformats) | `C:\Program Files\Simple Deck\PySide6\` |
| Zasoby: motyw QSS, ikony SVG, fonty | `C:\Program Files\Simple Deck\assets\` |
| Ikony aplikacji | `C:\Program Files\Simple Deck\icons\` |

#### Skróty
- Menu Start: `GREJEM INDUSTRIES\Simple Deck` (+ wpis Deinstaluj) — zawsze
- Ikona na pulpicie — opcjonalna (odznaczona domyślnie)
- Autostart przy starcie systemu — opcjonalny (odznaczony domyślnie)

#### Rejestr
- `HKLM\SOFTWARE\GREJEM INDUSTRIES\Simple Deck` (`InstallPath`, `Version`) + wpis w Programach i funkcjach. Instalator wymaga uprawnień administratora.

#### Czego instalator NIE instaluje
- Sterowników (urządzenie to USB HID — Windows używa wbudowanego `hid.dll`)
- Usług, zaplanowanych zadań, rozszerzeń powłoki ani dodatkowego oprogramowania

#### Dane użytkownika (tworzone przy pierwszym uruchomieniu)
- Profile i ustawienia: `~\.config\simple-deck\` — pozostają po deinstalacji.

---

## 3. Struktura

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

## 4. Testy

```bash
pytest tests/
```

Powinno przejść wszystkie testy:
- `test_protocol.py` — weryfikacja CRC16-CCITT (znane wektory) + round-trip encode/decode
- `test_filters.py` — adaptacyjny EMA + deadband (parowanie z firmware/src/adc.c)

---

## 5. Zobacz też

- Repozytorium firmware: [KacperL-i2c/simpleDeck_V3](https://github.com/KacperL-i2c/simpleDeck_V3)
- Zgłoszenie błędów /.bugów / request of features: [issues](https://github.com/quroski/SimpleDeck-Windows/issues)