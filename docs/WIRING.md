# Schemat podłączeń — GREJEM Stream Deck (STM32F103C6T6)

Mapowanie pinów i instrukcja montażu dla płytki „Blue Pill" (lub ekwiwalentu
STM32F103C6T6 w obudowie LQFP48).

## 1. Mapowanie pinów

```
                    STM32F103C6T6 (LQFP48)
                    ┌────────────────────────┐
         USB-DP ──▶ │ 21  PA12               │
         USB-DM ──▶ │ 22  PA11               │
                    │                        │
       POT 1 ──▶    │ 10  PA0  (ADC1_IN0)    │
       POT 2 ──▶    │ 11  PA1  (ADC1_IN1)    │
       POT 3 ──▶    │ 12  PA2  (ADC1_IN2)    │
       POT 4 ──▶    │ 13  PA3  (ADC1_IN3)    │
       POT 5 ──▶    │ 14  PA4  (ADC1_IN4)    │
                    │                        │
       BTN 1 ◀──   │ 42  PB6  (pull-up)      │ ──┐
       BTN 2 ◀──   │ 43  PB7  (pull-up)      │   │ do GND
       BTN 3 ◀──   │ 45  PB8  (pull-up)      │   │ przez SW
       BTN 4 ◀──   │ 46  PB9  (pull-up)      │ ──┘
                    │                        │
        LED 1 ◀──   │ 21  PB10 (push-pull, HW PWM TIM2 CH3) │ ──┐
        LED 2 ◀──   │ 22  PB11 (push-pull, HW PWM TIM2 CH4) │   │ do GND
        LED 3 ◀──   │ 25  PB12 (push-pull, SW PWM TIM3)     │   │ przez R ~330Ω
        LED 4 ◀──   │ 26  PB13 (push-pull, SW PWM TIM3)     │   │
        LED 5 ◀──   │ 27  PB14 (push-pull, SW PWM TIM3)     │   │
        LED 6 ◀──   │ 28  PB15 (push-pull, SW PWM TIM3)     │   │
        LED 7 ◀──   │ 30  PA9  (push-pull, SW PWM TIM3)*    │   │ *był USART1 TX
        LED 8 ◀──   │ 31  PA10 (push-pull, SW PWM TIM3)*    │ ──┘ *był USART1 RX
                    │                        │
       Status ◀──  │  2  PC13 (LED onboard)  │ ── active-low
                    │                        │
       SWDIO ──▶   │ 34  PA13               │
       SWCLK ──▶   │ 37  PA14               │
                    │                        │
       8 MHz  ──▶  │  5  PD0  (HSE IN)       │
       8 MHz  ◀──  │  6  PD1  (HSE OUT)      │
                    └────────────────────────┘

USB-C (złącze urządzenia):
   CC1, CC2 ──── 5.1 kΩ do GND (USB-C device role)
   D+      ──── PA12 (z 22 Ω szeregowo + ESD np. USBLC6-2)
   D-      ──── PA11 (z 22 Ω szeregowo + ESD)
   VBUS    ──── +5V (przez LDO 3.3V np. AMS1117-3.3)
   GND     ──── GND
   Shield  ──── GND (przez ferrite bead)
```

## 2. Lista komponentów (BOM)

| Element                    | Ilość | Notatka                          |
|----------------------------|-------|----------------------------------|
| STM32F103C6T6 (LQFP48)     | 1     | Blue Pill lub własna PCB         |
| USB-C złącze 16-pin        | 1     | Power-only wystarczy + D+/D-     |
| Quartz 8 MHz HC-49         | 1     | do PD0/PD1 + 2× 22 pF do GND     |
| LDO AMS1117-3.3            | 1     | +10 µF i 100 nF dekupling        |
| Potencjometr 10 kΩ liniowy | 5     | slider lub obrotowy, do GND/3V3  |
| Switch tact 6×6 mm         | 4     | do GND (aktywne low)             |
| LED 3 mm + R 330 Ω         | 8×2   | linijka VU bar (8 szt.)           |
| USBLC6-2 (ESD protection)  | 1     | ochrona linii D+/D-              |
| Rezystory 5.1 kΩ 0805      | 2     | pull-down CC1, CC2 (USB-C)       |
| Kondensatory 100 nF 0805   | 5+    | dekupling blisko każdego VDD     |
| Zworka BOOT0 + jumper      | 1     | do wejścia w DFU (na Blue Pill wbudowana) |

