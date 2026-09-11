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

from PIL import Image

from . import __version__
from . import backends as B
from . import landmarks as L
from . import masks as M
from . import presets as P
from . import prompts as PR
from . import sheet as S
from . import web as W

DEFAULT_OUT = Path("output") / "browlab"
ALIGN_TOLERANCE = 0.05  # pupils may move at most 5% of the inter-pupil distance for compositing
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
    manifest["cost_usd"] = round(float(manifest.get("cost_usd") or 0.0) + float(result.cost_usd or 0.0), 6)
    _log(f"토큰: 입력 {result.usage['input_tokens']} (텍스트 {result.usage['text_tokens']} · 이미지 {result.usage['image_tokens']})"
         f" · 출력 {result.usage['output_tokens']} · 추정 ${result.cost_usd:.4f} (누적 ${manifest['cost_usd']:.4f})")


def _explain_api_error(exc: BaseException) -> str:
    text = str(exc)
    if "rate_limit_exceeded" in text and "Limit 0" in text:
        return ("이 OpenAI 조직에서는 아직 gpt-image 계열 모델이 열리지 않았습니다(분당 한도 0). "
                "platform.openai.com → Settings → Organization → Limits 에서 결제 반영·조직 인증(Verify organization) 상태를 확인하세요. "
                "키가 만들어진 조직과 크레딧을 충전한 조직이 같은지도 확인하세요.")
    return ""


def _pt(p: Any) -> Tuple[float, float]:
    return (float(p[0]), float(p[1])) if isinstance(p, (tuple, list)) else (float(p.x), float(p.y))


def _edit_aligned(args: argparse.Namespace, edited: Image.Image, lm_orig: L.FaceLandmarks, size: Tuple[int, int]) -> Tuple[bool, str]:
    """Did the edit keep the face where it was? Compares pupil positions of the edited image with the original.

    Some models regenerate the whole picture instead of inpainting only the mask; pasting the eyebrow
    region of such an image back onto the original produces doubled eyes, so the caller skips compositing.
    """
    provider = "auto" if args.landmarks in ("manual", "auto") else args.landmarks
    img = edited if edited.size == size else edited.resize(size, Image.LANCZOS)
    try:
        lm_new = L.detect(img, None, provider=provider, codex_bin=args.codex_bin,
                          codex_model=getattr(args, "landmark_model", None),
                          download_model=not getattr(args, "no_download", False))
    except L.LandmarkError:
        lm_new = None
    if lm_new is None:
        return True, "편집 결과에서 얼굴을 찾지 못해 정렬 확인 생략"
    ipd = lm_orig.ipd_px or 1.0

    def dist(a: Any, b: Any) -> float:
        (ax, ay), (bx, by) = _pt(a), _pt(b)
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    shift = max(dist(lm_new.right_pupil, lm_orig.right_pupil), dist(lm_new.left_pupil, lm_orig.left_pupil)) / ipd
    ratio = abs(lm_new.ipd_px / ipd - 1.0)
    ok = shift <= ALIGN_TOLERANCE and ratio <= ALIGN_TOLERANCE
    return ok, f"눈 위치 이동 {shift * 100:.1f}% · 동공 간 거리 변화 {ratio * 100:.1f}% (허용 {ALIGN_TOLERANCE * 100:.0f}%)"


