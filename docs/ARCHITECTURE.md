# Architektura — GREJEM OS

> Eco-system aplikacji dla urządzenia typu **Stream Deck** opartego na STM32.

## 1. Diagram wysokiego poziomu

```
┌────────────────────────────────────────────────────────────────────┐
│                         PC (Windows / Linux)                       │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │                  GREJEM OS  (Python + PySide6)                │ │
│  │                                                                │ │
│  │   ┌─────────────────────────┐    ┌────────────────────────┐  │ │
│  │   │   UI  (Qt 6 + QSS)       │    │   EventBus (Qt sig)    │  │ │
│  │   │   • MainWindow          │◄──►│   • button_event        │  │ │
│  │   │   • DeckMap (5/4/8)     │    │   • pot_event           │  │ │
│  │   │   • Pages (overview…)   │    │   • heartbeat           │  │ │
│  │   │   Glossy/Glassmorphism  │    └──────────┬─────────────┘  │ │
│  │   └─────────────────────────┘                 │                │ │
│  │                                                │ route()        │ │
│  │   ┌──────────────────────────────────────────▼─────────────┐  │ │
│  │   │        ConnectionManager (FSM + watchdog)               │  │ │
│  │   │   DISCONNECTED → CONNECTING → CONNECTED                 │  │ │
│  │   │                          ↑↓                            │  │ │
│  │   │                       RECONNECTING                      │  │ │
│  │   └──────────────────────────┬─────────────────────────────┘  │ │
│  │                                │ frame_received                │ │
│  │   ┌────────────────────────────▼────────────────────────────┐ │ │
│  │   │   HIDDevice  (wątek daemon)    │  Backendy platformowe   │ │ │
│  │   │   • hid_write (EP1 OUT)        │  • WASAPI / PulseAudio  │ │ │
│  │   │   • hid_read  (EP1 IN)         │  • SendInput / xdotool  │ │ │
│  │   │   • auto-reconnect             │  • GetForegroundWindow  │ │ │
│  │   └────────────────────────────────┴────────────────────────┘ │ │
│  └────────────────────────────────────┬───────────────────────────┘ │
│                                       │                            │
└───────────────────────────────────────┼────────────────────────────┘
                                        │  USB-C  (Custom HID)
                                        │  VID 0x1209 / PID 0xDE10
                                        │  EP1 IN  + EP1 OUT (64 B)
                                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  STM32F103C6T6 (Cortex-M3 @ 72 MHz)                 │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │            SUPERLOOP  +  __WFI (sleep gdy idle)              │   │
│  └───────┬───────────────────────┬─────────────────┬───────────┘   │
│          │                       │                 │                │
│  ┌───────▼──────┐   ┌────────────▼─────────┐   ┌───▼──────────┐    │
│  │  Scheduler   │   │  USB Custom HID      │   │   ADC + DMA  │    │
│  │  (SysTick    │   │  • EP1 IN (TX)       │   │   • 5 kanały │    │
│  │   1 kHz)     │   │  • EP1 OUT (RX cb)   │   │   • DMA1 Ch1 │    │
│  │              │   │  • GET_DESCRIPTOR    │   │   • HT+TC IRQ│    │
│  │  Tasks:      │   │  • HID Report Descr  │   │              │    │
│  │   buttons 1ms│   │                      │   │  Filtr EMA:  │    │
│  │   usb pump   │   │                      │   │   • deadband │    │
│  │   pots 5ms   │   │                      │   │   • adaptive │    │
│  │   leds 20ms  │   │                      │   │   • adaptive │    │
│  │   heart 1.5s │   │                      │   │              │    │
│  └──────┬───────┘   └──────────────────────┘   └──────┬──────┘    │
│         │                                              │            │
│  ┌──────▼───────┐                              ┌────────▼────────┐  │
│  │   Buttons    │                              │   Potencjometry │  │
│  │   4× PB6-9   │                              │   5× PA0-PA4    │  │
│  │   debounce   │                              │   (12-bit ADC)  │  │
│  └──────────────┘                              └─────────────────┘  │
│                                                                      │
│  │  LEDy ×8     │  ┌──────────────┐                                 │
│  │  VU bar      │  │  LED onboard │                                 │
│  │  PB10-PB15   │  │  PC13 (akt.  │                                 │
│  │  +PA9/PA10   │  │   low, status│                                 │
│  │  PWM: TIM2   │  │              │                                 │
│  │  +SW(TIM3)   │  │              │                                 │
│  └──────────────┘  └──────────────┘                                 │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Stack technologiczny

| Warstwa            | Firmware (MCU)                  | Aplikacja (PC)                |
|--------------------|----------------------------------|--------------------------------|
| **Język**          | C11                              | Python 3.10+                   |
| **Framework**      | libopencm3 (bare-metal)         | PySide6 (Qt 6)                 |
| **Build**          | arm-none-eabi-gcc + Make        | setuptools / pip               |
| **Komunikacja**    | USB Custom HID (libopencm3 USB) | hidapi                         |
| **Format ramek**   | binarny SOF+CRC16-CCITT         | lustro w Pythonie              |
| **Pamięć**         | 9 KB Flash / 2.5 KB RAM (z 32/10) | ~80-110 MB RAM                 |

## 3. Filtrowanie danych (potencjometr → PC)

Pipeline od fizycznego obrotu potencjometru do reakcji w UI:

```
Potencjometr ──ADC──▶ Bufor DMA kołowy (5 ch × 16 próbek = 80 B)
                              │
                              ▼  (IRQ co 8 próbek/kanał)
                       uśrednienie okna
                              │
                              ▼
                    Filtr adaptacyjny EMA:
                    1. err = raw − ema
                    2. if |err| < DEADBAND → return (szum)
                    3. α = |err| > FAST_THR ? 0.80 : 0.05
                    4. ema += α × (raw − ema)     ← arytmetyka Q8 (×256)
                    5. if |ema − last_sent| ≥ SEND_THR → dirty=1
                              │
                              ▼  (scheduler co 5 ms)
                    if dirty: emit POT_EVT przez USB
                              │
                              ▼
                    PC: hid_read → decode_frame
                              │
                              ▼
                    EventBus.pot_event(idx, value)
                              │
                              ▼
                    UI: DeckMap aktualizuje pasek %