## 3. Szczegóły podłączeń

### Potencjometry (PA0..PA4)

Każdy potencjometr ma 3 piny:
```
        3V3 ──[ pot 10kΩ ]── GND
                  │
                  └──▶ PAx  (do ADC)
```

- **Linear** taper (nie logarytmiczny - daje pełen zakres 0..4095)
- Wartość 10 kΩ jest optymalna dla ADC (impedancja źródła < 50 kΩ)
- Nie potrzebują dekuplingu - filtr programowy w firmware

### Przyciski (PB6..PB9)

```
    3V3 ──[ wewn. pull-up ]── PBx ──[ switch ]── GND
                                   │
                                   └── aktywny low (pressed=0V)
```

Wewnętrzny pull-up mikrokontrolera jest wystarczający (40 kΩ typowo).
Nie potrzeba zewnętrznych rezystorów ani kondensatorów - debouncing jest
programowy (integrator 5 ms w `buttons.c`).

### LEDy (PB10..PB15, PA9, PA10) — linijka VU bar ×8

```
    PBx/PAx ──[ R 330 Ω ]──[ LED ]── GND
```

- Aktywne high (1 = świeci)
- Prąd: (3.3V - 1.8V) / 330 Ω ≈ 4.5 mA — bezpieczne dla pinu STM32
- PB10/PB11: hardware PWM (TIM2 CH3/CH4, 256 poziomów, ~1 kHz) — top LED VU bar
- PB12–PB15, PA9, PA10: software PWM (TIM3 ISR, 64 poziomy) — segmenty VU bar
- ⚠️ **PA9/PA10 były USART1 TX/RX** — w V2 przejęte dla LED 7/8. USART1 nieużywany
  (komunikacja tylko przez USB).

### Status LED (PC13)

Wbudowana na płytkach Blue Pill / Black Pill. **Aktywny low** (STM32 nguồn prąd
sink-only na PC13, max 3 mA).

```
    3V3 ──[ R 1kΩ ]──[ LED ]── PC13
```

## 4. USB-C szczegóły

| Pin USB-C | Funkcja          | Połączenie                         |
|-----------|------------------|------------------------------------|
| A1, B12   | GND              | GND                                |
| A4, B9    | VBUS (+5V)       | LDO AMS1117-3.3 input              |
| A5        | CC1              | 5.1 kΩ do GND (device role)        |
| B5        | CC2              | 5.1 kΩ do GND                      |
| A6, B7    | D+               | PA12 (przez 22Ω + ESD)             |
| A7, B6    | D-               | PA11 (przez 22Ω + ESD)             |
| A8, B8    | SBU1/SBU2        | NC                                 |
| Shield    | Shell            | GND (ferrite bead)                 |

Piny A i B złącza USB-C są symetryczne — podłącz oba (złącze jest odwracalne).

## 5. Konfiguracja zworek / BOOT

STM32F103 wybiera źródło bootu stanem pinu **BOOT0** przy resecie/power-up:

| BOOT0 | Skąd startuje MCU                | Kiedy używać                |
|-------|----------------------------------|-----------------------------|
| 0     | Flash usera (`0x08000000`)       | Normalna praca, codzienne uruchomienia |
| 1     | System Memory (`0x1FFFF800`)     | **USB DFU bootloader** — do flashowania bez ST-Link |

Na płytkach Blue Pill / Black Pill jest to mini-jumper na pinach BOOT0.
- **BOOT0 = 0** → jumper między środkowym pinem a GND (pozycja "0" / "1-2")
- **BOOT0 = 1** → jumper między środkowym pinem a 3V3 (pozycja "1" / "2-3")
- **BOOT1** zawsze = 0 (nieistotne dla DFU, zostaw na 0)

> **Tip:** System Memory zawiera fabryczny bootloader DFU od ST (2 KB ROM).
> Niezależny od user firmware — działa nawet na pustym Flashu (przydaje się do
> odzyskiwania po bricku).