def _backend(args: argparse.Namespace) -> B.BaseBackend:
    return B.make_backend(
        args.backend,
        model=args.model,
        edit_model=getattr(args, "edit_model", None),
        timeout=args.timeout,
        codex_bin=args.codex_bin,
        extra_args=args.codex_arg or (),
    )


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
    original = Image.open(src)
    original.load()
    prepared, scale = M.prepare_for_edit(original, max_edge=args.max_edge)
    prepared_path = out_dir / "00_original.png"
    prepared.save(prepared_path)
    _log(f"입력 이미지 준비: {prepared.size[0]}x{prepared.size[1]} (배율 {scale:.3f}) -> {prepared_path}")

    lm = _detect(args, prepared, prepared_path)
    mask: Optional[Image.Image] = None
    mask_api_path: Optional[Path] = None
    guide_path: Optional[Path] = None
    if lm is not None:
        mask = M.brow_region_mask(lm, pad_side=args.mask_side, pad_up=args.mask_up, pad_down=args.mask_down)
        mask.save(out_dir / "mask.png")
        mask_api_path = out_dir / "mask_api.png"
        M.api_mask_image(mask, prepared).save(mask_api_path)
        guide_path = out_dir / "mask_guide.png"
        M.guide_overlay(prepared, mask).save(guide_path)
        _log(f"눈썹 마스크 저장: {out_dir / 'mask.png'} (API용 알파 마스크: {mask_api_path.name})")
    else:
        _log("랜드마크가 없어 마스크 없이 프롬프트만으로 편집합니다 (합성 단계 생략).")

    rng = random.Random(args.seed)
    styles = _parse_styles(args.styles, rng)
    backend = _backend(args)
    use_guide = backend.name == "codex" and guide_path is not None and not args.no_guide_image
    manifest: Dict[str, Any] = {
        "tool": f"browlab {__version__}",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": str(src),
        "prepared": str(prepared_path),
        "backend": backend.name,
        "color": args.color,
        "styles": styles,
        "landmarks": lm.to_dict() if lm else None,
        "variants": [],
    }
    manifest_path = out_dir / "manifest.json"
    results: List[Tuple[str, Image.Image]] = []
    colour_ko = P.BROW_COLORS[args.color]["ko"]
    for i, style in enumerate(styles, 1):
        st = P.BROW_STYLES[style]
        prompt = PR.build_restyle_prompt(style, args.color, with_guide_image=use_guide, notes=args.notes or "")
        out_path = out_dir / f"{i:02d}_{style}_{args.color}.png"
        entry: Dict[str, Any] = {"style": style, "style_ko": st.ko, "prompt": prompt, "image": str(out_path)}
        if args.dry_run:
            print(f"===== [{i}/{len(styles)}] {st.ko}")
            print(prompt)
            print()
            continue
        _log(f"[{i}/{len(styles)}] {st.ko} ({colour_ko}) -> {out_path.name}")
        images: List[Path] = [prepared_path]
        if use_guide and guide_path is not None:
            images.append(guide_path)
        size = f"{prepared.size[0]}x{prepared.size[1]}" if backend.name == "api" else "auto"
        try:
            result = backend.edit(
                prompt, images, out_path,
                mask=(mask_api_path if backend.name == "api" and mask_api_path is not None and not args.no_mask else None),
                size=size, quality=args.quality,
            )
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
        _record_usage(entry, result, manifest)
        if result.pending:
            manifest["variants"].append(entry)
            _write_json(manifest_path, manifest)
            continue
        final_img = Image.open(result.path).convert("RGB")
        aligned = True
        if lm is not None and mask is not None and not args.no_composite and args.landmarks != "none":
            aligned, why = _edit_aligned(args, final_img, lm, prepared.size)
            entry["aligned"] = aligned
            entry["align_check"] = why
            if aligned:
                _log(f"정렬 확인: {why}")
            else:
                _log(f"경고: 편집 결과의 얼굴 위치가 원본과 다릅니다({why}). 눈썹만 합성하면 눈이 겹쳐 보이므로 "
                     "합성을 생략하고 편집 결과를 그대로 씁니다.")
        if mask is not None and not args.no_composite and aligned:
            final_img = M.composite_brows(prepared, final_img, mask)
            final_path = out_dir / f"{i:02d}_{style}_{args.color}_composited.png"
            final_img.save(final_path)
            entry["composited"] = str(final_path)
            _log(f"원본 위에 눈썹만 합성: {final_path.name}")
        results.append((f"{i}. {st.ko} · {colour_ko}" + ("" if aligned else " (합성 생략)"), final_img))
        manifest["variants"].append(entry)
        _write_json(manifest_path, manifest)

    if args.dry_run:
        return 0
    if results and args.sheet != "none":
        ipd = args.ipd_mm or P.default_ipd_mm(args.gender if args.gender != "random" else None, None)
        opts = _sheet_options(args, ipd, f"{src.name}  ·  {colour_ko}  ·  {_dt.date.today().isoformat()}", args.note or "")
        items = [("원본", prepared)] + results
        written: List[Path] = []
        if args.sheet in ("grid", "both"):
            pages = S.compose_grid_sheet(items, opts)
            written += S.save_pages(pages, out_dir / "sheet_compare", args.dpi, pdf=not args.no_pdf)
        if args.sheet in ("browzone", "both"):
            if lm is None:
                _log("눈썹 구역 1:1 시트는 랜드마크가 필요해 건너뜁니다.")
            else:
                pages = S.compose_browzone_sheet([(label, img, lm) for label, img in items], opts, copies=1)
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
    print()
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def _add_backend_args(p: argparse.ArgumentParser, *, with_size: bool) -> None:
    g = p.add_argument_group("생성 백엔드")
    g.add_argument("--backend", choices=["codex", "api", "manual"], default="codex",
                   help="codex: Codex CLI 내장 이미지 생성(ChatGPT 로그인, 기본값) / api: OpenAI Images API(OPENAI_API_KEY) / manual: 프롬프트만 저장")
    g.add_argument("--model", help="api: 이미지 모델(기본 생성 gpt-image-2.5-flare, 편집 gpt-image-2.5-sunburst) / codex: 에이전트 모델(-m)")
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
    r.add_argument("--notes", help="편집 프롬프트에 덧붙일 지시문")
    r.add_argument("--seed", type=int, help="random:N 스타일 선택 시드")
    r.add_argument("--out-dir", help="출력 폴더 (기본 output/browlab/restyle_<파일명>_<시각>)")
    r.add_argument("--edit-model", help="api 백엔드 편집 모델 (기본 gpt-image-2.5-sunburst)")
    r.add_argument("--max-edge", type=int, default=2048, help="편집 전 긴 변 최대 픽셀")
    r.add_argument("--mask-side", type=float, default=0.16, help="마스크 좌우 여유 (동공간 거리 배수)")
    r.add_argument("--mask-up", type=float, default=0.40, help="마스크 위쪽 여유 (동공간 거리 배수)")
    r.add_argument("--mask-down", type=float, default=0.12, help="마스크 아래쪽 여유 (동공간 거리 배수)")
    r.add_argument("--no-mask", action="store_true", help="api 백엔드에서 알파 마스크를 보내지 않음")
    r.add_argument("--no-guide-image", action="store_true", help="codex 백엔드에 빨간 영역 가이드 이미지를 첨부하지 않음")
    r.add_argument("--no-composite", action="store_true", help="결과의 눈썹 영역만 원본 위에 합성하는 단계를 생략")
    r.add_argument("--sheet", choices=["none", "grid", "browzone", "both"], default="both", help="비교 시트 종류")
    r.add_argument("--pupils", help="수동 랜드마크: 동공 픽셀 좌표 x1,y1,x2,y2 (준비된 이미지 기준)")
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
