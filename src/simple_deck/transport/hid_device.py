"""HID device wrapper - enkapsulacja biblioteki hidapi.

Czytanie raportów odbywa się w osobnym wątku daemon. Ramki dekodowane na bieżąco
są przekazywane do callbacku ``on_frame`` (ustawianego przez ConnectionManager).
ConnectionManager opakowuje te callbacki Qt-sygnałami kolejkowanymi cross-thread.

Thread-safety:
  - ``open()`` / ``close()`` / ``write_frame()`` używają ``self._lock``
  - Reader thread czyta device bez locka (po sprawdzeniu pod lockiem, że żyje)
  - ``close()`` zamyka device, co przerywa blokujący ``read()`` na większości
    platform - wtedy reader łapie OSError i kończy pracę
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import hid

from .protocol import (HID_REPORT_ID, PID, REPORT_SIZE, VID, Frame,
                       decode_frame)

log = logging.getLogger(__name__)


class HIDError(Exception):
    """Błąd komunikacji z urządzeniem USB."""


class HIDDevice:
    """Asynchroniczny reader HID + sync writer.

    Reader działa w osobnym wątku daemon. Komunikację z urządzeniem
    (otwarcie, write, close) wykonujemy pod lockiem. Celowo nie dziedziczymy
    po QObject - ta klasa jest czystym Pythonem i można jej używać z CLI/skryptów.
    Po stronie UI używamy Qt sygnałów w ConnectionManagerze który opakowuje tę klasę.
    """

    READ_TIMEOUT_MS = 500   # krótki timeout → wątek szybko reaguje na stop
    CLOSE_JOIN_TIMEOUT = 3.0  # musi być > READ_TIMEOUT_MS, by reader zdążył wyjść

    def __init__(self, vid: int = VID, pid: int = PID):
        self._vid = vid
        self._pid = pid
        self._device: Optional[hid.device] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        # Callbacki ustawiane przez ConnectionManager:
        self._on_frame = None       # Callable[[Frame], None]
        self._on_disconnect = None  # Callable[[], None]

    # ---- Właściwości ----
    @property
    def vid(self) -> int:
        return self._vid

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._device is not None

    @staticmethod
    def is_present(vid: int = VID, pid: int = PID) -> bool:
        """Sprawdza czy urządzenie jest widoczne w systemie (bez otwierania)."""
        try:
            return bool(hid.enumerate(vid, pid))
        except Exception:
            return False

    # ---- Callbacki ----
    def set_callbacks(self, *, on_frame=None, on_disconnect=None) -> None:
        self._on_frame = on_frame
        self._on_disconnect = on_disconnect

    # ============================================================
    # Cykl życia
    # ============================================================
    def open(self) -> None:
        """Otwórz urządzenie i uruchom reader thread.

        Raises:
            HIDError: jeśli urządzenie nie istnieje, zajęte, lub odmówiło.
        """
        with self._lock:
            if self._device is not None:
                return  # już otwarte
            try:
                # hid.device() konstruktor NIE otwiera urządzenia (ignoruje args).
                # Trzeba wywołać open(vid, pid) ręcznie — inaczej read/write rzuca
                # ValueError("not open").
                dev = hid.device()
                dev.open(self._vid, self._pid)
            except (OSError, ValueError) as e:
                # OSError: urządzenie zajęte/nie istnieje. ValueError: hidapi gdy
                # brak uprawnień do /dev/bus/usb/* (udev) lub /dev/hidraw*.
                raise HIDError(
                    f"Nie można otworzyć {self._vid:#06x}:{self._pid:#06x}: {e}"
                ) from e
            self._device = dev
            try:
                mfr = dev.get_manufacturer_string()
                prod = dev.get_product_string()
                log.info("HID device opened: %s / %s", mfr, prod)
            except Exception:
                log.info("HID device opened: %04x:%04x", self._vid, self._pid)

        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="HIDReader", daemon=True
        )
        self._reader_thread.start()

    def close(self) -> None:
        """Zatrzymaj reader thread i zamknij urządzenie. Idempotentna.

        Sekwencja:
          1. Ustaw stop_event → reader po.next iteracji zauważy
          2. Pod lockiem: zapamiętaj device, wyzeruj referencję
          3. Zamknij device (to zwykle przerywa blokujący read() w readerze)
          4. Poczekaj na zakończenie readera (timeout > READ_TIMEOUT_MS)
        """
        self._stop_event.set()
        with self._lock:
            dev = self._device
            self._device = None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                log.debug("device.close() raised (ignoring)", exc_info=True)
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=self.CLOSE_JOIN_TIMEOUT)
            if self._reader_thread.is_alive():
                # Reader nadal żyje po timeout - logujemy, ale nie zabijamy
                # (wątek daemon - zginie z procesem)
                log.warning("HID reader thread did not exit within %.1fs",
                            self.CLOSE_JOIN_TIMEOUT)
        self._reader_thread = None

    def write_frame(self, frame: Frame) -> int:
        """Wyślij ramkę do MCU. Zwraca liczbę bajtów wysłanych.

        Raises:
            HIDError: jeśli device nie otwarte lub write zwrócił błąd / OSError.
        """
        with self._lock:
            dev = self._device
        if dev is None:
            raise HIDError("device not open")
        buf = bytes([HID_REPORT_ID]) + frame.encode()
        try:
            n = dev.write(buf)
        except (OSError, ValueError) as e:
            # OSError: race z close() z innego wątku, albo USB odłączony.
            # ValueError("not open"): hidapi gdy uchwyt niedostępny - typowo
            # brak uprawnień do /dev/hidraw* (reguła udev) lub device już martwy.
            raise HIDError(f"hid_write {type(e).__name__}: {e}") from e
        except Exception as e:
            # Cokolwiek innego z warstwy hidapi - zamień na HIDError, żeby
            # send_frame mógł to złapać zamiast kraszować aplikację.
            raise HIDError(f"hid_write {type(e).__name__}: {e}") from e
        if n < 0:
            raise HIDError(f"hid_write failed: {n}")
        return n

    # ============================================================
    # Reader loop (działa w osobnym wątku daemon)
    # ============================================================
    def _reader_loop(self) -> None:
        """Pętla czytająca raporty IN. Krótka, odporna na wyjątki.

        Kończy pracę gdy:
          - ``_stop_event`` ustawione
          - device zostało zamknięte (``_device is None``)
          - OSError z device (USB unplugged) → emit ``on_disconnect`` i wyjdź
        """
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    dev = self._device
                if dev is None:
                    break  # zamknięte przez close() - koniec

                # Blocking read z timeoutem - zwraca listę intów lub pustą listę
                data = dev.read(REPORT_SIZE, timeout_ms=self.READ_TIMEOUT_MS)
                if not data:
                    continue  # timeout - sprawdź _stop_event i próbuj dalej

                # hidapi:
                #   - Linux: dane to 64-bajtowy payload (Report ID zjedzony przez kernel)
                #   - Windows: dane mogą zawierać Report ID jako pierwszy bajt
                raw = bytes(data)
                if len(raw) == REPORT_SIZE + 1 and raw[0] == HID_REPORT_ID:
                    raw = raw[1:]
                frame = decode_frame(raw)
                if frame is not None and self._on_frame is not None:
                    try:
                        self._on_frame(frame)
                    except Exception:
                        log.exception("on_frame callback failed")
            except (OSError, ValueError) as e:
                # USB unplugged / device zamknięte w trakcie read() → rozłączenie.
                # ValueError("not open") z hidapi: brak uprawnień do /dev/hidraw*
                # (udev) lub uchwyt niedostępny - również traktujemy jako disconnect
                # (zamiast spinania co 50 ms w broad-Exception).
                log.info("HID read failed (%s: %s) - disconnect",
                         type(e).__name__, e)
                if self._on_disconnect is not None:
                    try:
                        self._on_disconnect()
                    except Exception:
                        log.exception("on_disconnect callback failed")
                break
            except Exception:
                # Niezany błąd - loguj i spróbuj dalej (z krótkim snem)
                log.exception("unexpected error in reader loop")
                self._stop_event.wait(50)
