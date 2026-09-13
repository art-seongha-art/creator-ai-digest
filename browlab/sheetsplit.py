"""Cutting a sheet of drawn brow templates into one transparent PNG per brow.

Practitioners draw their templates as pairs on one white sheet, several pairs to a
page. This finds each pair, splits it into a left and a right brow, turns the white
paper into transparency and trims each one to its own ink.

Nothing here guesses at brightness thresholds for the artwork itself: the paper is
whatever the sheet's own white is, and every pixel darker than it keeps exactly the
alpha its darkness earns, so the wispy tips survive.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

Box = Tuple[int, int, int, int]


@dataclass
class Pair:
    """One row of the sheet: a left brow and a right brow."""

    index: int
    top: int
    bottom: int
    split: int
    left: Box
    right: Box
    label: str = ""


def ink_of(image: Image.Image, paper: Optional[float] = None) -> np.ndarray:
    """How far each pixel is from the paper, 0..255.

    ``paper`` defaults to the sheet's own white - the brightest value it actually
    uses - so a scan that is slightly grey does not come out as a grey wash.
    """
    grey = np.asarray(image.convert("L"), np.float32)
    white = float(np.percentile(grey, 99.5)) if paper is None else float(paper)
    white = max(white, 1.0)
    return np.clip((white - grey) * (255.0 / white), 0.0, 255.0)


def _runs(mask: Sequence[bool], min_len: int) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(mask) - start >= min_len:
        out.append((start, len(mask)))
    return out


def drop_flat_columns(ink: np.ndarray, min_run: int = 120, tol: float = 14.0) -> np.ndarray:
    """Blank out columns holding one long unbroken bar of near-constant ink.

    A screenshot of a sheet carries the scrollbar down one side. It is not the whole
    page high, so height alone does not find it; what gives it away is that its lit
    pixels are one continuous run of an even value. A column of drawn hair is broken
    up and its darkness varies along the stroke, so it never matches.
    """
    out = ink.copy()
    for x in range(out.shape[1]):
        col = out[:, x]
        runs = _runs(col > 2, min_run)
        for a, b in runs:
            piece = col[a:b]
            if float(piece.std()) <= tol:
                out[a:b, x] = 0.0
    return out


def find_pairs(ink: np.ndarray, min_gap: int = 12, min_height: int = 24,
               row_floor: float = 0.04) -> List[Pair]:
    """Each horizontal band of artwork, split into its left and right brow."""
    rows = ink.sum(axis=1)
    if rows.max() <= 0:
        return []
    bands = _runs(rows > rows.max() * row_floor, min_height)
    pairs: List[Pair] = []
    for n, (top, bottom) in enumerate(bands, 1):
        seg = ink[top:bottom]
        cols = seg.sum(axis=0)
        if cols.max() <= 0:
            continue
        mid = len(cols) // 2
        window = max(min_gap, len(cols) // 6)
        lo, hi = max(0, mid - window), min(len(cols), mid + window)
        # split in the middle of the emptiest stretch, not at its first column: with a
        # truly blank gap argmin lands on the edge of one brow
        quiet = cols[lo:hi] <= cols[lo:hi].min() + 1e-6
        runs = _runs(quiet, 1)
        a, b = max(runs, key=lambda r: r[1] - r[0]) if runs else (0, hi - lo)
        split = lo + (a + b) // 2
        left = _content_box(seg[:, :split], top, 0)
        right = _content_box(seg[:, split:], top, split)
        if left is None or right is None:
            continue
        pairs.append(Pair(index=n, top=top, bottom=bottom, split=split, left=left, right=right))
    return pairs


def _main_run(lit_cols: np.ndarray, max_gap: int) -> Tuple[int, int]:
    """The widest stretch of drawing, allowing gaps of up to ``max_gap`` blank columns.

    A long hair from the brow on the other side of the sheet reaches past the split and
    lands as a small clump separated by a wide blank gap; the brow itself never has one.
    """
    xs = np.nonzero(lit_cols)[0]
    if not len(xs):
        return 0, 0
    best = cur_start = xs[0]
    best_end = cur_end = xs[0]
    for x in xs[1:]:
        if x - cur_end <= max_gap + 1:
            cur_end = x
        else:
            if cur_end - cur_start > best_end - best:
                best, best_end = cur_start, cur_end
            cur_start = cur_end = x
    if cur_end - cur_start > best_end - best:
        best, best_end = cur_start, cur_end
    return int(best), int(best_end)


def _content_box(seg: np.ndarray, y_offset: int, x_offset: int, floor: float = 2.0,
                 gap_frac: float = 0.05) -> Optional[Box]:
    lit = seg > floor
    if not lit.any():
        return None
    cols = lit.any(axis=0)
    x0, x1 = _main_run(cols, max_gap=max(8, int(seg.shape[1] * gap_frac)))
    band = lit[:, x0:x1 + 1]
    if not band.any():
        return None
    ys = np.nonzero(band.any(axis=1))[0]
    return (x0 + x_offset, int(ys.min()) + y_offset,
            x1 + 1 + x_offset, int(ys.max()) + 1 + y_offset)


def lift_floor(ink: np.ndarray, floor: float) -> np.ndarray:
    """Drop everything at or below ``floor`` and stretch what is left back to full range.

    A sheet carries more than the drawing: a watermark, the paper's own gradient, and
    the pale halo anti-aliasing leaves around every stroke. On a real sheet all of it
    sat at or under ink 20 while the strokes themselves reached 191, so a floor takes
    the wash away whole and leaves the line work untouched. Rescaling afterwards keeps
    the strokes as dark as they were drawn.
    """
    if floor <= 0:
        return ink
    span = max(1.0, 255.0 - floor)
    return np.clip((ink - floor) * (255.0 / span), 0.0, 255.0)


def cut(image: Image.Image, ink: np.ndarray, box: Box, colour: Tuple[int, int, int] = (60, 48, 40),
        pad: int = 4) -> Image.Image:
    """One template: the drawing's own darkness as alpha, over a flat hair colour.

    Keeping a single colour rather than the scanned pixels means the template can be
    recoloured to the client's hair later without fighting the paper's cast.
    """
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(ink.shape[1], x1 + pad), min(ink.shape[0], y1 + pad)
    alpha = ink[y0:y1, x0:x1]
    out = np.zeros((alpha.shape[0], alpha.shape[1], 4), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = colour
    out[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def split_sheet(path: Path, out_dir: Path, *, names: Optional[Sequence[str]] = None,
                colour: Tuple[int, int, int] = (60, 48, 40),
                floor: float = 20.0) -> List[Path]:
    """Cut ``path`` into one PNG per brow under ``out_dir``. Returns what was written.

    ``floor`` clears the wash - watermark, paper gradient, the halo around each stroke -
    and what survives is rescaled so the line work keeps its original weight.
    """
    image = Image.open(path)
    image.load()
    ink = lift_floor(drop_flat_columns(ink_of(image)), floor)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for pair in find_pairs(ink):
        stem = (names[pair.index - 1] if names and pair.index <= len(names) else f"{pair.index:02d}")
        for side, box in (("right", pair.left), ("left", pair.right)):
            # the brow on the left of the sheet is the person's own right brow
            target = out_dir / f"{stem}_{side}.png"
            cut(image, ink, box, colour).save(target)
            written.append(target)
    return written
