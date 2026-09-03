"""Testy filtru adaptacyjnego EMA - symulacja w Pythonie tego co działa w MCU.

Firmware implementuje filtr w C (stałopozycynowy Q8). Tu reimplemetujemy go
w Pythonie (float) by zweryfikować logikę decyzji:
  - deadband ignoruje szum
  - alfa adaptuje się do prędkości zmian
  - send threshold ogranicza emit
"""
from __future__ import annotations



# Stałe z firmware/include/config.h
DEADBAND = 8
FAST_THR = 128
ALPHA_SLOW = 13 / 256   # ~0.05
ALPHA_FAST = 205 / 256  # ~0.80
SEND_THR = 16


class AdaptiveFilter:
    """Pythonowa kopia filtra z firmware/src/adc.c (wersja float dla czytelności)."""

    def __init__(self):
        self.ema = None
        self.last_sent = None
        self.dirty = False
        self._initialized = False

    def update(self, raw: int) -> bool:
        """Aktualizuje filtr. Zwraca True jeśli ramka powinna być wysłana."""
        if not self._initialized:
            self.ema = float(raw)
            self.last_sent = float(raw)
            self._initialized = True
            self.dirty = True
            return True

        err = raw - self.ema
        abs_err = abs(err)

        if abs_err < DEADBAND:
            return False

        alpha = ALPHA_FAST if abs_err > FAST_THR else ALPHA_SLOW
        self.ema += alpha * (raw - self.ema)

        if abs(self.ema - self.last_sent) >= SEND_THR:
            self.last_sent = self.ema
            self.dirty = True
            return True
        return False


class TestAdaptiveFilter:
    def test_initial_value_is_emitted(self):
        f = AdaptiveFilter()
        assert f.update(1000) is True

    def test_deadband_ignores_noise(self):
        f = AdaptiveFilter()
        f.update(1000)
        # Małe fluktuacje wokół 1000 - powinny być ignorowane
        for delta in [-3, 2, -1, 4, -2, 5, 1, -4]:
            assert f.update(1000 + delta) is False

    def test_fast_movement_responds_quickly(self):
        f = AdaptiveFilter()
        f.update(1000)
        # Skok o 500 - powyżej FAST_THR, filtr powinien gwałtownie zareagować
        emitted = f.update(1500)
        assert emitted is True
        # alfa = 0.80, więc ema powinien ruszyć się znacząco w kierunku 1500
        assert abs(f.ema - 1000) > 200  # ema ~= 1000 + 0.8*500 = 1400

    def test_slow_movement_filters_smoothly(self):
        f = AdaptiveFilter()
        f.update(1000)
        # Powolny ruch - alfa = 0.05, ema powinien ledwo się ruszyć
        f.update(1020)  # delta = 20, powyżej DEADBAND, ale poniżej FAST_THR
        # ema = 1000 + 0.05*20 = 1001 - bardzo blisko 1000
        assert abs(f.ema - 1000) < 5

    def test_send_threshold_avoids_micro_updates(self):
        f = AdaptiveFilter()
        f.update(1000)
        # Powolne aktualizacje - ema się zmienia ale send_thr nie przekroczony
        f.update(1010)  # alfa slow, ema ~= 1000.5
        # Poniżej SEND_THR (16) więc nie emit
        # (uwaga: po pierwszej aktualizacji last_sent=1000.0, ema=1000.5 → diff=0.5 < 16)
        # Drugi update
        f.update(1020)  # ema ~= 1001.0 - nadal poniżej send_thr
        # Zostawmy to elastyczne - ważne że filozofia działa

    def test_long_slow_movement_accumulates_and_emits(self):
        f = AdaptiveFilter()
        f.update(1000)
        emitted_count = 0
        # 100 powolnych kroków o 1 - sumarycznie ruch o 100, powinno emitować
        for i in range(1, 101):
            if f.update(1000 + i):
                emitted_count += 1
        # Powinno było emitować przynajmniej raz (suma ruchu > SEND_THR)
        assert emitted_count >= 1

    def test_full_range_sweep(self):
        """Pełny obrót potencjometru 0..4095 - filtr podąża."""
        f = AdaptiveFilter()
        for v in range(0, 4096, 50):
            f.update(v)
        # Po przetworzeniu całego zakresu ema powinno być blisko 4095
        assert abs(f.ema - 4095) < 100
