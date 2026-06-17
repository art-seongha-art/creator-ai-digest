from __future__ import annotations

import math
import urllib.parse
from datetime import datetime
from typing import Final

from digest_models import Candidate, Evidence, PublicItem
from digest_utils import canonical_key, clean_text

AUTHORITY: Final[dict[str, float]] = {
    "official": 13.0,
    "news": 8.0,
    "reddit": 5.0,
    "youtube": 5.0,
    "hn": 4.0,
    "arxiv": 7.0,
    "hf": 6.0,
    "creative_tool": 9.0,
}
PUBLIC_THRESHOLD: Final = 18.0
SINGLE_HN_CAP: Final = 16.0
NOISE_TERMS: Final = ("show hn:", "ask hn:", "status page", "outage", "resolved", "meme", "satire", "joke", "slop", "earnings transcript", "lawyer")


def merge_stories(candidates: list[Candidate]) -> list[Candidate]:
    grouped: dict[str, Candidate] = {}
    for candidate in candidates:
        candidate.ensure_evidence()
        key = canonical_key(candidate.title) or urllib.parse.urlsplit(candidate.url).path.strip("/")
        candidate.story_key = key
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = candidate
            continue
        existing.evidence.extend(candidate.evidence)
        if candidate.published_at and (existing.published_at is None or candidate.published_at > existing.published_at):
            existing.published_at = candidate.published_at
        if len(candidate.summary) > len(existing.summary):
            existing.summary = candidate.summary
        if AUTHORITY.get(candidate.source_group, 1) > AUTHORITY.get(existing.source_group, 1):
            existing.source_id = candidate.source_id
            existing.source_name = candidate.source_name
            existing.source_group = candidate.source_group
            existing.url = candidate.url
    return list(grouped.values())


def score_candidates(candidates: list[Candidate], now: datetime, start_dt: datetime, end_dt: datetime) -> list[Candidate]:
    scored = []
    for candidate in candidates:
        candidate.ensure_evidence()
        candidate.score_breakdown = _score_breakdown(candidate, now, start_dt, end_dt)
        cap = candidate.score_breakdown.get("single_hn_cap", 0.0)
        raw_score = sum(value for key, value in candidate.score_breakdown.items() if key != "evidence_count")
        candidate.score = min(raw_score, cap) if cap else raw_score
        _classify(candidate, start_dt, end_dt)
        scored.append(candidate)
    return sorted(scored, key=lambda item: item.score, reverse=True)


def select_public_items(candidates: list[Candidate], categories: list[str], max_total: int = 18, max_per_category: int = 3) -> tuple[list[PublicItem], list[str]]:
    counts = {category: 0 for category in categories}
    selected: list[PublicItem] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if candidate.selection_decision == "rejected":
            continue
        if candidate.category not in counts:
            candidate.category = "주요 뉴스"
            counts.setdefault("주요 뉴스", 0)
        if counts[candidate.category] >= max_per_category:
            candidate.selection_decision = "rejected"
            candidate.rejection_reason = "section_limit"
            continue
        selected.append(_public_item(candidate))
        candidate.selection_decision = "selected"
        counts[candidate.category] += 1
        if len(selected) >= max_total:
            break
    empty_sections = [category for category in categories if counts.get(category, 0) == 0]
    return selected, empty_sections


def _score_breakdown(candidate: Candidate, now: datetime, start_dt: datetime, end_dt: datetime) -> dict[str, float]:
    age_hours = (now - candidate.published_at).total_seconds() / 3600 if candidate.published_at else 9999.0
    recency = max(0.0, 18.0 - age_hours / 6.0)
    if age_hours > 72:
        recency *= 0.35
    authority = AUTHORITY.get(candidate.source_group, 4.0)
    social = _social_score(candidate)
    relevance = _relevance_score(candidate)
    content = min(5.0, len(candidate.summary.split()) / 18.0)
    novelty = _novelty_score(candidate)
    evidence_count = _evidence_count(candidate.evidence)
    evidence_bonus = min(8.0, max(0, evidence_count - 1) * 4.0)
    stale_penalty = -70.0 if not candidate.published_at or not (start_dt <= candidate.published_at <= end_dt) else 0.0
    breakdown = {
        "recency": round(recency, 3),
        "authority": authority,
        "social": round(social, 3),
        "relevance": relevance,
        "content": round(content, 3),
        "novelty": novelty,
        "evidence": evidence_bonus,
        "evidence_count": float(evidence_count),
        "stale_penalty": stale_penalty,
    }
    if evidence_count == 1 and candidate.evidence[0].source_group == "hn":
        breakdown["single_hn_cap"] = SINGLE_HN_CAP
    return breakdown


