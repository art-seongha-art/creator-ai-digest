from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime

from digest_models import Candidate, PublicItem, SourceReport
from digest_utils import iso_dt, public_text


def serialize_run(
    period_start: datetime,
    period_end: datetime,
    candidates: list[Candidate],
    selected: list[PublicItem],
    source_reports: list[SourceReport],
    empty_sections: list[str],
) -> dict[str, object]:
    return {
        "period": {"start": iso_dt(period_start), "end": iso_dt(period_end)},
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "source_reports": [report.to_json() for report in source_reports],
        "empty_sections": empty_sections,
        "candidates": [candidate.to_json() for candidate in candidates],
        "selected": [_public_json(item) for item in selected],
    }


def render_html(
    selected: list[PublicItem],
    empty_sections: list[str],
    period_start: datetime,
    period_end: datetime,
) -> str:
    del empty_sections
    by_category: dict[str, list[PublicItem]] = defaultdict(list)
    for item in selected:
        by_category[item.category].append(item)
    sections = []
    for category in ["주요 뉴스", "LLM / 모델", "에이전트 / 코딩", "이미지", "영상", "음악 / 오디오", "3D / 공간", "정책 / 저작권", "하드웨어 / 인프라", "논문"]:
        items = by_category.get(category, [])
        if not items:
            continue
        sections.append(f"<section><h2>{_esc(category)}</h2>{''.join(_item_html(item) for item in items)}</section>")
    return _page(period_start, period_end, "".join(sections))


def _public_json(item: PublicItem) -> dict[str, str | None | dict[str, str]]:
    return {
        "title": item.title,
        "url": item.url,
        "source_name": item.source_name,
        "published_at": iso_dt(item.published_at),
        "summary": item.summary,
        "category": item.category,
        "links": item.links,
    }


def _item_html(item: PublicItem) -> str:
    date = item.published_at.strftime("%Y.%m.%d") if item.published_at else ""
    links = [f'<a href="{_esc(item.url)}">원문</a>']
    links.extend(f'<a href="{_esc(url)}">{_esc(label)}</a>' for label, url in item.links.items())
    return (
        '<article class="item">'
        f'<div class="meta"><b>{_esc(item.source_name)}</b><span>{_esc(date)}</span></div>'
        f"<h3>{_esc(public_text(item.title))}</h3>"
        f"<p>{_esc(public_text(item.summary[:420]))}</p>"
        f'<div class="links">{" ".join(links)}</div>'
        "</article>"
    )


def _page(period_start: datetime, period_end: datetime, body: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>창작자를 위한 AI 다이제스트 - {period_end.strftime('%Y.%m.%d')}</title><style>
body{{margin:0;background:#f7f7f4;color:#151515;font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:30px 18px 64px}}
header{{border-bottom:3px solid #151515;padding-bottom:14px;margin-bottom:16px;display:flex;justify-content:space-between;gap:16px;align-items:end}}
h1{{font-size:44px;line-height:1;margin:0;letter-spacing:0}}
.period{{font-weight:800;color:#555}}
section{{border-top:1px solid #d9d9d2;margin:20px 0 0;padding-top:12px}}
h2{{font-size:24px;margin:0 0 8px}}
.item{{padding:16px 0;border-bottom:1px solid #e6e6df}}
.meta{{display:flex;gap:9px;flex-wrap:wrap;font-size:12px;color:#666;margin-bottom:7px}}
.meta b{{color:#21528d}}
h3{{font-size:20px;line-height:1.34;margin:0 0 8px;letter-spacing:0}}
p{{margin:0 0 10px;color:#333;line-height:1.58}}
.links a{{display:inline-block;border:1px solid #d4d4cc;border-radius:8px;padding:6px 9px;margin-right:6px;font-size:13px;color:#222;text-decoration:none;background:#fff}}
@media(max-width:760px){{header{{display:block}}h1{{font-size:36px}}.period{{margin-top:10px}}}}
</style></head><body><main class="wrap"><header><h1>창작자를 위한 AI 다이제스트</h1><div class="period">발행일 {period_end.strftime('%Y.%m.%d')}</div></header>{body}</main></body></html>"""


def _esc(value: str) -> str:
    return html.escape(str(value or ""), quote=True)
