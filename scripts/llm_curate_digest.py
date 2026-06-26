#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import html
import hashlib
import textwrap

BASE = Path('.')
OUTROOT = Path('docs')

CATEGORIES = [
    '바로 써볼 AI 도구', '수업·워크숍 아이디어', '창작·디자인 사례', '연구·논문', '정책·저작권',
    'LLM / 모델', '이미지', '영상', '음악 / 오디오', '3D / 공간', '에이전트 / 코딩', '하드웨어 / 인프라', '주요 뉴스'
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Use an LLM agent to write Korean public AI digest copy from candidate JSON.')
    p.add_argument('--input-json', default=str(BASE / 'output/latest_candidates.json'))
    p.add_argument('--output-json', default=str(BASE / 'output/latest_public_ko_llm.json'))
    p.add_argument('--output-html', default=str(OUTROOT / 'latest.html'))
    p.add_argument('--archive-dir', default=str(OUTROOT))
    p.add_argument('--model', default='sonnet')
    p.add_argument('--timeout', type=int, default=420)
    p.add_argument('--max-llm-items', type=int, default=8, help='Maximum selected items to send to the LLM curator.')
    p.add_argument('--render-existing-json', default='', help='Render this public JSON directly without calling the LLM.')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.render_existing_json:
        public = json.loads(Path(args.render_existing_json).read_text(encoding='utf-8'))
        html_text = render_html(public)
        output_html = Path(args.output_html)
        output_html.parent.mkdir(parents=True, exist_ok=True)
        output_html.write_text(html_text, encoding='utf-8')
        print(json.dumps({'output_json': args.render_existing_json, 'output_html': str(output_html), 'items': len(public.get('items', [])), 'mode': 'render_existing_json'}, ensure_ascii=False, indent=2))
        return 0
    data = json.loads(Path(args.input_json).read_text(encoding='utf-8'))
    selected = data.get('selected') or []
    if not selected:
        raise SystemExit('No selected items in input JSON')
    selected = prioritize_for_llm(selected, args.max_llm_items)
    prompt = build_prompt(data, selected)
    raw = run_claude(prompt, args.model, args.timeout)
    public = extract_json(raw)
    validate_public(public)
    public['period'] = data.get('period', {})
    public['curation'] = {
        'writer': 'claude-cli',
        'mode': 'llm_korean_digest_copy',
        'source_json': str(Path(args.input_json)),
    }
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
    html_text = render_html(public)
    output_html = Path(args.output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html_text, encoding='utf-8')
    period_end = parse_dt(public.get('period', {}).get('end')) or datetime.now()
    archive_dir = Path(args.archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f'weekly-ai-digest-v2-{period_end.strftime("%Y-%m-%d")}.html'
    archive.write_text(rewrite_asset_paths_for_archive(html_text), encoding='utf-8')
    print(json.dumps({'output_json': str(output_json), 'output_html': str(output_html), 'archive': str(archive), 'items': len(public['items'])}, ensure_ascii=False, indent=2))
    return 0


def rewrite_asset_paths_for_archive(html_text: str) -> str:
    """Archive pages live one directory below docs/, so local asset URLs need ../assets/."""
    return (html_text
            .replace('src="assets/', 'src="../assets/')
            .replace('poster="assets/', 'poster="../assets/')
            .replace('href="assets/', 'href="../assets/'))


def prioritize_for_llm(items: list[dict], limit: int) -> list[dict]:
    """Keep LLM input small and favor practical creative-tool / media / motion items."""
    def weight(item: dict) -> tuple[int, float]:
        text = f"{item.get('title','')} {item.get('summary','')} {item.get('source_name','')} {item.get('category','')}".lower()
        score = float(item.get('score') or 0)
        boost = 0
        for term in ['motionbricks','nvidia','nvlabs','kling','flow','veo','higgsfield','runway','luma','pika','midjourney','firefly','adobe','suno','elevenlabs','comfyui','3d','video','music','audio','robot','motion']:
            if term in text:
                boost += 10
        for term in ['earnings transcript','stock','lawyer','pricing','review 2026']:
            if term in text:
                boost -= 8
        return (boost, score)
    return sorted(items, key=weight, reverse=True)[:limit]


def build_prompt(data: dict, selected: list[dict]) -> str:
    slim = []
    for item in selected:
        summary = (item.get('summary') or '')
        if len(summary) > 700:
            summary = summary[:700] + '…'
        slim.append({
            'category': item.get('category'),
            'source_name': item.get('source_name'),
            'published_at': item.get('published_at'),
            'title_original': item.get('title'),
            'summary_original': summary,
            'url': item.get('url'),
            'links': item.get('links') or {},
            'source_group': item.get('source_group'),
        })
    period = data.get('period', {})
    return f"""
너는 AI×예술·디자인·교육 주간 브리프 큐레이터다.
입력 후보에서 6~8개를 골라 공개용 한국어 JSON만 출력한다. 독자는 AI 전문가가 아닐 수 있으므로 쉽고 친근하게, 현장에서 바로 써먹을 수 있는 말로 설명한다.
우선순위: MotionBricks/NVIDIA 로봇·모션, 영상/이미지/음악/3D 창작 도구 업데이트, Adobe/Runway/Luma/Kling/Higgsfield/Suno/ElevenLabs, 실습 가능한 에이전트/로컬AI.
제외: 스마트스피커, 데이터센터, 주식/실적, 단순 가격비교, 창작 연결이 약한 일반뉴스.
각 항목은 자연스러운 한국어로 쓰고, URL은 입력 그대로 유지한다. 없는 사실은 만들지 않는다. 요약은 어려운 용어를 풀어서 쓰고, 인사이트는 ‘그래서 오늘 창작자가 뭘 해볼 수 있나’가 보이게 쓴다.
quality: A=실제 기능/모델 업데이트, B=창작 도구·산업 사례, C=보조 인사이트.
출력 JSON 형식만:
{{"title":"창작자를 위한 AI 다이제스트","items":[{{"category":"바로 써볼 AI 도구|영상|이미지|음악 / 오디오|3D / 공간|창작·디자인 사례|연구·논문|LLM / 모델|에이전트 / 코딩","source_name":"","published_at":"","title_ko":"","summary_ko":"2~3문장","insight_ko":"1~2문장","image_prompt_ko":"16:9 관련 이미지 프롬프트","image_url":"","media_url":"","media_type":"","quality":"A|B|C","url":"","links":{{}}}}]}}
수집 기준: 발행일 기준 최근 10일 안의 주요 토픽
기간 데이터: {json.dumps(period, ensure_ascii=False)}
후보: {json.dumps(slim, ensure_ascii=False)}
""".strip()


def run_claude(prompt: str, model: str, timeout: int) -> str:
    cmd = [
        'claude', '-p', prompt,
        '--model', model,
        '--output-format', 'text',
        '--max-budget-usd', '1.00',
        '--no-session-persistence',
    ]
    proc = subprocess.run(cmd, cwd=str(BASE), text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f'claude failed rc={proc.returncode} stdout={proc.stdout[-2000:]} stderr={proc.stderr[-2000:]}')
    return proc.stdout


def extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def validate_public(public: dict) -> None:
    if not isinstance(public, dict) or not isinstance(public.get('items'), list):
        raise ValueError('LLM output missing items list')
    for i, item in enumerate(public['items']):
        for key in ['category', 'source_name', 'published_at', 'title_ko', 'summary_ko', 'insight_ko', 'image_prompt_ko', 'url']:
            if not item.get(key):
                raise ValueError(f'item {i} missing {key}')
        bad = ['candidate_count', 'score_breakdown', 'USER_ACTION_REQUIRED', 'source_reports', 'adapter', 'selected_reason']
        joined = (item.get('title_ko','') + ' ' + item.get('summary_ko',''))
        hits = [x for x in bad if x in joined]
        if hits:
            raise ValueError(f'item {i} contains internal terms: {hits}')


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def render_html(public: dict) -> str:
    period = public.get('period', {})
    start = parse_dt(period.get('start')) or datetime.now()
    end = parse_dt(period.get('end')) or datetime.now()
    by_category = defaultdict(list)
    for item in public.get('items', []):
        by_category[item.get('category', '주요 뉴스')].append(item)
    sections = []
    for category in CATEGORIES:
        items = by_category.get(category) or []
        if not items:
            continue
        body = ''.join(item_html(item) for item in items)
        sections.append(f'<section><h2>{esc(category)}</h2>{body}</section>')
    return page(start, end, ''.join(sections))


def item_html(item: dict) -> str:
    published = parse_dt(item.get('published_at'))
    date = published.strftime('%Y.%m.%d') if published else ''
    links = [f'<a href="{esc(item.get("url"))}">원문</a>']
    for label, url in (item.get('links') or {}).items():
        links.append(f'<a href="{esc(url)}">{esc(label)}</a>')
    return (
        '<article class="item">'
        + f'<div class="meta"><b>{esc(item.get("source_name"))}</b><span>{esc(date)}</span></div>'
        + f'<h3>{esc(item.get("title_ko"))}</h3>'
        + image_html(item)
        + f'<p>{esc(item.get("summary_ko"))}</p>'
        + f'<p class="application"><b>인사이트:</b> {esc(item.get("insight_ko"))}</p>'
        + f'<div class="links">{" ".join(links)}</div>'
        + '</article>'
    )


def image_html(item: dict) -> str:
    media_url = item.get('media_url') or item.get('image_url') or ''
    media_type = item.get('media_type') or ('image' if item.get('image_url') else '')
    prompt = item.get('image_prompt_ko') or ''
    title = item.get('title_ko') or ''
    if media_url and media_type == 'video':
        label = item.get('media_label') or '원문/공개 영상에서 가져온 영상'
        youtube_id = youtube_video_id(media_url)
        if youtube_id:
            return f'<figure class="thumb"><iframe src="https://www.youtube.com/embed/{esc(youtube_id)}" title="{esc(title)}" loading="lazy" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe><figcaption>{esc(label)}</figcaption></figure>'
        if media_url.lower().split('?',1)[0].endswith('.mp4'):
            return f'<figure class="thumb"><video src="{esc(media_url)}" controls muted playsinline preload="metadata"></video><figcaption>{esc(label)}</figcaption></figure>'
        return f'<figure class="thumb"><a class="video-card" href="{esc(media_url)}">영상 열기</a><figcaption>{esc(label)}</figcaption></figure>'
    if media_url:
        label = item.get('media_label') or media_label_for_url(media_url)
        return f'<figure class="thumb"><img src="{esc(media_url)}" alt="{esc(title)}" loading="lazy"><figcaption>{esc(label)}</figcaption></figure>'
    if prompt:
        asset = prompt_asset(title, prompt, item.get('category') or '')
        return f'<figure class="thumb"><img src="{esc(asset)}" alt="{esc(title)}" loading="lazy"><figcaption>관련 이미지를 생성했습니다</figcaption></figure>'
    return ''



def prompt_asset(title: str, prompt: str, category: str = '') -> str:
    """Create a lightweight SVG visual card when no official image/video is available."""
    h = hashlib.sha1((title + '|' + prompt).encode('utf-8')).hexdigest()[:12]
    assets = OUTROOT / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    path = assets / f'prompt-visual-{h}.svg'
    if not path.exists():
        palette = [
            ('#171717', '#7c3aed', '#06b6d4'),
            ('#111827', '#ef4444', '#f59e0b'),
            ('#0f172a', '#2563eb', '#22c55e'),
            ('#1f2937', '#ec4899', '#8b5cf6'),
        ][int(h[0], 16) % 4]
        bg, a, b = palette
        title_lines = textwrap.wrap(title, width=28)[:3]
        prompt_lines = textwrap.wrap(prompt, width=42)[:4]
        def tspan(lines, x, y, size, weight='500', fill='#ffffff'):
            out=[]
            for i,line in enumerate(lines):
                out.append(f'<text x="{x}" y="{y+i*(size+8)}" font-size="{size}" font-weight="{weight}" fill="{fill}" font-family="Apple SD Gothic Neo, Noto Sans KR, Arial, sans-serif">{html.escape(line)}</text>')
            return '\n'.join(out)
        parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675">',
            '<defs>',
            f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{bg}"/><stop offset="0.55" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient>',
            '<filter id="blur"><feGaussianBlur stdDeviation="36"/></filter>',
            '</defs>',
            '<rect width="1200" height="675" fill="url(#g)"/>',
            '<circle cx="940" cy="120" r="210" fill="#ffffff" opacity="0.13" filter="url(#blur)"/>',
            '<circle cx="170" cy="560" r="260" fill="#ffffff" opacity="0.12" filter="url(#blur)"/>',
            '<rect x="70" y="70" width="1060" height="535" rx="34" fill="#ffffff" opacity="0.12" stroke="#ffffff" stroke-opacity="0.28"/>',
            f'<text x="96" y="124" font-size="24" font-weight="800" fill="#e5e7eb" font-family="Apple SD Gothic Neo, Noto Sans KR, Arial, sans-serif">창작자를 위한 AI 다이제스트 · {html.escape(category or "AI")}</text>',
            tspan(title_lines, 96, 220, 48, '850'),
            '<rect x="96" y="408" width="1008" height="1" fill="#ffffff" opacity="0.35"/>',
            '<text x="96" y="462" font-size="20" font-weight="800" fill="#dbeafe" font-family="Apple SD Gothic Neo, Noto Sans KR, Arial, sans-serif">시각 프롬프트</text>',
            tspan(prompt_lines, 96, 505, 24, '500', '#f8fafc'),
            '</svg>',
        ]
        path.write_text('\n'.join(parts), encoding='utf-8')
    return f'assets/{path.name}'



def media_label_for_url(url: str) -> str:
    url = url or ''
    if url.startswith('assets/diffusiongemma-visual-reasoning') or 'prompt-visual-' in url:
        return '관련 이미지를 생성했습니다'
    return '원문 페이지에서 가져온 이미지'

def youtube_video_id(url: str) -> str:
    m = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{6,})', url or '')
    return m.group(1) if m else ''


