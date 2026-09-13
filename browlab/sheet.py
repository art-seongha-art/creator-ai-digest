"""A4 sheet composition: life-size (1:1) faces, brow-zone strips and grids."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from .fonts import SheetFont
from .landmarks import FaceLandmarks

A4_MM: Tuple[float, float] = (210.0, 297.0)
DEFAULT_DPI = 300

GRAY = (120, 120, 120)
LIGHT = (185, 185, 185)
DARK = (40, 40, 40)
GUIDE = (170, 170, 170)


def px_per_mm(dpi: int) -> float:
    return dpi / 25.4


def mm2px(mm: float, dpi: int) -> int:
    return int(round(mm * px_per_mm(dpi)))


@dataclass
class SheetOptions:
    dpi: int = DEFAULT_DPI
    ipd_mm: float = 63.0
    guides: bool = False
    title: str = ""
    caption: str = ""
    note: str = ""
    extra_lines: List[str] = field(default_factory=list)
    margin_mm: float = 8.0
    header_mm: float = 18.0
    footer_mm: float = 30.0
    font_path: Optional[str] = None
    show_ruler: bool = True
    fallback_image_height_mm: float = 320.0
    hair_allowance: float = 0.95   # in IPD units above the hairline
    neck_allowance: float = 0.35   # in IPD units below the chin
    print_scale: float = 1.0       # 1.0 = life size; 1.1 prints the face 10% larger
    grow_mm: float = 0.0           # extra mm of face on every side (left, right, top, bottom)

    @property
    def page_px(self) -> Tuple[int, int]:
        return mm2px(A4_MM[0], self.dpi), mm2px(A4_MM[1], self.dpi)


# ---------------------------------------------------------------------------
# drawing primitives
# ---------------------------------------------------------------------------
def _dashed_line(draw: ImageDraw.ImageDraw, p0, p1, fill, width: int, dash: Tuple[int, int] = (18, 12)) -> None:
    x0, y0 = p0
    x1, y1 = p1
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if length <= 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    on, off = dash
    pos = 0.0
    while pos < length:
        end = min(pos + on, length)
        draw.line([(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)], fill=fill, width=width)
        pos = end + off


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def _draw_ruler(draw: ImageDraw.ImageDraw, x: int, y: int, length_mm: int, dpi: int, font, label: str) -> None:
    """Horizontal ruler with 1 mm ticks; ``y`` is the baseline of the ruler bar."""
    ppm = px_per_mm(dpi)
    end_x = x + int(round(length_mm * ppm))
    draw.line([(x, y), (end_x, y)], fill=DARK, width=max(2, int(ppm * 0.25)))
    for mm in range(0, length_mm + 1):
        tx = x + int(round(mm * ppm))
        if mm % 10 == 0:
            h = int(ppm * 4.0)
            w = max(2, int(ppm * 0.25))
        elif mm % 5 == 0:
            h = int(ppm * 2.6)
            w = max(1, int(ppm * 0.18))
        else:
            h = int(ppm * 1.5)
            w = max(1, int(ppm * 0.12))
        draw.line([(tx, y), (tx, y - h)], fill=DARK, width=w)
        if mm % 10 == 0:
            num = str(mm)
            tw, th = _text_size(draw, num, font)
            draw.text((tx - tw // 2, y + int(ppm * 0.8)), num, font=font, fill=DARK)
    lw, lh = _text_size(draw, label, font)
    draw.text((x, y + int(ppm * 4.2)), label, font=font, fill=GRAY)


def _draw_square(draw: ImageDraw.ImageDraw, x: int, y: int, size_mm: float, dpi: int, font, label: str) -> None:
    s = mm2px(size_mm, dpi)
    draw.rectangle((x, y, x + s, y + s), outline=DARK, width=max(2, int(px_per_mm(dpi) * 0.25)))
    tw, th = _text_size(draw, label, font)
    draw.text((x + (s - tw) // 2, y + s + int(px_per_mm(dpi) * 0.8)), label, font=font, fill=GRAY)


# ---------------------------------------------------------------------------
# scaling / placement
# ---------------------------------------------------------------------------
def face_width_mm(lm: FaceLandmarks, ipd_mm: float) -> float:
    """Cheekbone-to-cheekbone width at the print scale (adult average: 137 mm / 146 mm)."""
    return abs(lm.left_cheek[0] - lm.right_cheek[0]) / lm.ipd_px * ipd_mm


def face_height_mm(lm: FaceLandmarks, ipd_mm: float) -> float:
    """Hairline-to-chin height at the print scale (adult average: 180 mm / 190 mm)."""
    return abs(lm.chin[1] - lm.forehead_top[1]) / lm.ipd_px * ipd_mm


def enlargement(lm: Optional[FaceLandmarks], opts: SheetOptions) -> float:
    """How much bigger than life size the face is printed.

    ``print_scale`` is the multiplier itself; ``grow_mm`` is the friendlier way
    to ask for it - "another centimetre on each side" - and is turned into a
    multiplier from the face's own measured width.
    """
    factor = opts.print_scale if opts.print_scale > 0 else 1.0
    if opts.grow_mm and lm is not None and lm.ipd_px > 0:
        width = face_width_mm(lm, opts.ipd_mm)
        if width > 0:
            factor *= (width + 2.0 * opts.grow_mm) / width
    return factor


def size_note(lm: Optional[FaceLandmarks], opts: SheetOptions, ko: bool = True) -> str:
    """The printed size in millimetres, so a ruler can confirm the sheet is right."""
    factor = enlargement(lm, opts)
    pct = "" if abs(factor - 1.0) < 0.005 else (f" · 배율 {factor * 100:.0f}%" if ko else f" · {factor * 100:.0f}% of life size")
    if lm is None or lm.ipd_px <= 0:
        return ("동공간 거리 기준 없음" + pct) if ko else ("no IPD reference" + pct)
    w = face_width_mm(lm, opts.ipd_mm) * factor
    h = face_height_mm(lm, opts.ipd_mm) * factor
    if ko:
        return f"동공간 {opts.ipd_mm * factor:.0f} mm · 얼굴 너비 {w:.0f} × 헤어라인~턱 {h:.0f} mm{pct}"
    return f"IPD {opts.ipd_mm * factor:.0f} mm · face {w:.0f} x {h:.0f} mm{pct}"


def life_size_scale(lm: Optional[FaceLandmarks], image_height_px: int, opts: SheetOptions) -> float:
    """Pixels-on-sheet per pixel-in-image so the face prints at real size."""
    ppm = px_per_mm(opts.dpi)
    if lm is not None and lm.ipd_px > 0:
        return (opts.ipd_mm * ppm) / lm.ipd_px * enlargement(lm, opts)
    return (opts.fallback_image_height_mm * ppm) / float(image_height_px) * enlargement(lm, opts)


def _scaled_face(image: Image.Image, lm: Optional[FaceLandmarks], scale: float) -> Tuple[Image.Image, Optional[FaceLandmarks]]:
    w, h = image.size
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    scaled = image.convert("RGB").resize(size, Image.LANCZOS)
    return scaled, (lm.scaled(scale) if lm is not None else None)


def place_face(
    scaled: Image.Image,
    lm: Optional[FaceLandmarks],
    area: Tuple[int, int, int, int],
    opts: SheetOptions,
) -> Tuple[Image.Image, Optional[FaceLandmarks]]:
    """Paste ``scaled`` into a white layer covering ``area`` (x0, y0, x1, y1).

    Returns the layer and the landmarks translated into page coordinates.
    """
    x0, y0, x1, y1 = area
    aw, ah = x1 - x0, y1 - y0
    layer = Image.new("RGB", (aw, ah), "white")
    sw, sh = scaled.size
    if lm is None:
        px = (aw - sw) // 2
        py = (ah - sh) // 2 if sh <= ah else 0
        layer.paste(scaled, (px, py))
        return layer, None
    ipd = lm.ipd_px
    box_top = lm.forehead_top[1] - opts.hair_allowance * ipd
    box_bottom = lm.chin[1] + opts.neck_allowance * ipd
    box_h = box_bottom - box_top
    cx = lm.eye_center[0]
    px = int(round(aw / 2.0 - cx))
    if box_h <= ah:
        py = int(round((ah - box_h) / 2.0 - box_top))
    else:
        # Not everything fits: keep eyebrows, eyes and chin; sacrifice hair/neck.
        py = int(round(0.40 * ah - lm.eye_center[1]))
        brow_top_on_layer = lm.brow_top_y - 0.6 * ipd + py
        if brow_top_on_layer < 0:
            py -= int(brow_top_on_layer)
    layer.paste(scaled, (px, py))
    page_lm = lm.translated(px + x0, py + y0, width=aw, height=ah)
    return layer, page_lm


def _draw_guides(draw: ImageDraw.ImageDraw, lm: FaceLandmarks, dpi: int) -> None:
    ipd = lm.ipd_px
    width = max(2, int(px_per_mm(dpi) * 0.2))
    top_y = lm.brow_top_y - 0.75 * ipd
    iris_r = 0.095 * ipd

    def through(origin, target):
        ox, oy = origin
        tx, ty = target
        if abs(ty - oy) < 1e-6:
            return
        # extend the line from origin through target up to top_y
        k = (top_y - oy) / (ty - oy)
        end = (ox + (tx - ox) * k, top_y)
        _dashed_line(draw, origin, end, GUIDE, width)

    # subject's right side (image left)
    through(lm.right_ala, lm.right_inner)
    through(lm.right_ala, (lm.right_pupil[0] - iris_r, lm.right_pupil[1]))
    through(lm.right_ala, lm.right_outer)
    # subject's left side (image right)
    through(lm.left_ala, lm.left_inner)
    through(lm.left_ala, (lm.left_pupil[0] + iris_r, lm.left_pupil[1]))
    through(lm.left_ala, lm.left_outer)
    # horizontal reference through the pupils
    _dashed_line(draw, (lm.right_outer[0] - 0.6 * ipd, lm.eye_center[1]), (lm.left_outer[0] + 0.6 * ipd, lm.eye_center[1]), GUIDE, width)


# ---------------------------------------------------------------------------
# sheets
# ---------------------------------------------------------------------------
def _header_footer(canvas: Image.Image, opts: SheetOptions, fonts: SheetFont, subtitle_right: str, factor: float = 1.0) -> Tuple[int, int]:
    """Draw the header/footer chrome. Returns (content_top, content_bottom) in px."""
    draw = ImageDraw.Draw(canvas)
    W, H = canvas.size
    dpi = opts.dpi
    m = mm2px(opts.margin_mm, dpi)
    f_title = fonts.get(mm2px(4.0, dpi))
    f_body = fonts.get(mm2px(2.8, dpi))
    f_small = fonts.get(mm2px(2.2, dpi))

    if abs(factor - 1.0) < 0.005:
        default_title = fonts.t("눈썹 디자인 연습 시트 · 실물 크기 1:1", "Brow design practice sheet · life size 1:1")
    else:
        default_title = fonts.t(f"눈썹 디자인 연습 시트 · 실물의 {factor * 100:.0f}%",
                                f"Brow design practice sheet · {factor * 100:.0f}% of life size")
    title = opts.title or default_title
    draw.text((m, m), title, font=f_title, fill=DARK)
    if subtitle_right:
        tw, th = _text_size(draw, subtitle_right, f_small)
        draw.text((W - m - tw, m + mm2px(0.8, dpi)), subtitle_right, font=f_small, fill=GRAY)
    if opts.caption:
        draw.text((m, m + mm2px(5.6, dpi)), opts.caption, font=f_body, fill=GRAY)
    header_bottom = m + mm2px(opts.header_mm, dpi)
    draw.line([(m, header_bottom - mm2px(2, dpi)), (W - m, header_bottom - mm2px(2, dpi))], fill=LIGHT, width=max(1, int(px_per_mm(dpi) * 0.12)))

    footer_top = H - m - mm2px(opts.footer_mm, dpi)
    draw.line([(m, footer_top), (W - m, footer_top)], fill=LIGHT, width=max(1, int(px_per_mm(dpi) * 0.12)))
    y = footer_top + mm2px(2.5, dpi)
    if opts.show_ruler:
        ruler_y = y + mm2px(5.0, dpi)
        _draw_ruler(
            draw, m, ruler_y, 100, dpi, f_small,
            fonts.t("100 mm — 인쇄 후 자로 확인 (프린터 배율 100% / '실제 크기', '페이지에 맞춤' 해제)",
                    "100 mm — check with a ruler after printing (print at 100% / actual size, no 'fit to page')"),
        )
        _draw_square(draw, W - m - mm2px(20, dpi), y, 20, dpi, f_small, fonts.t("20 mm 정사각형", "20 mm square"))
    ty = footer_top + mm2px(15.5, dpi)
    lines: List[str] = []
    if opts.note:
        lines.append(opts.note)
    lines.extend(opts.extra_lines)
    if opts.guides:
        lines.append(fonts.t(
            "가이드선: 콧방울→눈 앞머리(눈썹 앞), 콧방울→홍채 바깥(눈썹 산), 콧방울→눈꼬리(눈썹 끝)",
            "Guides: nostril→inner eye corner (brow head), nostril→outer iris (arch), nostril→outer eye corner (tail)",
        ))
    for line in lines[:4]:
        draw.text((m, ty), line, font=f_small, fill=GRAY)
        ty += mm2px(3.2, dpi)
    return header_bottom, footer_top


def compose_face_sheet(
    image: Image.Image,
    lm: Optional[FaceLandmarks],
    opts: SheetOptions,
    fonts: Optional[SheetFont] = None,
) -> Tuple[Image.Image, Optional[FaceLandmarks]]:
    """One life-size face per A4 page."""
    fonts = fonts or SheetFont(opts.font_path)
    W, H = opts.page_px
    canvas = Image.new("RGB", (W, H), "white")
    scale = life_size_scale(lm, image.size[1], opts)
    if lm is not None:
        right = fonts.t(f"A4 {opts.dpi}dpi · {size_note(lm, opts, ko=True)} · 랜드마크 {lm.source}",
                        f"A4 {opts.dpi}dpi · {size_note(lm, opts, ko=False)} · landmarks: {lm.source}")
    else:
        right = fonts.t(f"A4 {opts.dpi}dpi · 이미지 높이 {opts.fallback_image_height_mm:.0f} mm 가정 (랜드마크 없음)",
                        f"A4 {opts.dpi}dpi · assumes image height {opts.fallback_image_height_mm:.0f} mm (no landmarks)")
    top, bottom = _header_footer(canvas, opts, fonts, right, enlargement(lm, opts))
    m = mm2px(opts.margin_mm, opts.dpi)
    area = (m, top, W - m, bottom - mm2px(2, opts.dpi))
    scaled, lm_s = _scaled_face(image, lm, scale)
    layer, page_lm = place_face(scaled, lm_s, area, opts)
    canvas.paste(layer, (area[0], area[1]))
    if opts.guides and page_lm is not None:
        _draw_guides(ImageDraw.Draw(canvas), page_lm, opts.dpi)
    return canvas, page_lm


def browzone_box(lm: FaceLandmarks) -> Tuple[int, int, int, int]:
    """Crop box (in the landmarks' coordinate space) around eyebrows and eyes."""
    ipd = lm.ipd_px
    x0 = min(lm.right_cheek[0], lm.right_outer[0] - 0.7 * ipd) - 0.05 * ipd
    x1 = max(lm.left_cheek[0], lm.left_outer[0] + 0.7 * ipd) + 0.05 * ipd
    y0 = lm.brow_top_y - 0.25 * ipd
    y1 = lm.eye_center[1] + 0.25 * ipd
    return int(x0), int(y0), int(x1), int(y1)


def compose_browzone_sheet(
    items: Sequence[Tuple[str, Image.Image, FaceLandmarks]],
    opts: SheetOptions,
    fonts: Optional[SheetFont] = None,
    copies: int = 1,
) -> List[Image.Image]:
    """Life-size strips of the eyebrow/eye zone, several per page.

    ``items`` are (label, image, landmarks); each item is repeated ``copies``
    times. Pages are added as needed.
    """
    fonts = fonts or SheetFont(opts.font_path)
    W, H = opts.page_px
    dpi = opts.dpi
    m = mm2px(opts.margin_mm, dpi)
    gap = mm2px(3, dpi)
    label_h = mm2px(4, dpi)
    f_label = fonts.get(mm2px(2.6, dpi))

    factor = enlargement(items[0][2], opts) if items else 1.0
    strips: List[Tuple[str, Image.Image]] = []
    for label, image, lm in items:
        scale = life_size_scale(lm, image.size[1], opts)
        scaled, lm_s = _scaled_face(image, lm, scale)
        assert lm_s is not None
        box = browzone_box(lm_s)
        x0, y0, x1, y1 = box
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(scaled.size[0], x1), min(scaled.size[1], y1)
        crop = scaled.crop((x0, y0, x1, y1))
        max_w = W - 2 * m
        if crop.size[0] > max_w:  # very wide faces: keep centre
            off = (crop.size[0] - max_w) // 2
            crop = crop.crop((off, 0, off + max_w, crop.size[1]))
        for _ in range(max(1, copies)):
            strips.append((label, crop))

    pages: List[Image.Image] = []
    canvas: Optional[Image.Image] = None
    y = 0
    bottom = 0
    for label, crop in strips:
        need = crop.size[1] + label_h + gap
        if canvas is None or y + need > bottom:
            canvas = Image.new("RGB", (W, H), "white")
            scale_ko = "1:1" if abs(factor - 1.0) < 0.005 else f"실물의 {factor * 100:.0f}%"
            scale_en = "1:1" if abs(factor - 1.0) < 0.005 else f"{factor * 100:.0f}% of life size"
            right = fonts.t(f"눈썹 구역 {scale_ko} · A4 {dpi}dpi · 동공간 {opts.ipd_mm * factor:.0f} mm",
                            f"Brow zone {scale_en} · A4 {dpi}dpi · IPD {opts.ipd_mm * factor:.0f} mm")
            top, bottom = _header_footer(canvas, opts, fonts, right, factor)
            y = top
            pages.append(canvas)
        draw = ImageDraw.Draw(canvas)
        x = (W - crop.size[0]) // 2
        canvas.paste(crop, (x, y))
        draw.rectangle((x - 1, y - 1, x + crop.size[0], y + crop.size[1]), outline=LIGHT, width=1)
        draw.text((x, y + crop.size[1] + mm2px(0.8, dpi)), label, font=f_label, fill=GRAY)
        y += need
    return pages


def compose_grid_sheet(
    items: Sequence[Tuple[str, Image.Image]],
    opts: SheetOptions,
    fonts: Optional[SheetFont] = None,
    cols: int = 2,
    rows: Optional[int] = None,
) -> List[Image.Image]:
    """Comparison grid (not life size), e.g. original + brow-style variants."""
    fonts = fonts or SheetFont(opts.font_path)
    W, H = opts.page_px
    dpi = opts.dpi
    m = mm2px(opts.margin_mm, dpi)
    gap = mm2px(4, dpi)
    label_h = mm2px(5, dpi)
    f_label = fonts.get(mm2px(2.6, dpi))
    cols = max(1, cols)
    if rows is None:
        rows = 2 if len(items) <= 4 else 3
    rows = max(1, rows)
    per_page = cols * rows
    cell_w = (W - 2 * m - gap * (cols - 1)) // cols
    pages: List[Image.Image] = []
    subtitle = fonts.t("비교 시트 (실물 크기 아님)", "Comparison sheet (not life size)")
    for start in range(0, len(items), per_page):
        canvas = Image.new("RGB", (W, H), "white")
        top, bottom = _header_footer(canvas, opts, fonts, subtitle)
        cell_h = (bottom - top - gap * (rows - 1)) // rows
        draw = ImageDraw.Draw(canvas)
        for idx, (label, image) in enumerate(items[start:start + per_page]):
            r, c = divmod(idx, cols)
            img = image.convert("RGB")
            avail_h = cell_h - label_h
            ratio = min(cell_w / img.size[0], avail_h / img.size[1], 1.0)
            thumb = img.resize((max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio))), Image.LANCZOS)
            x = m + c * (cell_w + gap) + (cell_w - thumb.size[0]) // 2
            y = top + r * (cell_h + gap)
            canvas.paste(thumb, (x, y))
            draw.rectangle((x - 1, y - 1, x + thumb.size[0], y + thumb.size[1]), outline=LIGHT, width=1)
            draw.text((x, y + thumb.size[1] + mm2px(0.8, dpi)), label, font=f_label, fill=GRAY)
        pages.append(canvas)
    return pages


