"""Facial landmark detection used for life-size scaling, guides and masks.

Three providers are available:

* ``mediapipe`` - MediaPipe FaceLandmarker (478 points, iris included). Best.
* ``codex``     - asks the Codex CLI (vision model) for the key points. Slower,
                  approximate, but needs nothing besides a logged-in Codex CLI.
* ``manual``    - build landmarks from two pupil coordinates using average
                  facial proportions.

Coordinate convention: pixel coordinates with the origin at the top-left.
"right_*" always means the subject's right eye, which is on the LEFT side of
the image.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

Pt = Tuple[float, float]


class LandmarkError(RuntimeError):
    pass


@dataclass
class FaceLandmarks:
    width: int
    height: int
    right_pupil: Pt
    left_pupil: Pt
    right_inner: Pt
    right_outer: Pt
    left_inner: Pt
    left_outer: Pt
    right_upper_lid: Pt
    left_upper_lid: Pt
    right_ala: Pt
    left_ala: Pt
    nose_tip: Pt
    chin: Pt
    forehead_top: Pt
    right_cheek: Pt
    left_cheek: Pt
    right_brow: List[Pt] = field(default_factory=list)
    left_brow: List[Pt] = field(default_factory=list)
    source: str = ""

    # -- derived measurements ---------------------------------------------
    @property
    def ipd_px(self) -> float:
        dx = self.left_pupil[0] - self.right_pupil[0]
        dy = self.left_pupil[1] - self.right_pupil[1]
        return (dx * dx + dy * dy) ** 0.5

    @property
    def eye_center(self) -> Pt:
        return (
            (self.left_pupil[0] + self.right_pupil[0]) / 2.0,
            (self.left_pupil[1] + self.right_pupil[1]) / 2.0,
        )

    @property
    def face_height_px(self) -> float:
        return abs(self.chin[1] - self.forehead_top[1])

    @property
    def eye_top_y(self) -> float:
        return min(self.right_upper_lid[1], self.left_upper_lid[1])

    @property
    def brow_top_y(self) -> float:
        pts = self.right_brow + self.left_brow
        if not pts:
            return self.eye_center[1] - 0.5 * self.ipd_px
        return min(p[1] for p in pts)

    @property
    def brow_bottom_y(self) -> float:
        pts = self.right_brow + self.left_brow
        if not pts:
            return self.eye_center[1] - 0.25 * self.ipd_px
        return max(p[1] for p in pts)

    def scaled(self, factor: float) -> "FaceLandmarks":
        def s(p: Pt) -> Pt:
            return (p[0] * factor, p[1] * factor)

        d = asdict(self)
        out: Dict[str, Any] = {"width": int(round(self.width * factor)), "height": int(round(self.height * factor)), "source": self.source}
        for k, v in d.items():
            if k in ("width", "height", "source"):
                continue
            if k in ("right_brow", "left_brow"):
                out[k] = [s(tuple(p)) for p in v]
            else:
                out[k] = s(tuple(v))
        return FaceLandmarks(**out)

    def translated(self, dx: float, dy: float, width: Optional[int] = None, height: Optional[int] = None) -> "FaceLandmarks":
        def t(p: Pt) -> Pt:
            return (p[0] + dx, p[1] + dy)

        d = asdict(self)
        out: Dict[str, Any] = {
            "width": width if width is not None else self.width,
            "height": height if height is not None else self.height,
            "source": self.source,
        }
        for k, v in d.items():
            if k in ("width", "height", "source"):
                continue
            if k in ("right_brow", "left_brow"):
                out[k] = [t(tuple(p)) for p in v]
            else:
                out[k] = t(tuple(v))
        return FaceLandmarks(**out)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FaceLandmarks":
        kwargs = dict(d)
        for k in ("right_brow", "left_brow"):
            kwargs[k] = [tuple(p) for p in kwargs.get(k, [])]
        for k, v in list(kwargs.items()):
            if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
                kwargs[k] = (float(v[0]), float(v[1]))
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Manual provider: pupils + average proportions
# ---------------------------------------------------------------------------
def from_pupils(width: int, height: int, right_pupil: Pt, left_pupil: Pt, source: str = "manual") -> FaceLandmarks:
    """Estimate all landmarks from the two pupils using average proportions.

    Distances are expressed as fractions of the inter-pupillary distance ``d``.
    """
    rx, ry = right_pupil
    lx, ly = left_pupil
    cx, cy = (rx + lx) / 2.0, (ry + ly) / 2.0
    d = ((lx - rx) ** 2 + (ly - ry) ** 2) ** 0.5
    if d <= 0:
        raise LandmarkError("pupils must be two different points")
    # unit vectors along the eye line (u) and downwards (v)
    ux, uy = (lx - rx) / d, (ly - ry) / d
    vx, vy = -uy, ux  # rotate +90 degrees (y grows downward, so this points "down" the face)

    def at(along: float, down: float) -> Pt:
        return (cx + ux * along * d + vx * down * d, cy + uy * along * d + vy * down * d)

    def brow(sign: float) -> List[Pt]:
        # head -> arch -> tail on the upper edge, then back along the lower edge
        upper = [at(sign * 0.28, -0.42), at(sign * 0.55, -0.50), at(sign * 0.75, -0.52), at(sign * 0.95, -0.42)]
        lower = [at(sign * 0.95, -0.34), at(sign * 0.75, -0.38), at(sign * 0.55, -0.36), at(sign * 0.28, -0.28)]
        return upper + lower

    return FaceLandmarks(
        width=width,
        height=height,
        right_pupil=(rx, ry),
        left_pupil=(lx, ly),
        right_inner=at(-0.27, 0.02),
        right_outer=at(-0.75, 0.0),
        left_inner=at(0.27, 0.02),
        left_outer=at(0.75, 0.0),
        right_upper_lid=at(-0.5, -0.12),
        left_upper_lid=at(0.5, -0.12),
        right_ala=at(-0.30, 0.55),
        left_ala=at(0.30, 0.55),
        nose_tip=at(0.0, 0.65),
        chin=at(0.0, 1.85),
        forehead_top=at(0.0, -1.35),
        right_cheek=at(-1.15, 0.25),
        left_cheek=at(1.15, 0.25),
        right_brow=brow(-1.0),
        left_brow=brow(1.0),
        source=source,
    )


# ---------------------------------------------------------------------------
# MediaPipe provider
# ---------------------------------------------------------------------------
MEDIAPIPE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)

# Canonical face-mesh indices (478-point model with iris refinement).
MP_IDX = {
    "right_pupil": 468,
    "left_pupil": 473,
    "right_inner": 133,
    "right_outer": 33,
    "left_inner": 362,
    "left_outer": 263,
    "right_upper_lid": 159,
    "left_upper_lid": 386,
    "right_ala": 129,
    "left_ala": 358,
    "nose_tip": 1,
    "chin": 152,
    "forehead_top": 10,
    "right_cheek": 234,
    "left_cheek": 454,
}
# Closed eyebrow outlines: upper edge then lower edge back to the start.
MP_RIGHT_BROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
MP_LEFT_BROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]


def mediapipe_model_path(download: bool = True) -> Path:
    env = os.environ.get("BROWLAB_FACE_MODEL")
    if env:
        p = Path(os.path.expanduser(env))
        if p.is_file():
            return p
        raise LandmarkError(f"BROWLAB_FACE_MODEL points to a missing file: {p}")
    cache = Path(os.path.expanduser(os.environ.get("BROWLAB_CACHE_DIR", "~/.cache/browlab")))
    target = cache / "face_landmarker.task"
    if target.is_file() and target.stat().st_size > 0:
        return target
    if not download:
        raise LandmarkError(
            f"face landmark model not found at {target}. Download it from {MEDIAPIPE_MODEL_URL} "
            "or set BROWLAB_FACE_MODEL."
        )
    cache.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".download")
    print(f"[browlab] downloading MediaPipe face landmark model to {target} ...")
    with urllib.request.urlopen(MEDIAPIPE_MODEL_URL, timeout=120) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(target)
    return target


def detect_mediapipe(image: Image.Image, model_path: Optional[Path] = None, download: bool = True) -> Optional[FaceLandmarks]:
    """Run MediaPipe FaceLandmarker. Returns None when no face is found."""
    try:
        import numpy as np
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LandmarkError(
            "mediapipe is not installed. Run `pip install mediapipe numpy` or use --landmarks codex/manual."
        ) from exc

    model = Path(model_path) if model_path else mediapipe_model_path(download=download)
    options = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(model)), num_faces=1)
    rgb = np.asarray(image.convert("RGB"))
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not result.face_landmarks:
        return None
    pts = result.face_landmarks[0]
    w, h = image.size

    def px(i: int) -> Pt:
        return (pts[i].x * w, pts[i].y * h)

    kwargs: Dict[str, Any] = {name: px(i) for name, i in MP_IDX.items()}
    return FaceLandmarks(
        width=w,
        height=h,
        right_brow=[px(i) for i in MP_RIGHT_BROW],
        left_brow=[px(i) for i in MP_LEFT_BROW],
        source="mediapipe",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Codex (vision model) provider
# ---------------------------------------------------------------------------
_POINT_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
    "required": ["x", "y"],
    "additionalProperties": False,
}
_BROW_SCHEMA = {
    "type": "object",
    "properties": {
        "head": _POINT_SCHEMA,
        "arch": _POINT_SCHEMA,
        "tail": _POINT_SCHEMA,
        "thickness_px": {"type": "number"},
    },
    "required": ["head", "arch", "tail", "thickness_px"],
    "additionalProperties": False,
}
LANDMARK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "image_width": {"type": "number"},
        "image_height": {"type": "number"},
        "right_pupil": _POINT_SCHEMA,
        "left_pupil": _POINT_SCHEMA,
        "right_inner_corner": _POINT_SCHEMA,
        "right_outer_corner": _POINT_SCHEMA,
        "left_inner_corner": _POINT_SCHEMA,
        "left_outer_corner": _POINT_SCHEMA,
        "right_upper_lid": _POINT_SCHEMA,
        "left_upper_lid": _POINT_SCHEMA,
        "right_nostril_edge": _POINT_SCHEMA,
        "left_nostril_edge": _POINT_SCHEMA,
        "nose_tip": _POINT_SCHEMA,
        "chin": _POINT_SCHEMA,
        "hairline_center": _POINT_SCHEMA,
        "right_face_edge": _POINT_SCHEMA,
        "left_face_edge": _POINT_SCHEMA,
        "right_brow": _BROW_SCHEMA,
        "left_brow": _BROW_SCHEMA,
    },
    "required": [
        "image_width", "image_height", "right_pupil", "left_pupil", "right_inner_corner", "right_outer_corner",
        "left_inner_corner", "left_outer_corner", "right_upper_lid", "left_upper_lid", "right_nostril_edge",
        "left_nostril_edge", "nose_tip", "chin", "hairline_center", "right_face_edge", "left_face_edge",
        "right_brow", "left_brow",
    ],
    "additionalProperties": False,
}


def landmark_query_prompt(width: int, height: int) -> str:
    return (
        f"Image 1 is a photo of one face. The image is exactly {width} pixels wide and {height} pixels tall. "
        "Measure the following facial points as precisely as you can and answer ONLY with JSON matching the "
        "required schema. Coordinates are pixels with the origin at the top-left corner, x growing to the right "
        "and y growing downward.\n"
        "Naming uses the SUBJECT's left and right: 'right_*' is the subject's right side, which appears on the "
        "LEFT half of the image; 'left_*' appears on the RIGHT half of the image.\n"
        "Points: right_pupil / left_pupil (centre of each iris); right_inner_corner / left_inner_corner (eye "
        "corner next to the nose); right_outer_corner / left_outer_corner (eye corner away from the nose); "
        "right_upper_lid / left_upper_lid (highest point of each upper eyelid edge); right_nostril_edge / "
        "left_nostril_edge (outermost point of each nostril wing); nose_tip; chin (lowest point of the chin); "
        "hairline_center (where the forehead meets the hair, above the nose); right_face_edge / left_face_edge "
        "(outer face contour at the level of the nose tip). For each eyebrow give head (inner end next to the "
        "nose), arch (highest point) and tail (outer end) measured along the centre line of the brow, plus "
        "thickness_px (average vertical thickness in pixels). Do not run any commands; just look at the image."
    )


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def landmarks_from_codex_json(data: Dict[str, Any], width: int, height: int) -> FaceLandmarks:
    """Convert the JSON answered by the vision model into ``FaceLandmarks``.

    If the model reports a different image size than the real one, the
    coordinates are rescaled accordingly (models sometimes reason on a resized copy).
    """
    rw = float(data.get("image_width") or width)
    rh = float(data.get("image_height") or height)
    sx = width / rw if rw > 0 else 1.0
    sy = height / rh if rh > 0 else 1.0

    def pt(obj: Dict[str, Any]) -> Pt:
        return (float(obj["x"]) * sx, float(obj["y"]) * sy)

    def brow(obj: Dict[str, Any]) -> List[Pt]:
        head, arch, tail = pt(obj["head"]), pt(obj["arch"]), pt(obj["tail"])
        half = max(1.0, float(obj.get("thickness_px", 0)) * sy / 2.0)
        upper = [(head[0], head[1] - half), (arch[0], arch[1] - half), (tail[0], tail[1] - half)]
        lower = [(tail[0], tail[1] + half), (arch[0], arch[1] + half), (head[0], head[1] + half)]
        return upper + lower

    return FaceLandmarks(
        width=width,
        height=height,
        right_pupil=pt(data["right_pupil"]),
        left_pupil=pt(data["left_pupil"]),
        right_inner=pt(data["right_inner_corner"]),
        right_outer=pt(data["right_outer_corner"]),
        left_inner=pt(data["left_inner_corner"]),
        left_outer=pt(data["left_outer_corner"]),
        right_upper_lid=pt(data["right_upper_lid"]),
        left_upper_lid=pt(data["left_upper_lid"]),
        right_ala=pt(data["right_nostril_edge"]),
        left_ala=pt(data["left_nostril_edge"]),
        nose_tip=pt(data["nose_tip"]),
        chin=pt(data["chin"]),
        forehead_top=pt(data["hairline_center"]),
        right_cheek=pt(data["right_face_edge"]),
        left_cheek=pt(data["left_face_edge"]),
        right_brow=brow(data["right_brow"]),
        left_brow=brow(data["left_brow"]),
        source="codex",
    )


def detect_codex(
    image_path: Path,
    *,
    codex_bin: str = "codex",
    model: Optional[str] = None,
    timeout: int = 600,
) -> FaceLandmarks:
    """Ask the Codex CLI (vision) for the landmark coordinates."""
    image_path = Path(image_path)
    with Image.open(image_path) as im:
        width, height = im.size
    with tempfile.TemporaryDirectory(prefix="browlab-lm-") as tmp:
        tmpdir = Path(tmp)
        schema_file = tmpdir / "schema.json"
        schema_file.write_text(json.dumps(LANDMARK_SCHEMA), encoding="utf-8")
        out_file = tmpdir / "last_message.txt"
        cmd = [
            codex_bin, "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-s", "read-only",
            "-C", str(tmpdir),
            "-i", str(image_path.resolve()),
            "--output-schema", str(schema_file),
            "-o", str(out_file),
            "--color", "never",
        ]
        if model:
            cmd += ["-m", model]
        cmd.append("-")
        proc = subprocess.run(
            cmd, input=landmark_query_prompt(width, height), text=True, capture_output=True, timeout=timeout,
        )
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else proc.stdout
        if proc.returncode != 0 and not text.strip():
            raise LandmarkError(f"codex exec failed (rc={proc.returncode}): {proc.stderr[-1500:]}")
        try:
            data = _extract_json(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LandmarkError(f"could not parse landmark JSON from codex output: {text[-800:]}") from exc
    return landmarks_from_codex_json(data, width, height)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def detect(
    image: Image.Image,
    image_path: Optional[Path] = None,
    *,
    provider: str = "auto",
    pupils: Optional[Sequence[float]] = None,
    codex_bin: str = "codex",
    codex_model: Optional[str] = None,
    download_model: bool = True,
) -> Optional[FaceLandmarks]:
    """Detect landmarks with the requested provider.

    ``provider`` is one of auto, mediapipe, codex, manual, none. ``auto`` tries
    mediapipe, then codex (if the CLI is on PATH), then gives up (returns None).
    """
    w, h = image.size
    if pupils is not None:
        if len(pupils) != 4:
            raise LandmarkError("--pupils expects four numbers: x1,y1,x2,y2 (image-left eye first)")
        return from_pupils(w, h, (float(pupils[0]), float(pupils[1])), (float(pupils[2]), float(pupils[3])))
    if provider == "none":
        return None
    if provider == "manual":
        raise LandmarkError("provider 'manual' needs --pupils x1,y1,x2,y2")
    if provider in ("auto", "mediapipe"):
        try:
            lm = detect_mediapipe(image, download=download_model)
            if lm is not None or provider == "mediapipe":
                return lm
        except LandmarkError as exc:
            if provider == "mediapipe":
                raise
            print(f"[browlab] mediapipe unavailable ({exc}); trying next provider")
    if provider in ("auto", "codex"):
        if shutil.which(codex_bin) is None:
            if provider == "codex":
                raise LandmarkError(f"codex CLI not found on PATH ({codex_bin})")
            return None
        if image_path is None:
            tmp = tempfile.NamedTemporaryFile(prefix="browlab-", suffix=".png", delete=False)
            image.save(tmp.name)
            image_path = Path(tmp.name)
        return detect_codex(image_path, codex_bin=codex_bin, model=codex_model)
    raise LandmarkError(f"unknown landmark provider: {provider}")
