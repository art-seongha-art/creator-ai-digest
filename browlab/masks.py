"""Eyebrow region masks, guide overlays and compositing helpers."""
from __future__ import annotations

from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .landmarks import FaceLandmarks

Box = Tuple[float, float, float, float]

# gpt-image-2 family size limits
GPT_IMAGE_MAX_EDGE = 3840
GPT_IMAGE_MIN_PIXELS = 655_360
GPT_IMAGE_MAX_PIXELS = 8_294_400
GPT_IMAGE_MULTIPLE = 16


def _brow_box(points, ipd: float, pad_side: float, pad_up: float, pad_down: float) -> Box:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (
        min(xs) - pad_side * ipd,
        min(ys) - pad_up * ipd,
        max(xs) + pad_side * ipd,
        max(ys) + pad_down * ipd,
    )


def brow_region_mask(
    lm: FaceLandmarks,
    *,
    pad_side: float = 0.16,
    pad_up: float = 0.40,
    pad_down: float = 0.12,
    protect_eyes: bool = True,
) -> Image.Image:
    """Return an ``L`` image: 255 where the eyebrows may be redrawn, 0 elsewhere.

    Padding values are fractions of the inter-pupillary distance so the mask
    scales with the face. ``pad_up`` is generous on purpose: new designs are
    often taller or higher than the existing brows.
    """
    mask = Image.new("L", (lm.width, lm.height), 0)
    draw = ImageDraw.Draw(mask)
    ipd = lm.ipd_px
    eye_limit = lm.eye_top_y - 0.06 * ipd if protect_eyes else None
    for pts in (lm.right_brow, lm.left_brow):
        if not pts:
            continue
        x0, y0, x1, y1 = _brow_box(pts, ipd, pad_side, pad_up, pad_down)
        if eye_limit is not None:
            y1 = min(y1, eye_limit)
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(float(lm.width - 1), x1), min(float(lm.height - 1), y1)
        if x1 <= x0 or y1 <= y0:
            continue
        radius = max(2, int(0.35 * (y1 - y0)))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=255)
    return mask


def api_mask_image(mask: Image.Image, base: Optional[Image.Image] = None) -> Image.Image:
    """Build the RGBA mask the Images API expects (alpha 0 = editable)."""
    size = mask.size
    rgb = base.convert("RGB").resize(size) if base is not None else Image.new("RGB", size, (0, 0, 0))
    alpha = ImageChops.invert(mask.convert("L"))
    out = rgb.copy()
    out.putalpha(alpha)
    return out


def guide_overlay(image: Image.Image, mask: Image.Image, color=(255, 0, 0), alpha: float = 0.45) -> Image.Image:
    """Tint the editable region so a vision model can see where changes are allowed."""
    base = image.convert("RGB")
    tint = Image.new("RGB", base.size, color)
    m = mask.convert("L").point(lambda v: int(v * alpha))
    return Image.composite(tint, base, m)


def composite_brows(original: Image.Image, edited: Image.Image, mask: Image.Image, feather_px: Optional[int] = None) -> Image.Image:
    """Paste only the masked (eyebrow) region of ``edited`` onto ``original``.

    Guarantees that everything outside the feathered mask stays pixel-identical
    to the original photo, whatever the model did elsewhere.
    """
    base = original.convert("RGB")
    top = edited.convert("RGB")
    if top.size != base.size:
        top = top.resize(base.size, Image.LANCZOS)
    m = mask.convert("L")
    if m.size != base.size:
        m = m.resize(base.size, Image.LANCZOS)
    if feather_px is None:
        feather_px = max(2, int(min(base.size) * 0.006))
    if feather_px > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather_px))
    return Image.composite(top, base, m)


def prepare_for_edit(image: Image.Image, max_edge: int = 2048) -> Tuple[Image.Image, float]:
    """Resize/crop an arbitrary photo so it satisfies the gpt-image-2 size rules.

    Returns the prepared RGB image and the scale factor applied (prepared / original).
    Edges become multiples of 16, the long edge is limited to ``max_edge`` and the
    pixel count stays inside the model limits.
    """
    img = image.convert("RGB")
    w, h = img.size
    scale = 1.0
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
    if (w * scale) * (h * scale) > GPT_IMAGE_MAX_PIXELS:
        scale = min(scale, (GPT_IMAGE_MAX_PIXELS / (w * h)) ** 0.5)
    if (w * scale) * (h * scale) < GPT_IMAGE_MIN_PIXELS:
        scale = max(scale, (GPT_IMAGE_MIN_PIXELS / (w * h)) ** 0.5 * 1.02)
    if abs(scale - 1.0) > 1e-6:
        img = img.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.LANCZOS)
    w, h = img.size
    cw = (w // GPT_IMAGE_MULTIPLE) * GPT_IMAGE_MULTIPLE
    ch = (h // GPT_IMAGE_MULTIPLE) * GPT_IMAGE_MULTIPLE
    if cw < GPT_IMAGE_MULTIPLE or ch < GPT_IMAGE_MULTIPLE:
        raise ValueError("image too small to edit")
    if (cw, ch) != (w, h):
        left = (w - cw) // 2
        top = (h - ch) // 2
        img = img.crop((left, top, left + cw, top + ch))
    return img, scale


def validate_gpt_image_size(size: str) -> str:
    """Validate a ``WIDTHxHEIGHT`` (or ``auto``) size string for gpt-image-2 models."""
    size = size.strip().lower()
    if size == "auto":
        return size
    try:
        w_s, h_s = size.split("x")
        w, h = int(w_s), int(h_s)
    except ValueError as exc:
        raise ValueError(f"size must look like 1536x2304 or auto, got {size!r}") from exc
    if max(w, h) > GPT_IMAGE_MAX_EDGE:
        raise ValueError("size: maximum edge length is 3840px")
    if w % GPT_IMAGE_MULTIPLE or h % GPT_IMAGE_MULTIPLE:
        raise ValueError("size: width and height must be multiples of 16px")
    if max(w, h) / min(w, h) > 3.0:
        raise ValueError("size: long-to-short edge ratio must not exceed 3:1")
    if not (GPT_IMAGE_MIN_PIXELS <= w * h <= GPT_IMAGE_MAX_PIXELS):
        raise ValueError("size: total pixels must be between 655,360 and 8,294,400")
    return f"{w}x{h}"