def digest_entries() -> list[dict]:
    """User-facing digest archive list. Keep only canonical/public items here."""
    entries = [
        {
            'date': '2026-06-26',
            'title': '2026.06.26 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/',
        },
        {
            'date': '2026-06-25',
            'title': '2026.06.25 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-25.html',
        },
        {
            'date': '2026-06-22',
            'title': '2026.06.22 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-22.html',
        },
        {
            'date': '2026-06-21',
            'title': '2026.06.21 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-21.html',
        },
        {
            'date': '2026-06-20',
            'title': '2026.06.20 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-20.html',
        },
        {
            'date': '2026-06-19',
            'title': '2026.06.19 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-19.html',
        },
        {
            'date': '2026-06-18',
            'title': '2026.06.18 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-18.html',
        },
        {
            'date': '2026-06-17',
            'title': '2026.06.17 다이제스트',
            'url': 'https://art-seongha-art.github.io/creator-ai-digest/archive/weekly-ai-digest-v2-2026-06-17.html',
        },
    ]
    return sorted(entries, key=lambda x: x['date'], reverse=True)

def digest_nav_html(current: datetime) -> str:
    current_key = current.strftime('%Y-%m-%d')
    entries = digest_entries()
    asc = sorted(entries, key=lambda x: x['date'])
    prev_entry = next((e for e in reversed(asc) if e['date'] < current_key), None)
    next_entry = next((e for e in asc if e['date'] > current_key), None)
    buttons = []
    if prev_entry:
        buttons.append(f'<a class="navbtn" href="{esc(prev_entry["url"])}">← 이전 다이제스트 보기</a>')
    else:
        buttons.append('<span class="navbtn disabled" aria-disabled="true">← 이전 다이제스트 없음</span>')
    if next_entry:
        buttons.append(f'<a class="navbtn" href="{esc(next_entry["url"])}">다음 다이제스트 보기 →</a>')
    else:
        buttons.append('<span class="navbtn disabled" aria-disabled="true">다음 다이제스트 준비 중 →</span>')
    rows = []
    for e in entries:
        active = ' active' if e['date'] == current_key else ''
        rows.append(f'<li class="digest-row{active}"><span>{esc(e["date"].replace("-", "."))}</span><a href="{esc(e["url"])}">{esc(e["title"])}</a></li>')
    return '<nav class="digest-nav" aria-label="이전 다이제스트">' + '<div class="digest-nav-buttons">' + ''.join(buttons) + '</div>' + '<h2>이전 다이제스트</h2><ul>' + ''.join(rows) + '</ul></nav>'