```

Stałe filtra (`firmware/include/config.h`):
- `CFG_DEADBAND` = 8 / 4095 (~0.2%)
- `CFG_FAST_THR` = 128 / 4095 (~3.1%)
- `CFG_ALPHA_SLOW` = 13/256 ≈ 0.05
- `CFG_ALPHA_FAST` = 205/256 ≈ 0.80
- `CFG_SEND_THR` = 16 / 4095 (~0.4%)

## 4. FSM auto-reconnectu (ConnectionManager)

```
              start()
DISCONNECTED ──────────▶ CONNECTING ─────────▶ CONNECTED
                              │                     │
                              │ device not          │ USB unplugged
                              │ present             │ (reader thread
                              ▼                     │  exits)
                         RECONNECTING ◀─────────────┘
                              │
                              │ QTimer 1 s
                              │ → _try_connect()
                              │
                              ▼
                          CONNECTED (po sukcesie)
                              │
                              │ brak HEARTBEAT przez 4.5 s
                              │ (watchdog timeout)
                              ▼
                          RECONNECTING
```

Kluczowe własności:
- **UI nigdy się nie zawiesza** — reader HID działa w osobnym wątku
- Sygnały Qt automatycznie kolejkują się między wątkami (`QueuedConnection`)
- Reconnect próbuje co 1 sekundę w tle, bez alertów do użytkownika
- Po reconnect automatycznie wysyłane jest `GET_VERSION` → PC wie z jakim FW gada

## 5. Profile i auto-switch

```
WindowDetector (QTimer 1 s, osobny wątek)
       │
       │ get_foreground_window()
       ▼
   ┌───────────────┐
   │ process_name  │   np. "discord", "spotify", "Code.exe"
   └───────┬───────┘
           │ rules: { "discord": "Discord", "spotify": "Spotify" }
           ▼
   ProfileManager.load(name)
           │
           ▼
   HotkeyDispatcher.set_profile(profile)
   MainWindow.set_profile(profile)   ← aktualizuje UI
```

Profile zapisane w `~/.config/grejem-os/profiles/*.json`. Każdy profil ma
5 PotConfig + 4 ButtonConfig + `vu_bar_enabled` (schema v2).

## 6. Co jest thread-safe

| Operacja                          | Wątek              | Bezpieczeństwo                  |
|------------------------------------|--------------------|---------------------------------|
| `hid.read()`                       | HIDReader (daemon) | własny lock w HIDDevice         |
| `hid_write()`                      | main (Qt)          | lock w HIDDevice                |
| Emit sygnału z readera             | HIDReader          | Qt auto-queued do main          |
| QTimer `state_changed.emit`        | main               | bezpośrednie wywołanie slotów   |
| `pot_state_t` filtra               | DMA IRQ            | wyłącznie w DMA1_Channel1_IRQ   |
| `tx_queue` (kolejka TX)            | main + IRQ         | single-producer (main) → ✓      |

## 7. Co celowo NIE zaimplementowano (limity)

- **Audio backend Wayland**: tylko PulseAudio-Pulse; natywny PipeWire wymagałby
  `pywayland` który jest niestabilny. `pulsectl` działa też przez PipeWire-Pulse.
- **Window detection Wayland**: brak standardowego API; wymaga integracji
  per-compositor (wlr-foreign-toplevel dla wlroots, D-Bus dla GNOME/KDE).
- **I2C LED expandery**: niepotrzebne — 8 LED na GPIO bezpośrednio (VU bar).
- **V1 5-LED legacy modes**: V2 protokół wysyła tylko VU_BAR (mode=9). Stare
  tryby OFF/ON/BLINK/DIM/PULSE/BREATHE/STROBE/HEARTBEAT (0–7) są NAK'owane.
- **Symulacja myszy**: tylko klawiatura (hotkey). Mysz była by nadmiarowa.
