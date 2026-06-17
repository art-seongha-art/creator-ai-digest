from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Final

KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "LLM / 모델": ("gpt", "claude", "gemini", "llama", "mistral", "qwen", "deepseek", "model", "llm", "opus", "sonnet"),
    "에이전트 / 코딩": ("agent", "agents", "coding", "codex", "claude code", "computer use", "automation", "developer"),
    "이미지": ("image", "imagen", "firefly", "midjourney", "flux", "dall", "visual"),
    "영상": ("video", "veo", "sora", "runway", "kling", "pika", "text-to-video"),
    "음악 / 오디오": ("music", "audio", "suno", "udio", "elevenlabs", "voice", "song"),
    "3D / 공간": ("3d", "spatial", "tripo", "meshy", "gaussian", "splatting", "mesh"),
    "정책 / 저작권": ("policy", "copyright", "ai act", "regulation", "lawsuit", "safety", "governance"),
    "하드웨어 / 인프라": ("nvidia", "amd", "blackwell", "gpu", "tpu", "inference", "datacenter", "data center"),
    "논문": ("arxiv", "paper", "benchmark", "research"),
}


def clean_text(value: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_dt(value: str | int | float | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def iso_dt(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def absolute_url(base_url: str, maybe_url: str) -> str:
    return urllib.parse.urljoin(base_url, html.unescape(maybe_url.strip()))


def canonical_key(title: str) -> str:
    text = re.sub(r"[^a-z0-9가-힣 ]+", " ", title.lower())
    stop = {"the", "and", "with", "for", "from", "this", "that", "into", "to", "of", "in", "on", "are"}
    words = [word for word in text.split() if word not in stop and len(word) > 2]
    return " ".join(words[:8])


def categorize(title: str, summary: str = "", hints: list[str] | None = None) -> str:
    hinted = hints or []
    for hint in hinted:
        if hint in KEYWORDS:
            return hint
    haystack = f"{title} {summary} {' '.join(hinted)}".lower()
    best_category = "AI News"
    best_score = 0
    for category, keywords in KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword.lower() in haystack)
        if score > best_score:
            best_category = category
            best_score = score
    return best_category


def public_text(value: str) -> str:
    text = re.sub(r"\bagents?\b", "에이전트", value, flags=re.IGNORECASE)
    text = re.sub(r"\bcandidates?\b", "항목", text, flags=re.IGNORECASE)
    return text
