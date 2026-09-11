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
BACKENDS = ("codex", "api", "manual")
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
    default_backend: str = "codex"
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


def build_argv(kind: str, p: Dict[str, Any], job_dir: Path, cfg: WebConfig) -> Tuple[List[str], Dict[str, Any]]:
    """Translate the page's JSON into a whitelisted CLI argument list."""
    if kind not in KINDS:
        raise BadRequest("알 수 없는 작업 종류")
    argv = [sys.executable, "-m", "browlab", kind]
    shown: Dict[str, Any] = {}
    if kind in ("restyle", "sheet"):
        photo = save_upload(job_dir, p.get("photo"))
        argv.append(str(photo))
        shown["photo"] = _text(p.get("photo_name"), 120) or photo.name
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
            ("ethnicity", "--ethnicity", P.ETHNICITY_CHOICES),
        ):
            v = _choice(p.get(key), choices, "random")
            argv += [flag, v]
            shown[key] = v
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

    def summary(self, job: Job) -> Dict[str, Any]:
        return {
            "id": job.id, "kind": job.kind, "kind_ko": KIND_KO.get(job.kind, job.kind), "created": job.created,
            "status": job.status, "started": job.started, "finished": job.finished, "error": job.error,
            "params": job.params, "rc": job.rc,
        }

    def detail(self, job: Job) -> Dict[str, Any]:
        data = self.summary(job)
        data["files"] = self.list_files(job)
        data["log"] = self.log_tail(job)
        manifest = self.job_dir(job.id) / "manifest.json"
        if manifest.exists():
            try:
                data["manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                pass
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
        {"key": k, "ko": f"{P.AGE_GROUP_KO[k]} ({lo}~{hi}세)"} for k, (lo, hi) in P.AGE_GROUPS.items()
    ]
    rnd = {"key": "random", "ko": "무작위"}
    return {
        "ages": ages,
        "genders": [rnd] + [{"key": k, "ko": v["ko"]} for k, v in P.GENDERS.items()],
        "face_shapes": [rnd] + [{"key": k, "ko": v.ko, "tip": v.brow_tip_ko} for k, v in P.FACE_SHAPES.items()],
        "brow_conditions": [rnd] + [{"key": k, "ko": v.ko} for k, v in P.BROW_CONDITIONS.items()],
        "ethnicities": [{"key": "random", "ko": "가중 무작위 (한국인 비중 높음)"}, {"key": "any", "ko": "균등 무작위"}]
        + [{"key": k, "ko": v.ko} for k, v in P.ETHNICITIES.items()],
        "brow_styles": [{"key": k, "ko": v.ko} for k, v in P.BROW_STYLES.items()],
        "brow_colors": [{"key": k, "ko": v["ko"]} for k, v in P.BROW_COLORS.items()],
        "qualities": list(QUALITIES),
        "layouts": [{"key": "both", "ko": "얼굴 전체 + 눈썹 구역"}, {"key": "face", "ko": "얼굴 전체 1:1"}, {"key": "browzone", "ko": "눈썹 구역만 1:1"}],
        "sheets": [{"key": "both", "ko": "비교표 + 눈썹 구역 1:1"}, {"key": "grid", "ko": "비교표만"}, {"key": "browzone", "ko": "눈썹 구역 1:1만"}, {"key": "none", "ko": "시트 없음"}],
        "backends": [
            {"key": "codex", "ko": "Codex (ChatGPT 구독, 추가 요금 없음)", "available": shutil.which(cfg.codex_bin) is not None},
            {"key": "api", "ko": "OpenAI API (키 필요, 장당 과금)", "available": bool(os.environ.get("OPENAI_API_KEY"))},
            {"key": "manual", "ko": "프롬프트만 저장 (직접 생성)", "available": True},
        ],
        "default_backend": cfg.default_backend,
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
                self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/healthz":
                self.send_bytes(b"ok", "text/plain")
                return
            if path == "/api/me":
                sessions = self.server.sessions
                self.send_json({
                    "authed": self.authed(), "auth_required": sessions.required, "version": __version__,
                    "locked": sessions.required and sessions.locked(self.client_ip()),
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
            if path == "/api/login":
                body = self.read_json()
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
                body = self.read_json()
                kind = body.get("kind")
                if kind not in KINDS:
                    raise BadRequest("작업 종류를 고르세요")
                job = self.server.store.submit(kind, body)
                self.send_json(self.server.store.summary(job), HTTPStatus.CREATED)
                return
            m = re.match(r"^/api/jobs/([A-Za-z0-9_]+)/cancel$", path)
            if m:
                if m.group(1) not in self.server.store.jobs:
                    self.send_json({"error": "없는 작업"}, HTTPStatus.NOT_FOUND)
                    return
                job = self.server.store.cancel(m.group(1))
                self.send_json(self.server.store.summary(job))
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except BadRequest as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ---------------------------------------------------------------------------
# page (all user-provided text is rendered with textContent, never as markup)
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BrowLab · 눈썹 디자인 연습 시트</title>
<style>
:root{--bg:#f6f4f1;--card:#fff;--ink:#222;--muted:#6b6560;--line:#e2ddd7;--accent:#b5543a;--warn:#a13d2d}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",system-ui,sans-serif;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.5}
header{display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
header h1{font-size:18px;margin:0}
header small{color:var(--muted);font-weight:normal}
header .sp{flex:1}
main{max-width:1100px;margin:0 auto;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tabs button{border:1px solid var(--line);background:#fff;padding:8px 14px;border-radius:999px;cursor:pointer;font-size:14px}
.tabs button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
[data-panel]{display:none}[data-panel].on{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 14px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:3px}
input[type=text],input[type=number],input[type=password],select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff}
textarea{min-height:64px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.chk{display:flex;gap:6px;align-items:center;font-size:14px;color:var(--ink);margin:0}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chips label{display:flex;gap:5px;align-items:center;border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:13px;color:var(--ink);cursor:pointer;margin:0}
.chips label.on{background:#f3e7e2;border-color:var(--accent)}
button.primary{background:var(--accent);color:#fff;border:0;border-radius:10px;padding:10px 18px;font-size:15px;cursor:pointer}
button.primary:disabled{opacity:.5}
button.ghost{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 14px;cursor:pointer}
.msg{color:var(--warn);font-size:14px;min-height:20px;margin-top:8px;white-space:pre-wrap}
.hint{color:var(--muted);font-size:13px}
.job{display:flex;gap:10px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;cursor:pointer;background:#fff}
.job:hover,.job.sel{border-color:var(--accent)}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;background:#eee;white-space:nowrap}
.badge.running{background:#fff1c9}.badge.done{background:#d9f0e6}.badge.failed{background:#f8d6d0}.badge.queued{background:#e8e8e8}.badge.cancelled{background:#e8e8e8}
.job .t{flex:1;min-width:0}.job .t b{display:block;font-size:14px}.job .t span{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
pre.log{background:#1f1d1b;color:#e8e2da;padding:12px;border-radius:10px;font-size:12px;overflow:auto;max-height:280px;white-space:pre-wrap}
.thumbs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.thumbs a{display:block;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff;text-decoration:none;color:var(--ink)}
.thumbs img{width:100%;display:block;aspect-ratio:3/4;object-fit:contain;background:#faf8f5}
.thumbs span{display:block;font-size:11px;padding:4px 6px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.files a{display:inline-block;margin:4px 8px 4px 0;font-size:14px}
.kv{font-size:13px;color:var(--muted)}
.split{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:900px){.split{grid-template-columns:340px 1fr}}
h2{font-size:16px;margin:0 0 10px}
h3{font-size:14px;margin:14px 0 6px;color:var(--muted)}
.note{background:#fbf3ea;border:1px solid #f0dcc8;border-radius:10px;padding:10px 12px;font-size:13px;margin-bottom:10px}
</style>
</head>
<body>
<header>
  <h1>BrowLab <small>눈썹 디자인 연습 시트 · A4 1:1</small></h1>
  <span class="sp"></span>
  <span id="who" class="hint"></span>
  <button id="logoutBtn" class="ghost" hidden>로그아웃</button>
</header>
<main>
  <section id="login" class="card" hidden>
    <h2>비밀번호</h2>
    <div class="row">
      <input id="pw" type="password" placeholder="비밀번호" style="max-width:260px" autocomplete="current-password">
      <button id="loginBtn" class="primary">들어가기</button>
    </div>
    <div id="loginMsg" class="msg"></div>
  </section>

  <div id="app" hidden>
    <nav class="tabs">
      <button data-tab="generate" class="on">얼굴 생성</button>
      <button data-tab="restyle">내 사진 눈썹</button>
      <button data-tab="sheet">사진 시트 · 보정</button>
      <button data-tab="jobs">작업 목록 <span id="jobCount" class="badge"></span></button>
    </nav>

    <section data-panel="generate" class="on card">
      <h2>눈썹이 부족한 연습용 얼굴 만들기</h2>
      <p class="hint">조건을 고르면 흰 배경 정면 얼굴을 만들어 A4 실물 크기(동공 간 거리 기준) 시트 PNG·PDF 로 저장합니다. 비우면 무작위.</p>
      <form id="fGen">
        <div class="grid">
          <div><label>인원 수</label><input name="count" type="number" min="1" max="8" value="1"></div>
          <div><label>나이대</label><select name="age" data-src="ages"></select></div>
          <div><label>정확한 나이 (선택, 15~79)</label><input name="age_exact" type="number" min="15" max="79" placeholder="비우면 나이대 안에서 무작위"></div>
          <div><label>성별</label><select name="gender" data-src="genders"></select></div>
          <div><label>얼굴형</label><select name="face_shape" data-src="face_shapes"></select></div>
          <div><label>눈썹 상태</label><select name="brow_condition" data-src="brow_conditions"></select></div>
          <div><label>외모</label><select name="ethnicity" data-src="ethnicities"></select></div>
          <div><label>시드 (같은 조합 재현)</label><input name="seed" type="number" placeholder="비우면 무작위"></div>
          <div><label>시트 구성</label><select name="layout" data-src="layouts"></select></div>
          <div><label>동공 간 거리 mm (비우면 성별 평균)</label><input name="ipd_mm" type="number" step="0.5" min="50" max="75" placeholder="여 62 / 남 64"></div>
        </div>
        <div style="margin-top:10px"><label>추가 지시 (선택)</label><textarea name="notes" placeholder="예: 안경 없음, 앞머리 없음, 눈썹은 거의 없이"></textarea></div>
        <div class="row" style="margin-top:10px">
          <label class="chk"><input name="guides" type="checkbox" checked> 눈썹 황금비 가이드선</label>
        </div>
        <h3>생성 엔진</h3>
        <div class="grid">
          <div><label>백엔드</label><select name="backend" data-src="backends"></select></div>
          <div><label>품질 (API)</label><select name="quality" data-src="qualities"></select></div>
          <div><label>크기 (API, 16의 배수)</label><input name="size" type="text" value="1536x2304"></div>
        </div>
        <div id="genBackendHint" class="hint" style="margin-top:6px"></div>
        <div class="row" style="margin-top:14px"><button class="primary" type="submit">생성 시작</button><span class="hint">한 장에 1~3분. 작업 목록에서 진행을 볼 수 있습니다.</span></div>
        <div class="msg" data-msg></div>
      </form>
    </section>

    <section data-panel="restyle" class="card">
      <h2>내 사진의 눈썹만 여러 스타일로</h2>
      <div class="note">사진은 눈썹 검출 후 편집을 위해 OpenAI 로 전송됩니다. 본인 또는 동의한 사람의 사진만 올리세요. 정면·눈썹이 보이는 사진이 좋습니다.</div>
      <form id="fRestyle">
        <div class="grid">
          <div><label>얼굴 사진</label><input name="photo" type="file" accept="image/*" required></div>
          <div><label>눈썹 색</label><select name="color" data-src="brow_colors"></select></div>
          <div><label>비교 시트</label><select name="sheet" data-src="sheets"></select></div>
          <div><label>성별 (동공 간 거리 기본값)</label><select name="gender" data-src="genders"></select></div>
        </div>
        <h3>스타일 (여러 개 선택)</h3>
        <div class="chips" id="styleChips"></div>
        <div style="margin-top:10px"><label>추가 지시 (선택)</label><textarea name="notes" placeholder="예: 눈썹 앞머리는 연하게, 꼬리는 조금 길게"></textarea></div>
        <div class="row" style="margin-top:10px">
          <label class="chk"><input name="no_composite" type="checkbox"> 합성 없이 편집 결과 그대로 (얼굴이 바뀌면 끄기)</label>
        </div>
        <h3>생성 엔진</h3>
        <div class="grid">
          <div><label>백엔드</label><select name="backend" data-src="backends"></select></div>
          <div><label>품질 (API)</label><select name="quality" data-src="qualities"></select></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="primary" type="submit">눈썹 바꾸기 시작</button></div>
        <div class="msg" data-msg></div>
      </form>
    </section>

    <section data-panel="sheet">
      <div class="card">
        <h2>가지고 있는 얼굴 사진을 A4 1:1 시트로</h2>
        <form id="fSheet">
          <div class="grid">
            <div><label>얼굴 이미지</label><input name="photo" type="file" accept="image/*" required></div>
            <div><label>시트 구성</label><select name="layout" data-src="layouts"></select></div>
            <div><label>성별 (동공 간 거리 기본값)</label><select name="gender" data-src="genders"></select></div>
            <div><label>얼굴형 (추천 문구, 선택)</label><select name="face_shape" data-src="face_shapes"></select></div>
            <div><label>동공 간 거리 mm (선택)</label><input name="ipd_mm" type="number" step="0.5" min="50" max="75"></div>
            <div><label>동공 픽셀 좌표 x1,y1,x2,y2 (검출 실패 시)</label><input name="pupils" type="text" placeholder="비우면 자동 검출"></div>
          </div>
          <div class="row" style="margin-top:10px"><label class="chk"><input name="guides" type="checkbox" checked> 가이드선</label></div>
          <div class="row" style="margin-top:14px"><button class="primary" type="submit">시트 만들기</button></div>
          <div class="msg" data-msg></div>
        </form>
      </div>
      <div class="card">
        <h2>프린터 배율 확인 시트</h2>
        <p class="hint">100 mm 자·20 mm 정사각형·평균 동공 간격이 인쇄된 시트입니다. 프린터에서 "실제 크기(100%)" 로 인쇄한 뒤 자로 재어 배율을 확인하세요.</p>
        <form id="fCal"><button class="primary" type="submit">보정 시트 만들기</button><div class="msg" data-msg></div></form>
      </div>
    </section>

    <section data-panel="jobs">
      <div class="split">
        <div class="card" style="margin:0">
          <h2>작업 목록</h2>
          <div id="jobList"></div>
        </div>
        <div class="card" id="jobDetail" style="margin:0"><p class="hint">왼쪽에서 작업을 고르세요.</p></div>
      </div>
    </section>
  </div>
</main>
<script>
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
let presets=null, jobs=[], current=null, timer=null;
const el=(tag, cls, text)=>{ const e=document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e; };
async function api(path, opts={}){
  const r = await fetch(path, Object.assign({credentials:'same-origin'}, opts));
  let data={}; try{ data = await r.json(); }catch(e){}
  if(r.status===401){ showLogin(); throw new Error(data.error||'로그인이 필요합니다'); }
  if(!r.ok) throw new Error(data.error||('HTTP '+r.status));
  return data;
}
const post=(path, body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
function showLogin(){ $('#login').hidden=false; $('#app').hidden=true; $('#logoutBtn').hidden=true; $('#pw').focus(); }
async function showApp(){
  $('#login').hidden=true; $('#app').hidden=false; $('#logoutBtn').hidden=false;
  presets = await api('api/presets');
  fillSelects(); await refreshJobs(); startPolling();
}
function fillSelects(){
  $$('select[data-src]').forEach(sel=>{
    const src=sel.dataset.src; let items=presets[src];
    if(src==='qualities') items=items.map(k=>({key:k,ko:k}));
    if(src==='backends') items=items.map(b=>({key:b.key,ko:b.ko+(b.available?'':' — 사용 불가')}));
    sel.replaceChildren();
    const sheetShape = sel.name==='face_shape' && sel.form && sel.form.id==='fSheet';
    if(sheetShape) sel.appendChild(new Option('없음',''));
    items.forEach(it=>{ if(sheetShape && it.key==='random') return; sel.appendChild(new Option(it.ko,it.key)); });
    if(src==='backends') sel.value=presets.default_backend;
    if(src==='qualities') sel.value='high';
  });
  const chips=$('#styleChips'); chips.replaceChildren();
  const def=['korean_natural','straight','soft_arch','feathered'];
  presets.brow_styles.forEach(s=>{
    const l=el('label'); const c=document.createElement('input'); c.type='checkbox'; c.value=s.key; c.checked=def.includes(s.key);
    l.className=c.checked?'on':''; c.onchange=()=>l.className=c.checked?'on':'';
    l.appendChild(c); l.appendChild(document.createTextNode(s.ko)); chips.appendChild(l);
  });
  const hint=()=>{ const k=$('#fGen [name=backend]').value; $('#genBackendHint').textContent = k==='codex'?'Codex: ChatGPT 구독 한도를 씁니다. 크기·품질은 프롬프트로만 요청됩니다.': k==='api'?'API: 서버에 OPENAI_API_KEY 가 있어야 하며 장당 과금됩니다. 크기·품질을 정확히 지정할 수 있습니다.':'manual: 프롬프트 파일만 저장합니다. 이미지를 직접 만들어 "사진 시트" 탭에서 시트로 만드세요.'; };
  $('#fGen [name=backend]').onchange=hint; hint();
}
function formData(form){
  const o={}; new FormData(form).forEach((v,k)=>{ if(!(v instanceof File)) o[k]=v; });
  form.querySelectorAll('input[type=checkbox]').forEach(c=>{ if(c.name) o[c.name]=c.checked; });
  return o;
}
function readFile(file){ return new Promise((res,rej)=>{ if(!file||!file.size) return rej(new Error('사진을 고르세요')); if(file.size>25*1024*1024) return rej(new Error('사진은 25 MB 이하')); const r=new FileReader(); r.onload=()=>res(r.result.split(',')[1]); r.onerror=()=>rej(new Error('파일을 읽지 못했습니다')); r.readAsDataURL(file); }); }
async function submit(form, kind, extra){
  const msg=form.querySelector('[data-msg]'); msg.textContent=''; const btn=form.querySelector('button[type=submit]'); btn.disabled=true;
  try{
    const body=Object.assign({kind}, formData(form), extra||{});
    const f=form.querySelector('input[type=file]');
    if(f){ body.photo=await readFile(f.files[0]); body.photo_name=f.files[0].name; }
    const job=await post('api/jobs', body);
    await refreshJobs(); openJob(job.id); switchTab('jobs');
  }catch(e){ msg.textContent=e.message; }
  finally{ btn.disabled=false; }
}
$('#fGen').onsubmit=e=>{ e.preventDefault(); const d=formData(e.target); const extra={}; if(d.age_exact) extra.age=d.age_exact; submit(e.target,'generate',extra); };
$('#fRestyle').onsubmit=e=>{ e.preventDefault(); const styles=$$('#styleChips input:checked').map(c=>c.value); if(!styles.length){ e.target.querySelector('[data-msg]').textContent='스타일을 하나 이상 고르세요'; return; } submit(e.target,'restyle',{styles}); };
$('#fSheet').onsubmit=e=>{ e.preventDefault(); submit(e.target,'sheet'); };
$('#fCal').onsubmit=e=>{ e.preventDefault(); submit(e.target,'calibrate'); };
$('#loginBtn').onclick=async()=>{ $('#loginMsg').textContent=''; try{ await post('api/login',{password:$('#pw').value}); $('#pw').value=''; await showApp(); }catch(e){ $('#loginMsg').textContent=e.message; } };
$('#pw').onkeydown=e=>{ if(e.key==='Enter') $('#loginBtn').click(); };
$('#logoutBtn').onclick=async()=>{ try{ await post('api/logout'); }catch(e){} stopPolling(); showLogin(); };
$$('.tabs button').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
function switchTab(name){ $$('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===name)); $$('[data-panel]').forEach(p=>p.classList.toggle('on',p.dataset.panel===name)); }
const STATUS_KO={queued:'대기',running:'진행 중',done:'완료',failed:'실패',cancelled:'취소'};
function describe(j){ const p=j.params||{}; const parts=[]; if(j.kind==='generate'){ parts.push(`${p.count||1}명`, p.age, p.gender, p.face_shape, p.brow_condition, p.backend); } else if(j.kind==='restyle'){ parts.push(p.photo, (p.styles||[]).length+'개 스타일', p.color, p.backend); } else if(j.kind==='sheet'){ parts.push(p.photo, p.layout); } else parts.push('A4 보정'); return parts.filter(Boolean).join(' · '); }
async function refreshJobs(){
  const d=await api('api/jobs'); jobs=d.jobs; const list=$('#jobList'); list.replaceChildren();
  const active=jobs.filter(j=>j.status==='running'||j.status==='queued').length; $('#jobCount').textContent=active?active+' 진행':'';
  if(!jobs.length){ list.appendChild(el('p','hint','아직 작업이 없습니다.')); return; }
  jobs.forEach(j=>{ const row=el('div','job'+(j.id===current?' sel':'')); row.onclick=()=>openJob(j.id);
    const t=el('div','t'); t.appendChild(el('b','',j.kind_ko)); t.appendChild(el('span','',j.created.replace('T',' ')+' · '+describe(j)));
    row.appendChild(t); row.appendChild(el('span','badge '+j.status, STATUS_KO[j.status]||j.status)); list.appendChild(row); });
}
async function openJob(id){ current=id; $$('.job').forEach(r=>r.classList.remove('sel')); await renderJob(); }
function fileLinks(parent, title, files, icon){ if(!files.length) return; parent.appendChild(el('h3','',title)); const d=el('div','files'); files.forEach(f=>{ const a=el('a','',icon+f.name); a.href=f.url; a.target='_blank'; d.appendChild(a); }); parent.appendChild(d); }
async function renderJob(){
  if(!current) return; let j; try{ j=await api('api/jobs/'+current); }catch(e){ $('#jobDetail').replaceChildren(el('p','msg',e.message)); return; }
  const box=$('#jobDetail'); box.replaceChildren();
  box.appendChild(el('h2','',j.kind_ko+' · '+(STATUS_KO[j.status]||j.status)));
  box.appendChild(el('div','kv',j.created.replace('T',' ')+'  ·  '+describe(j)+(j.error?('  ·  '+j.error):'')));
  if(j.status==='running'||j.status==='queued'){ const c=el('button','ghost','취소'); c.style.marginTop='8px'; c.onclick=async()=>{ await post('api/jobs/'+j.id+'/cancel'); renderJob(); refreshJobs(); }; box.appendChild(c); }
  const base=f=>f.name.split('/').pop();
  const imgs=j.files.filter(f=>f.kind==='image' && !/^(mask|mask_api|mask_guide)\.png$/.test(base(f)));
  fileLinks(box, '인쇄용 PDF (A4, 배율 100% 로 인쇄)', j.files.filter(f=>f.kind==='pdf'), '📄 ');
  if(imgs.length){ box.appendChild(el('h3','','이미지 (누르면 원본)')); const g=el('div','thumbs'); imgs.forEach(f=>{ const a=el('a'); a.href=f.url; a.target='_blank'; const im=document.createElement('img'); im.loading='lazy'; im.src=f.url+'?w=480'; im.alt=f.name; a.appendChild(im); a.appendChild(el('span','',f.name)); g.appendChild(a); }); box.appendChild(g); }
  fileLinks(box, '기타 파일', j.files.filter(f=>f.kind==='other'), '');
  box.appendChild(el('h3','','로그')); const pre=el('pre','log', j.log||'(아직 없음)'); box.appendChild(pre); pre.scrollTop=pre.scrollHeight;
}
function startPolling(){ stopPolling(); timer=setInterval(async()=>{ try{ const wasActive=jobs.some(j=>j.status==='running'||j.status==='queued'); if(wasActive) await refreshJobs(); const cur=jobs.find(j=>j.id===current); if(cur && (wasActive || cur.status==='running' || cur.status==='queued')) await renderJob(); }catch(e){} }, 4000); }
function stopPolling(){ if(timer) clearInterval(timer); timer=null; }
(async()=>{ try{ const me=await api('api/me'); $('#who').textContent='v'+me.version; if(me.authed) await showApp(); else showLogin(); }catch(e){ showLogin(); } })();
</script>
</body>
</html>
"""


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
    p.add_argument("--default-backend", choices=BACKENDS, default="codex")
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
