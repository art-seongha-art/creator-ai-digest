"""Check MediaPipe landmark detection on BrowLab images: overlays + metrics.

    python browlab/tools/landmark_check.py <image | job dir> ... --out <dir>

For every face image (face_*.png, input.*, 00_original.png, 00_face_tile.png) it runs the
MediaPipe detector, prints one line of metrics, and writes two PNGs into --out:
  <name>_lm.png    landmarks + eyebrow mask (red) on the whole image
  <name>_zoom.png  the eye/brow band enlarged 2x
Metrics (all relative to the inter-pupil distance, IPD):
  tilt      |pupil y difference| / IPD           (0 = level face)
  gap       (eye top - brow bottom) / IPD          (brow-to-eye distance; ~0.15-0.35 typical)
  browW     brow polygon width / IPD, right/left   (~0.6-0.9 typical)
  browH     brow polygon height / IPD, right/left  (~0.06-0.15 typical)
  maskH     eyebrow mask height / IPD
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

_here = Path(__file__).resolve()
ROOT = _here.parents[2] if len(_here.parents) > 2 and (_here.parents[2] / "browlab").is_dir() else Path.cwd()
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from browlab import landmarks as L  # noqa: E402
from browlab import masks as M  # noqa: E402

NAMES = ("face_", "input.", "00_original", "00_face_tile")


def collect(paths: List[str]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and f.name.startswith(NAMES) and ".thumbs" not in f.parts:
                    out.append(f)
        elif path.is_file():
            out.append(path)
    return out


def poly_box(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def draw_overlay(img: Image.Image, lm: L.FaceLandmarks, mask: Image.Image) -> Image.Image:
    base = M.guide_overlay(img.convert("RGB"), mask, color=(255, 0, 0), alpha=0.28)
    d = ImageDraw.Draw(base)
    r = max(2, int(lm.ipd_px * 0.02))
    for name, colour in (("right_pupil", (0, 200, 255)), ("left_pupil", (0, 200, 255)), ("right_inner", (0, 255, 0)), ("right_outer", (0, 255, 0)),
                         ("left_inner", (0, 255, 0)), ("left_outer", (0, 255, 0)), ("right_upper_lid", (255, 255, 0)), ("left_upper_lid", (255, 255, 0)),
                         ("right_ala", (255, 128, 0)), ("left_ala", (255, 128, 0)), ("nose_tip", (255, 128, 0)), ("chin", (255, 0, 255)),
                         ("forehead_top", (255, 0, 255)), ("right_cheek", (128, 128, 255)), ("left_cheek", (128, 128, 255))):
        x, y = getattr(lm, name)
        d.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=max(1, r // 2))
    for pts, colour in ((lm.right_brow, (0, 255, 0)), (lm.left_brow, (0, 255, 0))):
        if len(pts) >= 3:
            d.line([tuple(p) for p in pts] + [tuple(pts[0])], fill=colour, width=max(1, r // 2))
    # inter-pupil line
    d.line([tuple(lm.right_pupil), tuple(lm.left_pupil)], fill=(0, 200, 255), width=1)
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    files = collect(args.paths)
    print(f"{len(files)} images")
    print("file | size | ms | ipd_px | tilt | gap | browW R/L | browH R/L | maskH | pupils")
    n_ok = 0
    for f in files:
        img = Image.open(f)
        img.load()
        t0 = time.time()
        try:
            lm: Optional[L.FaceLandmarks] = L.detect(img, None, provider="mediapipe")
        except L.LandmarkError as exc:
            print(f"{f.parent.name}/{f.name} | {img.size[0]}x{img.size[1]} | ERROR {exc}")
            continue
        ms = int((time.time() - t0) * 1000)
        tag = f"{f.parent.name}/{f.name}"
        if lm is None:
            print(f"{tag} | {img.size[0]}x{img.size[1]} | {ms} | NOT DETECTED")
            continue
        n_ok += 1
        ipd = lm.ipd_px
        tilt = abs(lm.right_pupil[1] - lm.left_pupil[1]) / ipd
        gap = (lm.eye_top_y - lm.brow_bottom_y) / ipd
        rb, lb = poly_box(lm.right_brow), poly_box(lm.left_brow)
        mask = M.brow_region_mask(lm)
        bbox = mask.getbbox()
        mask_h = (bbox[3] - bbox[1]) / ipd if bbox else 0
        print(f"{tag} | {img.size[0]}x{img.size[1]} | {ms} | {ipd:.0f} | {tilt:.3f} | {gap:.2f} | "
              f"{(rb[2]-rb[0])/ipd:.2f}/{(lb[2]-lb[0])/ipd:.2f} | {(rb[3]-rb[1])/ipd:.2f}/{(lb[3]-lb[1])/ipd:.2f} | {mask_h:.2f} | "
              f"R({lm.right_pupil[0]:.0f},{lm.right_pupil[1]:.0f}) L({lm.left_pupil[0]:.0f},{lm.left_pupil[1]:.0f})")
        stem = f"{f.parent.name}__{f.stem}"
        ov = draw_overlay(img, lm, mask)
        ov.save(out / f"{stem}_lm.png")
        # eye/brow band, 2x
        top = int(max(0, lm.brow_top_y - 0.6 * ipd))
        bottom = int(min(img.size[1], lm.eye_top_y + 0.5 * ipd))
        left = int(max(0, lm.right_outer[0] - 0.4 * ipd))
        right = int(min(img.size[0], lm.left_outer[0] + 0.4 * ipd))
        band = ov.crop((left, top, right, bottom))
        band = band.resize((band.size[0] * 2, band.size[1] * 2), Image.LANCZOS)
        band.save(out / f"{stem}_zoom.png")
    print(f"detected {n_ok}/{len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
