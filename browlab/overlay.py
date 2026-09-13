"""Placing a hand-drawn brow template on a detected face.

The generative path asks a model to draw a brow and then fights it about where the
brow went. This path never asks: a transparent PNG drawn once by the practitioner is
placed on the face by measurement, and adjusted by dragging control points.

A template is authored in one canonical orientation - the **right** brow of the person
(the left one as you look at the photo), head at the left edge of the image, tail at
the right, filling the frame. The left brow is the same file mirrored.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .landmarks import FaceLandmarks

Pt = Tuple[float, float]


@dataclass
class Placement:
    """Where one template goes, in the coordinates of the photo it is placed on.

    ``(x, y)`` is the brow head. ``angle_deg`` is the head-to-tail direction, positive
    clockwise on screen. ``length`` and ``thickness`` are the box the template is drawn
    into before rotation; ``mirror`` flips it for the other side of the face.
    """

    side: str            # "right" / "left" - the person's own side
    x: float
    y: float
    angle_deg: float
    length: float
    thickness: float
    mirror: bool

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("x", "y", "angle_deg", "length", "thickness"):
            d[k] = round(float(d[k]), 2)
        return d


def _axis(points: Sequence[Pt], inner_first: bool) -> Tuple[Pt, Pt]:
    """The brow's head and tail, as the two ends furthest apart along its own length."""
    xs = [p[0] for p in points]
    lo = points[xs.index(min(xs))]
    hi = points[xs.index(max(xs))]
    return (lo, hi) if inner_first else (hi, lo)


def _thickness(points: Sequence[Pt], head: Pt, tail: Pt) -> float:
    """Largest distance from the head-tail line to any brow point, doubled."""
    dx, dy = tail[0] - head[0], tail[1] - head[1]
    span = math.hypot(dx, dy)
    if span < 1e-6:
        return 0.0
    worst = 0.0
    for px, py in points:
        # perpendicular distance from the point to the head-tail line
        worst = max(worst, abs((px - head[0]) * dy - (py - head[1]) * dx) / span)
    return worst * 2.0


def placements(lm: FaceLandmarks, *, grow: float = 1.0, lift: float = 0.0) -> List[Placement]:
    """Where each template sits on this face, measured from the detected brows.

    ``grow`` scales length and thickness together (1.08 draws the template a little
    proud of the real brow, which is usually what a design does). ``lift`` moves the
    template along the brow's own normal, in units of its thickness - positive is up.
    """
    out: List[Placement] = []
    centre = lm.eye_center[0]
    for side, points in (("right", lm.right_brow), ("left", lm.left_brow)):
        if len(points) < 2:
            continue
        # the head is the end nearer the middle of the face
        mean_x = sum(p[0] for p in points) / len(points)
        inner_first = mean_x > centre        # brow on the image right: head is its leftmost point
        head, tail = _axis(points, inner_first)
        length = math.hypot(tail[0] - head[0], tail[1] - head[1]) * grow
        thick = _thickness(points, head, tail) * grow
        angle = math.degrees(math.atan2(tail[1] - head[1], tail[0] - head[0]))
        x, y = head
        if lift and thick:
            # move along the normal of the head-tail line
            rad = math.radians(angle)
            x -= math.sin(rad) * -thick * lift
            y -= math.cos(rad) * thick * lift
        out.append(Placement(side=side, x=x, y=y, angle_deg=angle,
                             length=length, thickness=thick, mirror=(side == "left")))
    return out


def placement_payload(lm: FaceLandmarks, **kw: Any) -> Dict[str, Any]:
    """What the browser needs to draw and then adjust the overlay."""
    return {
        "width": lm.width,
        "height": lm.height,
        "ipd_px": round(lm.ipd_px, 2),
        "brows": [p.to_dict() for p in placements(lm, **kw)],
        "source": lm.source,
    }


def control_points(p: Placement, count: int = 5) -> List[Dict[str, Any]]:
    """The handles for stage one: head, arch and tail, plus the top and bottom edges.

    They are returned in photo coordinates so the browser can draw them directly, each
    with the name the adjustment uses, and the fraction along the brow it sits at.
    """
    rad = math.radians(p.angle_deg)
    cs, sn = math.cos(rad), math.sin(rad)

    def at(along: float, across: float) -> Pt:
        # along: 0 at the head, 1 at the tail; across: -0.5 top edge, +0.5 bottom edge
        u, v = along * p.length, across * p.thickness
        return (p.x + u * cs - v * sn, p.y + u * sn + v * cs)

    spec = [("head", 0.0, 0.0), ("arch", 0.62, -0.5), ("tail", 1.0, 0.0),
            ("top", 0.62, -0.5), ("bottom", 0.62, 0.5)]
    if count == 3:
        spec = spec[:3]
    seen: List[Dict[str, Any]] = []
    for name, along, across in spec[:count]:
        px, py = at(along, across)
        seen.append({"name": name, "x": round(px, 2), "y": round(py, 2),
                     "along": along, "across": across})
    return seen
