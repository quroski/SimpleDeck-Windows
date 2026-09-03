# Protokół binarny — GREJEM OS

Specyfikacja protokołu komunikacji MCU ↔ PC. Implementacje:
- C: `firmware/include/protocol.h` + `firmware/src/protocol.c`
- Python: `desktop/src/grejem_os/transport/protocol.py`

## 1. Warstwa fizyczna

USB Full-Speed (12 Mb/s), **Custom HID** (klasa 0x03).

| Parametr              | Wartość                                    |
|-----------------------|---------------------------------------------|
| VID                   | `0x1209` (pid.codes — public)              |
| PID                   | `0xDE10` (GREJEM Stream Deck)              |
| Endpoint IN           | `0x81` (EP1 IN, interrupt, 64 B, 1 ms poll)|
| Endpoint OUT          | `0x01` (EP1 OUT, interrupt, 64 B, 1 ms poll)|
| Manufacturer string   | "GREJEM INDUSTRIES"                        |
| Product string        | "GREJEM Stream Deck"                       |
| Serial number         | "GREJEM-DECK-0001"                         |
| HID descriptor ver    | HID 1.11                                   |
| USB version           | USB 2.0                                    |
| MaxPower              | 100 mA (bus-powered)                       |

### Konwencja Report ID 0x00

Deskryptor HID **nie deklaruje** jawnego `Report ID`, więc host stack traktuje
wszystkie raporty jako "Report 0". Konwencja:

- **Host (`hid_write`)** — pierwszy bajt bufora = `0x00` (Report ID),
  konsumowany przez stack hosta, **nie trafia na EP**.
- **MCU** — bufor EP zawiera wyłącznie 64 B payloadu (pełna ramka protokołu).

Implementacja Python: `make_hid_report()` zwraca `bytes([0x00]) + frame.encode()`
(długość 65 B).

## 2. Format ramki (zagnieżdżony w raporcie HID)

```
Offset  Pole       Rozmiar  Opis
─────── ────────── ───────── ────────────────────────────────────────
0       SOF        1 B       Sync byte, zawsze 0xA5
1       TYPE       1 B       Kod komendy/zdarzenia (patrz §3)
2       CH         1 B       Numer kanału (przycisk/pot/LED) lub 0
3       LEN        1 B       Długość payloadu (0..32)
4..3+LEN PAYLOAD   LEN B     Dane specyficzne dla TYPE
4+LEN   CRC_LO     1 B       CRC16-CCITT (low byte)
5+LEN   CRC_HI     1 B       CRC16-CCITT (high byte)
```

Po `CRC_HI` reszta 64 B raportu jest zerowana (padding).

**CRC16-CCITT** (CCITT-FALSE): poly `0x1021`, init `0xFFFF`, brak odbicia
(reflection), XOR-out `0x0000`. Liczone od `TYPE` (offset 1) do końca
`PAYLOAD` (offset `3 + LEN`). SOF nie jest wliczany.

### Wektor testowy
```
crc16_ccitt(b"123456789") == 0x29B1
crc16_ccitt(b"A")         == 0xB915
crc16_ccitt(b"")          == 0xFFFF  (init)
```

## 3. Typy ramek

### MCU → PC (zdarzenia asynchroniczne)

| TYPE | Nazwa         | CH    | Payload                                 | Kiedy                |
|------|---------------|-------|------------------------------------------|----------------------|
| 0x01 | HEARTBEAT     | 0x00  | `uptime_ms(LE4) + fw_version(1)`        | co 1500 ms           |
| 0x02 | BUTTON_EVT    | 0..3  | `state(1)`                              | przy zmianie stanu   |
| 0x03 | POT_EVT       | 0..4  | `value(LE2, 0..4095)`                   | gdy wartość przekroczy SEND_THR |
| 0x13 | VERSION       | 0x00  | `major(1), minor(1), patch(1)`          | odpowiedź na GET_VERSION |
| 0x10 | ACK           | 0x00  | `acked_type(1)`                          | po obsłudze komendy  |
| 0x11 | NAK           | 0x00  | `err_code(1)`                            | gdy błąd             |

### PC → MCU (komendy)

| TYPE | Nazwa         | CH    | Payload                                 | Działanie            |
|------|---------------|-------|------------------------------------------|----------------------|
| 0x04 | LED_CMD       | 0..4  | `mode(9), level(0..255)` (V2: VU bar)   | Ustaw poziom linijki głośności |
| 0x05 | CFG_CMD       | 0x00  | `deadband, slow, fast, send_thr` (4×1B)  | Runtime tuning filtra |
| 0x12 | GET_VERSION   | 0x00  | (pusty)                                  | Żądaj wersji FW      |

### LED_CMD — V2: wskaźnik głośności VU bar (mode=9)

Wersja **V2** (FW ≥ 1.1.0) używa 8 LED jako linijki wskaźnika głośności (VU bar).
PC wysyła poziom głośności aktywnego potencjometru, a MCU zapala proporcjonalną
liczbę segmentów. Po 3 s bezczynności linijka płynnie wygasa (fade 300 ms → SLEEP).

| LEN | Pola                  | Opis                                           |
|-----|-----------------------|-------------------------------------------------|
| 2   | `mode(9), level(0..255)` | VU_BAR: level = głośność × 255               |

**`level` → liczba świecących LED:**

| level | Pełne segmenty | Top LED (PWM)     |
|-------|----------------|--------------------|
| 0     | 0              | wygaszony          |
| 1–31  | 0              | PWM proporcjonalny |
| 32    | 1              | wygaszony          |
| 64    | 2              | wygaszony          |
| 128   | 4              | wygaszony          |
| 255   | 8 (pełna linijka) | pełna jasność   |

