"""Paleta kolorów stylu Glossy Simple Deck.

Wszystkie kolory używane w QSS + Pythonie (QPainter) w jednym miejscu.
Modyfikuj tutaj, nie w poszczególnych QSS.
"""
from __future__ import annotations

# === Tło aplikacji - głęboki gradient ciemnoszafirnowy ===
BG_TOP = "#0E0E14"
BG_BOTTOM = "#1A1A2A"
BG_RADIAL = "#221E33"

# === Glass cards ===
GLASS_BG = "rgba(28, 30, 42, 200)"            # 78% nieprzezroczystości
GLASS_BG_HOVER = "rgba(38, 42, 58, 220)"
GLASS_BORDER = "rgba(255, 255, 255, 18)"      # 7% białej obwódki
GLASS_BORDER_HOVER = "rgba(255, 255, 255, 40)"
GLASS_RADIUS = 18

# === Neon ===
NEON_CYAN = "#2DD4FF"
NEON_MAGENTA = "#FF2EC4"
NEON_PURPLE = "#9B5CFF"
NEON_GREEN = "#3CFFB0"
NEON_AMBER = "#FFB13C"
NEON_RED = "#FF5C6C"

# === Stan połączenia ===
STATUS_ONLINE = "#3CFFB0"     # zielony
STATUS_CONNECTING = "#FFB13C"  # bursztynowy
STATUS_OFFLINE = "#FF5C6C"     # czerwony
STATUS_RECONNECTING = "#9B5CFF" # fioletowy

# === Tekst ===
TEXT_PRIMARY = "#F5F7FA"
TEXT_SECONDARY = "#A5ABC0"
TEXT_MUTED = "#6A7080"
TEXT_PLACEHOLDER = "#444A5C"

# === Kontekstowe ===
TRACK = "rgba(255, 255, 255, 24)"
TRACK_ACTIVE = NEON_CYAN
SELECTION = "rgba(45, 212, 255, 60)"
SHADOW = "rgba(0, 0, 0, 160)"
