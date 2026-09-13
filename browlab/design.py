"""The consultation simulator's server side: a library of drawn brow templates, and the
measurements that put one on a client's photo.

Nothing generative happens here. The practitioner's own templates - one transparent PNG
per brow, or a whole sheet that is cut up on arrival - are kept under the data folder,
and a photo is answered with where its brows are so the browser can lay a template over
them and let the practitioner drag it into the design they are proposing.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps

from . import overlay as O
from . import sheetsplit as S

SIDES = ("right", "left", "pair")
TEMPLATE_COLOUR = (60, 48, 40)
PHOTO_MAX_EDGE = 3000
_NAME_RE = re.compile(r"^t_[0-9a-f]{8}\.png$")
# Designs that ship with the app, so the first consultation has brows to show before the
# practitioner has drawn any: the five pairs cut from the studio's own template sheet.
BUILTIN_DIR = Path(__file__).with_name("templates")
BUILTINS = [("스파인 6", "spine6"), ("스파인 3", "spine3"), ("스파인 2.5", "spine2_5"),
            ("스파인 2 up", "spine2up"), ("스파인 5", "spine5")]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _has_transparency(image: Image.Image) -> bool:
    """A PNG somebody already cut out, as opposed to a drawing on paper."""
    if image.mode not in ("RGBA", "LA") and not (image.mode == "P" and "transparency" in image.info):
        return False
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    return bool(alpha.min() < 250) and bool(alpha.max() > 5)


def _trim(rgba: Image.Image, pad: int = 4) -> Image.Image:
    """Crop to the ink, leaving a small margin so nothing touches the edge."""
    alpha = np.asarray(rgba)[..., 3]
    ys, xs = np.nonzero(alpha > 4)
    if not len(xs):
        return rgba
    x0, x1 = max(0, int(xs.min()) - pad), min(rgba.width, int(xs.max()) + 1 + pad)
    y0, y1 = max(0, int(ys.min()) - pad), min(rgba.height, int(ys.max()) + 1 + pad)
    return rgba.crop((x0, y0, x1, y1))


def _flat_colour(rgba: Image.Image) -> Image.Image:
    """Keep only the alpha: the page recolours a template to the pigment being proposed."""
    alpha = np.asarray(rgba.convert("RGBA"))[..., 3]
    out = np.zeros((alpha.shape[0], alpha.shape[1], 4), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = TEMPLATE_COLOUR
    out[..., 3] = alpha
    return Image.fromarray(out, "RGBA")


def cut_upload(image: Image.Image, *, floor: float = 20.0) -> List[Tuple[str, Image.Image, Optional[Image.Image]]]:
    """Turn whatever was uploaded into templates: ``[(side, right_or_only, left_or_None)]``.

    A transparent PNG is one template as it is. A drawing on paper becomes as many pairs
    as the sheet holds, or one template when it is a single brow.
    """
    image = ImageOps.exif_transpose(image)
    if _has_transparency(image):
        return [("single", _flat_colour(_trim(image.convert("RGBA"))), None)]
    ink = S.lift_floor(S.drop_flat_columns(S.ink_of(image)), floor)
    pairs = [p for p in S.find_pairs(ink)
             if min(p.left[2] - p.left[0], p.right[2] - p.right[0]) >= 40]
    if pairs:
        # the brow drawn on the left of the sheet is the person's own right brow
        return [("pair", S.cut(image, ink, p.left, TEMPLATE_COLOUR), S.cut(image, ink, p.right, TEMPLATE_COLOUR))
                for p in pairs]
    if ink.max() <= 0:
        raise ValueError("그림을 찾지 못했습니다")
    h, w = ink.shape
    return [("single", _trim(S.cut(image, ink, (0, 0, w, h), TEMPLATE_COLOUR, pad=0)), None)]


class TemplateStore:
    """Transparent brow PNGs under ``root``, listed in ``templates.json``.

    Each entry is a design: a ``right`` file, a ``left`` file, or both. A single file is
    mirrored for the other side of the face by the page.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._seed()

    def _seed(self) -> None:
        """A brand-new library starts with the built-in pairs. Deleting one sticks: the
        index exists from then on, so nothing is put back on the next start."""
        rows: List[Dict[str, Any]] = []
        for name, stem in BUILTINS:
            files = {side: BUILTIN_DIR / f"{stem}_{side}.png" for side in ("right", "left")}
            if not all(f.is_file() for f in files.values()):
                continue
            row: Dict[str, Any] = {"id": secrets.token_hex(4), "name": name, "created": _now(), "side": "pair",
                                   "builtin": True}
            for side, src in files.items():
                with Image.open(src) as im:
                    im.load()
                    picture = im.convert("RGBA")
                row[side] = self._store(picture)
            row["w"], row["h"] = picture.size
            rows.append(row)
        with self.lock:
            self._write(rows)

    @property
    def index_path(self) -> Path:
        return self.root / "templates.json"

    def _read(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            rows = data.get("templates", []) if isinstance(data, dict) else []
            return [r for r in rows if isinstance(r, dict) and r.get("id")]
        except Exception:
            return []

    def _write(self, rows: List[Dict[str, Any]]) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"templates": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def list(self) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self._read()
        return [self._public(r) for r in rows if self._files_exist(r)]

    def _files_exist(self, row: Dict[str, Any]) -> bool:
        return any((self.root / row[k]).is_file() for k in ("right", "left") if row.get(k))

    @staticmethod
    def _public(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for k in ("right", "left"):
            out[k] = f"tfiles/{row[k]}" if row.get(k) else None
        return out

    def file(self, name: str) -> Optional[Path]:
        if not _NAME_RE.match(name or ""):
            return None
        path = self.root / name
        return path if path.is_file() else None

    def _store(self, image: Image.Image) -> str:
        name = f"t_{secrets.token_hex(4)}.png"
        image.save(self.root / name)
        return name

    def add(self, name: str, image: Image.Image, side: str = "right") -> List[Dict[str, Any]]:
        """Add one upload; returns the templates it produced (a sheet gives several)."""
        if side not in ("right", "left"):
            side = "right"
        pieces = cut_upload(image)
        base = (name or "도안").strip()[:40] or "도안"
        added: List[Dict[str, Any]] = []
        with self.lock:
            rows = self._read()
            for n, (kind, first, second) in enumerate(pieces, 1):
                label = base if len(pieces) == 1 else f"{base} {n}"
                row: Dict[str, Any] = {"id": secrets.token_hex(4), "name": label, "created": _now(),
                                       "right": None, "left": None}
                if kind == "pair" and second is not None:
                    row["side"] = "pair"
                    row["right"], row["left"] = self._store(first), self._store(second)
                else:
                    row["side"] = side
                    row[side] = self._store(first)
                row["w"], row["h"] = first.size
                rows.append(row)
                added.append(row)
            self._write(rows)
        return [self._public(r) for r in added]

    def rename(self, template_id: str, name: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            rows = self._read()
            for r in rows:
                if r["id"] == template_id:
                    r["name"] = (name or "").strip()[:40] or r["name"]
                    self._write(rows)
                    return self._public(r)
        return None

    def remove(self, template_id: str) -> bool:
        with self.lock:
            rows = self._read()
            keep = [r for r in rows if r["id"] != template_id]
            if len(keep) == len(rows):
                return False
            for r in rows:
                if r["id"] == template_id:
                    for k in ("right", "left"):
                        if r.get(k):
                            (self.root / r[k]).unlink(missing_ok=True)
            self._write(keep)
        return True


# ---------------------------------------------------------------------------
# photos
# ---------------------------------------------------------------------------
def prepare_photo(image: Image.Image, max_edge: int = PHOTO_MAX_EDGE) -> Image.Image:
    """Upright, RGB, and no larger than the page can usefully draw."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    if max(image.size) > max_edge:
        image = image.copy()
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)
    return image


def analyse(image: Image.Image, *, provider: str = "mediapipe") -> Tuple[Optional[Dict[str, Any]], str]:
    """Where the brows are on this photo, or why that could not be measured.

    Returns ``(payload, warning)``: the payload has the landmark set and the placement
    of each brow; when no face is found it is ``None`` and the warning says so, and the
    page falls back to a hand placement.
    """
    from . import landmarks as L

    try:
        lm = L.detect(image, provider=provider)
    except L.LandmarkError as exc:
        return None, f"얼굴 인식을 쓸 수 없습니다: {exc}"
    except Exception as exc:  # pragma: no cover - depends on the mediapipe build
        return None, f"얼굴 인식 오류: {exc}"
    if lm is None:
        return None, "얼굴을 찾지 못했습니다. 눈썹 위치를 직접 맞춰 주세요."
    payload = O.placement_payload(lm)
    payload["landmarks"] = lm.to_dict()
    payload["controls"] = {p.side: O.control_points(p) for p in O.placements(lm)}
    return payload, ""