## 6. Wgrywanie firmware'u

### Sposób A — USB DFU (zalecany, codzienny, bez programatora)

Wymaga tylko kabla USB-C i zworki BOOT0. Działa na pustym Flashu (pierwszy
flash) i po bricku. Wbudowany bootloader jest w ROM — firmware nie musi go
obsługiwać.

**Kroki:**
1. Odłącz USB-C od płytki
2. Przestaw zworkę **BOOT0 = 1** (do 3V3)
3. Podłącz USB-C → urządzenie pojawi się jako `0483:df11` (ST DFU)
4. Sprawdź: `lsusb | grep 0483:df11`
5. Wgrywaj:
   ```bash
   cd firmware && make && make dfuflash
   # lub z sanity-checkiem i hintami:
   ./tools/dfu-enter.sh
   ```
6. Przestaw **BOOT0 = 0**, wciśnij RESET (lub odłącz/podłącz USB)
7. Urządzenie uruchomi user firmware → `lsusb | grep 1209:de10`

**Bez STM32CubeProgrammer** (np. czysty Linux):
```bash
sudo apt install dfu-util
dfu-util -a 0 -d 0483:df11 -s 0x08000000:leave -D build/grejem-fw.bin
```
Opcja `:leave` automatycznie przeskakuje do user firmware po flashu (BOOT0
musi być już wtedy = 0).

### Sposób B — SWD przez ST-Link (do debugu / brick recovery)

Gdy USB DFU nie wchodzi (uszkodzony bootloader, złe USB, chce debugować):
```
ST-Link v2 (lub v3):
   SWDIO ─── PA13
   SWCLK ─── PA14
   GND    ─── GND
   3V3    ─── 3V3 (opcjonalnie - zasilanie z programatora)

cd firmware && make ocdflash
```

### Weryfikacja po flashu

```bash
lsusb | grep 1209:de10
# Bus xxx Device xxx: ID 1209:de10 Generic GREJEM Stream Deck

# PC13 powinien migać co ~500 ms (status)
# LED 0 (PB10) powinien migać co ~250 ms (heartbeat indicator)
```

## 7. Diagnostyka problemów

| Symptom                          | Możliwa przyczyna                  | Rozwiązanie                       |
|----------------------------------|------------------------------------|-----------------------------------|
| PC nie widzi USB                 | BOOT0=1 (DFU mode)                 | BOOT0=0, reset                    |
| PC widzi „Unknown Device"        | zła konfiguracja quartz / PLL      | Sprawdź 8 MHz + 22pF              |
| `lsusb` widzi ale `/dev/hidraw` brak | reguła udev niezaładowana      | `sudo udevadm control --reload`   |
| Aplikacja: „Permission denied"   | reguła udev nie ma MODE=0666       | Patrz `installer/linux/udev/`     |
| LEDy nie świecą                  | zła polaryzacja (aktywne high)     | Odwróć LED lub zmień kod          |
| Przyciski zawsze „wciśnięte"     | brak pull-up / switch do VCC zamiast GND | Sprawdź polaryzację switcha  |
| Potencjometr skacze              | zły kontakt / za krótki sample time| 239.5 cykli w `config.h`, sprawdzić luty |
| Heartbeat timeout                | firmware się crashuje              | Podłącz SWD, sprawdź `HardFault`  |

## 8. Modyfikacje pinów

Jeśli chcesz użyć innych pinów, edytuj:

1. **`firmware/include/board.h`** — stałe `POT_PORT`, `POT_PIN_MASK`,
   `POT_ADC_CHANNEL_LIST`, tablice `board_buttons[]`, `board_leds[]`
2. **`firmware/include/config.h`** — tylko jeśli zmieniasz strojenie filtru
3. Zbuduj ponownie: `cd firmware && make clean && make`

Pinout musi zachować ograniczenia:
- ADC1 kanały IN0..IN17 — patrz datasheet F103, nie każdy pin ma ADC
- USB jest hard-wired do PA11/PA12 — nie zmienisz
- PC13 jest open-drain (3 mA max) — tylko dla LED statusowego