def page(start: datetime, end: datetime, body: str) -> str:
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>창작자를 위한 AI 다이제스트 - {end.strftime('%Y.%m.%d')}</title><style>
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
p{{margin:0 0 10px;color:#333;line-height:1.62}}
.application{{background:#fff;border-left:4px solid #21528d;padding:10px 12px;color:#222}}
.thumb{{margin:10px 0 12px;border:1px solid #deded6;background:#fff;border-radius:12px;overflow:hidden}}
.thumb img,.thumb video{{display:block;max-width:100%;width:auto;height:auto;max-height:70vh;object-fit:contain;background:#000;margin:0 auto}}
.thumb iframe{{display:block;width:100%;aspect-ratio:16/9;border:0;background:#000}}
.thumb figcaption{{font-size:12px;color:#777;padding:7px 10px;border-top:1px solid #ecece6}}
.digest-nav{{margin-top:34px;padding-top:20px;border-top:3px solid #151515}}
.digest-nav-buttons{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}
.navbtn{{display:inline-block;background:#151515;color:#fff;text-decoration:none;border-radius:10px;padding:10px 13px;font-weight:800}}
.navbtn.disabled{{background:#d7d7d0;color:#666}}
.digest-nav ul{{list-style:none;margin:0;padding:0;border:1px solid #deded6;border-radius:14px;overflow:hidden;background:#fff}}
.digest-row{{display:flex;gap:14px;align-items:center;padding:12px 14px;border-bottom:1px solid #ecece6}}
.digest-row:last-child{{border-bottom:0}}
.digest-row span{{font-weight:900;color:#21528d;min-width:96px}}
.digest-row a{{color:#151515;text-decoration:none;font-weight:700}}
.digest-row.active{{background:#f0f6ff}}
.thumb.placeholder div{{min-height:120px;padding:18px;color:#555;background:linear-gradient(135deg,#f0f3f7,#ffffff);font-size:14px;line-height:1.55}}
.links a{{display:inline-block;border:1px solid #d4d4cc;border-radius:8px;padding:6px 9px;margin-right:6px;font-size:13px;color:#222;text-decoration:none;background:#fff}}
@media(max-width:760px){{header{{display:block}}h1{{font-size:36px}}.period{{margin-top:10px}}}}
</style></head><body><main class="wrap"><header><h1>창작자를 위한 AI 다이제스트</h1><div class="period">발행일 {end.strftime('%Y.%m.%d')}</div></header>{body}{digest_nav_html(end)}</main></body></html>'''


def esc(value: object) -> str:
    return html.escape(str(value or ''), quote=True)


if __name__ == '__main__':
    raise SystemExit(main())
