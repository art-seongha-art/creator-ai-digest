"""Web UI for BrowLab (standard library only).

    python -m browlab web --host 127.0.0.1 --port 8177 --data-dir ~/browlab_data \
        --password-file ~/.config/browlab/password.txt --base-path /browlab

Every request from the page becomes one ``python -m browlab <command> ... --out-dir <job dir>``
subprocess. A single worker thread runs them one at a time (image generation is slow and
the Codex/API quota is shared), the browser polls ``api/jobs/<id>`` and downloads the
PNG/PDF results from ``files/<id>/...``.

The page uses only relative URLs, so it works at ``/`` and behind a path prefix such as
``https://host/browlab/`` (``--base-path /browlab`` makes the server strip that prefix).
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hmac
import io
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from . import masks as M
from . import presets as P

KINDS = ("generate", "restyle", "sheet", "calibrate")
KIND_KO = {"generate": "얼굴 생성", "restyle": "내 사진 눈썹 바꾸기", "sheet": "사진을 1:1 시트로", "calibrate": "프린터 보정 시트"}
# shorter chip labels for the page (full names stay in presets.py)
BROW_SHORT_KO = {"sparse": "모량 부족", "faint": "연함", "patchy": "군데군데 빔", "missing_tail": "꼬리 없음", "asymmetric": "비대칭",
                 "overplucked": "과도하게 뽑음", "undefined": "형태 불분명", "scar_gap": "흉터", "almost_none": "거의 없음"}
BACKENDS = ("auto", "codex", "api", "manual")
LAYOUTS = ("face", "browzone", "both")
SHEETS = ("none", "grid", "browzone", "both")
LANDMARKS = ("auto", "mediapipe", "codex", "manual", "none")
QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FILE_EXTS = IMAGE_EXTS | {".pdf", ".json", ".txt"}
MAX_BODY = 40 * 1024 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
SESSION_TTL = 30 * 24 * 3600
LOGIN_FAILS = 8
LOGIN_WINDOW = 15 * 60
COOKIE = "browlab_session"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


class BadRequest(ValueError):
    pass


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
@dataclass
class WebConfig:
    data_dir: Path
    repo_root: Path
    password: Optional[str]
    base_path: str = ""
    codex_bin: str = "codex"
    default_backend: str = "auto"
    timeout: int = 900
    font: Optional[str] = None

    @property
    def jobs_root(self) -> Path:
        return self.data_dir / "jobs"


# ---------------------------------------------------------------------------
# parameter sanitising (the page is public-facing: never pass raw values to argv)
# ---------------------------------------------------------------------------
def _choice(value: Any, choices: Tuple[str, ...] | List[str], default: str) -> str:
    return value if isinstance(value, str) and value in choices else default


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _int_opt(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadRequest("정수가 아닙니다")


def _float_opt(value: Any, lo: float, hi: float) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise BadRequest("숫자가 아닙니다")
    if not lo <= f <= hi:
        raise BadRequest(f"{lo}~{hi} 범위여야 합니다")
    return f


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return default


def _text(value: Any, maxlen: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch == "\n" or ord(ch) >= 32)
    return cleaned.strip()[:maxlen]


def _age(value: Any) -> str:
    if isinstance(value, str) and value in P.AGE_CHOICES:
        return value
    n = _int_opt(value)
    if n is None:
        return "random"
    if not 15 <= n <= 79:
        raise BadRequest("나이는 15~79세")
    return str(n)


def _pupils(value: Any) -> Optional[str]:
    if not value:
        return None
    parts = [p for p in str(value).replace(";", ",").split(",") if p.strip()]
    if len(parts) != 4:
        raise BadRequest("동공 좌표는 x1,y1,x2,y2")
    try:
        return ",".join(str(float(p)) for p in parts)
    except ValueError as exc:
        raise BadRequest("동공 좌표는 숫자") from exc


def _styles(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",")]
    if not isinstance(value, list):
        return []
    if "all" in value:
        return list(P.BROW_STYLE_CHOICES)
    return [v for v in value if isinstance(v, str) and v in P.BROW_STYLE_CHOICES]


def _size(value: Any) -> str:
    text = value if isinstance(value, str) and value.strip() else "1536x2304"
    try:
        return M.validate_gpt_image_size(text.strip())
    except Exception as exc:  # argparse-style type errors
        raise BadRequest(f"크기 오류: {exc}") from exc


def _model(value: Any) -> Optional[str]:
    if not value:
        return None
    if not isinstance(value, str) or not _MODEL_RE.match(value):
        raise BadRequest("모델 이름 형식 오류")
    return value


def save_upload(job_dir: Path, b64: Any) -> Path:
    """Decode a base64 upload, verify it is an image, fix EXIF rotation, save as input.*"""
    if not isinstance(b64, str) or not b64:
        raise BadRequest("사진이 없습니다")
    if b64.startswith("data:") and "," in b64[:64]:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise BadRequest("사진 인코딩 오류") from exc
    if len(raw) > MAX_UPLOAD:
        raise BadRequest("사진은 25 MB 이하")
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
        with Image.open(io.BytesIO(raw)) as im:
            fmt = (im.format or "").lower()
            fixed = ImageOps.exif_transpose(im)
            fixed.load()
    except Exception as exc:
        raise BadRequest("이미지 파일이 아닙니다 (PNG/JPEG/WebP)") from exc
    if fmt == "png":
        path = job_dir / "input.png"
        fixed.save(path)
    else:
        path = job_dir / "input.jpg"
        fixed.convert("RGB").save(path, quality=95)
    return path


def _backend_args(p: Dict[str, Any], cfg: WebConfig, *, with_size: bool) -> Tuple[List[str], Dict[str, Any]]:
    backend = _choice(p.get("backend"), BACKENDS, cfg.default_backend)
    quality = _choice(p.get("quality"), QUALITIES, "high")
    argv = ["--backend", backend, "--quality", quality, "--timeout", str(cfg.timeout), "--codex-bin", cfg.codex_bin]
    shown: Dict[str, Any] = {"backend": backend, "quality": quality}
    if with_size:
        size = _size(p.get("size"))
        argv += ["--size", size]
        shown["size"] = size
    model = _model(p.get("model"))
    if model:
        argv += ["--model", model]
        shown["model"] = model
    return argv, shown


def _sheet_args(p: Dict[str, Any], cfg: WebConfig) -> Tuple[List[str], Dict[str, Any]]:
    argv: List[str] = []
    shown: Dict[str, Any] = {}
    ipd = _float_opt(p.get("ipd_mm"), 50, 75)
    if ipd is not None:
        argv += ["--ipd-mm", str(ipd)]
        shown["ipd_mm"] = ipd
    landmarks = _choice(p.get("landmarks"), LANDMARKS, "auto")
    pupils = _pupils(p.get("pupils"))
    if landmarks == "manual" and not pupils:
        raise BadRequest("manual 랜드마크는 동공 좌표가 필요합니다")
    if pupils and landmarks == "auto":
        landmarks = "manual"
    argv += ["--landmarks", landmarks]
    shown["landmarks"] = landmarks
    if pupils:
        argv += ["--pupils", pupils]
        shown["pupils"] = pupils
    if cfg.font:
        argv += ["--font", cfg.font]
    return argv, shown


def copy_from_job(job_dir: Path, jobs_root: Path, ref: Any) -> Path:
    """Reuse an image of an earlier job: ``photo_from = {"job": id, "file": relative name}``."""
    if not isinstance(ref, dict):
        raise BadRequest("photo_from 형식 오류")
    job_id, rel = ref.get("job"), ref.get("file")
    if not isinstance(job_id, str) or not re.match(r"^[A-Za-z0-9_]+$", job_id) or not isinstance(rel, str):
        raise BadRequest("photo_from 형식 오류")
    root = (jobs_root / job_id).resolve()
    src = (root / rel).resolve()
    if root not in src.parents or not src.is_file() or src.suffix.lower() not in IMAGE_EXTS:
        raise BadRequest("이전 작업의 이미지를 찾지 못했습니다")
    dst = job_dir / ("input" + src.suffix.lower())
    shutil.copyfile(src, dst)
    return dst


def build_argv(kind: str, p: Dict[str, Any], job_dir: Path, cfg: WebConfig) -> Tuple[List[str], Dict[str, Any]]:
    """Translate the page's JSON into a whitelisted CLI argument list."""
    if kind not in KINDS:
        raise BadRequest("알 수 없는 작업 종류")
    argv = [sys.executable, "-m", "browlab", kind]
    shown: Dict[str, Any] = {}
    if kind in ("restyle", "sheet"):
        if p.get("photo_from"):
            photo = copy_from_job(job_dir, cfg.jobs_root, p.get("photo_from"))
            shown["photo"] = _text(p.get("photo_name"), 120) or f"{p['photo_from'].get('job')}/{p['photo_from'].get('file')}"
            shown["photo_from"] = {"job": p["photo_from"].get("job"), "file": p["photo_from"].get("file")}
        else:
            photo = save_upload(job_dir, p.get("photo"))
            shown["photo"] = _text(p.get("photo_name"), 120) or photo.name
        argv.append(str(photo))
    argv += ["--out-dir", str(job_dir)]

    if kind == "generate":
        count = _int(p.get("count"), 1, 8, 1)
        age = _age(p.get("age"))
        argv += ["-n", str(count), "--age", age]
        shown.update(count=count, age=age)
        for key, flag, choices in (
            ("gender", "--gender", P.GENDER_CHOICES),
            ("face_shape", "--face-shape", P.FACE_SHAPE_CHOICES),
            ("brow_condition", "--brow-condition", P.BROW_CONDITION_CHOICES),
        ):
            v = _choice(p.get(key), choices, "random")
            argv += [flag, v]
            shown[key] = v
        # 외모 기본값은 한국인 (연구자 지시 2026-09-12). 무작위/다른 외모는 고급 설정에서만.
        eth = _choice(p.get("ethnicity"), P.ETHNICITY_CHOICES, "korean")
        argv += ["--ethnicity", eth]
        shown["ethnicity"] = eth
        notes = _text(p.get("notes"), 600)
        if notes:
            argv.append(f"--notes={notes}")
            shown["notes"] = notes
        seed = _int_opt(p.get("seed"))
        if seed is not None:
            argv += ["--seed", str(seed)]
            shown["seed"] = seed
        layout = _choice(p.get("layout"), LAYOUTS, "both")
        argv += ["--layout", layout, "--copies", str(_int(p.get("copies"), 1, 6, 3))]
        shown["layout"] = layout
        if _bool(p.get("guides"), True):
            argv.append("--guides")
            shown["guides"] = True
        b, s = _backend_args(p, cfg, with_size=True)
        argv += b
        shown.update(s)
        b, s = _sheet_args(p, cfg)
        argv += b
        shown.update(s)

    elif kind == "restyle":
        styles = _styles(p.get("styles")) or ["korean_natural", "straight", "soft_arch", "feathered"]
        color = _choice(p.get("color"), P.BROW_COLOR_CHOICES, "match_hair")
        argv += ["--styles", ",".join(styles), "--color", color]
        shown.update(styles=styles, color=color)
        notes = _text(p.get("notes"), 600)
        if notes:
            argv.append(f"--notes={notes}")
            shown["notes"] = notes
        sheet = _choice(p.get("sheet"), SHEETS, "both")
        argv += ["--sheet", sheet, "--gender", _choice(p.get("gender"), P.GENDER_CHOICES, "random")]
        shown["sheet"] = sheet
        if _bool(p.get("no_composite")):
            argv.append("--no-composite")
            shown["no_composite"] = True
        b, s = _backend_args(p, cfg, with_size=False)
        argv += b
        shown.update(s)
        edit_model = _model(p.get("edit_model"))
        if edit_model:
            argv += ["--edit-model", edit_model]
            shown["edit_model"] = edit_model
        b, s = _sheet_args(p, cfg)
        argv += b
        shown.update(s)

    elif kind == "sheet":
        layout = _choice(p.get("layout"), LAYOUTS, "both")
        argv += ["--layout", layout, "--copies", str(_int(p.get("copies"), 1, 6, 3)),
                 "--gender", _choice(p.get("gender"), P.GENDER_CHOICES, "random"), "--codex-bin", cfg.codex_bin]
        shown["layout"] = layout
        if _bool(p.get("guides"), True):
            argv.append("--guides")
            shown["guides"] = True
        age_group = p.get("age_group")
        if isinstance(age_group, str) and age_group in P.AGE_GROUPS:
            argv += ["--age-group", age_group]
            shown["age_group"] = age_group
        face_shape = p.get("face_shape")
        if isinstance(face_shape, str) and face_shape in P.FACE_SHAPES:
            argv += ["--face-shape", face_shape]
            shown["face_shape"] = face_shape
        for key, flag in (("caption", "--caption"), ("note", "--note")):
            v = _text(p.get(key), 200)
            if v:
                argv.append(f"{flag}={v}")
                shown[key] = v
        b, s = _sheet_args(p, cfg)
        argv += b
        shown.update(s)

    elif kind == "calibrate":
        ipd = _float_opt(p.get("ipd_mm"), 50, 75)
        if ipd is not None:
            argv += ["--ipd-mm", str(ipd)]
            shown["ipd_mm"] = ipd
        if cfg.font:
            argv += ["--font", cfg.font]
    return argv, shown


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    kind: str
    created: str
    params: Dict[str, Any]
    argv: List[str]
    status: str = "queued"  # queued | running | done | failed | cancelled
    rc: Optional[int] = None
    started: Optional[str] = None
    finished: Optional[str] = None
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class JobStore:
    def __init__(self, cfg: WebConfig) -> None:
        self.cfg = cfg
        self.jobs: Dict[str, Job] = {}
        self.procs: Dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self.queue: "queue.Queue[str]" = queue.Queue()
        cfg.jobs_root.mkdir(parents=True, exist_ok=True)
        self._load()
        self.worker = threading.Thread(target=self._run_forever, name="browlab-worker", daemon=True)
        self.worker.start()

    # -- persistence ----------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.cfg.jobs_root / job_id

    def _save(self, job: Job) -> None:
        path = self.job_dir(job.id) / "job.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> None:
        for meta in sorted(self.cfg.jobs_root.glob("*/job.json")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                job = Job(**{k: data.get(k) for k in Job.__dataclass_fields__ if k in data})
            except Exception:
                continue
            if job.status in ("queued", "running"):
                job.status = "failed"
                job.error = "서버가 재시작되어 중단됨"
                job.finished = job.finished or _now()
                self._save(job)
            self.jobs[job.id] = job

    # -- API ------------------------------------------------------------------
    def submit(self, kind: str, params: Dict[str, Any]) -> Job:
        job_id = f"{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{kind}_{secrets.token_hex(2)}"
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        try:
            argv, shown = build_argv(kind, params, job_dir, self.cfg)
        except BadRequest:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        job = Job(id=job_id, kind=kind, created=_now(), params=shown, argv=argv)
        with self.lock:
            self.jobs[job_id] = job
            self._save(job)
        self.queue.put(job_id)
        return job

    def cancel(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs[job_id]
            proc = self.procs.get(job_id)
            if job.status == "queued":
                job.status = "cancelled"
                job.finished = _now()
                self._save(job)
            elif job.status == "running" and proc is not None:
                job.status = "cancelled"  # the worker records rc/finished when the process exits
                self._save(job)
                try:
                    proc.terminate()
                except OSError:
                    pass
        return job

    def delete(self, job_id: str) -> None:
        """Remove a job from the list; its folder moves to data_dir/trash/<id> (recoverable by hand)."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            proc = self.procs.get(job_id)
            if job.status in ("queued", "running"):
                job.status = "cancelled"
                job.error = "삭제됨"
                if proc is not None:
                    try:
                        proc.terminate()
                    except OSError:
                        pass
            self.jobs.pop(job_id, None)
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        src = self.job_dir(job_id)
        if src.exists():
            trash = self.cfg.data_dir / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            dst = trash / job_id
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))

    def gallery(self) -> List[Dict[str, Any]]:
        """Flat list of result cards (one per image) for the gallery view, newest job first."""
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)[:300]
        items: List[Dict[str, Any]] = []
        for job in jobs:
            manifest = self._manifest(job)
            base = self.summary(job)
            root = self.job_dir(job.id)
            files = {f["name"]: f for f in self.list_files(job)}

            def url(name: str) -> Optional[str]:
                return files[name]["url"] if name in files else None

            def add(image: Optional[str], label: str, pdfs: List[Tuple[str, str]], **extra: Any) -> None:
                item = dict(base)
                item.update({
                    "image": url(image) if image else None,
                    "image_name": image,
                    "label": label,
                    "pdfs": [{"label": lab, "url": url(n)} for n, lab in pdfs if n in files],
                })
                item.update(extra)
                items.append(item)

            before = len(items)
            if job.kind == "generate":
                for face in manifest.get("faces", []):
                    if face.get("error") or face.get("pending"):
                        continue
                    name = Path(str(face.get("image", ""))).name
                    if name not in files:
                        continue
                    stem = name[:-4]
                    add(name, face.get("label_ko") or base["title"],
                        [(f"sheets/{stem}_A4.pdf", "얼굴 1:1 PDF"), (f"sheets/{stem}_browzone.pdf", "눈썹 구역 PDF")],
                        prompt=face.get("prompt", ""), seed=face.get("seed"), face=face.get("label_ko"))
            elif job.kind == "restyle":
                pdfs = [("sheet_compare.pdf", "비교표 PDF"), ("sheet_browzone.pdf", "눈썹 구역 1:1 PDF")]
                for v in manifest.get("variants", []):
                    if v.get("error") or v.get("pending"):
                        continue
                    name = Path(str(v.get("composited") or v.get("image") or "")).name
                    if name in files:
                        add(name, f"{v.get('style_ko', '')} · {P.BROW_COLORS.get(job.params.get('color', ''), {}).get('ko', '')}", pdfs,
                            aligned=v.get("aligned", True), style=v.get("style"))
            elif job.kind == "sheet":
                inp = next((n for n in files if n.startswith("input.")), None)
                add(inp, base["title"], [(n, "얼굴 1:1 PDF" if n.endswith("_A4.pdf") else "눈썹 구역 PDF") for n in sorted(files) if n.endswith(".pdf")])
            elif job.kind == "calibrate":
                add("calibration_A4.png" if "calibration_A4.png" in files else None, "프린터 보정 시트", [("calibration_A4.pdf", "보정 시트 PDF")])
            if len(items) == before and job.status in ("queued", "running", "failed", "cancelled"):
                add(None, base["title"], [])
        return items

    def _run_forever(self) -> None:
        while True:
            job_id = self.queue.get()
            with self.lock:
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                job.status = "running"
                job.started = _now()
                self._save(job)
            log_path = self.job_dir(job_id) / "job.log"
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            rc: Optional[int] = None
            error = ""
            try:
                with open(log_path, "ab") as log:
                    log.write(("$ " + " ".join(job.argv) + "\n").encode("utf-8"))
                    log.flush()
                    proc = subprocess.Popen(
                        job.argv, cwd=str(self.cfg.repo_root), stdout=log, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, env=env, start_new_session=True,
                    )
                    with self.lock:
                        self.procs[job_id] = proc
                    rc = proc.wait()
            except Exception as exc:  # e.g. interpreter missing
                error = f"실행 실패: {exc}"
            with self.lock:
                self.procs.pop(job_id, None)
                job.rc = rc
                job.finished = _now()
                if job.status == "cancelled":
                    job.error = job.error or "사용자가 취소함"
                elif error:
                    job.status = "failed"
                    job.error = error
                elif rc == 0:
                    job.status = "done"
                else:
                    job.status = "failed"
                    job.error = f"종료 코드 {rc} (아래 로그 참고)"
                self._save(job)

    # -- views ----------------------------------------------------------------
    def list_files(self, job: Job) -> List[Dict[str, Any]]:
        root = self.job_dir(job.id)
        out: List[Dict[str, Any]] = []
        if not root.is_dir():
            return out
        for p in sorted(root.rglob("*")):
            if not p.is_file() or ".thumbs" in p.parts or p.name in ("job.json", "job.log", "job.json.tmp"):
                continue
            ext = p.suffix.lower()
            if ext not in FILE_EXTS:
                continue
            rel = p.relative_to(root).as_posix()
            kind = "image" if ext in IMAGE_EXTS else "pdf" if ext == ".pdf" else "other"
            out.append({"name": rel, "size": p.stat().st_size, "kind": kind, "url": f"files/{job.id}/{rel}"})
        return out

    def log_tail(self, job: Job, limit: int = 6000) -> str:
        path = self.job_dir(job.id) / "job.log"
        if not path.exists():
            return ""
        data = path.read_bytes()
        return data[-limit:].decode("utf-8", errors="replace")

    def _manifest(self, job: Job) -> Dict[str, Any]:
        path = self.job_dir(job.id) / "manifest.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def title_ko(self, job: Job, manifest: Dict[str, Any]) -> str:
        p = job.params
        if job.kind == "generate":
            labels = [f.get("label_ko") for f in manifest.get("faces", []) if f.get("label_ko")]
            if labels:
                return labels[0] + (f" 외 {len(labels) - 1}명" if len(labels) > 1 else "")
            age = p.get("age", "random")
            age_ko = "무작위 나이" if age == "random" else (P.AGE_GROUP_KO.get(age) or f"{age}세")
            gender_ko = P.GENDERS.get(p.get("gender", ""), {}).get("ko", "성별 무작위")
            shape = P.FACE_SHAPES.get(p.get("face_shape", ""))
            brow = P.BROW_CONDITIONS.get(p.get("brow_condition", ""))
            parts = [f"{p.get('count', 1)}명", age_ko, gender_ko, shape.ko if shape else "얼굴형 무작위", brow.ko if brow else "눈썹 무작위"]
            return " · ".join(parts)
        if job.kind == "restyle":
            colour = P.BROW_COLORS.get(p.get("color", ""), {}).get("ko", "")
            return f"{p.get('photo', '사진')} · {len(p.get('styles', []))}개 스타일 · {colour}"
        if job.kind == "sheet":
            layout_ko = {"both": "얼굴 + 눈썹 구역", "face": "얼굴 1:1", "browzone": "눈썹 구역"}.get(p.get("layout", ""), "")
            return f"{p.get('photo', '사진')} · {layout_ko}"
        return "100 mm 자 · 20 mm 정사각형"

    def thumb(self, job: Job) -> Optional[str]:
        root = self.job_dir(job.id)
        if not root.is_dir():
            return None
        patterns = {
            "generate": ["face_*.png", "sheets/*_A4.png"],
            "restyle": ["*_composited.png", "0[1-9]_*.png", "00_original.png"],
            "sheet": ["*_A4.png", "input.*"],
            "calibrate": ["calibration_A4.png"],
        }.get(job.kind, ["*.png"])
        for pat in patterns:
            hits = sorted(root.glob(pat))
            if hits:
                return f"files/{job.id}/{hits[0].relative_to(root).as_posix()}"
        return None

    # -- usage / settings -----------------------------------------------------
    @property
    def settings_path(self) -> Path:
        return self.cfg.data_dir / "settings.json"

    def get_settings(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cur = self.get_settings()
        if "budget_usd" in patch:
            value = patch["budget_usd"]
            if value in (None, ""):
                cur.pop("budget_usd", None)
                cur.pop("budget_set_at", None)
            else:
                cur["budget_usd"] = _float_opt(value, 0, 100000)
                cur["budget_set_at"] = _now()
        if "budget_note" in patch:
            cur["budget_note"] = _text(patch["budget_note"], 80)
        tmp = self.settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.settings_path)
        return cur

    @staticmethod
    def job_cost(manifest: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Tuple[float, int]:
        """(estimated USD, number of API images) from the token usage in a job's manifest.

        Re-priced with the current rate table on every read, so a corrected rate applies to old jobs too.
        """
        from . import backends as B

        entries = [e for e in list(manifest.get("faces", [])) + list(manifest.get("variants", [])) if isinstance(e, dict)]
        cost = 0.0
        n = 0
        for e in entries:
            if not e.get("usage"):
                continue
            n += 1
            p = params or {}
            model = e.get("model") or manifest.get("model") or p.get("edit_model") or p.get("model")
            cost += B.estimate_cost_usd(e["usage"], model) or float(e.get("cost_usd") or 0.0)
        return round(cost, 6), n

    def usage_totals(self) -> Dict[str, Any]:
        total, images, api_jobs, first = 0.0, 0, 0, None
        with self.lock:
            jobs = list(self.jobs.values())
        for j in jobs:
            cost, n = self.job_cost(self._manifest(j), j.params)
            if n:
                total += cost
                images += n
                api_jobs += 1
                first = j.created if first is None or j.created < first else first
        settings = self.get_settings()
        budget = settings.get("budget_usd")
        return {
            "total_usd": round(total, 4), "api_images": images, "api_jobs": api_jobs, "first": first,
            "budget_usd": budget, "budget_set_at": settings.get("budget_set_at"), "budget_note": settings.get("budget_note", ""),
            "remaining_usd": round(float(budget) - total, 4) if budget is not None else None,
            "rates": {"image_out_per_m": 30.0, "image_in_per_m": 8.0, "text_in_per_m": 5.0},
        }

    def summary(self, job: Job) -> Dict[str, Any]:
        manifest = self._manifest(job)
        cost, api_images = self.job_cost(manifest, job.params)
        progress = ""
        if job.kind == "generate" and job.status == "running":
            progress = f"{len(manifest.get('faces', []))}/{job.params.get('count', 1)}"
        elif job.kind == "restyle" and job.status == "running":
            progress = f"{len(manifest.get('variants', []))}/{len(job.params.get('styles', []))}"
        used: List[str] = []  # backend/model that actually produced each image (auto backend may vary)
        hint = ""
        for e in list(manifest.get("faces", [])) + list(manifest.get("variants", [])):
            if not isinstance(e, dict):
                continue
            if e.get("backend"):
                tag = f"{e['backend']} {e.get('model') or ''}".strip()
                if tag not in used:
                    used.append(tag)
            if e.get("hint") and not hint:
                hint = str(e["hint"])
        return {
            "id": job.id, "kind": job.kind, "kind_ko": KIND_KO.get(job.kind, job.kind), "created": job.created,
            "status": job.status, "started": job.started, "finished": job.finished, "error": job.error, "used": used, "hint": hint,
            "params": job.params, "rc": job.rc, "title": self.title_ko(job, manifest), "thumb": self.thumb(job),
            "progress": progress, "cost_usd": cost, "api_images": api_images,
        }

    def detail(self, job: Job) -> Dict[str, Any]:
        data = self.summary(job)
        data["files"] = self.list_files(job)
        data["log"] = self.log_tail(job)
        manifest = self._manifest(job)
        if manifest:
            data["manifest"] = manifest
        return data


# ---------------------------------------------------------------------------
# sessions / auth
# ---------------------------------------------------------------------------
class Sessions:
    def __init__(self, password: Optional[str]) -> None:
        self.password = password
        self.tokens: Dict[str, float] = {}
        self.fails: Dict[str, List[float]] = {}
        self.lock = threading.Lock()

    @property
    def required(self) -> bool:
        return self.password is not None

    def check(self, token: Optional[str]) -> bool:
        if not self.required:
            return True
        if not token:
            return False
        with self.lock:
            exp = self.tokens.get(token)
            if exp is None:
                return False
            if exp < time.time():
                self.tokens.pop(token, None)
                return False
            return True

    def login(self, password: Any, ip: str) -> Optional[str]:
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < LOGIN_WINDOW]
            self.fails[ip] = recent
            if len(recent) >= LOGIN_FAILS:
                return None
            ok = isinstance(password, str) and self.password is not None and hmac.compare_digest(
                password.encode("utf-8"), self.password.encode("utf-8"))
            if not ok:
                recent.append(now)
                return None
            token = secrets.token_urlsafe(32)
            self.tokens[token] = now + SESSION_TTL
            self.fails.pop(ip, None)
            return token

    def locked(self, ip: str) -> bool:
        now = time.time()
        with self.lock:
            return len([t for t in self.fails.get(ip, []) if now - t < LOGIN_WINDOW]) >= LOGIN_FAILS

    def logout(self, token: Optional[str]) -> None:
        if token:
            with self.lock:
                self.tokens.pop(token, None)


# ---------------------------------------------------------------------------
# presets for the page
# ---------------------------------------------------------------------------
def presets_json(cfg: WebConfig) -> Dict[str, Any]:
    ages = [{"key": "random", "ko": "무작위"}] + [
        {"key": k, "ko": f"{P.AGE_GROUP_KO[k]} ({lo}~{hi}세)", "short": P.AGE_GROUP_KO[k]} for k, (lo, hi) in P.AGE_GROUPS.items()
    ]
    rnd = {"key": "random", "ko": "무작위"}
    return {
        "ages": ages,
        "genders": [rnd] + [{"key": k, "ko": v["ko"]} for k, v in P.GENDERS.items()],
        "face_shapes": [rnd] + [{"key": k, "ko": v.ko, "tip": v.brow_tip_ko} for k, v in P.FACE_SHAPES.items()],
        "brow_conditions": [rnd] + [{"key": k, "ko": v.ko, "short": BROW_SHORT_KO.get(k, v.ko)} for k, v in P.BROW_CONDITIONS.items()],
        "ethnicities": [{"key": "korean", "ko": "한국인 (기본)"}]
        + [{"key": k, "ko": v.ko} for k, v in P.ETHNICITIES.items() if k != "korean"]
        + [{"key": "random", "ko": "가중 무작위 (한국인 비중 높음)"}, {"key": "any", "ko": "균등 무작위"}],
        "brow_styles": [{"key": k, "ko": v.ko} for k, v in P.BROW_STYLES.items()],
        "brow_colors": [{"key": k, "ko": v["ko"]} for k, v in P.BROW_COLORS.items()],
        "qualities": list(QUALITIES),
        "layouts": [{"key": "both", "ko": "얼굴 전체 + 눈썹 구역"}, {"key": "face", "ko": "얼굴 전체 1:1"}, {"key": "browzone", "ko": "눈썹 구역만 1:1"}],
        "sheets": [{"key": "both", "ko": "비교표 + 눈썹 구역 1:1"}, {"key": "grid", "ko": "비교표만"}, {"key": "browzone", "ko": "눈썹 구역 1:1만"}, {"key": "none", "ko": "시트 없음"}],
        "backends": [
            {"key": "auto", "ko": "자동 (Codex 먼저 → 안 되면 API 2.5→2→1.5→1→1-mini)",
             "available": shutil.which(cfg.codex_bin) is not None or bool(os.environ.get("OPENAI_API_KEY"))},
            {"key": "codex", "ko": "Codex (ChatGPT 구독, 추가 요금 없음)", "available": shutil.which(cfg.codex_bin) is not None},
            {"key": "api", "ko": "OpenAI API (키 필요, 장당 과금)", "available": bool(os.environ.get("OPENAI_API_KEY"))},
            {"key": "manual", "ko": "프롬프트만 저장 (직접 생성)", "available": True},
        ],
        "default_backend": cfg.default_backend,
        "models": [
            {"key": "", "ko": "기본 · 최신 GPT Image 2.5 (생성 flare · 편집 sunburst)"},
            {"key": "gpt-image-2.5-sunburst", "ko": "gpt-image-2.5-sunburst 로 생성도 (정밀, 느림)"},
            {"key": "gpt-image-1-mini", "ko": "gpt-image-1-mini — 임시 (2.5 가 열릴 때까지만, 품질 낮음)"},
        ],
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class BrowLabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: Tuple[str, int], cfg: WebConfig) -> None:
        super().__init__(addr, Handler)
        self.cfg = cfg
        self.store = JobStore(cfg)
        self.sessions = Sessions(cfg.password)


class Handler(BaseHTTPRequestHandler):
    server: BrowLabServer
    server_version = f"BrowLab/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- helpers --------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than the default
        sys.stderr.write("[browlab-web] %s %s\n" % (self.client_ip(), fmt % args))

    def client_ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        return self.client_address[0]

    def is_https(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def route(self) -> Tuple[str, Dict[str, List[str]]]:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        base = self.server.cfg.base_path
        if base:
            if path == base:
                path = ""  # handled as redirect below
            elif path.startswith(base + "/"):
                path = path[len(base):]
        return path, parse_qs(parts.query)

    def token(self) -> Optional[str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        morsel = jar.get(COOKIE)
        return morsel.value if morsel else None

    def send_json(self, data: Any, status: int = 200, extra: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data: bytes, ctype: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise BadRequest("요청이 너무 큽니다")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            raise BadRequest("JSON 형식 오류") from exc
        if not isinstance(data, dict):
            raise BadRequest("JSON 객체가 필요합니다")
        return data

    def authed(self) -> bool:
        return self.server.sessions.check(self.token())

    def require_auth(self) -> bool:
        if self.authed():
            return True
        self.send_json({"error": "로그인이 필요합니다", "auth": False}, HTTPStatus.UNAUTHORIZED)
        return False

    def cookie_header(self, token: str, clear: bool = False) -> str:
        path = (self.server.cfg.base_path or "") + "/"
        parts = [f"{COOKIE}={'' if clear else token}", f"Path={path}", "HttpOnly", "SameSite=Lax",
                 f"Max-Age={0 if clear else SESSION_TTL}"]
        if self.is_https():
            parts.append("Secure")
        return "; ".join(parts)

    # -- GET --------------------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        try:
            path, query = self.route()
            if path == "" and self.server.cfg.base_path:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", self.server.cfg.base_path + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path in ("", "/", "/index.html"):
                self.send_bytes(index_html(), "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self.send_bytes(b"ok", "text/plain")
                return
            if path == "/api/me":
                sessions = self.server.sessions
                self.send_json({
                    "authed": self.authed(), "auth_required": sessions.required, "version": __version__,
                    "locked": sessions.required and sessions.locked(self.client_ip()),
                    "codex": shutil.which(self.server.cfg.codex_bin) is not None,
                    "api_key": bool(os.environ.get("OPENAI_API_KEY")),
                })
                return
            if not path.startswith(("/api/", "/files/")):
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if not self.require_auth():
                return
            if path == "/api/presets":
                self.send_json(presets_json(self.server.cfg))
                return
            if path == "/api/usage":
                self.send_json(self.server.store.usage_totals())
                return
            if path == "/api/gallery":
                self.send_json({"items": self.server.store.gallery()})
                return
            if path == "/api/jobs":
                store = self.server.store
                with store.lock:
                    jobs = sorted(store.jobs.values(), key=lambda j: j.created, reverse=True)[:200]
                self.send_json({"jobs": [store.summary(j) for j in jobs]})
                return
            m = re.match(r"^/api/jobs/([A-Za-z0-9_]+)$", path)
            if m:
                job = self.server.store.jobs.get(m.group(1))
                if job is None:
                    self.send_json({"error": "없는 작업"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json(self.server.store.detail(job))
                return
            m = re.match(r"^/files/([A-Za-z0-9_]+)/(.+)$", path)
            if m:
                self.serve_file(m.group(1), m.group(2), query)
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except BadRequest as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_file(self, job_id: str, rel: str, query: Dict[str, List[str]]) -> None:
        store = self.server.store
        job = store.jobs.get(job_id)
        if job is None:
            self.send_json({"error": "없는 작업"}, HTTPStatus.NOT_FOUND)
            return
        root = store.job_dir(job_id).resolve()
        target = (root / rel).resolve()
        if (root not in target.parents or not target.is_file() or target.suffix.lower() not in FILE_EXTS
                or target.name.startswith("job.json") or ".thumbs" in target.relative_to(root).parts):
            self.send_json({"error": "없는 파일"}, HTTPStatus.NOT_FOUND)
            return
        ext = target.suffix.lower()
        width = _int(query.get("w", [0])[0], 0, 2000, 0)
        if width and ext in IMAGE_EXTS:
            data = self.thumbnail(root, target, width)
            self.send_bytes(data, "image/jpeg", cache="private, max-age=3600")
            return
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".pdf": "application/pdf", ".json": "application/json; charset=utf-8"}.get(ext, "text/plain; charset=utf-8")
        self.send_bytes(target.read_bytes(), ctype, cache="private, max-age=3600")

    @staticmethod
    def thumbnail(root: Path, target: Path, width: int) -> bytes:
        cache_dir = root / ".thumbs"
        cache_dir.mkdir(exist_ok=True)
        cached = cache_dir / (target.relative_to(root).as_posix().replace("/", "__") + f"_w{width}.jpg")
        if cached.exists() and cached.stat().st_mtime >= target.stat().st_mtime:
            return cached.read_bytes()
        from PIL import Image

        with Image.open(target) as im:
            im = im.convert("RGB")
            im.thumbnail((width, width * 3))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
        cached.write_bytes(buf.getvalue())
        return buf.getvalue()

    # -- POST -------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        try:
            path, _ = self.route()
            # Always consume the request body first: with keep-alive, an unread body would be
            # parsed as the start of the next request on the same connection.
            body = self.read_json()
            if path == "/api/login":
                sessions = self.server.sessions
                if not sessions.required:
                    self.send_json({"ok": True, "auth_required": False})
                    return
                if sessions.locked(self.client_ip()):
                    self.send_json({"error": "실패가 많아 15분간 잠겼습니다"}, HTTPStatus.TOO_MANY_REQUESTS)
                    return
                token = sessions.login(body.get("password"), self.client_ip())
                if token is None:
                    self.send_json({"error": "비밀번호가 틀립니다"}, HTTPStatus.UNAUTHORIZED)
                    return
                self.send_json({"ok": True}, extra={"Set-Cookie": self.cookie_header(token)})
                return
            if path == "/api/logout":
                self.server.sessions.logout(self.token())
                self.send_json({"ok": True}, extra={"Set-Cookie": self.cookie_header("", clear=True)})
                return
            if not self.require_auth():
                return
            if path == "/api/jobs":
                kind = body.get("kind")
                if kind not in KINDS:
                    raise BadRequest("작업 종류를 고르세요")
                job = self.server.store.submit(kind, body)
                self.send_json(self.server.store.summary(job), HTTPStatus.CREATED)
                return
            if path == "/api/settings":
                self.server.store.set_settings(body)
                self.send_json(self.server.store.usage_totals())
                return
            m = re.match(r"^/api/jobs/([A-Za-z0-9_]+)/cancel$", path)
            if m:
                if m.group(1) not in self.server.store.jobs:
                    self.send_json({"error": "없는 작업"}, HTTPStatus.NOT_FOUND)
                    return
                job = self.server.store.cancel(m.group(1))
                self.send_json(self.server.store.summary(job))
                return
            m = re.match(r"^/api/jobs/([A-Za-z0-9_]+)/delete$", path)
            if m:
                try:
                    self.server.store.delete(m.group(1))
                except KeyError:
                    self.send_json({"error": "없는 작업"}, HTTPStatus.NOT_FOUND)
                    return
                self.send_json({"ok": True})
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except BadRequest as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
_INDEX_PATH = Path(__file__).with_name("index.html")
_index_cache: Dict[str, Any] = {}


def index_html() -> bytes:
    """The single-page UI (browlab/index.html), re-read when the file changes."""
    mtime = _INDEX_PATH.stat().st_mtime
    if _index_cache.get("mtime") != mtime:
        _index_cache["mtime"] = mtime
        _index_cache["body"] = _INDEX_PATH.read_bytes()
    return _index_cache["body"]


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
def add_web_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="127.0.0.1", help="바인드 주소 (외부 노출은 리버스 프록시/tailscale serve 로)")
    p.add_argument("--port", type=int, default=8177)
    p.add_argument("--data-dir", default=None, help="작업 폴더 (기본 output/browlab/web)")
    p.add_argument("--password-file", default=None, help="비밀번호 파일 (첫 줄). BROWLAB_PASSWORD 환경변수로도 지정 가능")
    p.add_argument("--no-auth", action="store_true", help="비밀번호 없이 열기 (로컬 전용)")
    p.add_argument("--base-path", default="", help="프록시 경로 접두어 (예: /browlab)")
    p.add_argument("--codex-bin", default="codex", help="codex 실행 파일 (절대경로 권장)")
    p.add_argument("--default-backend", choices=BACKENDS, default="auto",
                   help="auto: Codex 먼저, 안 되면 API(2.5→2→1.5→1→1-mini)")
    p.add_argument("--timeout", type=int, default=900, help="생성 1건당 제한 시간(초)")
    p.add_argument("--font", default=None, help="시트 한글 글꼴 파일")


def load_password(args: argparse.Namespace) -> Optional[str]:
    if args.no_auth:
        return None
    pw = os.environ.get("BROWLAB_PASSWORD", "").strip()
    if args.password_file:
        path = Path(args.password_file).expanduser()
        lines = path.read_text(encoding="utf-8").strip().splitlines() if path.exists() else []
        pw = lines[0].strip() if lines else ""
    if not pw:
        raise SystemExit("비밀번호가 없습니다: --password-file 또는 BROWLAB_PASSWORD 를 주거나, 로컬 전용이면 --no-auth")
    return pw


def make_config(args: argparse.Namespace) -> WebConfig:
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else repo_root / "output" / "browlab" / "web"
    base = (args.base_path or "").rstrip("/")
    if base and not base.startswith("/"):
        base = "/" + base
    return WebConfig(
        data_dir=data_dir, repo_root=repo_root, password=load_password(args), base_path=base,
        codex_bin=args.codex_bin, default_backend=args.default_backend, timeout=args.timeout, font=args.font,
    )


def cmd_web(args: argparse.Namespace) -> int:
    cfg = make_config(args)
    server = BrowLabServer((args.host, args.port), cfg)
    host, port = server.server_address[:2]
    print(f"[browlab-web] v{__version__}  http://{host}:{port}{cfg.base_path}/  data={cfg.data_dir}  "
          f"auth={'on' if cfg.password else 'OFF'}  codex={cfg.codex_bin}  api_key={'yes' if os.environ.get('OPENAI_API_KEY') else 'no'}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
