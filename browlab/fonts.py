"""Font discovery for the printed sheets (Korean-capable when available)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import ImageFont

# Fonts known to contain Hangul glyphs, in preference order.
KOREAN_FONT_CANDIDATES: List[str] = [
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    "~/Library/Fonts/NanumGothic.ttf",
    "~/Library/Fonts/NotoSansKR-Regular.ttf",
    "~/Library/Fonts/NotoSansKR-Regular.otf",
    # Windows
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/gulim.ttc",
    # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "~/.fonts/NanumGothic.ttf",
    "~/.local/share/fonts/NanumGothic.ttf",
]

LATIN_FONT_CANDIDATES: List[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def find_font_file(explicit: Optional[str] = None) -> Tuple[Optional[Path], bool]:
    """Return (path, supports_korean). ``path`` is None when only PIL's bitmap font is available."""
    candidates: List[Tuple[str, bool]] = []
    if explicit:
        candidates.append((explicit, True))
    env = os.environ.get("BROWLAB_FONT")
    if env:
        candidates.append((env, True))
    candidates += [(c, True) for c in KOREAN_FONT_CANDIDATES]
    candidates += [(c, False) for c in LATIN_FONT_CANDIDATES]
    for candidate, korean in candidates:
        p = Path(os.path.expanduser(candidate))
        if p.is_file():
            return p, korean
    return None, False


class SheetFont:
    """Small helper that loads one font file at several sizes."""

    def __init__(self, explicit: Optional[str] = None) -> None:
        self.path, self.korean = find_font_file(explicit)
        self._cache: dict = {}

    def get(self, size: int) -> ImageFont.ImageFont:
        size = max(8, int(size))
        if size in self._cache:
            return self._cache[size]
        font: ImageFont.ImageFont
        if self.path is None:
            try:
                font = ImageFont.load_default(size=size)  # Pillow >= 10.1
            except TypeError:  # pragma: no cover - very old Pillow
                font = ImageFont.load_default()
        else:
            font = ImageFont.truetype(str(self.path), size=size)
        self._cache[size] = font
        return font

    def t(self, ko: str, en: str) -> str:
        """Pick the Korean label when the font can draw it, else the English one."""
        return ko if self.korean else en
