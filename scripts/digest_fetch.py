from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

UA: Final = "Mozilla/5.0 weekly-ai-digest-v2/0.2"
SECRET_KEYS: Final = {"key", "api_key", "access_token", "token", "bearer", "client_secret"}


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [(key, "[REDACTED]" if key.lower() in SECRET_KEYS else value) for key, value in query]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(redacted)))


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in (headers or {}).items():
        safe[key] = "[REDACTED]" if key.lower() in {"authorization", "x-api-key"} else value
    return safe


class HttpClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        request_headers = {"User-Agent": UA}
        request_headers.update(headers or {})
        req = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("content-type", "")
                final_url = response.geturl()
                return FetchResult(url=redact_url(url), final_url=redact_url(final_url), status_code=response.status, content_type=content_type, text=raw.decode("utf-8", "ignore"))
        except urllib.error.HTTPError as err:
            raw = err.read()
            return FetchResult(url=redact_url(url), final_url=redact_url(err.geturl()), status_code=err.code, content_type=err.headers.get("content-type", ""), text=raw.decode("utf-8", "ignore"), error=f"HTTP {err.code}")
        except urllib.error.URLError as err:
            return FetchResult(url=redact_url(url), final_url=redact_url(url), status_code=0, content_type="", text="", error=f"URL error: {err.reason}")
        except (TimeoutError, OSError, UnicodeError) as err:
            return FetchResult(url=redact_url(url), final_url=redact_url(url), status_code=0, content_type="", text="", error=type(err).__name__)


class FixtureHttpClient(HttpClient):
    def __init__(self, fixture_dir: Path) -> None:
        super().__init__()
        self.fixture_dir = fixture_dir

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        del headers
        parsed = urllib.parse.urlsplit(url)
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", f"{parsed.netloc}_{parsed.path}_{parsed.query}").strip("_")
        for suffix in (".xml", ".json", ".html", ".txt"):
            path = self.fixture_dir / f"{safe_name}{suffix}"
            if path.exists():
                return FetchResult(url=url, final_url=url, status_code=200, content_type=suffix.lstrip("."), text=path.read_text(encoding="utf-8"))
        return FetchResult(url=url, final_url=url, status_code=404, content_type="text/plain", text="", error="fixture missing")
