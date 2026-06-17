from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from digest_fetch import FetchResult, redact_url
from source_adapters import (
    AuthGatedSocialAdapter,
    RSSAdapter,
    RedditAdapter,
    YouTubeAdapter,
)


class FakeHttpClient:
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        self.calls.append(url)
        return self.responses.get(
            url,
            FetchResult(
                url=url,
                final_url=url,
                status_code=404,
                content_type="text/plain",
                text="missing",
                error="not found",
            ),
        )


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 6, 10, tzinfo=timezone.utc)
        self.end = datetime(2026, 6, 17, tzinfo=timezone.utc)
        self.now = datetime(2026, 6, 17, tzinfo=timezone.utc)
        self.fixtures = ROOT / "tests" / "fixtures"

    def test_official_rss_404_uses_html_fallback_when_available(self) -> None:
        html = (self.fixtures / "official_news.html").read_text(encoding="utf-8")
        client = FakeHttpClient(
            {
                "https://example.com/rss.xml": FetchResult(
                    url="https://example.com/rss.xml",
                    final_url="https://example.com/rss.xml",
                    status_code=404,
                    content_type="text/html",
                    text="not found",
                    error="HTTP 404",
                ),
                "https://example.com/news": FetchResult(
                    url="https://example.com/news",
                    final_url="https://example.com/news",
                    status_code=200,
                    content_type="text/html",
                    text=html,
                ),
            },
        )
        source = {
            "id": "anthropic",
            "display_name": "Anthropic News",
            "source_group": "official",
            "adapter": "rss",
            "primary_url": "https://example.com/rss.xml",
            "fallback_urls": ["https://example.com/news"],
            "category_hints": ["LLM / 모델"],
            "max_items": 3,
        }

        result = RSSAdapter(source, client).collect(self.start, self.end, self.now)

        self.assertEqual("degraded", result.report.status)
        self.assertEqual(1, len(result.candidates))
        self.assertEqual("official", result.candidates[0].source_group)
        self.assertEqual("LLM / 모델", result.candidates[0].category)
        self.assertIn("rss", result.report.errors[0].lower())

    def test_youtube_uses_public_rss_without_api_key_and_redacts_key_urls(self) -> None:
        xml = (self.fixtures / "youtube_feed.xml").read_text(encoding="utf-8")
        feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"
        client = FakeHttpClient(
            {
                feed_url: FetchResult(
                    url=feed_url,
                    final_url=feed_url,
                    status_code=200,
                    content_type="application/atom+xml",
                    text=xml,
                )
            },
        )
        source = {
            "id": "youtube-openai",
            "display_name": "OpenAI YouTube",
            "source_group": "youtube",
            "adapter": "youtube",
            "channel_id": "UC123",
            "category_hints": ["영상"],
            "max_items": 3,
            "auth_env": "YOUTUBE_API_KEY",
        }

        with patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}, clear=False):
            result = YouTubeAdapter(source, client).collect(
                self.start,
                self.end,
                self.now,
            )

        self.assertEqual("ok", result.report.status)
        self.assertEqual([feed_url], client.calls)
        self.assertEqual("youtube", result.candidates[0].source_group)
        self.assertNotIn("SECRET", redact_url("https://x.test/?key=SECRET"))

    def test_reddit_degrades_from_public_json_403_to_subreddit_rss(self) -> None:
        xml = (self.fixtures / "reddit_feed.xml").read_text(encoding="utf-8")
        json_url = "https://www.reddit.com/r/LocalLLaMA/new.json?limit=3"
        rss_url = "https://www.reddit.com/r/LocalLLaMA/.rss"
        client = FakeHttpClient(
            {
                json_url: FetchResult(
                    url=json_url,
                    final_url=json_url,
                    status_code=403,
                    content_type="application/json",
                    text="blocked",
                    error="HTTP 403",
                ),
                rss_url: FetchResult(
                    url=rss_url,
                    final_url=rss_url,
                    status_code=200,
                    content_type="application/atom+xml",
                    text=xml,
                ),
            },
        )
        source = {
            "id": "reddit-localllama",
            "display_name": "r/LocalLLaMA",
            "source_group": "reddit",
            "adapter": "reddit",
            "subreddit": "LocalLLaMA",
            "category_hints": ["LLM / 모델"],
            "max_items": 3,
        }

        result = RedditAdapter(source, client).collect(self.start, self.end, self.now)

        self.assertEqual("degraded", result.report.status)
        self.assertEqual([json_url, rss_url], client.calls)
        self.assertEqual(1, len(result.candidates))
        self.assertEqual("reddit", result.candidates[0].source_group)

    def test_reddit_public_json_epoch_timestamp_stays_selectable(self) -> None:
        json_url = "https://www.reddit.com/r/OpenAI/new.json?limit=3"
        body = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "OpenAI releases a new model",
                            "permalink": "/r/OpenAI/comments/abc/model/",
                            "created_utc": 1781598600,
                            "score": 240,
                            "num_comments": 80,
                            "selftext": "Discussion of an official AI model release.",
                        }
                    }
                ]
            }
        }
        client = FakeHttpClient(
            {
                json_url: FetchResult(
                    url=json_url,
                    final_url=json_url,
                    status_code=200,
                    content_type="application/json",
                    text=__import__("json").dumps(body),
                )
            },
        )
        source = {
            "id": "reddit-openai",
            "display_name": "r/OpenAI",
            "source_group": "reddit",
            "adapter": "reddit",
            "subreddit": "OpenAI",
            "category_hints": ["LLM / 모델"],
            "max_items": 3,
        }

        result = RedditAdapter(source, client).collect(self.start, self.end, self.now)

        self.assertEqual("ok", result.report.status)
        self.assertEqual(1, len(result.candidates))
        self.assertIsNotNone(result.candidates[0].published_at)
        self.assertTrue(result.candidates[0].selectable)

    def test_auth_gated_social_without_env_records_action_required_without_fetch(self) -> None:
        client = FakeHttpClient({})
        source = {
            "id": "x-ai",
            "display_name": "X AI search",
            "source_group": "x",
            "adapter": "auth_gated_social",
            "required_env": ["X_BEARER_TOKEN"],
            "primary_url": "https://api.x.com/2/tweets/search/recent",
        }

        with patch.dict(os.environ, {"X_BEARER_TOKEN": ""}, clear=False):
            result = AuthGatedSocialAdapter(source, client).collect(
                self.start,
                self.end,
                self.now,
            )

        self.assertEqual("user_action_required", result.report.status)
        self.assertEqual("USER_ACTION_REQUIRED", result.report.action_required)
        self.assertEqual([], result.candidates)
        self.assertEqual([], client.calls)


if __name__ == "__main__":
    unittest.main()
