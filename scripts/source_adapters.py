from __future__ import annotations

import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

from digest_fetch import FetchResult, HttpClient, redact_url
from digest_models import AdapterResult, Candidate, Evidence, SourceReport, SourceStatus
from digest_utils import absolute_url, categorize, clean_text, parse_dt

SourceConfig = dict[str, str | int | bool | list[str]]


def _source_list(source: SourceConfig, key: str) -> list[str]:
    raw = source.get(key, [])
    return raw if isinstance(raw, list) else []


def _source_str(source: SourceConfig, key: str, default: str = "") -> str:
    raw = source.get(key, default)
    return raw if isinstance(raw, str) else default


def _source_int(source: SourceConfig, key: str, default: int) -> int:
    raw = source.get(key, default)
    return raw if isinstance(raw, int) else default


def _report(source: SourceConfig, status: SourceStatus, count: int, urls: list[str], errors: list[str], action: str | None = None) -> SourceReport:
    return SourceReport(
        source_id=_source_str(source, "id"),
        display_name=_source_str(source, "display_name"),
        adapter=_source_str(source, "adapter"),
        status=status,
        candidate_count=count,
        fetched_urls=[redact_url(url) for url in urls],
        errors=errors[:5],
        action_required=action,
    )


def _candidate(source: SourceConfig, title: str, url: str, published: datetime | None, summary: str, metrics: dict[str, int] | None = None) -> Candidate:
    hints = _source_list(source, "category_hints")
    metric_values = metrics or {}
    evidence = Evidence(
        source_id=_source_str(source, "id"),
        source_name=_source_str(source, "display_name"),
        source_group=_source_str(source, "source_group", "news"),
        url=url,
        title=title,
        published_at=published,
        summary=summary,
        metrics=metric_values,
    )
    return Candidate(
        title=title,
        url=url,
        source_id=evidence.source_id,
        source_name=evidence.source_name,
        source_group=evidence.source_group,
        published_at=published,
        summary=summary,
        category=categorize(title, summary, hints),
        evidence=[evidence],
        metrics=metric_values,
        selectable=published is not None,
    )


def _node_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return clean_text(found.text)
        found = node.find(f"{{http://www.w3.org/2005/Atom}}{name}")
        if found is not None and found.text:
            return clean_text(found.text)
    return ""


def parse_feed(text: str, source: SourceConfig, limit: int) -> list[Candidate]:
    root = ET.fromstring(text.encode("utf-8"))
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out: list[Candidate] = []
    for node in nodes[:limit]:
        title = _node_text(node, ("title",))
        link = _node_text(node, ("link",))
        if not link:
            link_el = node.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href", "") if link_el is not None else ""
        summary = _node_text(node, ("description", "summary", "content"))
        published = parse_dt(_node_text(node, ("pubDate", "published", "updated")))
        if title and link:
            out.append(_candidate(source, title, link, published, summary))
    return out


def parse_official_html(text: str, base_url: str, source: SourceConfig, limit: int) -> list[Candidate]:
    blocks = re.findall(r"<article\b[^>]*>(.*?)</article>", text, flags=re.IGNORECASE | re.DOTALL)
    if not blocks:
        blocks = re.findall(r"<a\b[^>]*href=[\"'][^\"']+[\"'][^>]*>.*?</a>", text, flags=re.IGNORECASE | re.DOTALL)
    out: list[Candidate] = []
    for block in blocks[: limit * 4]:
        link_match = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", block, flags=re.IGNORECASE | re.DOTALL)
        if not link_match:
            continue
        title = clean_text(link_match.group(2))
        if len(title) < 8:
            continue
        href = absolute_url(base_url, link_match.group(1))
        dt_match = re.search(r"datetime=[\"']([^\"']+)[\"']", block, flags=re.IGNORECASE)
        if not dt_match:
            dt_match = re.search(r"(\d{4}-\d{2}-\d{2})", block)
        published = parse_dt(dt_match.group(1) if dt_match else None)
        summary_match = re.search(r"<p\b[^>]*>(.*?)</p>", block, flags=re.IGNORECASE | re.DOTALL)
        summary = clean_text(summary_match.group(1) if summary_match else "")
        out.append(_candidate(source, title, href, published, summary))
        if len(out) >= limit:
            break
    return out


class RSSAdapter:
    def __init__(self, source: SourceConfig, client: HttpClient) -> None:
        self.source = source
        self.client = client

    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del start_dt, end_dt, now
        limit = _source_int(self.source, "max_items", 20)
        primary = _source_str(self.source, "primary_url")
        urls: list[str] = []
        errors: list[str] = []
        candidates: list[Candidate] = []
        if primary:
            rss = self.client.fetch(primary)
            urls.append(primary)
            candidates, error = _parse_feed_result(rss, self.source, limit)
            if error:
                errors.append(f"rss {primary}: {error}")
        if candidates:
            return AdapterResult(candidates, _report(self.source, "ok", len(candidates), urls, errors))
        for fallback_url in _source_list(self.source, "fallback_urls"):
            result = self.client.fetch(fallback_url)
            urls.append(fallback_url)
            if not result.ok:
                errors.append(f"html {fallback_url}: {result.error or result.status_code}")
                continue
            candidates = parse_official_html(result.text, fallback_url, self.source, limit)
            if candidates:
                return AdapterResult(candidates, _report(self.source, "degraded", len(candidates), urls, errors))
            errors.append(f"html {fallback_url}: no dated items")
        status = "degraded" if errors else "ok"
        return AdapterResult([], _report(self.source, status, 0, urls, errors))


def _parse_feed_result(result: FetchResult, source: SourceConfig, limit: int) -> tuple[list[Candidate], str]:
    if not result.ok:
        return [], result.error or f"HTTP {result.status_code}"
    try:
        return parse_feed(result.text, source, limit), ""
    except ET.ParseError as err:
        return [], f"parse error: {err}"


