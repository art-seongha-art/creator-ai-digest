#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path('.')
OUTROOT = Path('docs')

MEDIA_PATTERNS = [
    ('video', r'<meta[^>]+property=["\']og:(?:video|video:url|video:secure_url)["\'][^>]+content=["\']([^"\']+)["\']'),
    ('video', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:(?:video|video:url|video:secure_url)["\']'),
    ('video', r'<meta[^>]+name=["\']twitter:player["\'][^>]+content=["\']([^"\']+)["\']'),
    ('video', r'<video[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']'),
    ('video', r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']'),
    ('video', r'([A-Za-z0-9_./:%?=&-]+\.mp4)'),
    ('image', r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'),
    ('image', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'),
    ('image', r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']'),
    ('image', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']'),
]

LEGACY_IMG_PATTERNS = [
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
]


def parse_args():
    p=argparse.ArgumentParser(description='Find related source images for public digest items using og:image/twitter:image.')
    p.add_argument('--input-json', default=str(BASE/'output/latest_public_ko_llm.json'))
    p.add_argument('--output-json', default='')
    p.add_argument('--timeout', type=int, default=12)
    return p.parse_args()


def main() -> int:
    args=parse_args()
    path=Path(args.input_json)
    data=json.loads(path.read_text(encoding='utf-8'))
    for item in data.get('items', []):
        media=find_media_for_item(item, args.timeout)
        if media:
            item['media_url']=media['url']
            item['media_type']=media['type']
            item['media_source']='source_page_metadata'
            if media['type']=='image':
                item['image_url']=media['url']
                item['image_source']='source_page_metadata'
            else:
                item['image_url']=''
                item['image_source']='video_available'
        else:
            item['media_url']=''
            item['media_type']=''
            item['media_source']='not_found'
            item['image_url']=''
            item['image_source']='not_found'
    out=Path(args.output_json) if args.output_json else path
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output_json': str(out), 'items': len(data.get('items', [])), 'media_found': sum(1 for x in data.get('items', []) if x.get('media_url')), 'images_found': sum(1 for x in data.get('items', []) if x.get('media_type')=='image'), 'videos_found': sum(1 for x in data.get('items', []) if x.get('media_type')=='video')}, ensure_ascii=False, indent=2))
    return 0


def find_media_for_item(item: dict, timeout: int) -> dict:
    urls=[]
    main_url=item.get('url') or ''
    if 'news.google.com/' not in main_url:
        urls.append(main_url)
    links=item.get('links') or {}
    urls.extend(links.values())
    seen=set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        media=find_media(url, timeout)
        if media:
            return media
    return {}


def find_media(url: str, timeout: int) -> dict:
    try:
        req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 ai-digest-image-probe/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype=(r.headers.get('content-type') or '').lower()
            if 'text/html' not in ctype and 'xml' not in ctype and 'application/xhtml' not in ctype:
                return ''
            text=r.read(600000).decode('utf-8','ignore')
    except Exception:
        return ''
    # Prefer video over still images when the source page provides both.
    for media_type, pat in MEDIA_PATTERNS:
        for m in re.finditer(pat, text, flags=re.I|re.S):
            raw = m.group(1)
            if raw in ('image','video') and len(m.groups()) > 1:
                raw = m.group(2)
            media_url=html_unescape(raw.strip())
            if not media_url or media_url.startswith('data:'):
                continue
            media_url=urllib.parse.urljoin(url, media_url)
            if media_type=='image' and not is_usable_image(media_url):
                continue
            if media_type=='video' and not is_usable_video(media_url):
                continue
            return {'type': media_type, 'url': media_url}
    return {}


def is_usable_video(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if 'news.google.com' in host or 'googleusercontent.com' in host or 'gstatic.com' in host:
        return False
    return url.lower().split('?',1)[0].endswith('.mp4') or 'youtube.com' in host or 'youtu.be' in host or 'vimeo.com' in host


def is_usable_image(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if 'googleusercontent.com' in host or 'gstatic.com' in host:
        return False
    if 'arxiv.org' in host and ('arxiv-logo' in url.lower() or '/static/' in url.lower()):
        return False
    if 'news.google.com' in host:
        return False
    return True


def html_unescape(s: str) -> str:
    return (s.replace('&amp;','&').replace('&quot;','"').replace('&#039;',"'").replace('&apos;',"'"))


if __name__ == '__main__':
    raise SystemExit(main())
