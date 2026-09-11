"""Eyebrow region masks, guide overlays and compositing helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Tuple

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


def _dilated_polygon(draw: ImageDraw.ImageDraw, pts, radius: float, dy: float = 0.0) -> None:
    """Fill a polygon grown by ``radius`` (rounded), optionally shifted vertically by ``dy``."""
    shifted = [(x, y + dy) for x, y in pts]
    draw.polygon(shifted, fill=255)
    if radius <= 0:
        return
    draw.line(shifted + [shifted[0]], fill=255, width=max(1, int(round(2 * radius))), joint="curve")
    for x, y in shifted:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)


def brow_region_mask(
    lm: FaceLandmarks,
    *,
    pad_side: float = 0.10,
    pad_up: float = 0.14,
    pad_down: float = 0.06,
    protect_eyes: bool = True,
    shape: str = "brow",
) -> Image.Image:
    """Return an ``L`` image: 255 where the eyebrows may be redrawn, 0 elsewhere.

    ``shape="brow"`` (default) follows the detected eyebrow outline: the brow
    polygon grown by ``pad_side`` all around, stretched upwards by ``pad_up`` so
    higher/thicker designs have room, and by ``pad_down`` below, never reaching
    the upper eyelid. ``shape="box"`` is the older rounded bounding box.
    All paddings are fractions of the inter-pupillary distance.
    """
    mask = Image.new("L", (lm.width, lm.height), 0)
    ipd = lm.ipd_px
    brows = ((lm.right_brow, lm.right_upper_lid[1]), (lm.left_brow, lm.left_upper_lid[1]))
    for pts, lid_y in brows:
        if not pts:
            continue
        layer = Image.new("L", mask.size, 0)
        draw = ImageDraw.Draw(layer)
        if shape == "box":
            x0, y0, x1, y1 = _brow_box(pts, ipd, pad_side, pad_up, pad_down)
            if protect_eyes:
                y1 = min(y1, lid_y - 0.06 * ipd)
            x0, y0 = max(0.0, x0), max(0.0, y0)
            x1, y1 = min(float(lm.width - 1), x1), min(float(lm.height - 1), y1)
            if x1 <= x0 or y1 <= y0:
                continue
            draw.rounded_rectangle((x0, y0, x1, y1), radius=max(2, int(0.35 * (y1 - y0))), fill=255)
        else:
            radius = max(1.0, pad_side * ipd)
            up = max(0.0, pad_up * ipd)
            down = max(0.0, pad_down * ipd)
            steps = max(1, int(round((up + down) / max(radius, 1.0))) * 2)
            for i in range(steps + 1):
                dy = -up + (up + down) * i / steps
                _dilated_polygon(draw, pts, radius, dy)
        if protect_eyes:
            limit = int(lid_y - 0.05 * ipd)
            if limit < lm.height:
                draw.rectangle((0, max(0, limit), lm.width, lm.height), fill=0)
        mask = ImageChops.lighter(mask, layer)
    return mask


def api_mask_image(mask: Image.Image, base: Optional[Image.Image] = None) -> Image.Image:
    """Build the RGBA mask the Images API expects (alpha 0 = editable).

    Only the alpha channel matters to the API, so the colour channels stay black by
    default: a photo-based RGBA PNG of a 1024x1536 tile can exceed the 4 MB mask limit.
    """
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
        feather_px = feather_for(m)
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


# ---------------------------------------------------------------------------
# Face tile workflow: crop the face, edit the tile, align, paste the brows back
# ---------------------------------------------------------------------------
def parse_size(text: str) -> Tuple[int, int]:
    w_s, h_s = validate_gpt_image_size(text).split("x")
    return int(w_s), int(h_s)


def downscale_to(image: Image.Image, max_edge: int = 2048) -> Tuple[Image.Image, float]:
    """Shrink (never crop) so the long edge is at most ``max_edge``. Returns (image, scale)."""
    img = image.convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_edge:
        return img, 1.0
    scale = max_edge / long_edge
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS), scale


def face_tile_box(lm: FaceLandmarks, image_size: Tuple[int, int], *, aspect: float = 2.0 / 3.0, margin: float = 1.0) -> Tuple[int, int, int, int]:
    """Box around the face (hair top to below the chin) with the requested width/height ratio.

    The box is centred on the eyes, scaled down if the photo is smaller than the
    box and shifted inside the photo, so cropping it never distorts or pads.
    """
    W, H = image_size
    d = lm.ipd_px
    cx, _ = lm.eye_center
    half_w = max(1.45 * d, abs(lm.left_cheek[0] - lm.right_cheek[0]) / 2.0 + 0.3 * d) * margin
    top = min(lm.forehead_top[1] - 0.9 * d * margin, lm.brow_top_y - 1.2 * d)
    bottom = lm.chin[1] + 0.35 * d * margin
    w, h = 2.0 * half_w, bottom - top
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    cy = (top + bottom) / 2.0
    s = min(1.0, W / w, H / h)
    w, h = w * s, h * s
    x0 = min(max(cx - w / 2.0, 0.0), W - w)
    y0 = min(max(cy - h / 2.0, 0.0), H - h)
    return int(round(x0)), int(round(y0)), int(round(x0 + w)), int(round(y0 + h))


def crop_tile(image: Image.Image, box: Tuple[int, int, int, int], tile_size: Tuple[int, int]) -> Image.Image:
    return image.convert("RGB").crop(box).resize(tile_size, Image.LANCZOS)


def landmarks_to_tile(lm: FaceLandmarks, box: Tuple[int, int, int, int], tile_size: Tuple[int, int]) -> FaceLandmarks:
    x0, y0, x1, y1 = box
    sx = tile_size[0] / float(max(1, x1 - x0))
    out = lm.translated(-x0, -y0, width=x1 - x0, height=y1 - y0).scaled(sx)
    out.width, out.height = int(tile_size[0]), int(tile_size[1])  # exact tile size (rounding may differ by a pixel)
    return out


@dataclass
class Similarity:
    """Maps edited-image coordinates onto reference coordinates: r = scale * R(angle) * e + t."""

    scale: float
    angle_deg: float
    tx: float
    ty: float
    shift_frac: float   # largest pupil displacement, as a fraction of the reference IPD
    scale_ratio: float  # |scale - 1|


def similarity_from_pupils(src: FaceLandmarks, ref: FaceLandmarks) -> Similarity:
    sx0, sy0 = src.right_pupil
    sx1, sy1 = src.left_pupil
    rx0, ry0 = ref.right_pupil
    rx1, ry1 = ref.left_pupil
    vs = (sx1 - sx0, sy1 - sy0)
    vr = (rx1 - rx0, ry1 - ry0)
    ls = math.hypot(*vs)
    lr = math.hypot(*vr)
    if ls <= 0 or lr <= 0:
        raise ValueError("pupils coincide")
    scale = lr / ls
    angle = math.atan2(vr[1], vr[0]) - math.atan2(vs[1], vs[0])
    cs, sn = math.cos(angle), math.sin(angle)
    cxs, cys = src.eye_center
    cxr, cyr = ref.eye_center
    tx = cxr - scale * (cs * cxs - sn * cys)
    ty = cyr - scale * (sn * cxs + cs * cys)
    shift = max(math.hypot(sx0 - rx0, sy0 - ry0), math.hypot(sx1 - rx1, sy1 - ry1)) / lr
    return Similarity(scale, math.degrees(angle), tx, ty, shift, abs(scale - 1.0))


def warp_similarity(image: Image.Image, sim: Similarity, size: Tuple[int, int]) -> Image.Image:
    """Resample ``image`` so that its face lands where the reference face is."""
    a = math.radians(sim.angle_deg)
    cs, sn = math.cos(a), math.sin(a)
    s = sim.scale
    coeffs = (
        cs / s, sn / s, -(cs * sim.tx + sn * sim.ty) / s,
        -sn / s, cs / s, (sn * sim.tx - cs * sim.ty) / s,
    )
    return image.convert("RGB").transform(size, Image.AFFINE, coeffs, resample=Image.BICUBIC)


def _mask_band_px(mask: Image.Image) -> int:
    """Vertical thickness of the masked band (both brows sit at a similar height)."""
    bbox = mask.getbbox()
    return max(1, bbox[3] - bbox[1]) if bbox else 1


def feather_for(mask: Image.Image, frac: float = 0.10, minimum: int = 3) -> int:
    """Soften the mask edge in proportion to how thick the brow band is."""
    return max(minimum, int(round(frac * _mask_band_px(mask))))


def _box_blur(a: "Any", radius: int, passes: int = 3) -> "Any":
    """Separable box blur (3 passes ≈ Gaussian), zero-padded, numpy only."""
    import numpy as np

    r = max(1, int(round(radius / passes)))
    out = a.astype(np.float32)
    k = 2 * r + 1
    for _ in range(passes):
        for axis in (0, 1):
            pad = [(0, 0)] * out.ndim
            pad[axis] = (r, r)
            c = np.cumsum(np.pad(out, pad, mode="constant"), axis=axis)
            head = list(c.shape)
            head[axis] = 1
            c = np.concatenate([np.zeros(head, np.float32), c], axis=axis)
            n = out.shape[axis]
            hi = [slice(None)] * out.ndim
            lo = [slice(None)] * out.ndim
            hi[axis] = slice(k, k + n)
            lo[axis] = slice(0, n)
            out = (c[tuple(hi)] - c[tuple(lo)]) / k
    return out


def match_tone(
    edited: Image.Image,
    original: Image.Image,
    mask: Image.Image,
    ring_px: Optional[int] = None,
    max_offset: int = 40,
) -> Image.Image:
    """Make the edited brow area sit in the original photo's skin without a seam.

    A single global offset leaves a visible colour step at the mask edge, and the
    ring around a brow contains hair, lashes and eyes, which skews it. Instead a
    smooth correction field is measured on the skin just outside the mask and
    extrapolated inwards (a cheap seamless-cloning approximation): at the border
    the corrected image equals the original, further in the field varies slowly,
    so the model's brow detail survives while the colour follows the local skin.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy ships with mediapipe
        return edited.convert("RGB")

    base = original.convert("RGB")
    top = edited.convert("RGB")
    if top.size != base.size:
        top = top.resize(base.size, Image.LANCZOS)
    m = mask.convert("L")
    if m.size != base.size:
        m = m.resize(base.size, Image.LANCZOS)

    # The radius must span the mask, otherwise the field never reaches its middle.
    band = int(ring_px or max(12, _mask_band_px(m)))
    inside = np.asarray(m, np.float32) / 255.0
    near = np.asarray(m.filter(ImageFilter.GaussianBlur(band * 0.6)), np.float32) / 255.0
    valid = (near > 0.02) & (inside < 0.02)          # skin close to the mask, not the mask itself
    if int(valid.sum()) < 64:
        return top

    o = np.asarray(base, np.float32)
    e = np.asarray(top, np.float32)
    luma = o @ np.array([0.299, 0.587, 0.114], np.float32)
    median = float(np.median(luma[valid]))
    skin = valid & (np.abs(luma - median) <= 40.0)    # drop hair, lashes, background
    if int(skin.sum()) >= 64:
        valid = skin
    w = valid.astype(np.float32)

    diff = (o - e) * w[..., None]
    den = _box_blur(w, band)
    field = _box_blur(diff, band) / np.maximum(den, 1e-6)[..., None]
    weak = den < 0.02                                 # too far from any sample: use the overall shift
    if bool(weak.any()):
        field[weak] = (o - e)[valid].mean(axis=0)
    field = np.clip(field, -max_offset, max_offset)
    return Image.fromarray(np.clip(e + field, 0, 255).astype(np.uint8))


def paste_back(full: Image.Image, tile_result: Image.Image, box: Tuple[int, int, int, int], mask_tile: Image.Image, feather_px: Optional[int] = None) -> Image.Image:
    """Put the (composited) tile back into the full photo, only where the mask allows."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    back = tile_result.convert("RGB").resize((w, h), Image.LANCZOS)
    m = mask_tile.convert("L").resize((w, h), Image.LANCZOS)
    if feather_px is None:
        feather_px = feather_for(m)
    if feather_px > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather_px))
    out = full.convert("RGB").copy()
    region = out.crop(box)
    out.paste(Image.composite(back, region, m), (x0, y0))
    return out
