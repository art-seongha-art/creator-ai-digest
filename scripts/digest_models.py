from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from digest_utils import iso_dt

SourceStatus = Literal["ok", "degraded", "skipped", "user_action_required", "failed"]
MetricValue = int | float | str | bool | None


@dataclass(slots=True)
class Evidence:
    source_id: str
    source_name: str
    source_group: str
    url: str
    title: str
    published_at: datetime | None
    summary: str = ""
    metrics: dict[str, MetricValue] = field(default_factory=dict)

    def to_json(self) -> dict[str, MetricValue | dict[str, MetricValue]]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_group": self.source_group,
            "url": self.url,
            "title": self.title,
            "published_at": iso_dt(self.published_at),
            "summary": self.summary,
            "metrics": self.metrics,
        }


@dataclass(slots=True)
class Candidate:
    title: str
    url: str
    source_id: str
    source_name: str
    source_group: str
    published_at: datetime | None
    summary: str = ""
    category: str = "AI News"
    evidence: list[Evidence] = field(default_factory=list)
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    story_key: str = ""
    selection_decision: str = "pending"
    rejection_reason: str = ""
    selectable: bool = True

    def ensure_evidence(self) -> None:
        if self.evidence:
            return
        self.evidence.append(
            Evidence(
                source_id=self.source_id,
                source_name=self.source_name,
                source_group=self.source_group,
                url=self.url,
                title=self.title,
                published_at=self.published_at,
                summary=self.summary,
                metrics=self.metrics,
            )
        )

    def to_json(self) -> dict[str, MetricValue | list[dict[str, MetricValue | dict[str, MetricValue]]] | dict[str, float]]:
        return {
            "title": self.title,
            "url": self.url,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_group": self.source_group,
            "published_at": iso_dt(self.published_at),
            "summary": self.summary,
            "category": self.category,
            "score": round(self.score, 3),
            "score_breakdown": self.score_breakdown,
            "story_key": self.story_key,
            "selection_decision": self.selection_decision,
            "rejection_reason": self.rejection_reason,
            "selectable": self.selectable,
            "evidence": [item.to_json() for item in self.evidence],
        }


@dataclass(slots=True)
class SourceReport:
    source_id: str
    display_name: str
    adapter: str
    status: SourceStatus
    candidate_count: int
    fetched_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    action_required: str | None = None

    def to_json(self) -> dict[str, str | int | list[str] | None]:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "status": self.status,
            "candidate_count": self.candidate_count,
            "fetched_urls": self.fetched_urls,
            "errors": self.errors,
            "action_required": self.action_required,
        }


@dataclass(slots=True)
class AdapterResult:
    candidates: list[Candidate]
    report: SourceReport


@dataclass(slots=True)
class PublicItem:
    title: str
    url: str
    source_name: str
    published_at: datetime | None
    summary: str
    category: str
    links: dict[str, str] = field(default_factory=dict)