class YouTubeAdapter(RSSAdapter):
    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        api_key_name = _source_str(self.source, "auth_env", "YOUTUBE_API_KEY")
        api_key = os.environ.get(api_key_name, "")
        if api_key:
            channel_id = _source_str(self.source, "channel_id")
            api_url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode({"part": "snippet", "channelId": channel_id, "order": "date", "type": "video", "maxResults": _source_int(self.source, "max_items", 5), "key": api_key})
            result = self.client.fetch(api_url)
            if result.ok:
                return _youtube_api_result(self.source, result)
        channel_id = _source_str(self.source, "channel_id")
        feed_url = _source_str(self.source, "feed_url") or f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        rss_source = dict(self.source)
        rss_source["primary_url"] = feed_url
        return RSSAdapter(rss_source, self.client).collect(start_dt, end_dt, now)


def _youtube_api_result(source: SourceConfig, result: FetchResult) -> AdapterResult:
    data = json.loads(result.text)
    candidates: list[Candidate] = []
    for item in data.get("items", [])[: _source_int(source, "max_items", 5)]:
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        if snippet.get("title") and video_id:
            candidates.append(_candidate(source, clean_text(snippet["title"]), f"https://www.youtube.com/watch?v={video_id}", parse_dt(snippet.get("publishedAt")), clean_text(snippet.get("description", ""))))
    return AdapterResult(candidates, _report(source, "ok", len(candidates), [result.url], []))


class RedditAdapter(RSSAdapter):
    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del start_dt, end_dt, now
        subreddit = _source_str(self.source, "subreddit")
        limit = _source_int(self.source, "max_items", 10)
        json_url = _source_str(self.source, "json_url") or f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
        rss_url = _source_str(self.source, "rss_url") or f"https://www.reddit.com/r/{subreddit}/.rss"
        urls = [json_url]
        result = self.client.fetch(json_url)
        if result.ok:
            candidates = _reddit_json_candidates(self.source, result.text, limit)
            return AdapterResult(candidates, _report(self.source, "ok", len(candidates), urls, []))
        errors = [f"json {json_url}: {result.error or result.status_code}"]
        rss = self.client.fetch(rss_url)
        urls.append(rss_url)
        candidates, error = _parse_feed_result(rss, self.source, limit)
        if error:
            errors.append(f"rss {rss_url}: {error}")
        status = "degraded" if candidates or errors else "ok"
        return AdapterResult(candidates, _report(self.source, status, len(candidates), urls, errors))


def _reddit_json_candidates(source: SourceConfig, text: str, limit: int) -> list[Candidate]:
    data = json.loads(text)
    out: list[Candidate] = []
    for child in data.get("data", {}).get("children", [])[:limit]:
        post = child.get("data", {})
        title = clean_text(post.get("title", ""))
        permalink = post.get("permalink", "")
        if title and permalink:
            metrics = {"reddit_score": int(post.get("score") or 0), "reddit_comments": int(post.get("num_comments") or 0)}
            out.append(_candidate(source, title, absolute_url("https://www.reddit.com", permalink), parse_dt(post.get("created_utc")), clean_text(post.get("selftext", "")), metrics))
    return out



class ThreadsAdapter:
    def __init__(self, source: SourceConfig, client: HttpClient) -> None:
        self.source = source
        self.client = client

    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del start_dt, end_dt, now
        user_id = os.environ.get(_source_str(self.source, "user_id_env", "THREADS_USER_ID"), "")
        token = os.environ.get(_source_str(self.source, "token_env", "THREADS_ACCESS_TOKEN"), "")
        if not user_id or not token:
            return AdapterResult([], _report(self.source, "user_action_required", 0, [], [], "USER_ACTION_REQUIRED"))
        fields = "id,text,timestamp,permalink"
        url = f"https://graph.threads.net/v1.0/{urllib.parse.quote(user_id)}/threads?" + urllib.parse.urlencode({
            "fields": fields,
            "limit": _source_int(self.source, "max_items", 5),
            "access_token": token,
        })
        result = self.client.fetch(url)
        if not result.ok:
            return AdapterResult([], _report(self.source, "degraded", 0, [url], [result.error or f"HTTP {result.status_code}"]))
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as err:
            return AdapterResult([], _report(self.source, "degraded", 0, [url], [f"json parse error: {err}"]))
        candidates: list[Candidate] = []
        for item in data.get("data", [])[: _source_int(self.source, "max_items", 5)]:
            text = clean_text(item.get("text", ""))
            if not text:
                continue
            title = text.split("\n", 1)[0][:120]
            if len(title) < 8:
                title = text[:120]
            permalink = item.get("permalink") or f"https://www.threads.net/@_/post/{item.get('id', '')}"
            published = parse_dt(item.get("timestamp"))
            metrics = {"threads_items": 1}
            candidates.append(_candidate(self.source, title, permalink, published, text[:600], metrics))
        return AdapterResult(candidates, _report(self.source, "ok", len(candidates), [url], []))


class AuthGatedSocialAdapter:
    def __init__(self, source: SourceConfig, client: HttpClient) -> None:
        self.source = source
        self.client = client

    def collect(self, start_dt: datetime, end_dt: datetime, now: datetime) -> AdapterResult:
        del start_dt, end_dt, now
        required = _source_list(self.source, "required_env")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            report = _report(self.source, "user_action_required", 0, [], [], "USER_ACTION_REQUIRED")
            return AdapterResult([], report)
        report = _report(self.source, "skipped", 0, [], ["authenticated collection is intentionally optional"], None)
        return AdapterResult([], report)