def calibration_sheet(opts: SheetOptions, fonts: Optional[SheetFont] = None) -> Image.Image:
    """A page with rulers only, to verify the printer really prints at 100%."""
    fonts = fonts or SheetFont(opts.font_path)
    W, H = opts.page_px
    dpi = opts.dpi
    canvas = Image.new("RGB", (W, H), "white")
    o = SheetOptions(**{**opts.__dict__, "title": fonts.t("인쇄 배율 확인 시트", "Print scale check sheet"), "caption": fonts.t("이 페이지의 눈금이 실제 mm와 같으면 얼굴 시트도 1:1로 인쇄됩니다.", "If these rulers measure true, face sheets print at 1:1 too.")})
    top, bottom = _header_footer(canvas, o, fonts, fonts.t(f"A4 {dpi}dpi", f"A4 {dpi}dpi"))
    draw = ImageDraw.Draw(canvas)
    m = mm2px(opts.margin_mm, dpi)
    f_small = fonts.get(mm2px(2.2, dpi))
    _draw_ruler(draw, m, top + mm2px(12, dpi), 150, dpi, f_small, fonts.t("150 mm", "150 mm"))
    # vertical 150 mm ruler
    ppm = px_per_mm(dpi)
    x = m + mm2px(6, dpi)
    y0 = top + mm2px(30, dpi)
    draw.line([(x, y0), (x, y0 + int(150 * ppm))], fill=DARK, width=max(2, int(ppm * 0.25)))
    for mm in range(0, 151):
        ty = y0 + int(round(mm * ppm))
        length = ppm * (4.0 if mm % 10 == 0 else 2.6 if mm % 5 == 0 else 1.5)
        draw.line([(x, ty), (x + int(length), ty)], fill=DARK, width=max(1, int(ppm * (0.25 if mm % 10 == 0 else 0.12))))
        if mm % 10 == 0:
            draw.text((x + int(ppm * 5), ty - mm2px(1.2, dpi)), str(mm), font=f_small, fill=DARK)
    _draw_square(draw, W - m - mm2px(50, dpi), y0, 50, dpi, f_small, fonts.t("50 mm 정사각형", "50 mm square"))
    # a life-size average eye pair for reference
    f_body = fonts.get(mm2px(2.8, dpi))
    cy = y0 + mm2px(95, dpi)
    cx = W // 2 + mm2px(20, dpi)
    half = mm2px(opts.ipd_mm / 2.0, dpi)
    r = mm2px(5.9, dpi)
    for sx in (-1, 1):
        draw.ellipse((cx + sx * half - r, cy - r, cx + sx * half + r, cy + r), outline=GRAY, width=max(2, int(ppm * 0.2)))
        draw.ellipse((cx + sx * half - r // 3, cy - r // 3, cx + sx * half + r // 3, cy + r // 3), fill=GRAY)
    draw.text((cx - half, cy + r + mm2px(2, dpi)), fonts.t(f"동공 간 거리 {opts.ipd_mm:.0f} mm (성인 평균)", f"Inter-pupillary distance {opts.ipd_mm:.0f} mm (adult average)"), font=f_body, fill=GRAY)
    return canvas


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------
def save_pages(pages: Iterable[Image.Image], out_base: Path, dpi: int, pdf: bool = True) -> List[Path]:
    """Save pages as PNG(s) with DPI metadata and one (multi-page) PDF."""
    pages = list(pages)
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for i, page in enumerate(pages, 1):
        suffix = "" if len(pages) == 1 else f"_p{i}"
        png = out_base.with_name(out_base.stem + suffix + ".png")
        page.save(png, dpi=(dpi, dpi))
        written.append(png)
    if pdf and pages:
        pdf_path = out_base.with_suffix(".pdf")
        first, rest = pages[0], pages[1:]
        first.save(pdf_path, "PDF", resolution=float(dpi), save_all=bool(rest), append_images=rest)
        written.append(pdf_path)
    return written


def today_stamp() -> str:
    return _dt.date.today().isoformat()
