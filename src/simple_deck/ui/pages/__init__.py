"""Strony aplikacji Simple Deck.

V7: Brak eager re-export'ów — moduły są importowane lazily z ``main_window``
by oszczędzić ~80-150 ms cold-start (897-line config_pages + 361-line led_page
nie są parsowane gdy user tylko patrzy na Overview). Kto potrzebuje klas,
importuje bezpośrednio z odpowiedniego modułu:

    from simple_deck.ui.pages.overview import OverviewPage
    from simple_deck.ui.pages.config_pages import PotsPage, ButtonsPage, SettingsPage
    from simple_deck.ui.pages.led_page import LedPage
"""
__all__ = ["OverviewPage", "PotsPage", "ButtonsPage", "LedPage", "SettingsPage"]
