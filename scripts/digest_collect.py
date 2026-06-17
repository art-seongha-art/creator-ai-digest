from __future__ import annotations

from datetime import datetime

from digest_fetch import HttpClient
from digest_models import SourceReport
from extra_adapters import ArxivAdapter, HackerNewsAdapter, HuggingFaceDailyAdapter
from source_adapters import AuthGatedSocialAdapter, RSSAdapter, RedditAdapter, SourceConfig, ThreadsAdapter, YouTubeAdapter, _report


def collect_sources(sources: list[SourceConfig], client: HttpClient, start_dt: datetime, end_dt: datetime, now: datetime) -> tuple[list, list[SourceReport]]:
    candidates = []
    reports: list[SourceReport] = []
    for source in sources:
        if source.get("enabled") is False:
            reports.append(_report(source, "skipped", 0, [], ["disabled"]))
            continue
        try:
            result = _adapter_for(source, client).collect(start_dt, end_dt, now)
        except (OSError, ValueError, KeyError) as err:
            reports.append(_report(source, "degraded", 0, [], [f"{type(err).__name__}: {str(err)[:120]}"]))
            continue
        candidates.extend(result.candidates)
        reports.append(result.report)
    return candidates, reports


def _adapter_for(source: SourceConfig, client: HttpClient):
    adapter = source.get("adapter")
    if adapter == "youtube":
        return YouTubeAdapter(source, client)
    if adapter == "reddit":
        return RedditAdapter(source, client)
    if adapter == "threads":
        return ThreadsAdapter(source, client)
    if adapter == "auth_gated_social":
        return AuthGatedSocialAdapter(source, client)
    if adapter == "hn":
        return HackerNewsAdapter(source, client)
    if adapter == "arxiv":
        return ArxivAdapter(source, client)
    if adapter == "hf_daily":
        return HuggingFaceDailyAdapter(source, client)
    return RSSAdapter(source, client)
