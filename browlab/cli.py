"""Command line interface for BrowLab.

    python -m browlab generate  --count 4 --age 30s --gender female
    python -m browlab sheet     photo.png --guides
    python -m browlab restyle   photo.jpg --styles straight,soft_arch,feathered
    python -m browlab calibrate
    python -m browlab presets
    python -m browlab web       --port 8177 --password-file ~/.config/browlab/password.txt
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

from . import __version__
from . import backends as B
from . import landmarks as L
from . import masks as M
from . import presets as P
from . import prompts as PR
from . import sheet as S
from . import web as W

DEFAULT_OUT = Path("output") / "browlab"
ALIGN_TOLERANCE = 0.05   # pupils moved at most 5% of the inter-pupil distance: composite as is
ALIGN_SCALE_MAX = 0.30   # beyond these the edit is treated as a different picture (no compositing)
ALIGN_ANGLE_MAX = 12.0
QUALITY_CHOICES = ["low", "medium", "high", "xhigh", "max", "auto"]


def _log(msg: str) -> None:
    print(f"[browlab] {msg}", flush=True)


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _out_dir(arg: Optional[str], kind: str) -> Path:
    path = Path(arg) if arg else DEFAULT_OUT / f"{kind}_{_stamp()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_pupils(text: Optional[str]) -> Optional[List[float]]:
    if not text:
        return None
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise SystemExit("--pupils expects x1,y1,x2,y2 (image-left eye first)")
    return [float(p) for p in parts]


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_usage(entry: Dict[str, Any], result: B.GenResult, manifest: Dict[str, Any]) -> None:
    """Keep the Images API token usage and cost estimate in the manifest (API backend only)."""
    if not result.usage:
        return
    entry["usage"] = result.usage
    entry["cost_usd"] = result.cost_usd
    entry["model"] = result.model
    manifest["cost_usd"] = round(float(manifest.get("cost_usd") or 0.0) + float(result.cost_usd or 0.0), 6)
    _log(f"토큰: 입력 {result.usage['input_tokens']} (텍스트 {result.usage['text_tokens']} · 이미지 {result.usage['image_tokens']})"
         f" · 출력 {result.usage['output_tokens']} · 추정 ${result.cost_usd:.4f} (누적 ${manifest['cost_usd']:.4f})")


def _explain_api_error(exc: BaseException) -> str:
    text = str(exc)
    if "usage limit" in text and "codex" in text.lower():
        return ("Codex(ChatGPT 구독) 이미지 생성 한도가 소진되었습니다. 엔진을 '자동'(Codex 실패 시 API로 넘어감) 또는 "
                "'OpenAI API'로 바꾸거나, https://chatgpt.com/codex/settings/usage 에서 크레딧을 구매하거나 안내된 시각 이후 다시 시도하세요.")
    if "rate_limit_exceeded" in text and "Limit 0" in text:
        return ("이 OpenAI 조직에서는 아직 gpt-image 계열 모델이 열리지 않았습니다(분당 한도 0). "
                "platform.openai.com → Settings → Organization → Limits 에서 결제 반영·조직 인증(Verify organization) 상태를 확인하세요. "
                "키가 만들어진 조직과 크레딧을 충전한 조직이 같은지도 확인하세요.")
    return ""


def _pt(p: Any) -> Tuple[float, float]:
    return (float(p[0]), float(p[1])) if isinstance(p, (tuple, list)) else (float(p.x), float(p.y))


def _detect_plain(args: argparse.Namespace, image: Image.Image, path: Optional[Path] = None) -> Optional[L.FaceLandmarks]:
    """Landmark detection without manual pupils (used for the face tile and for edited images)."""
    provider = "auto" if args.landmarks in ("manual", "auto") else args.landmarks
    try:
        return L.detect(image, path, provider=provider, codex_bin=args.codex_bin,
                        codex_model=getattr(args, "landmark_model", None),
                        download_model=not getattr(args, "no_download", False))
    except L.LandmarkError:
        return None


def _align_edit(
    args: argparse.Namespace, edited: Image.Image, lm_ref: L.FaceLandmarks, size: Tuple[int, int],
) -> Tuple[Optional[Image.Image], str, Dict[str, Any], Optional[L.FaceLandmarks]]:
    """Bring an edited image back onto the reference face geometry.

    Some models regenerate the whole picture instead of inpainting only the mask.
    Small moves are corrected with a similarity transform fitted on the pupils;
    large ones mean a different picture, so compositing is skipped (image None).
    Returns (image or None, status, info, landmarks of the edited image).
    """
    img = edited if edited.size == size else edited.resize(size, Image.LANCZOS)
    lm_new = _detect_plain(args, img)
    if lm_new is None:
        return img, "unchecked", {"note": "편집 결과에서 얼굴을 찾지 못해 정렬 확인 생략"}, None
    try:
        sim = M.similarity_from_pupils(lm_new, lm_ref)
    except ValueError:
        return img, "unchecked", {"note": "동공 좌표가 이상해 정렬 확인 생략"}, lm_new
    info: Dict[str, Any] = {"shift_pct": round(sim.shift_frac * 100, 1), "scale_pct": round(sim.scale_ratio * 100, 1),
                            "angle_deg": round(sim.angle_deg, 1)}
    if sim.shift_frac <= ALIGN_TOLERANCE and sim.scale_ratio <= ALIGN_TOLERANCE:
        return img, "aligned", info, lm_new
    if sim.shift_frac <= args.align_max and sim.scale_ratio <= ALIGN_SCALE_MAX and abs(sim.angle_deg) <= ALIGN_ANGLE_MAX:
        return M.warp_similarity(img, sim, size), "warped", info, lm_new
    return None, "misaligned", info, lm_new


def _brow_height_fix(
    args: argparse.Namespace, aligned: Image.Image, lm_ref: L.FaceLandmarks,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """Slide the edit vertically so the new brow sits at the original brow's height.

    Models lift brows onto the forehead even when the prompt forbids it, and the
    mask leaves room above for taller designs, so the lift survives compositing.
    Measuring both brow baselines and shifting the edit fixes it geometrically.
    """
    info: Dict[str, Any] = {}
    ref = M.brow_baseline(lm_ref)
    lm_new = _detect_plain(args, aligned)
    new = M.brow_baseline(lm_new) if lm_new is not None else None
    if ref is None or new is None:
        info["brow_align"] = "편집 결과에서 눈썹을 찾지 못해 높이 보정 생략"
        return aligned, info
    ipd = lm_ref.ipd_px or 1.0
    dy = ref - new
    limit = args.brow_align_max * ipd
    if abs(dy) < 0.02 * ipd:
        info["brow_shift_px"] = 0.0
        return aligned, info
    dy = max(-limit, min(limit, dy))
    info["brow_shift_px"] = round(dy, 1)
    info["brow_shift_ipd"] = round(dy / ipd, 3)
    return M.shift_image(aligned, 0, dy), info


def _chain(text: Optional[str]) -> Optional[List[str]]:
    if not text:
        return None
    return [m.strip() for m in text.split(",") if m.strip()]


def _backend(args: argparse.Namespace) -> B.BaseBackend:
    backend = B.make_backend(
        args.backend,
        model=args.model,
        edit_model=getattr(args, "edit_model", None),
        model_chain=_chain(getattr(args, "model_chain", None)),
        edit_model_chain=_chain(getattr(args, "edit_model_chain", None)),
        timeout=args.timeout,
        codex_bin=args.codex_bin,
        extra_args=args.codex_arg or (),
    )
    for note in getattr(backend, "notes", []):
        _log(note)
    if isinstance(backend, B.FallbackBackend):
        _log(("자동" if backend.name == "auto" else "API") + " 순서: " + " → ".join(label for label, _ in backend.attempts))
    return backend


def _record_backend(entry: Dict[str, Any], result: B.GenResult) -> None:
    """Which backend/model actually produced this image (matters for the auto backend)."""
    entry["backend"] = result.backend
    if result.model:
        entry["model"] = result.model
    if result.fallback:
        entry["fallback"] = list(result.fallback)


def _detect(args: argparse.Namespace, image: Image.Image, path: Optional[Path]) -> Optional[L.FaceLandmarks]:
    try:
        lm = L.detect(
            image,
            path,
            provider=args.landmarks,
            pupils=_parse_pupils(getattr(args, "pupils", None)),
            codex_bin=args.codex_bin,
            codex_model=getattr(args, "landmark_model", None),
            download_model=not getattr(args, "no_download", False),
        )
    except L.LandmarkError as exc:
        _log(f"랜드마크 검출 실패: {exc}")
        return None
    if lm is None:
        _log("얼굴 랜드마크를 찾지 못했습니다. 이미지 높이 기준의 근사 배율을 사용합니다 (--pupils 로 직접 지정 가능).")
    else:
        _log(f"랜드마크: {lm.source}, 동공간 거리 {lm.ipd_px:.1f}px")
    return lm


def _sheet_options(args: argparse.Namespace, ipd_mm: float, caption: str, note: str, extra: Sequence[str] = ()) -> S.SheetOptions:
    return S.SheetOptions(
        dpi=args.dpi,
        ipd_mm=ipd_mm,
        guides=bool(getattr(args, "guides", False)),
        title=getattr(args, "title", "") or "",
        caption=caption,
        note=note,
        extra_lines=list(extra),
        font_path=args.font,
        fallback_image_height_mm=getattr(args, "image_height_mm", 320.0),
        print_scale=(getattr(args, "print_scale", 100.0) or 100.0) / 100.0,
        grow_mm=getattr(args, "grow_mm", 0.0) or 0.0,
    )


def make_sheets(
    image_path: Path,
    args: argparse.Namespace,
    sheet_dir: Path,
    *,
    ipd_mm: float,
    caption: str,
    note: str,
    label: str,
    extra: Sequence[str] = (),
) -> Tuple[List[str], Optional[L.FaceLandmarks]]:
    """Compose the requested A4 layouts for one image. Returns written paths and landmarks."""
    image = Image.open(image_path)
    image.load()
    lm = _detect(args, image, image_path)
    opts = _sheet_options(args, ipd_mm, caption, note, extra)
    layouts = {"face": ["face"], "browzone": ["browzone"], "both": ["face", "browzone"]}[args.layout]
    written: List[Path] = []
    stem = image_path.stem
    if "face" in layouts:
        canvas, _ = S.compose_face_sheet(image, lm, opts)
        written += S.save_pages([canvas], sheet_dir / f"{stem}_A4", args.dpi, pdf=not args.no_pdf)
    if "browzone" in layouts:
        if lm is None:
            _log("눈썹 구역 시트는 랜드마크가 필요해 건너뜁니다.")
        else:
            pages = S.compose_browzone_sheet([(label, image, lm)], opts, copies=args.copies)
            written += S.save_pages(pages, sheet_dir / f"{stem}_browzone", args.dpi, pdf=not args.no_pdf)
    if getattr(args, "save_landmarks", False) and lm is not None:
        lm_path = sheet_dir / f"{stem}_landmarks.json"
        _write_json(lm_path, lm.to_dict())
        written.append(lm_path)
    for p in written:
        _log(f"저장: {p}")
    return [str(p) for p in written], lm


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------
def cmd_generate(args: argparse.Namespace) -> int:
    specs = PR.make_specs(
        args.count,
        args.seed,
        age=args.age,
        gender=args.gender,
        face_shape=args.face_shape,
        brow_condition=args.brow_condition,
        ethnicity=args.ethnicity,
        notes=args.notes or "",
    )
    if args.dry_run:
        for i, spec in enumerate(specs, 1):
            print(f"===== [{i}/{len(specs)}] {spec.label_ko()}  (seed {spec.seed}, IPD {spec.ipd_mm:.0f} mm)")
            print(PR.build_face_prompt(spec))
            print()
        return 0
    out_dir = _out_dir(args.out_dir, "faces")
    sheet_dir = out_dir / "sheets"
    backend = _backend(args)
    manifest: Dict[str, Any] = {
        "tool": f"browlab {__version__}",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "backend": backend.name,
        "model": getattr(backend, "model", None),
        "seed": args.seed,
        "size": args.size,
        "quality": args.quality,
        "faces": [],
    }
    manifest_path = out_dir / "manifest.json"
    _log(f"출력 폴더: {out_dir}  (백엔드: {backend.name})")
    failures = 0
    for i, spec in enumerate(specs, 1):
        prompt = PR.build_face_prompt(spec)
        out_path = out_dir / f"face_{i:02d}_{spec.slug()}.png"
        entry: Dict[str, Any] = {"index": i, **spec.to_dict(), "prompt": prompt, "image": str(out_path)}
        _log(f"[{i}/{len(specs)}] {spec.label_ko()} -> {out_path.name}")
        try:
            result = backend.generate(prompt, out_path, size=args.size, quality=args.quality)
        except Exception as exc:  # GenerationError or an API/SDK error
            failures += 1
            entry["error"] = str(exc)
            _log(f"생성 실패: {exc}")
            hint = _explain_api_error(exc)
            if hint:
                _log(hint)
                entry["hint"] = hint
            manifest["faces"].append(entry)
            _write_json(manifest_path, manifest)
            continue
        entry["pending"] = result.pending
        _record_backend(entry, result)
        _record_usage(entry, result, manifest)
        if result.pending:
            _log(f"수동 모드: 프롬프트를 저장했습니다 -> {result.log}")
        elif not args.no_sheet:
            ipd = args.ipd_mm or spec.ipd_mm
            note = P.FACE_SHAPES[spec.face_shape].brow_tip_ko
            written, _ = make_sheets(
                result.path, args, sheet_dir,
                ipd_mm=ipd,
                caption=f"{spec.label_ko()}  ·  seed {spec.seed}  ·  {_dt.date.today().isoformat()}",
                note=f"{P.FACE_SHAPES[spec.face_shape].ko} 추천: {note}",
                label=spec.label_ko(),
            )
            entry["sheets"] = written
        manifest["faces"].append(entry)
        _write_json(manifest_path, manifest)
    _log(f"완료: {len(specs) - failures}/{len(specs)} 생성, manifest -> {manifest_path}")
    if backend.name == "manual":
        _log("이미지를 만들어 위 경로에 저장한 뒤 `python -m browlab sheet <이미지>` 로 A4 시트를 만드세요.")
    return 1 if failures and failures == len(specs) else 0


# ---------------------------------------------------------------------------
# sheet
# ---------------------------------------------------------------------------
def cmd_sheet(args: argparse.Namespace) -> int:
    out_dir = _out_dir(args.out_dir, "sheets")
    ipd = args.ipd_mm or P.default_ipd_mm(args.gender if args.gender != "random" else None, None)
    if args.age_group and args.age_group == "10s":
        ipd = args.ipd_mm or P.DEFAULT_IPD_MM["teen"]
    rc = 0
    for image_arg in args.images:
        path = Path(image_arg)
        if not path.is_file():
            _log(f"파일 없음: {path}")
            rc = 1
            continue
        note = args.note or ""
        if args.face_shape and args.face_shape != "random":
            fs = P.FACE_SHAPES[args.face_shape]
            note = note or f"{fs.ko} 추천: {fs.brow_tip_ko}"
        caption = args.caption or f"{path.name}  ·  {_dt.date.today().isoformat()}"
        make_sheets(path, args, out_dir, ipd_mm=ipd, caption=caption, note=note, label=path.stem)
    return rc


# ---------------------------------------------------------------------------
# restyle
# ---------------------------------------------------------------------------
def _parse_styles(text: str, rng: random.Random) -> List[str]:
    text = (text or "").strip()
    if not text or text == "all":
        return list(P.BROW_STYLES)
    if text.startswith("random"):
        n = int(text.split(":", 1)[1]) if ":" in text else 3
        return rng.sample(list(P.BROW_STYLES), k=min(n, len(P.BROW_STYLES)))
    keys = [k.strip() for k in text.split(",") if k.strip()]
    for k in keys:
        if k not in P.BROW_STYLES:
            raise SystemExit(f"unknown brow style: {k} (see `python -m browlab presets`)")
    return keys


def cmd_restyle(args: argparse.Namespace) -> int:
    src = Path(args.image)
    if not src.is_file():
        _log(f"파일 없음: {src}")
        return 1
    out_dir = _out_dir(args.out_dir, f"restyle_{src.stem}")
    with Image.open(src) as im:
        original = ImageOps.exif_transpose(im).convert("RGB")
    full, scale = M.downscale_to(original, max_edge=args.max_edge)
    full_path = out_dir / "00_original.png"
    full.save(full_path)
    _log(f"입력 사진 준비: {full.size[0]}x{full.size[1]} (배율 {scale:.3f}) -> {full_path}")

    # 1) landmarks on the whole photo -> 2) face tile (what the model actually edits)
    lm_full = _detect(args, full, full_path)
    tile_size = M.parse_size(args.tile_size)
    tile_mode = lm_full is not None and not args.no_tile
    box: Optional[Tuple[int, int, int, int]] = None
    lm_edit: Optional[L.FaceLandmarks] = None
    if tile_mode:
        assert lm_full is not None
        box = M.face_tile_box(lm_full, full.size, margin=args.tile_margin)
        edit_img = M.crop_tile(full, box, tile_size)
        edit_path = out_dir / "00_face_tile.png"
        edit_img.save(edit_path)
        if args.landmarks != "manual":
            lm_edit = _detect_plain(args, edit_img, edit_path)
        if lm_edit is None:
            lm_edit = M.landmarks_to_tile(lm_full, box, tile_size)
        _log(f"얼굴 타일: 원본 좌표 {box} -> {edit_img.size[0]}x{edit_img.size[1]} (동공 간 {lm_edit.ipd_px:.0f}px)")
    else:
        edit_img, _ = M.prepare_for_edit(full, max_edge=args.max_edge)
        edit_path = out_dir / "00_prepared.png"
        edit_img.save(edit_path)
        if lm_full is not None:
            lm_edit = lm_full if edit_img.size == full.size else (_detect_plain(args, edit_img, edit_path) or lm_full)
            _log("타일 모드 해제(--no-tile): 사진 전체를 편집합니다.")
        else:
            _log("랜드마크가 없어 얼굴 타일·마스크 없이 프롬프트만으로 편집합니다 (합성 단계 생략).")

    mask: Optional[Image.Image] = None
    mask_api_path: Optional[Path] = None
    guide_path: Optional[Path] = None
    if lm_edit is not None:
        mask = M.brow_region_mask(lm_edit, pad_side=args.mask_side, pad_up=args.mask_up, pad_down=args.mask_down, shape=args.mask_shape)
        mask.save(out_dir / "mask.png")
        mask_api_path = out_dir / "mask_api.png"
        M.api_mask_image(mask).save(mask_api_path, optimize=True)  # black RGB + alpha: tiny file, alpha is all the API reads
        guide_path = out_dir / "mask_guide.png"
        M.guide_overlay(edit_img, mask).save(guide_path)
        _log(f"눈썹 마스크 저장: {out_dir / 'mask.png'} (API용 알파 마스크: {mask_api_path.name})")
        _log("편집 요청 방식 — API: 얼굴 타일 + 알파 마스크를 보내 마스크 안쪽만 다시 그리게 함 / "
             "Codex: 내장 도구에 마스크 인자가 없어 빨간 영역 가이드 이미지를 함께 첨부")

    rng = random.Random(args.seed)
    styles = _parse_styles(args.styles, rng)
    backend = _backend(args)
    guide_ok = guide_path is not None and not args.no_guide_image
    use_guide = backend.name in ("codex", "auto") and guide_ok
    api_mask = mask_api_path if mask_api_path is not None and not args.no_mask else None
    api_size = f"{edit_img.size[0]}x{edit_img.size[1]}"
    manifest: Dict[str, Any] = {
        "tool": f"browlab {__version__}",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(src),
        "prepared": str(edit_path),
        "tile": {"box": list(box), "size": list(tile_size)} if box is not None else None,
        "backend": backend.name,
        "color": args.color,
        "height": args.height,
        "styles": styles,
        "landmarks": lm_full.to_dict() if lm_full else None,
        "variants": [],
    }
    manifest_path = out_dir / "manifest.json"
    results: List[Tuple[str, Image.Image, Optional[L.FaceLandmarks]]] = []
    colour_ko = P.BROW_COLORS[args.color]["ko"]
    for i, style in enumerate(styles, 1):
        st = P.BROW_STYLES[style]
        prompt_codex = PR.build_restyle_prompt(style, args.color, height_key=args.height, intensity_key=args.intensity,
                                               with_guide_image=use_guide, notes=args.notes or "")
        prompt_api = PR.build_restyle_prompt(style, args.color, height_key=args.height, intensity_key=args.intensity,
                                             with_guide_image=False, notes=args.notes or "")
        prompt = prompt_codex if backend.name == "codex" else prompt_api
        out_path = out_dir / f"{i:02d}_{style}_{args.color}.png"
        entry: Dict[str, Any] = {"style": style, "style_ko": st.ko, "prompt": prompt, "image": str(out_path)}
        if args.dry_run:
            print(f"===== [{i}/{len(styles)}] {st.ko}")
            print(prompt)
            print()
            continue
        _log(f"[{i}/{len(styles)}] {st.ko} ({colour_ko}) -> {out_path.name}")
        codex_images: List[Path] = [edit_path] + ([guide_path] if use_guide and guide_path is not None else [])
        api_images: List[Path] = [edit_path]
        try:
            if backend.name == "auto":
                # Codex gets the red region guide and no mask; the API gets the alpha mask and the exact size.
                result = backend.edit(  # type: ignore[call-arg]
                    prompt_api, api_images, out_path, mask=api_mask, size=api_size, quality=args.quality,
                    variants={"codex": {"prompt": prompt_codex, "images": codex_images, "mask": None, "size": "auto"}},
                )
            elif backend.name == "codex":
                result = backend.edit(prompt_codex, codex_images, out_path, mask=None, size="auto", quality=args.quality)
            elif backend.name == "api":
                result = backend.edit(prompt_api, api_images, out_path, mask=api_mask, size=api_size, quality=args.quality)
            else:  # manual
                result = backend.edit(prompt_api, api_images, out_path, mask=None, size="auto", quality=args.quality)
        except Exception as exc:  # GenerationError or an API/SDK error
            entry["error"] = str(exc)
            _log(f"편집 실패: {exc}")
            hint = _explain_api_error(exc)
            if hint:
                _log(hint)
                entry["hint"] = hint
            manifest["variants"].append(entry)
            _write_json(manifest_path, manifest)
            continue
        entry["pending"] = result.pending
        entry["mask_sent"] = bool(api_mask is not None and result.backend == "api")
        _record_backend(entry, result)
        _record_usage(entry, result, manifest)
        if result.pending:
            manifest["variants"].append(entry)
            _write_json(manifest_path, manifest)
            continue
        edited = Image.open(result.path).convert("RGB")
        if edited.size != edit_img.size:
            edited = edited.resize(edit_img.size, Image.LANCZOS)
        final_img: Image.Image = edited
        final_lm: Optional[L.FaceLandmarks] = None
        suffix = ""
        if mask is not None and lm_edit is not None and not args.no_composite and args.landmarks != "none":
            aligned_img, status, info, lm_new = _align_edit(args, edited, lm_edit, edit_img.size)
            entry["align"] = {"status": status, **info}
            entry["aligned"] = status != "misaligned"
            if aligned_img is None:
                _log(f"경고: 편집 결과의 얼굴 위치가 원본과 많이 달라 합성을 생략합니다 (이동 {info.get('shift_pct')}% · "
                     f"크기 {info.get('scale_pct')}% · 회전 {info.get('angle_deg')}°). 편집 결과를 그대로 씁니다.")
                final_lm = lm_new
                suffix = " (합성 생략)"
            else:
                if status == "warped":
                    _log(f"정렬 보정: 이동 {info['shift_pct']}% · 크기 {info['scale_pct']}% · 회전 {info['angle_deg']}° -> 원본 눈 위치에 맞춤")
                elif status == "aligned":
                    _log(f"정렬 확인: 이동 {info['shift_pct']}% · 크기 {info['scale_pct']}% (허용 {ALIGN_TOLERANCE * 100:.0f}%)")
                else:
                    _log(str(info.get("note", "")))
                if args.height == "keep" and not args.no_brow_align:
                    aligned_img, hinfo = _brow_height_fix(args, aligned_img, lm_edit)
                    entry.update(hinfo)
                    shift = hinfo.get("brow_shift_px")
                    if shift:
                        mm = shift / (lm_edit.ipd_px or 1.0) * (args.ipd_mm or P.default_ipd_mm(None, None))
                        _log(f"눈썹 높이 보정: {'아래로' if shift > 0 else '위로'} {abs(shift):.0f}px "
                             f"(실물 약 {abs(mm):.1f}mm) — 원래 눈썹 아래선에 맞춤")
                    elif hinfo.get("brow_align"):
                        _log(hinfo["brow_align"])
                tile_result = aligned_img if args.no_tone_match else M.match_tone(aligned_img, edit_img, mask)
                comp = M.composite_brows(edit_img, tile_result, mask)
                if box is not None:
                    final_img = M.paste_back(full, comp, box, mask)
                    final_lm = lm_full
                else:
                    final_img = comp
                    final_lm = lm_edit
                final_path = out_dir / f"{i:02d}_{style}_{args.color}_composited.png"
                final_img.save(final_path)
                entry["composited"] = str(final_path)
                _log(f"원본 사진에 눈썹만 합성: {final_path.name}")
        results.append((f"{i}. {st.ko} · {colour_ko}{suffix}", final_img, final_lm))
        manifest["variants"].append(entry)
        _write_json(manifest_path, manifest)

    if args.dry_run:
        return 0
    if results and args.sheet != "none":
        ipd = args.ipd_mm or P.default_ipd_mm(args.gender if args.gender != "random" else None, None)
        opts = _sheet_options(args, ipd, f"{src.name}  ·  {colour_ko}  ·  {_dt.date.today().isoformat()}", args.note or "")
        written: List[Path] = []
        if args.sheet in ("grid", "both"):
            pages = S.compose_grid_sheet([("원본", full)] + [(label, img) for label, img, _ in results], opts)
            written += S.save_pages(pages, out_dir / "sheet_compare", args.dpi, pdf=not args.no_pdf)
        if args.sheet in ("browzone", "both"):
            if lm_full is None:
                _log("눈썹 구역 1:1 시트는 랜드마크가 필요해 건너뜁니다.")
            else:
                items = [("원본", full, lm_full)] + [(label, img, lm) for label, img, lm in results if lm is not None]
                pages = S.compose_browzone_sheet(items, opts, copies=1)
                written += S.save_pages(pages, out_dir / "sheet_browzone", args.dpi, pdf=not args.no_pdf)
        for p in written:
            _log(f"저장: {p}")
        manifest["sheets"] = [str(p) for p in written]
        _write_json(manifest_path, manifest)
    _log(f"완료: {len(results)}/{len(styles)} 변형, manifest -> {manifest_path}")
    return 0 if results or backend.name == "manual" else 1


# ---------------------------------------------------------------------------
# calibrate / presets
# ---------------------------------------------------------------------------
def cmd_calibrate(args: argparse.Namespace) -> int:
    out_dir = _out_dir(args.out_dir, "calibrate")
    ipd = args.ipd_mm or 63.0
    opts = _sheet_options(args, ipd, "", "")
    page = S.calibration_sheet(opts)
    for p in S.save_pages([page], out_dir / "calibration_A4", args.dpi, pdf=not args.no_pdf):
        _log(f"저장: {p}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """Which image models can the configured API key actually use?"""
    rows = B.check_models(probe=args.probe, timeout=args.timeout)
    width = max(len(r["model"]) for r in rows)
    print("\n이미지 모델 사용 가능 여부" + (" (실제 생성 1장으로 확인)" if args.probe else " (무료 확인 · 한도 0 여부는 --probe 로)"))
    for r in rows:
        mark = "O" if r["usable"] else "X"
        cost = f"  (${r['cost_usd']:.4f})" if r.get("cost_usd") else ""
        detail = f"  {r['detail'][:90]}" if r.get("detail") and not r["usable"] else ""
        print(f"  {mark}  {r['model'].ljust(width)}  {r['label']}{cost}{detail}")
    print()
    return 0 if any(r["usable"] for r in rows) else 1


def cmd_presets(args: argparse.Namespace) -> int:
    def table(title: str, rows: List[Tuple[str, str]]) -> None:
        print(f"\n## {title}")
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            print(f"  {k.ljust(width)}  {v}")

    table("나이대 (--age)", [(k, f"{lo}-{hi}세 ({P.AGE_GROUP_KO[k]})") for k, (lo, hi) in P.AGE_GROUPS.items()] + [("random", "무작위"), ("<숫자>", "정확한 나이")])
    table("성별 (--gender)", [(k, v["ko"]) for k, v in P.GENDERS.items()] + [("random", "무작위")])
    table("얼굴형 (--face-shape)", [(k, f"{v.ko} — {v.brow_tip_ko}") for k, v in P.FACE_SHAPES.items()] + [("random", "무작위")])
    table("눈썹 상태 (--brow-condition)", [(k, v.ko) for k, v in P.BROW_CONDITIONS.items()] + [("random", "무작위")])
    table("인종/외모 (--ethnicity)", [(k, f"{v.ko} (가중치 {v.weight:g})") for k, v in P.ETHNICITIES.items()] + [("random", "가중 무작위(한국인 비중 높음)"), ("any", "균등 무작위")])
    table("눈썹 스타일 (restyle --styles)", [(k, v.ko) for k, v in P.BROW_STYLES.items()] + [("all", "전체"), ("random:N", "N개 무작위")])
    table("눈썹 색 (restyle --color)", [(k, v["ko"]) for k, v in P.BROW_COLORS.items()])
    table("눈썹 높이 (restyle --height)", [(k, v["ko"]) for k, v in P.BROW_HEIGHTS.items()])
    table("눈썹 농도 (restyle --intensity)", [(k, v["ko"]) for k, v in P.BROW_INTENSITIES.items()])
    print()
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def _add_backend_args(p: argparse.ArgumentParser, *, with_size: bool) -> None:
    g = p.add_argument_group("생성 백엔드")
    g.add_argument("--backend", choices=["auto", "codex", "codex-only", "api", "manual"], default="auto",
                   help="auto: Codex 먼저, 안 되면 API를 2.5 → 2 → 1.5 → 1 → 1-mini 순으로(기본값) / codex: auto 와 같음(API 키가 없으면 Codex만) / "
                        "codex-only: Codex 만 / api: OpenAI Images API 만, 같은 모델 순서로 내려감 / manual: 프롬프트만 저장")
    g.add_argument("--model", help="api·auto: 이미지 모델 고정(기본 생성 gpt-image-2.5-flare, 편집 gpt-image-2.5-sunburst) / codex: 에이전트 모델(-m)")
    g.add_argument("--model-chain", help="auto: API 생성 모델 순서(쉼표 구분). 기본 " + ",".join(B.GENERATE_MODEL_CHAIN))
    g.add_argument("--edit-model-chain", help="auto: API 편집 모델 순서(쉼표 구분). 기본 " + ",".join(B.EDIT_MODEL_CHAIN))
    g.add_argument("--quality", choices=QUALITY_CHOICES, default="high", help="api 백엔드 품질 (기본 high)")
    if with_size:
        g.add_argument("--size", default="1536x2304", type=M.validate_gpt_image_size,
                       help="api 백엔드 출력 크기 WIDTHxHEIGHT (16의 배수, 기본 1536x2304 세로 2:3)")
    g.add_argument("--timeout", type=int, default=900, help="생성 1건당 제한 시간(초)")
    g.add_argument("--codex-bin", default="codex", help="codex 실행 파일 경로")
    g.add_argument("--codex-arg", action="append", help="codex exec 에 그대로 넘길 추가 인자 (반복 가능)")
    g.add_argument("--dry-run", action="store_true", help="프롬프트만 출력하고 생성하지 않음")


def _add_sheet_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("A4 시트")
    g.add_argument("--dpi", type=int, default=S.DEFAULT_DPI, help="출력 해상도 (기본 300)")
    g.add_argument("--ipd-mm", type=float, help="동공 간 거리(mm). 생략 시 성별/나이 평균(여 62, 남 64, 10대 60)")
    g.add_argument("--print-scale", type=float, default=100.0, help="출력 배율 %%. 100 = 실물 1:1, 110 = 10%% 크게")
    g.add_argument("--grow-mm", type=float, default=0.0, help="얼굴을 상하좌우로 각각 몇 mm 키울지. 10 이면 좌우·위아래 1cm씩 (--print-scale 과 곱해짐)")
    g.add_argument("--layout", choices=["face", "browzone", "both"], default="face", help="face: 얼굴 전체 1:1 / browzone: 눈썹 구역만 1:1 여러 장 / both")
    g.add_argument("--copies", type=int, default=3, help="browzone 레이아웃에서 한 장에 반복할 수")
    g.add_argument("--guides", action="store_true", help="눈썹 황금비 가이드선(콧방울-눈앞머리/홍채/눈꼬리) 표시")
    g.add_argument("--title", help="시트 제목")
    g.add_argument("--font", help="시트 글꼴 파일(.ttf/.ttc). 생략 시 시스템 한글 글꼴 자동 탐색")
    g.add_argument("--no-pdf", action="store_true", help="PDF 저장 생략(PNG만)")
    g.add_argument("--image-height-mm", type=float, default=320.0, help="랜드마크가 없을 때 이미지 전체 높이로 가정할 mm")
    g.add_argument("--landmarks", choices=["auto", "mediapipe", "codex", "manual", "none"], default="auto",
                   help="얼굴 랜드마크 검출기 (auto: mediapipe -> codex 순)")
    g.add_argument("--landmark-model", help="codex 랜드마크 검출에 쓸 모델(-m)")
    g.add_argument("--no-download", action="store_true", help="mediapipe 모델 자동 다운로드 금지")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browlab",
        description="눈썹 문신(반영구) 디자인 연습용 실물 크기(A4 1:1) 얼굴 시트 생성기",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"browlab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate ---------------------------------------------------------------
    g = sub.add_parser("generate", help="눈썹이 부족한 연습용 얼굴을 생성하고 A4 1:1 시트로 만듭니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    g.add_argument("-n", "--count", type=int, default=1, help="생성할 얼굴 수")
    g.add_argument("--age", default="random", help="10s..70s, 정확한 나이(예: 34), random")
    g.add_argument("--gender", choices=P.GENDER_CHOICES, default="random")
    g.add_argument("--face-shape", choices=P.FACE_SHAPE_CHOICES, default="random")
    g.add_argument("--brow-condition", choices=P.BROW_CONDITION_CHOICES, default="random")
    g.add_argument("--ethnicity", choices=P.ETHNICITY_CHOICES, default="korean",
                   help="외모. 기본 korean(한국인 100%%). random 은 한국인 비중 높은 가중 무작위, any 는 균등")
    g.add_argument("--notes", help="프롬프트에 덧붙일 자유 지시문")
    g.add_argument("--seed", type=int, help="무작위 조합 재현용 시드")
    g.add_argument("--out-dir", help="출력 폴더 (기본 output/browlab/faces_<시각>)")
    g.add_argument("--no-sheet", action="store_true", help="이미지만 생성하고 A4 시트는 만들지 않음")
    g.add_argument("--save-landmarks", action="store_true", help="검출한 랜드마크를 JSON으로 저장")
    _add_backend_args(g, with_size=True)
    _add_sheet_args(g)
    g.set_defaults(func=cmd_generate)

    # sheet ------------------------------------------------------------------
    s = sub.add_parser("sheet", help="기존 얼굴 이미지를 A4 1:1 시트로 만듭니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    s.add_argument("images", nargs="+", help="얼굴 이미지 파일들")
    s.add_argument("--out-dir", help="출력 폴더 (기본 output/browlab/sheets_<시각>)")
    s.add_argument("--gender", choices=P.GENDER_CHOICES, default="random", help="IPD 기본값 선택용")
    s.add_argument("--age-group", choices=list(P.AGE_GROUPS), help="IPD 기본값 선택용(10s면 60mm)")
    s.add_argument("--face-shape", choices=P.FACE_SHAPE_CHOICES, help="시트 하단에 얼굴형별 추천 문구 표시")
    s.add_argument("--caption", help="시트 상단 설명 문구")
    s.add_argument("--note", help="시트 하단 메모")
    s.add_argument("--pupils", help="수동 배율: 동공 픽셀 좌표 x1,y1,x2,y2 (이미지 왼쪽 눈 먼저)")
    s.add_argument("--save-landmarks", action="store_true")
    s.add_argument("--codex-bin", default="codex")
    _add_sheet_args(s)
    s.set_defaults(func=cmd_sheet)

    # restyle ----------------------------------------------------------------
    r = sub.add_parser("restyle", help="사진의 눈썹 부분만 검출해 여러 스타일로 다시 생성합니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    r.add_argument("image", help="얼굴 사진")
    r.add_argument("--styles", default="korean_natural,straight,soft_arch,feathered",
                   help="쉼표로 구분한 스타일 키, all, random:N")
    r.add_argument("--color", choices=P.BROW_COLOR_CHOICES, default="match_hair")
    r.add_argument("--height", choices=P.BROW_HEIGHT_CHOICES, default="keep",
                   help="새 눈썹의 높이. keep: 원래 눈썹 높이 그대로(기본) / slight_up·slight_down: 2~3mm 만 올리거나 내림")
    r.add_argument("--intensity", choices=P.BROW_INTENSITY_CHOICES, default="natural",
                   help="눈썹 농도. natural: 자연스럽게(기본) / soft: 연하게 / bold: 진하게")
    r.add_argument("--notes", help="편집 프롬프트에 덧붙일 지시문")
    r.add_argument("--seed", type=int, help="random:N 스타일 선택 시드")
    r.add_argument("--out-dir", help="출력 폴더 (기본 output/browlab/restyle_<파일명>_<시각>)")
    r.add_argument("--edit-model", help="api 백엔드 편집 모델 (기본 gpt-image-2.5-sunburst)")
    r.add_argument("--max-edge", type=int, default=2048, help="작업용 원본 사진의 긴 변 최대 픽셀(더 크면 축소)")
    r.add_argument("--tile-size", default="1024x1536", type=M.validate_gpt_image_size,
                   help="얼굴 타일(모델이 실제로 편집하는 이미지) 크기. 1024x1536 은 모든 gpt-image 모델이 받음")
    r.add_argument("--tile-margin", type=float, default=1.0, help="얼굴 타일 여유 배수 (1.0 = 머리 위 0.9 IPD, 턱 아래 0.35 IPD)")
    r.add_argument("--no-tile", action="store_true", help="얼굴을 잘라내지 않고 사진 전체를 편집 (이전 방식)")
    r.add_argument("--no-tone-match", action="store_true", help="합성 전 마스크 주변 피부톤 맞춤 생략")
    r.add_argument("--no-brow-align", action="store_true",
                   help="눈썹 높이 자동 보정 생략 (기본은 --height keep 일 때 새 눈썹을 원래 눈썹 아래선에 맞춤)")
    r.add_argument("--brow-align-max", type=float, default=0.35,
                   help="눈썹 높이 보정 최대 이동량 (동공 간 거리 배수)")
    r.add_argument("--align-max", type=float, default=0.45,
                   help="편집 결과의 눈 위치가 이 비율(동공 간 거리 대비) 이내로 움직였으면 정렬 보정 후 합성, 넘으면 합성 생략")
    r.add_argument("--mask-shape", choices=["brow", "box"], default="brow",
                   help="brow: 검출된 눈썹 윤곽을 따라가는 마스크(기본) / box: 눈썹을 감싸는 둥근 사각형(이전 방식)")
    r.add_argument("--mask-side", type=float, default=0.035, help="마스크 둘레 여유 (동공 간 거리 배수, 63mm 기준 ≈ 2mm)")
    r.add_argument("--mask-up", type=float, default=0.03,
                   help="눈썹 위 추가 여유. 둘레 여유와 합쳐 눈썹 위 약 4mm. 키우면 모델이 눈썹을 이마 쪽으로 올립니다")
    r.add_argument("--mask-down", type=float, default=0.03, help="눈썹 아래 추가 여유. 윗눈꺼풀 위에서 항상 잘립니다")
    r.add_argument("--no-mask", action="store_true", help="api 백엔드에서 알파 마스크를 보내지 않음")
    r.add_argument("--no-guide-image", action="store_true", help="codex 백엔드에 빨간 영역 가이드 이미지를 첨부하지 않음")
    r.add_argument("--no-composite", action="store_true", help="결과의 눈썹 영역만 원본 위에 합성하는 단계를 생략")
    r.add_argument("--sheet", choices=["none", "grid", "browzone", "both"], default="both", help="비교 시트 종류")
    r.add_argument("--pupils", help="수동 랜드마크: 동공 픽셀 좌표 x1,y1,x2,y2 (00_original.png 기준)")
    r.add_argument("--gender", choices=P.GENDER_CHOICES, default="random", help="IPD 기본값 선택용")
    r.add_argument("--note", help="시트 하단 메모")
    _add_backend_args(r, with_size=False)
    _add_sheet_args(r)
    r.set_defaults(func=cmd_restyle)

    # calibrate --------------------------------------------------------------
    c = sub.add_parser("calibrate", help="프린터 배율 확인용 눈금 시트를 만듭니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    c.add_argument("--out-dir", help="출력 폴더")
    _add_sheet_args(c)
    c.set_defaults(func=cmd_calibrate)

    # models -----------------------------------------------------------------
    m = sub.add_parser("models", help="API 키로 쓸 수 있는 이미지 모델을 확인합니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    m.add_argument("--probe", action="store_true", help="실제로 1장(1024x1024, low) 생성해 한도 0 여부까지 확인 (약 $0.01)")
    m.add_argument("--timeout", type=int, default=180, help="확인 제한 시간(초)")
    m.set_defaults(func=cmd_models)

    # presets ----------------------------------------------------------------
    p = sub.add_parser("presets", help="선택 가능한 값 목록을 보여줍니다")
    p.set_defaults(func=cmd_presets)

    # web --------------------------------------------------------------------
    w = sub.add_parser("web", help="브라우저/휴대폰용 웹 UI 서버를 띄웁니다",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    W.add_web_args(w)
    w.set_defaults(func=W.cmd_web)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ValueError as exc:
        parser.error(str(exc))
        return 2
    except KeyboardInterrupt:
        _log("중단됨")
        return 130
    except BrokenPipeError:  # e.g. `| head`
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
