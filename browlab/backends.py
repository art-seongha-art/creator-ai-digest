"""Image generation backends.

* ``codex``  - drives the Codex CLI (``codex exec`` + the built-in ``$imagegen``
               tool). Works with a ChatGPT login; no API key needed. This is the
               default because it is what the user already has.
* ``api``    - calls the OpenAI Images API directly through the ``openai`` SDK.
               Needs ``OPENAI_API_KEY``. Supports exact sizes and alpha masks.
* ``manual`` - writes the prompt next to the target path so the image can be
               made by hand (ChatGPT, Codex app, any generator) and dropped in.
* ``auto``   - (default) Codex first; if that fails, the Images API walking down
               gpt-image-2.5 -> 2 -> 1.5 -> 1 -> 1-mini. See ``FallbackBackend``.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FIXED_SIZES = {"1024x1024", "1536x1024", "1024x1536"}


class GenerationError(RuntimeError):
    pass


@dataclass
class GenResult:
    path: Path
    backend: str
    prompt: str
    model: Optional[str] = None
    log: str = ""
    pending: bool = False
    usage: Optional[Dict[str, int]] = None   # token usage reported by the Images API
    cost_usd: Optional[float] = None         # estimate from usage x official per-token rates
    fallback: List[str] = field(default_factory=list)  # attempts that failed before this result (auto backend)


# Official per-1M-token rates in USD (developers.openai.com/api/docs/pricing, read 2026-09-12).
# gpt-image-1.5 / gpt-image-2 / gpt-image-2.5-* share the "default" row.
IMAGE_RATES: Dict[str, Dict[str, float]] = {
    "gpt-image-1": {"text_in": 5.0, "image_in": 10.0, "image_out": 40.0},
    # gpt-image-1-mini: calibrated 2026-09-12 against the dashboard (10 requests, 11,212 tokens -> $0.48)
    "gpt-image-1-mini": {"text_in": 2.0, "image_in": 2.5, "image_out": 8.0},
    "default": {"text_in": 5.0, "image_in": 8.0, "image_out": 30.0},
}


def usage_dict(resp: Any) -> Optional[Dict[str, int]]:
    """Flatten an Images API response's ``usage`` object (None when the response has none)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    det = getattr(u, "input_tokens_details", None)
    out = {
        "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
        "text_tokens": int(getattr(det, "text_tokens", 0) or 0) if det is not None else 0,
        "image_tokens": int(getattr(det, "image_tokens", 0) or 0) if det is not None else 0,
    }
    if det is None:  # no breakdown: treat all input as text (the cheaper rate is not assumed)
        out["text_tokens"] = out["input_tokens"]
    return out


def estimate_cost_usd(usage: Optional[Dict[str, int]], model: Optional[str]) -> Optional[float]:
    if not usage:
        return None
    m = model or ""
    key = "gpt-image-1-mini" if m.startswith("gpt-image-1-mini") else "gpt-image-1" if (m.startswith("gpt-image-1") and not m.startswith("gpt-image-1.5")) else "default"
    r = IMAGE_RATES[key]
    cost = (usage.get("text_tokens", 0) * r["text_in"] + usage.get("image_tokens", 0) * r["image_in"]
            + usage.get("output_tokens", 0) * r["image_out"]) / 1_000_000
    return round(cost, 6)


class BaseBackend:
    name = "base"

    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        raise NotImplementedError

    def edit(
        self,
        prompt: str,
        images: Sequence[Path],
        out_path: Path,
        *,
        mask: Optional[Path] = None,
        size: str = "auto",
        quality: str = "high",
    ) -> GenResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# manual
