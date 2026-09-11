"""Image generation backends.

* ``codex``  - drives the Codex CLI (``codex exec`` + the built-in ``$imagegen``
               tool). Works with a ChatGPT login; no API key needed. This is the
               default because it is what the user already has.
* ``api``    - calls the OpenAI Images API directly through the ``openai`` SDK.
               Needs ``OPENAI_API_KEY``. Supports exact sizes and alpha masks.
* ``manual`` - writes the prompt next to the target path so the image can be
               made by hand (ChatGPT, Codex app, any generator) and dropped in.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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


# Official per-1M-token rates in USD (developers.openai.com/api/docs/pricing, read 2026-09-12).
# gpt-image-1.5 / gpt-image-2 / gpt-image-2.5-* share the "default" row.
IMAGE_RATES: Dict[str, Dict[str, float]] = {
    "gpt-image-1": {"text_in": 5.0, "image_in": 10.0, "image_out": 40.0},
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
    key = "gpt-image-1" if (model or "").startswith("gpt-image-1") and not (model or "").startswith("gpt-image-1.5") else "default"
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

    def generate(self, prompt: str, out_path: Path, *, size: str = "1536x2304", quality: str = "high") -> GenResult:
        kwargs: Dict[str, Any] = dict(model=self.model, prompt=prompt, n=1, size=size, quality=quality, output_format="png")
        resp = self.client().images.generate(**kwargs)
        path = self._save_first(resp, Path(out_path))
        usage = usage_dict(resp)
        return GenResult(path=path, backend=self.name, prompt=prompt, model=self.model, usage=usage,
                         cost_usd=estimate_cost_usd(usage, self.model))

    def edit(self, prompt, images, out_path, *, mask=None, size="auto", quality="high") -> GenResult:
        model = self.edit_model
        handles = [open(Path(p), "rb") for p in images]
        mask_handle = open(Path(mask), "rb") if mask else None
        try:
            kwargs: Dict[str, Any] = dict(
                model=model,
                prompt=prompt,
                image=handles if len(handles) > 1 else handles[0],
                n=1,
                size=size,
                quality=quality,
                output_format="png",
            )
            if mask_handle is not None:
                kwargs["mask"] = mask_handle
            if model.startswith("gpt-image-1"):
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
def make_backend(name: str, **kwargs: Any) -> BaseBackend:
    name = (name or "codex").lower()
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
