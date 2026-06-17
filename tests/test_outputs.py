from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from digest_models import Candidate, Evidence, SourceReport
from digest_render import render_html, serialize_run
from digest_scoring import score_candidates, select_public_items


class OutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 6, 17, 12, tzinfo=timezone.utc)
        self.start = self.now - timedelta(days=7)
        evidence = Evidence(
            source_id="official-openai",
            source_name="OpenAI News",
            source_group="official",
            url="https://openai.com/news/model",
            title="OpenAI launches a new model",
            published_at=self.now - timedelta(hours=6),
            summary="Official announcement.",
            metrics={},
        )
        self.candidate = Candidate(
            title="OpenAI launches a new model",
            url="https://openai.com/news/model",
            source_id="official-openai",
            source_name="OpenAI News",
            source_group="official",
            published_at=self.now - timedelta(hours=6),
            summary="Official announcement for Korean readers.",
            category="LLM / 모델",
            evidence=[evidence],
            metrics={},
        )

    def test_json_keeps_internal_evidence_and_public_html_hides_internal_terms(self) -> None:
        scored = score_candidates([self.candidate], self.now, self.start, self.now)
        selected, empty_sections = select_public_items(scored, ["LLM / 모델", "이미지"])
        report = SourceReport(
            source_id="x-ai",
            display_name="X AI search",
            adapter="auth_gated_social",
            status="user_action_required",
            candidate_count=0,
            fetched_urls=[],
            errors=[],
            action_required="USER_ACTION_REQUIRED",
        )

        payload = serialize_run(
            period_start=self.start,
            period_end=self.now,
            candidates=scored,
            selected=selected,
            source_reports=[report],
            empty_sections=empty_sections,
        )
        html = render_html(
            selected=selected,
            empty_sections=empty_sections,
            period_start=self.start,
            period_end=self.now,
        )

        raw = json.dumps(payload, ensure_ascii=False)
        self.assertIn("evidence", raw)
        self.assertIn("score_breakdown", raw)
        self.assertIn("selection_decision", raw)
        self.assertIn("rejection_reason", raw)
        self.assertIn("source_reports", raw)
        self.assertIn("empty_sections", raw)
        self.assertIn("USER_ACTION_REQUIRED", raw)
        for forbidden in [
            "score_breakdown",
            "selected_reason",
            "candidate_count",
            "unique_count",
            "USER_ACTION_REQUIRED",
            "source_reports",
            "Traceback",
            "adapter",
            "agent",
            "candidate",
        ]:
            self.assertNotIn(forbidden, html)


if __name__ == "__main__":
    unittest.main()