# ---------------------------------------------------------------------------
class ManualBackend(BaseBackend):
    name = "manual"

    def _write(self, prompt: str, out_path: Path, extra: str = "") -> GenResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_file = out_path.with_suffix(".prompt.txt")
        text = (
            f"# BrowLab manual mode\n# Generate this image with ChatGPT / Codex / any tool and save it as:\n#   {out_path}\n"
            f"{extra}\n{prompt}\n"
        )
        prompt_file.write_text(text, encoding="utf-8")
        return GenResult(path=out_path, backend=self.name, prompt=prompt, pending=True, log=f"prompt written to {prompt_file}")

    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        return self._write(prompt, out_path, extra=f"# Suggested size: {size}, quality: {quality}\n")

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high") -> GenResult:
        lines = "\n".join(f"#   Image {i}: {p}" for i, p in enumerate(images, 1))
        extra = f"# Input images:\n{lines}\n" + (f"# Mask (alpha 0 = editable): {mask}\n" if mask else "")
        return self._write(prompt, out_path, extra=extra)


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------
GENERATE_INSTRUCTION = """$imagegen
Task: generate exactly ONE image with the built-in image_gen tool, following the specification below exactly.
Rules: use the built-in tool (default mode, no CLI fallback); do not ask questions; generate only one image; do not generate variants.
Preferred output: vertical portrait orientation (2:3 aspect ratio), the highest resolution the tool offers, PNG.

When the image has been generated, copy the generated file (the newest file under $CODEX_HOME/generated_images, usually ~/.codex/generated_images) into the current working directory using exactly this file name, overwriting if it already exists:
{filename}
Then reply with a single line: SAVED: {filename}

=== IMAGE SPECIFICATION ===
{prompt}
"""

EDIT_INSTRUCTION = """$imagegen
Task: edit the attached Image 1 with the built-in image_gen tool, following the specification below exactly.
Rules: use the built-in tool (default mode, no CLI fallback); do not ask questions; produce exactly one edited image; keep the same framing and image size as Image 1.

When the edited image has been generated, copy the generated file (the newest file under $CODEX_HOME/generated_images, usually ~/.codex/generated_images) into the current working directory using exactly this file name, overwriting if it already exists:
{filename}
Then reply with a single line: SAVED: {filename}

=== EDIT SPECIFICATION ===
{prompt}
"""


