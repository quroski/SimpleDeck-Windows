"""Single-instance guard via QLockFile + IPC raise signal.

Pierwsza instancja Simple Deck nabywa blokadę (``QLockFile``) i startuje
serwer IPC (``QLocalServer``). Kolejna instancja, zamiast tworzyć konkurencyjny
proces (który zakłócałby połączenie PulseAudio i walczył o urządzenie HID),
wykrywa zablokowany zamek, wysyła jednobajtowy ping po gnieździe, po czym kończy
się z kodem 0. Pierwsza instancja odbiera ping i emituje ``raise_requested``
— wołający podpina ten sygnał pod ``window.showNormal()/raise_()/activateWindow()``
co przywołuje istniejące okno na wierzch (nawet gdy było ukryte w trayu lub
zminimalizowane przez OS).

To rozszerzenie czystego ``QLockFile`` (które tylko powodowało milczący exit
drugiej instancji) jest konieczne, by kliknięcie w menu aplikacji / skrócie
robiło to, czego oczekuje użytkownik: pokazywało okno.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QLockFile

from .settings import settings_dir

log = logging.getLogger(__name__)

# Nazwa gniazda IPC (w katalogu ustawień, bez rozszerzenia — QLocalServer sam
# dopisuje sufiks odpowiedni dla platformy: .sock na Uniksie, nazwa potoku na Win).
SOCKET_NAME = "simple-deck-raise"
# Krótki payload wysyłany przez drugą instancję. Tylko sam fakt połączenia jest
# istotny; treść zostawiona dla ewentualnego rozszerzenia (np. --demo forward).
RAISE_PAYLOAD = b"raise\n"


def _socket_path() -> str:
    """Pełna ścieżka gniazda IPC w katalogu ustawień."""
    return str(settings_dir() / SOCKET_NAME)


def acquire_single_instance() -> QLockFile | None:
    """Próbuje nabyć blokadę single-instance.

    Zwraca QLockFile (trzymaj referencję by zamek był aktywny!) lub None jeśli
    inna instancja już działa. W drugim przypadku loguje warning — wołający
    powinien spróbować ``notify_existing_instance()`` a potem ``sys.exit(0)``.
    """
    lock_path = settings_dir() / "simple-deck.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)  # 0 = nie usuwaj automatycznie (bezpieczne)
    if not lock.tryLock(100):
        log.warning("Simple Deck już działa (lock: %s) — druga instancja kończy.",
                    lock_path)
        return None
    return lock


def notify_existing_instance() -> bool:
    """Pinguj działającą instancję by przywołała okno.

    Zwraca True jeśli udało się połączyć i wysłać payload, False jeśli serwer
    IPC nie nasłuchuje (np. stary zamek po crashu poprzedniego procesu, albo
    pierwsza instancja to starsza wersja bez serwera). Wołający powinien
    w obu przypadkach zakończyć proces (exit 0) — utrzymujemy kontrakt
    single-instance.
    """
    sock = QLocalSocket()
    sock.connectToServer(_socket_path())
    if not sock.waitForConnected(500):
        log.warning("  existing instance not reachable on IPC socket: %s",
                    sock.errorString())
        return False
    sock.write(RAISE_PAYLOAD)
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.UnconnectedState:
        sock.waitForDisconnected(500)
    return True


class SingleInstanceCoordinator(QObject):
    """Zarządza blokadą + serwerem IPC dla pierwszej (działającej) instancji.

    Signals:
        raise_requested: druga instancja poprosiła o przywołanie okna na wierzch.
    """

    raise_requested = Signal()

    def __init__(self, lock: QLockFile, parent=None):
        super().__init__(parent)
        self._lock = lock
        path = _socket_path()

        # QLocalServer zostawia plik gniazda po crashu procesu — kolejne listen()
        # zawiedzie błędem AddressInUseError. removeServer() czyści zarówno plik
        # jak i ewentualne wpisy wewnętrzne; bezpieczne nawet gdy nic nie ma.
        QLocalServer.removeServer(path)

        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self._server.listen(path):
            # Nasłuch nie wyszedł — nie krytyczne (blokada QLockFile i tak działa),
            # ale wtedy kolejna instancja tylko się wyłączy bez raise. Loguj warning.
            log.warning("Single-instance IPC server failed to listen at %s: %s",
                        path, self._server.errorString())
        else:
            log.debug("Single-instance IPC server listening at %s", path)
        self._server.newConnection.connect(self._on_new_connection)

    def _on_new_connection(self) -> None:
        """Druga instancja się połączyła — odbierz i emituj raise_requested."""
        # Pobierz każde oczekujące połączenie (może być kilka jeśli user kliknął
        # szybko wielokrotnie). Emitujemy raise raz na połączenie.
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            # Odczytaj dane i od razu zamykaj — nie chcemy wiszących klientów.
            conn.readyRead.connect(lambda c=conn: self._drain(c))
            conn.disconnected.connect(conn.deleteLater)
            # Nie czekamy na dane — sam fakt połączenia = prośba o raise.
            self.raise_requested.emit()

    @staticmethod
    def _drain(conn: QLocalSocket) -> None:
        """Wyczyść bufor przychodzący by gniazdo nie zatykać."""
        try:
            conn.readAll()
        except Exception:
            log.exception("Failed to drain single-instance IPC socket")

    def cleanup(self) -> None:
        """Zatrzymaj serwer IPC. Blokada QLockFile zostaje zwolniona gdy GC."""
        try:
            self._server.close()
        except Exception:
            log.exception("Failed to close single-instance IPC server")
