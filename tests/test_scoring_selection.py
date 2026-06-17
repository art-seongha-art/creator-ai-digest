from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from digest_models import Candidate, Evidence
from digest_scoring import merge_stories, score_candidates, select_public_items


def make_candidate(
    title: str,
    source_group: str,
    published_at: datetime,
    *,
    category: str = "LLM / 모델",
    summary: str = "A substantial AI model release with strong relevance.",
    url: str = "https://example.com/story",
    metrics: dict[str, int] | None = None,
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        source_id=f"{source_group}-source",
        source_name=f"{source_group} source",
        source_group=source_group,
        published_at=published_at,
        summary=summary,
        category=category,
        evidence=[
            Evidence(
                source_id=f"{source_group}-source",
                source_name=f"{source_group} source",
                source_group=source_group,
                url=url,
                title=title,
                published_at=published_at,
                summary=summary,
                metrics=metrics or {},
            )
        ],
        metrics=metrics or {},
    )


class ScoringSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
        self.start = self.now - timedelta(days=7)
        self.categories = ["LLM / 모델", "이미지", "영상", "음악 / 오디오", "3D / 공간"]

    def test_hn_only_candidate_is_capped_and_rejected_from_public_selection(self) -> None:
        hn = make_candidate(
            "AI model release gets 5000 points",
            "hn",
            self.now - timedelta(hours=2),
            metrics={"hn_points": 5000, "hn_comments": 1200},
        )

        scored = score_candidates([hn], self.now, self.start, self.now)
        selected, empty_sections = select_public_items(scored, self.categories)

        self.assertEqual([], selected)
        self.assertIn("single_hn_cap", scored[0].rejection_reason)
        self.assertIn("LLM / 모델", empty_sections)

    def test_distinct_evidence_sources_increase_evidence_count_without_domain_duplication(self) -> None:
        official = make_candidate(
            "OpenAI introduces new multimodal model",
            "official",
            self.now - timedelta(hours=5),
            url="https://openai.com/news/model",
        )
        reddit = make_candidate(
            "OpenAI introduces new multimodal model",
            "reddit",
            self.now - timedelta(hours=4),
            url="https://www.reddit.com/r/OpenAI/comments/model",
            metrics={"reddit_score": 250, "reddit_comments": 90},
        )

        merged = merge_stories([official, reddit])
        scored = score_candidates(merged, self.now, self.start, self.now)

        self.assertEqual(1, len(scored))
        self.assertEqual(2, scored[0].score_breakdown["evidence_count"])
        self.assertGreater(scored[0].score_breakdown["evidence"], 0)

    def test_out_of_period_candidate_remains_json_diagnostic_only(self) -> None:
        stale = make_candidate(
            "Old image model release",
            "official",
            self.now - timedelta(days=10),
            category="이미지",
        )

        scored = score_candidates([stale], self.now, self.start, self.now)
        selected, empty_sections = select_public_items(scored, self.categories)

        self.assertEqual([], selected)
        self.assertEqual("period_outside", scored[0].rejection_reason)
        self.assertIn("이미지", empty_sections)

    def test_weak_media_sections_are_not_force_filled(self) -> None:
        weak = make_candidate(
            "Minor visual tool update",
            "news",
            self.now - timedelta(days=6, hours=20),
            category="이미지",
            summary="Small update.",
        )

        scored = score_candidates([weak], self.now, self.start, self.now)
        selected, empty_sections = select_public_items(scored, self.categories)

        self.assertEqual([], selected)
        self.assertIn("이미지", empty_sections)


if __name__ == "__main__":
    unittest.main()
