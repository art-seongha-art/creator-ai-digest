from __future__ import annotations

import json
import math
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from digest_fetch import HttpClient
from digest_models import AdapterResult
from digest_utils import clean_text, parse_dt
from source_adapters import RSSAdapter, SourceConfig, _candidate, _parse_feed_result, _report, _source_int, _source_str


class HackerNewsAdapter:
    def __init__(self, source: SourceConfig, client: HttpClient) -> None:
        self.source = source
        self.client = client

    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del end_dt, now
        query = _source_str(self.source, "query")
        params = urllib.parse.urlencode({"query": query, "tags": "story", "numericFilters": f"created_at_i>{int(start_dt.timestamp())}", "hitsPerPage": _source_int(self.source, "max_items", 20)})
        url = "https://hn.algolia.com/api/v1/search?" + params
        result = self.client.fetch(url)
        if not result.ok:
            return AdapterResult([], _report(self.source, "degraded", 0, [url], [result.error or str(result.status_code)]))
        data = json.loads(result.text)
        candidates = []
        for hit in data.get("hits", []):
            title = clean_text(hit.get("title", ""))
            link = hit.get("url", "")
            created = hit.get("created_at_i") or 0
            if title and link:
                published = datetime.fromtimestamp(created, tz=timezone.utc)
                metrics = {"hn_points": int(hit.get("points") or 0), "hn_comments": int(hit.get("num_comments") or 0)}
                candidates.append(_candidate(self.source, title, link, published, f"HN points {metrics['hn_points']}, comments {metrics['hn_comments']}", metrics))
        return AdapterResult(candidates, _report(self.source, "ok", len(candidates), [url], []))


class ArxivAdapter(RSSAdapter):
    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del start_dt, end_dt, now
        params = urllib.parse.urlencode({"search_query": _source_str(self.source, "query"), "sortBy": "submittedDate", "sortOrder": "descending", "max_results": str(_source_int(self.source, "max_items", 20))})
        url = "https://export.arxiv.org/api/query?" + params
        result = self.client.fetch(url)
        candidates, error = _parse_feed_result(result, self.source, _source_int(self.source, "max_items", 20))
        status = "ok" if not error else "degraded"
        return AdapterResult(candidates, _report(self.source, status, len(candidates), [url], [error] if error else []))


class HuggingFaceDailyAdapter:
    def __init__(self, source: SourceConfig, client: HttpClient) -> None:
        self.source = source
        self.client = client

    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del now
        day = start_dt.date()
        urls: list[str] = []
        errors: list[str] = []
        candidates = []
        max_items = _source_int(self.source, "max_items", 12)
        while day <= end_dt.date() and len(candidates) < max_items:
            url = f"https://huggingface.co/api/daily_papers?date={day.isoformat()}"
            urls.append(url)
            result = self.client.fetch(url)
            if result.ok:
                candidates.extend(_hf_items(self.source, result.text, day.isoformat(), max_items - len(candidates)))
            else:
                errors.append(f"{day.isoformat()}: {result.error or result.status_code}")
            day += timedelta(days=1)
        status = "ok" if candidates else "degraded"
        return AdapterResult(candidates, _report(self.source, status, len(candidates), urls, errors))


def _hf_items(source: SourceConfig, text: str, day: str, limit: int):
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:limit]:
        paper = item.get("paper", {})
        arxiv_id = paper.get("id") or item.get("id")
        title = clean_text(item.get("title") or paper.get("title") or "")
        if arxiv_id and title:
            metrics = {"hf_upvotes": int(paper.get("upvotes") or item.get("upvotes") or 0)}
            out.append(_candidate(source, title, f"https://huggingface.co/papers/{arxiv_id}", parse_dt(day), clean_text(item.get("summary") or paper.get("summary") or ""), metrics))
    return out