class CodexBackend(BaseBackend):
    name = "codex"

    def __init__(
        self,
        codex_bin: str = "codex",
        model: Optional[str] = None,
        timeout: int = 900,
        codex_home: Optional[Path] = None,
        sandbox: str = "workspace-write",
        extra_args: Sequence[str] = (),
    ) -> None:
        self.codex_bin = codex_bin
        self.model = model
        self.timeout = timeout
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
        self.sandbox = sandbox
        self.extra_args = list(extra_args)

    # -- helpers ------------------------------------------------------------
    @property
    def generated_dir(self) -> Path:
        return self.codex_home / "generated_images"

    def _snapshot(self) -> Set[Path]:
        if not self.generated_dir.exists():
            return set()
        return {p for p in self.generated_dir.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS}

    def _new_images(self, before: Set[Path], started: float) -> List[Path]:
        found = [p for p in self._snapshot() if p not in before or p.stat().st_mtime >= started - 1]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def _run(self, instruction: str, work_dir: Path, images: Sequence[Path] = ()) -> Tuple[int, str, str, str]:
        if shutil.which(self.codex_bin) is None:
            raise GenerationError(
                f"Codex CLI not found ({self.codex_bin}). Install with `npm install -g @openai/codex` and run `codex login`."
            )
        # codex resolves -C relative to its own cwd (which we also set to work_dir),
        # so a relative work_dir would be looked up twice -> "No such file or directory".
        work_dir = Path(work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        last_msg = work_dir / ".browlab_last_message.txt"
        cmd = [
            self.codex_bin, "exec",
            "--skip-git-repo-check",
            "-s", self.sandbox,
            "-C", str(work_dir),
            "-o", str(last_msg),
            "--color", "never",
        ]
        for img in images:
            cmd += ["-i", str(Path(img).resolve())]
        if self.model:
            cmd += ["-m", self.model]
        cmd += self.extra_args
        cmd.append("-")
        env = dict(os.environ)
        env.setdefault("CODEX_HOME", str(self.codex_home))
        try:
            proc = subprocess.run(
                cmd, input=instruction, text=True, capture_output=True, timeout=self.timeout, cwd=str(work_dir), env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenerationError(f"codex exec timed out after {self.timeout}s") from exc
        last = last_msg.read_text(encoding="utf-8", errors="replace") if last_msg.exists() else ""
        try:
            last_msg.unlink()
        except OSError:
            pass
        return proc.returncode, proc.stdout, proc.stderr, last

    def _collect(self, out_path: Path, before: Set[Path], started: float, rc: int, stdout: str, stderr: str, last: str) -> Path:
        out_path = Path(out_path)
        if out_path.exists() and out_path.stat().st_size > 0 and out_path.stat().st_mtime >= started - 1:
            return out_path
        candidates = self._new_images(before, started)
        if candidates:
            src = candidates[0]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() == out_path.suffix.lower():
                shutil.copyfile(src, out_path)
            else:
                from PIL import Image

                with Image.open(src) as im:
                    im.convert("RGB").save(out_path)
            return out_path
        raise GenerationError(
            "codex exec finished but no image was produced.\n"
            f"rc={rc}\nlast message: {last[-800:]}\nstderr: {stderr[-1500:]}\nstdout: {stdout[-800:]}"
        )

    # -- API ------------------------------------------------------------------
    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        out_path = Path(out_path)
        work_dir = out_path.parent
        before = self._snapshot()
        started = time.time()
        instruction = GENERATE_INSTRUCTION.format(filename=out_path.name, prompt=prompt)
        rc, stdout, stderr, last = self._run(instruction, work_dir)
        path = self._collect(out_path, before, started, rc, stdout, stderr, last)
        return GenResult(path=path, backend=self.name, prompt=prompt, model=self.model, log=last.strip()[-2000:])

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high") -> GenResult:
        out_path = Path(out_path)
        work_dir = out_path.parent
        before = self._snapshot()
        started = time.time()
        instruction = EDIT_INSTRUCTION.format(filename=out_path.name, prompt=prompt)
        rc, stdout, stderr, last = self._run(instruction, work_dir, images=list(images))
        path = self._collect(out_path, before, started, rc, stdout, stderr, last)
        return GenResult(path=path, backend=self.name, prompt=prompt, model=self.model, log=last.strip()[-2000:])


# ---------------------------------------------------------------------------
# OpenAI Images API
# ---------------------------------------------------------------------------
class OpenAIBackend(BaseBackend):
    """Direct Images API access (gpt-image-2 / gpt-image-2.5 family)."""

    name = "api"
    DEFAULT_GENERATE_MODEL = "gpt-image-2.5-flare"
    DEFAULT_EDIT_MODEL = "gpt-image-2.5-sunburst"

    def __init__(
        self,
        model: Optional[str] = None,
        edit_model: Optional[str] = None,
        api_key: Optional[str] = None,
        client: Any = None,
        timeout: int = 600,
    ) -> None:
        self.model = model or self.DEFAULT_GENERATE_MODEL
        self.edit_model = edit_model or model or self.DEFAULT_EDIT_MODEL
        self.api_key = api_key
        self._client = client
        self.timeout = timeout

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise GenerationError("OPENAI_API_KEY is not set (required for --backend api).")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise GenerationError("the `openai` package is missing: pip install openai") from exc
        self._client = OpenAI(api_key=key, timeout=self.timeout)
        return self._client

    @staticmethod
    def _save_first(resp: Any, out_path: Path) -> Path:
        data = getattr(resp, "data", None) or []
        if not data:
            raise GenerationError("Images API returned no data")
        item = data[0]
        b64 = getattr(item, "b64_json", None)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if b64:
            out_path.write_bytes(base64.b64decode(b64))
            return out_path
        url = getattr(item, "url", None)
        if url:
            import urllib.request

            with urllib.request.urlopen(url, timeout=120) as r:
                out_path.write_bytes(r.read())
            return out_path
        raise GenerationError("Images API item had neither b64_json nor url")

    @staticmethod
    def _fixed_size_model(model: Optional[str]) -> bool:
        """gpt-image-1 / 1-mini / 1.5 only accept 1024x1024, 1536x1024, 1024x1536 or auto."""
        return (model or "").startswith("gpt-image-1")

    @classmethod
    def _snap_size(cls, model: Optional[str], size: str) -> str:
        if not cls._fixed_size_model(model) or size == "auto" or size in FIXED_SIZES:
            return size
        try:
            w, h = (int(v) for v in size.lower().split("x"))
        except ValueError:
            return size
        return "1024x1536" if h > w else "1536x1024" if w > h else "1024x1024"

    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        size = self._snap_size(self.model, size)
        quality = coerce_quality(self.model, quality)
        kwargs: Dict[str, Any] = dict(model=self.model, prompt=prompt, n=1, size=size, quality=quality, output_format="png")
        resp = self.client().images.generate(**kwargs)
        path = self._save_first(resp, Path(out_path))
        usage = usage_dict(resp)
        return GenResult(path=path, backend=self.name, prompt=prompt, model=self.model, usage=usage,
                         cost_usd=estimate_cost_usd(usage, self.model))

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high") -> GenResult:
        model = self.edit_model
        quality = coerce_quality(model, quality)
        handles = [open(Path(p), "rb") for p in images]
        mask_handle = open(Path(mask), "rb") if mask else None
        try:
            kwargs: Dict[str, Any] = dict(
                model=model,
                prompt=prompt,
                image=handles if len(handles) > 1 else handles[0],
                n=1,
                size="auto" if self._fixed_size_model(model) else size,  # gpt-image-1 family: no custom sizes
                quality=quality,
                output_format="png",
            )
            if mask_handle is not None:
                kwargs["mask"] = mask_handle
            if model == "gpt-image-1":  # only the original model takes input_fidelity
                kwargs["input_fidelity"] = "high"
            resp = self.client().images.edit(**kwargs)
        finally:
            for h in handles:
                h.close()
            if mask_handle is not None:
                mask_handle.close()
        path = self._save_first(resp, Path(out_path))
        usage = usage_dict(resp)
        return GenResult(path=path, backend=self.name, prompt=prompt, model=model, usage=usage,
                         cost_usd=estimate_cost_usd(usage, model))


# ---------------------------------------------------------------------------
# Automatic fallback: Codex first, then the Images API down the model ladder
# ---------------------------------------------------------------------------
GENERATE_MODEL_CHAIN: List[str] = ["gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"]
EDIT_MODEL_CHAIN: List[str] = ["gpt-image-2.5-sunburst", "gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"]

# quality values only the 2.5 family accepts; older models get the nearest supported value
_EXTENDED_QUALITY = {"xhigh", "max"}


def coerce_quality(model: Optional[str], quality: str) -> str:
    if quality in _EXTENDED_QUALITY and not (model or "").startswith("gpt-image-2.5"):
        return "high"
    return quality


# (reason, patterns): an error containing one of the patterns means the backend/model is
# unavailable for the rest of the run (not just for this one image), so it is skipped afterwards.
UNAVAILABLE_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("Codex 사용 한도 소진", ("usage limit", "usage_limit")),
    ("Codex 없음", ("Codex CLI not found",)),
    ("Codex 로그인 필요", ("Not logged in", "not logged in", "codex login", "No Codex credentials", "no Codex credentials")),
    ("API 키 없음", ("OPENAI_API_KEY is not set", "package is missing")),
    ("API 키 오류", ("invalid_api_key", "Incorrect API key", "Error code: 401")),
    ("모델 미개방(Limit 0)", ("Limit 0",)),
    ("조직 인증 필요", ("must be verified",)),
    ("모델 없음/권한 없음", ("model_not_found", "does not exist", "Error code: 403", "Error code: 404")),
    ("API 결제/쿼터", ("insufficient_quota", "exceeded your current quota", "billing_hard_limit")),
]


def unavailable_reason(exc: BaseException) -> Optional[str]:
    text = str(exc)
    for reason, patterns in UNAVAILABLE_PATTERNS:
        if any(p in text for p in patterns):
            return reason
    return None


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return (text.splitlines()[0][:200] if text else exc.__class__.__name__)


class FallbackBackend(BaseBackend):
    """Try several backends in order and remember which ones are unavailable.

    ``attempts`` is an ordered list of ``(label, backend)``. A failure whose message
    matches ``UNAVAILABLE_PATTERNS`` (usage limit, missing key, model not opened for
    the organisation, ...) disables that attempt for the rest of the run; any other
    failure (timeout, moderation, 5xx) only moves on to the next attempt for this
    one image and is retried on the next one.
    """

    name = "auto"

    def __init__(self, attempts: Sequence[Tuple[str, BaseBackend]], notes: Sequence[str] = ()) -> None:
        self.attempts: List[Tuple[str, BaseBackend]] = list(attempts)
        self.skipped: Dict[str, str] = {}
        self.notes: List[str] = list(notes)
        self.model: Optional[str] = None       # model of the last successful attempt
        self.last_backend: Optional[str] = None
        self.log = lambda msg: print(f"[browlab] {msg}", flush=True)

    def _try_all(self, op: str, call: Any) -> GenResult:
        if not self.attempts:
            raise GenerationError("사용할 수 있는 백엔드가 없습니다 (codex 실행 파일도 OPENAI_API_KEY 도 없음).")
        failed: List[str] = []
        tried: List[str] = []
        for label, backend in self.attempts:
            if label in self.skipped:
                continue
            tried.append(label)
            try:
                result = call(backend)
            except Exception as exc:  # GenerationError or an SDK error
                reason = unavailable_reason(exc)
                short = _first_line(exc)
                if reason:
                    self.skipped[label] = reason
                    self.log(f"{label} 사용 불가({reason}) → 다음 백엔드로. {short}")
                else:
                    self.log(f"{label} 실패 → 다음 백엔드로. {short}")
                failed.append(f"{label}: {reason or short}")
                continue
            result.fallback = failed
            self.model = result.model or label
            self.last_backend = result.backend
            if failed:
                self.log(f"{label} 성공 (앞선 시도 실패: {' / '.join(failed)})")
            return result
        earlier = [f"{k}: {v} (앞서 사용 불가 판정)" for k, v in self.skipped.items() if k not in tried]
        raise GenerationError(f"모든 백엔드가 실패했습니다 ({op}).\n" + "\n".join(f"- {f}" for f in failed + earlier))

    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        return self._try_all("generate", lambda b: b.generate(prompt, out_path, size=size, quality=quality))

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high", variants=None) -> GenResult:
        """``variants`` maps a backend name (``codex``/``api``) to overrides for prompt/images/mask/size."""
        variants = variants or {}

        def call(b: BaseBackend) -> GenResult:
            v = variants.get(b.name, {})
            return b.edit(v.get("prompt", prompt), v.get("images", images), out_path,
                          mask=v.get("mask", mask), size=v.get("size", size), quality=quality)

        return self._try_all("edit", call)


def make_auto_backend(
    *,
    codex_bin: str = "codex",
    timeout: int = 900,
    extra_args: Sequence[str] = (),
    model: Optional[str] = None,
    edit_model: Optional[str] = None,
    model_chain: Optional[Sequence[str]] = None,
    edit_model_chain: Optional[Sequence[str]] = None,
    api_key: Optional[str] = None,
) -> FallbackBackend:
    """Codex first, then the Images API with 2.5 -> 2 -> 1.5 -> 1 -> 1-mini (or a pinned model)."""
    notes: List[str] = []
    attempts: List[Tuple[str, BaseBackend]] = []
    if shutil.which(codex_bin) is not None:
        attempts.append(("codex", CodexBackend(codex_bin=codex_bin, timeout=timeout, extra_args=extra_args)))
    else:
        notes.append(f"codex 실행 파일({codex_bin})이 없어 Codex 단계는 건너뜁니다.")
    gen_chain = [m for m in (model_chain or ([model] if model else GENERATE_MODEL_CHAIN)) if m]
    # a custom generate chain also drives edits unless an edit chain / edit model is given explicitly
    edit_default = list(model_chain) if model_chain else ([model] if model else EDIT_MODEL_CHAIN)
    edit_chain = [m for m in (edit_model_chain or ([edit_model] if edit_model else edit_default)) if m]
    if api_key or os.environ.get("OPENAI_API_KEY"):
        for i in range(max(len(gen_chain), len(edit_chain))):
            g = gen_chain[min(i, len(gen_chain) - 1)]
            e = edit_chain[min(i, len(edit_chain) - 1)]
            label = f"api:{g}" if g == e else f"api:{g}|{e}"
            attempts.append((label, OpenAIBackend(model=g, edit_model=e, api_key=api_key, timeout=timeout)))
    else:
        notes.append("OPENAI_API_KEY 가 없어 API 단계는 건너뜁니다 (Codex만 시도).")
    return FallbackBackend(attempts, notes)


# ---------------------------------------------------------------------------
def make_backend(name: str, **kwargs: Any) -> BaseBackend:
    name = (name or "auto").lower()
    if name == "auto":
        return make_auto_backend(
            codex_bin=kwargs.get("codex_bin", "codex"),
            timeout=kwargs.get("timeout", 900),
            extra_args=kwargs.get("extra_args", ()),
            model=kwargs.get("model"),
            edit_model=kwargs.get("edit_model"),
            model_chain=kwargs.get("model_chain"),
            edit_model_chain=kwargs.get("edit_model_chain"),
        )
    if name == "codex":
        return CodexBackend(
            codex_bin=kwargs.get("codex_bin", "codex"),
            model=kwargs.get("model"),
            timeout=kwargs.get("timeout", 900),
            extra_args=kwargs.get("extra_args", ()),
        )
    if name in ("api", "openai"):
        return OpenAIBackend(model=kwargs.get("model"), edit_model=kwargs.get("edit_model"), timeout=kwargs.get("timeout", 600))
    if name == "manual":
        return ManualBackend()
    raise ValueError(f"unknown backend: {name}")
