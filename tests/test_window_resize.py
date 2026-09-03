"""Testy zmiany rozmiaru okna (V1.3.3).

Frameless okno nie ma natywnych uchwytów resize — na ekranach z DPI
scaling 125-150% startowy 1280x800 wystawał poza ekran i nie dało się
go zmniejszyć (min 1024x640 + brak resize). V1.3.3 dodaje: natywny
resize krawędziami (WM_NCHITTEST na Windows + startSystemResize
fallback), niższe minimum (820x520), clamp rozmiaru startowego do
ekranu i persistencję rozmiaru w settings.window_size.
"""
from __future__ import annotations

import json

import pytest

from PySide6.QtCore import QPoint, QRect

from simple_deck.core.settings import Settings
from simple_deck.ui.main_window import MainWindow


class _FakeScreen:
    """Duck-typed ekran — tylko availableGeometry()."""

    def __init__(self, w: int, h: int):
        self._avail = QRect(0, 0, w, h)

    def availableGeometry(self) -> QRect:
        return self._avail


# ============================================================================
#  _initial_size — czysta logika (bez GUI)
# ============================================================================

class TestInitialSize:
    def test_default_1280x800(self):
        assert MainWindow._initial_size() == (1280, 800)

    def test_saved_size_restored(self):
        s = Settings()
        s.window_size = [950, 600]
        assert MainWindow._initial_size(settings=s) == (950, 600)

    def test_saved_size_clamped_to_screen(self):
        """Ekran 1366x768 z paskiem zadań (avail ~1366x728) → okno mieści się."""
        s = Settings()
        s.window_size = [1280, 800]      # zapisane na dużym ekranie
        size = MainWindow._initial_size(settings=s, screen=_FakeScreen(1366, 728))
        assert size[0] <= 1366 - 24
        assert size[1] <= 728 - 24

    def test_default_clamped_to_small_screen(self):
        """Bez zapisanego rozmiaru: 1280x800 na avail 1280x620 (DPI 150%)."""
        size = MainWindow._initial_size(screen=_FakeScreen(1280, 620))
        assert size[0] <= 1256
        assert size[1] <= 596

    def test_garbage_saved_ignored(self):
        s = Settings()
        s.window_size = ["abc", None]
        assert MainWindow._initial_size(settings=s) == (1280, 800)

    def test_zero_saved_ignored(self):
        s = Settings()
        s.window_size = [0, 0]
        assert MainWindow._initial_size(settings=s) == (1280, 800)


# ============================================================================
#  _edges_at — mapowanie punktu na krawędzie (GUI, offscreen)
# ============================================================================

@pytest.fixture
def main_win(qapp, bus, mock_connection):
    win = MainWindow(bus=bus, connection=mock_connection, settings=Settings())
    yield win
    win.close()


class TestEdgesAt:
    def test_left_edge(self, main_win):
        assert main_win._edges_at(QPoint(2, main_win.height() // 2)) \
            == Qt_Edges("left")

    def test_right_edge(self, main_win):
        assert main_win._edges_at(QPoint(main_win.width() - 2, 200)) \
            == Qt_Edges("right")

    def test_top_edge(self, main_win):
        assert main_win._edges_at(QPoint(main_win.width() // 2, 3)) \
            == Qt_Edges("top")

    def test_bottom_right_corner(self, main_win):
        pos = QPoint(main_win.width() - 2, main_win.height() - 2)
        assert main_win._edges_at(pos) == Qt_Edges("right+bottom")

    def test_center_no_edges(self, main_win):
        center = QPoint(main_win.width() // 2, main_win.height() // 2)
        assert not main_win._edges_at(center)


def Qt_Edges(spec: str):
    """Helper budujący Qt.Edges z opisu ('left', 'right+bottom', ...)."""
    from PySide6.QtCore import Qt
    flags = Qt.Edges()
    mapping = {
        "left": Qt.LeftEdge, "right": Qt.RightEdge,
        "top": Qt.TopEdge, "bottom": Qt.BottomEdge,
    }
    for part in spec.split("+"):
        flags |= mapping[part]
    return flags


# ============================================================================
#  Persistencja rozmiaru
# ============================================================================

class TestWindowSizePersistence:
    def test_settings_roundtrip(self, tmp_path):
        s = Settings()
        s.window_size = [1024, 700]
        p = tmp_path / "settings.json"
        s.to_json(p)

        s2 = Settings.from_json(p)
        assert s2.window_size == [1024, 700]

    def test_garbage_window_size_dropped(self, tmp_path):
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"window_size": [-5, "big"]}), encoding="utf-8")
        assert Settings.from_json(p).window_size == []

    def test_flush_settings_records_geometry(self, qapp, bus, mock_connection,
                                             monkeypatch, tmp_path):
        import simple_deck.core.settings as settings_mod
        target = tmp_path / "settings.json"
        monkeypatch.setattr(settings_mod, "settings_path", lambda: target)

        s = Settings()
        win = MainWindow(bus=bus, connection=mock_connection, settings=s)
        try:
            win.resize(950, 560)
            win._flush_settings()
        finally:
            win.close()

        assert s.window_size == [950, 560]
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["window_size"] == [950, 560]
        # Restore path: nowy okno dostanie 950x560 (clamped do fake screen)
        assert MainWindow._initial_size(settings=s) == (950, 560)


class TestMinimumSize:
    def test_min_allows_small_screens(self, qapp, bus, mock_connection):
        """Min 820x520 — mieści się na 1366x768 z DPI 150% (avail ~728 wys.)."""
        win = MainWindow(bus=bus, connection=mock_connection, settings=Settings())
        try:
            assert win.minimumWidth() <= 900
            assert win.minimumHeight() <= 560
        finally:
            win.close()
