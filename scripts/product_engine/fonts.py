"""Download and cache Jost (Futura PT substitute) from Google Fonts."""
from __future__ import annotations

import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONTS_DIR = Path(__file__).parent / "fonts"
FONT_BOLD = FONTS_DIR / "Jost-Bold.ttf"
FONT_LIGHT = FONTS_DIR / "Jost-Light.ttf"

_VAR_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/jost/Jost%5Bwght%5D.ttf"


def _download_var_font() -> Path:
    path = FONTS_DIR / "Jost-variable.ttf"
    if not path.exists():
        print("[fonts] Downloading Jost variable font …")
        urllib.request.urlretrieve(_VAR_FONT_URL, path)
    return path


def _instantiate(var_path: Path, weight: int, out: Path) -> None:
    font = TTFont(var_path)
    instantiateVariableFont(font, {"wght": weight})
    font.save(str(out))


def ensure_fonts() -> tuple[Path, Path]:
    FONTS_DIR.mkdir(exist_ok=True)
    if FONT_BOLD.exists() and FONT_LIGHT.exists():
        return FONT_BOLD, FONT_LIGHT

    var_path = _download_var_font()
    print("[fonts] Instantiating Jost-Bold (700) and Jost-Light (300) …")
    _instantiate(var_path, 700, FONT_BOLD)
    _instantiate(var_path, 300, FONT_LIGHT)
    print("[fonts] Done.")
    return FONT_BOLD, FONT_LIGHT