Top LED używa PWM dla płynnego przejścia: `brightness = (level % 32) × 255 / 31`.

### Tryby legacy (V1, mode 0–7)

Tryby OFF(0)…HEARTBEAT(7) z V1 **nie są obsługiwane w V2**. Firmware odrzuca je
ramką NAK (`ERR_BAD_TYPE`). Zostały zachowane w kodzie `leds.c` jako ścieżka
dormant, ale protokół V2 ich nie trigeruje.

**PWM (gdy VU bar aktywny):** PB10/PB11 używają TIM2 CH3/CH4 (hardware PWM,
256 poziomów, ~1 kHz). PB12–PB15, PA9, PA10 używają software PWM (TIM3 ISR,
64 poziomy, konwersja z 8-bit). Top LED VU bar jest na PWM dla płynności.

## 4. Kody błędów (NAK payload)

| Kod | Nazwa           | Opis                                       |
|-----|------------------|---------------------------------------------|
| 0x00 | ERR_OK          | Sukces (zwykle nie wysyłany — ACK zamiast)|
| 0x09 | ERR_BAD_CRC     | CRC się nie zgadza                         |
| 0x0A | ERR_BAD_FRAME   | Zła długość / SOF / payload               |
| 0x0C | ERR_BAD_TYPE    | Nieznany TYPE komendy                      |
| 0x0D | ERR_BAD_CHANNEL | CH poza zakresem                           |
| 0x0E | ERR_OVERFLOW    | Kolejka TX pełna                           |

## 5. Przykłady ramek (hex)

### Heartbeat (uptime 305419896 ms, FW 1.2.x)
```
A5 01 00 05  78 56 34 12 12  ?? ??  00 00 00 ... (padding do 64 B)
│  │  │  │   └─────────────┘  └─────┘
│  │  │  │   payload           CRC16-CCITT
│  │  │  └─ LEN=5
│  │  └──── CH=0
│  └─────── TYPE=0x01 (HEARTBEAT)
└────────── SOF=0xA5
```

### Button event (BTN 2 wciśnięty)
```
A5 02 01 01  01  ?? ??  00 00 00 ...
   │  │  │   │
   │  │  │   └─ state=1 (pressed)
   │  │  └──── LEN=1
   │  └─────── CH=1 (drugi przycisk, indeksowanie od 0)
   └────────── TYPE=0x02 (BUTTON_EVT)
```

### LED command (LED 3 = blink, legacy V1 — NAK w V2)
```
PC wysyła do MCU:
   00 A5 04 02 02 ?? ??  00 ... (padding)
   │  │  │  │  │  │  └───┘
   │  │  │  │  │  │   CRC16-CCITT(body)
   │  │  │  │  │  └── CRC_LO
   │  │  │  │  └───── mode=2 (blink) → NAK(ERR_BAD_TYPE) w V2
   │  │  │  └──────── LEN=1
   │  │  └─────────── CH=2 (trzeci LED)
   │  └────────────── TYPE=0x04 (LED_CMD)
   └───────────────── Report ID 0x00 (hidapi prefix)
```

### LED command — V2 VU bar (pot 2, level=128, ~50% głośności)
```
PC wysyła do MCU:
   00 A5 04 02 02 09 80 ?? ??  00 ... (padding)
   │  │  │  │  │  │  │  └───┘
   │  │  │  │  │  │  │   CRC16-CCITT(body)
   │  │  │  │  │  │  └── level=0x80 (128/255 ≈ 50%)
   │  │  │  │  │  └───── mode=9 (VU_BAR)
   │  │  │  │  └──────── LEN=2
   │  │  │  └─────────── CH=2 (potencjometr 2)
   │  │  └────────────── TYPE=0x04 (LED_CMD)
   └───────────────── Report ID 0x00 (hidapi prefix)
```

### LED command — V1 legacy (NAK w V2)
```
PC wysyła mode=3 (DIM) → MCU odpowiada NAK(ERR_BAD_TYPE):
   00 A5 04 00 02 03 80 ?? ??   ← PC: LED_CMD mode=3 brightness=128
   A5 11 00 01 0C ?? ??         ← MCU: NAK err_code=0x0C (BAD_TYPE)
```

## 6. Spójność C ↔ Python

Obie implementacje muszą dawać **identyczne** wyniki CRC i interpretację
payloadu. Weryfikują to testy:

- `desktop/tests/test_protocol.py::TestCRC16CCITT` — wektory 0x29B1, 0xFFFF, 0xB915
- `desktop/tests/test_protocol.py::TestFirmwareCRCConsistency` — ręczna symulacja
  C-owego algorytmu w Pythonie porównana z implementacją produkcyjną
- Round-trip testy dla wszystkich TYPE ramek

Jeśli zmienisz format w C, **musisz** zmienić go też w Pythonie (i odwrotnie)
oraz zaktualizować testy.

## 7. Co należałoby dodać w przyszłości

- **Pipelining komend**: obecnie PC wysyła komendę i czeka na ACK. Można
  dodać numery sekwencji i kolejkowanie wielu komend bez czekania.
- **Retransmisja NAK**: PC obecnie loguje NAK ale nie wznawia automatycznie.
  Można dodać retry z backoffem.
- **Signing**: CRC16 chroni przed błędami transmisji ale nie przed modyfikacją.
  Dla wrażliwych komend (np. RUN_COMMAND) można dodać HMAC.