def _social_score(candidate: Candidate) -> float:
    metrics = candidate.metrics
    total = 0.0
    total += min(8.0, math.log1p(float(metrics.get("hn_points") or 0)) * 1.2 + math.log1p(float(metrics.get("hn_comments") or 0)))
    total += min(7.0, math.log1p(float(metrics.get("reddit_score") or 0)) * 1.1 + math.log1p(float(metrics.get("reddit_comments") or 0)))
    total += min(6.0, math.log1p(float(metrics.get("hf_upvotes") or 0)) * 1.5)
    return total


def _relevance_score(candidate: Candidate) -> float:
    text = f"{candidate.title} {candidate.summary}".lower()
    # Retargeted for an art/design/education department brief, not a generic AI industry digest.
    core_ai = ("ai", "model", "llm", "openai", "anthropic", "claude", "gemini", "agent")
    creative = ("image", "video", "audio", "music", "3d", "spatial", "design", "creative", "art", "artist", "media", "robot", "visual", "render", "animation", "firefly", "runway", "suno", "elevenlabs", "adobe")
    education = ("education", "classroom", "student", "teaching", "research", "paper", "copyright", "policy", "workflow", "tool", "practice", "dataset")
    infra_only = ("data center", "pension", "stake", "funding", "chip", "gpu cluster")
    score = 0.0
    score += sum(0.8 for term in core_ai if term in text)
    score += sum(2.2 for term in creative if term in text)
    score += sum(1.8 for term in education if term in text)
    score -= sum(2.0 for term in infra_only if term in text and not any(c in text for c in creative))
    return max(0.0, min(12.0, score))


def _novelty_score(candidate: Candidate) -> float:
    text = f"{candidate.title} {candidate.summary}".lower()
    terms = ("announce", "launch", "introducing", "release", "released", "unveil", "new model", "update", "feature", "workflow", "generally available", "available now")
    penalty_terms = ("review", "best", "top", "pricing", "alternatives", "valuation", "stock", "earnings", "worth", "takes equity")
    score = sum(1.3 for term in terms if term in text)
    score -= sum(1.0 for term in penalty_terms if term in text)
    return max(0.0, min(8.0, score))


def _evidence_count(evidence: list[Evidence]) -> int:
    groups = set()
    for item in evidence:
        host = urllib.parse.urlsplit(item.url).netloc.lower()
        groups.add((item.source_group, host))
    source_groups = {item.source_group for item in evidence}
    return max(len(source_groups), len(groups))


def _classify(candidate: Candidate, start_dt: datetime, end_dt: datetime) -> None:
    text = f"{candidate.title} {candidate.summary}".lower()
    if not candidate.published_at or not (start_dt <= candidate.published_at <= end_dt):
        candidate.selection_decision = "rejected"
        candidate.rejection_reason = "period_outside"
    elif any(term in text for term in NOISE_TERMS):
        candidate.selection_decision = "rejected"
        candidate.rejection_reason = "noise_filter"
    elif candidate.score_breakdown.get("single_hn_cap") and candidate.score <= SINGLE_HN_CAP:
        candidate.selection_decision = "rejected"
        candidate.rejection_reason = "single_hn_cap"
    elif candidate.score < PUBLIC_THRESHOLD:
        candidate.selection_decision = "rejected"
        candidate.rejection_reason = "below_threshold"
    else:
        candidate.selection_decision = "eligible"
        candidate.rejection_reason = ""


def _public_item(candidate: Candidate) -> PublicItem:
    links = {}
    for evidence in candidate.evidence:
        if evidence.source_group in {"arxiv", "hf"} and "arxiv.org" in evidence.url:
            links["논문"] = evidence.url
    return PublicItem(
        title=clean_text(candidate.title),
        url=candidate.url,
        source_name=candidate.source_name,
        published_at=candidate.published_at,
        summary=clean_text(candidate.summary),
        category=candidate.category,
        links=links,
    )
