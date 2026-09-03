# Ikony aplikacji Simple Deck

Pliki ikon wymagane przez instalatory Windows i Linux.

## Zawartość

- `simple_deck.svg` - wektorowy (skalowalny) - Linux .desktop i AppImage
- `simple_deck.ico` - format Windows (wielowarstwowy) - wygenerowany z SVG (patrz niżej)
- `simple_deck_256.png` - raster 256×256 - wygenerowany z SVG (patrz niżej)

## Generowanie brakujących formatów

### Wymagania

- `imagemagick` (`convert`), `inkscape` lub `rsvg-convert` do rasterizacji SVG
- `icoutils` (`icotool`) do budowy `.ico`

### Linux (ImageMagick + icoutils)

```bash
cd installer/icons

# Rasteryzacja do różnych rozmiarów PNG
for size in 16 32 48 64 128 256; do
    convert -background none simple_deck.svg -resize ${size}x${size} \
            simple_deck_${size}.png
done

# Budowa wielowarstwowego .ico (Windows)
icotool -c -o simple_deck.ico \
    simple_deck_16.png \
    simple_deck_32.png \
    simple_deck_48.png \
    simple_deck_64.png \
    simple_deck_128.png \
    simple_deck_256.png

# Skopiuj raster 256 (używany przez AppImage)
cp simple_deck_256.png .  # już gotowe
```

### Inkscape (alternatywnie)

```bash
inkscape -w 256 -h 256 simple_deck.svg -o simple_deck_256.png
inkscape -w 128 -h 128 simple_deck.svg -o simple_deck_128.png
# …itd
```

## Instalacja narzędzi (Fedora)

```bash
sudo dnf install ImageMagick icoutils
```

## Instalacja narzędzi (Debian/Ubuntu)

```bash
sudo apt install imagemagick icoutils
```

## Po wygenerowaniu

- `simple_deck.ico` → używane przez `installer/windows/simple_deck.iss` (`SetupIconFile`)
- `simple_deck_256.png` → używane przez `installer/linux/build_appimage.sh` (`.DirIcon`)
- `simple_deck.svg` → kopiowane do `/usr/share/icons/hicolor/scalable/apps/` przez `.deb`
