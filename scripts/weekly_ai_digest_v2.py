#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_collect import collect_sources
from digest_fetch import FixtureHttpClient, HttpClient
from digest_render import render_html, serialize_run
from digest_scoring import merge_stories, score_candidates, select_public_items
from digest_utils import parse_dt

def load_env_files(paths: list[Path]) -> None:
    """Load only digest source credentials; do not import unrelated LLM/provider keys."""
    import os
    allowed = {
        "YOUTUBE_API_KEY",
        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT",
        "THREADS_USER_ID", "THREADS_ACCESS_TOKEN", "THREADS_APP_ID", "THREADS_APP_SECRET",
        "META_APP_ID", "META_APP_SECRET",
        "X_BEARER_TOKEN",
    }
    for env_path in paths:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in allowed:
                continue
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

BASE = Path(".")
OUTROOT = Path("docs")
DEFAULT_ENV_FILES = [
    BASE / ".env",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the weekly Korean AI digest.")
    parser.add_argument("--config", default=str(BASE / "config/source_registry.json"))
    parser.add_argument("--output-json", default=str(BASE / "output/latest_candidates.json"))
    parser.add_argument("--output-html", default=str(OUTROOT / "latest.html"))
    parser.add_argument("--archive-dir", default=str(OUTROOT))
    parser.add_argument("--period-days", type=int, default=None)
    parser.add_argument("--now", default="")
    parser.add_argument("--offline-fixtures", default="")
    parser.add_argument("--live-smoke", action="store_true")
    parser.add_argument("--limit-per-source", type=int, default=None)
    parser.add_argument("--skip-llm-curation", action="store_true", help="Do not run the LLM Korean curator stage.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_files(DEFAULT_ENV_FILES)
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    period_days = args.period_days or int(config.get("period_days", 7))
    now = parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=period_days)
    sources = list(config.get("sources", []))
    if args.limit_per_source:
        sources = [_limited_source(source, args.limit_per_source) for source in sources]
    client = FixtureHttpClient(Path(args.offline_fixtures)) if args.offline_fixtures else HttpClient(timeout=8 if args.live_smoke else 20)
    collected, source_reports = collect_sources(sources, client, start_dt, now, now)
    merged = merge_stories(collected)
    scored = score_candidates(merged, now, start_dt, now)
    categories = list(config.get("categories", []))
    selected, empty_sections = select_public_items(scored, categories)
    payload = serialize_run(start_dt, now, scored, selected, source_reports, empty_sections)
    json_path = Path(args.output_json)
    html_path = Path(args.output_html)
    archive_dir = Path(args.archive_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    html = render_html(selected, empty_sections, start_dt, now)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    archive_path = archive_dir / f"weekly-ai-digest-v2-{now.strftime('%Y-%m-%d')}.html"
    archive_path.write_text(html, encoding="utf-8")
    llm_summary = None
    if not args.skip_llm_curation and not args.offline_fixtures:
        llm_summary = _run_llm_curator(json_path, html_path, archive_dir)
    summary = _summary(payload, json_path, html_path, archive_path)
    if llm_summary:
        summary["llm_curator"] = llm_summary
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_llm_curator(json_path: Path, html_path: Path, archive_dir: Path) -> dict[str, object]:
    cmd = [
        "python3", str(BASE / "scripts/llm_curate_digest.py"),
        "--input-json", str(json_path),
        "--output-html", str(html_path),
        "--archive-dir", str(archive_dir),
    ]
    proc = subprocess.run(cmd, cwd=str(BASE), text=True, capture_output=True, timeout=480)
    if proc.returncode != 0:
        raise RuntimeError(f"LLM curator failed: {proc.stderr[-2000:] or proc.stdout[-2000:]}")
    try:
        summary = json.loads(proc.stdout)
    except json.JSONDecodeError:
        summary = {"raw": proc.stdout[-1000:]}
    public_json = Path(str(summary.get("output_json", BASE / "output/latest_public_ko_llm.json")))
    img_cmd = ["python3", str(BASE / "scripts/find_related_images.py"), "--input-json", str(public_json)]
    img_proc = subprocess.run(img_cmd, cwd=str(BASE), text=True, capture_output=True, timeout=180)
    if img_proc.returncode == 0:
        try:
            summary["related_images"] = json.loads(img_proc.stdout)
        except json.JSONDecodeError:
            summary["related_images"] = {"raw": img_proc.stdout[-1000:]}
        rerender_cmd = ["python3", str(BASE / "scripts/llm_curate_digest.py"), "--input-json", str(json_path), "--output-html", str(html_path), "--archive-dir", str(archive_dir), "--render-existing-json", str(public_json)]
        rerender = subprocess.run(rerender_cmd, cwd=str(BASE), text=True, capture_output=True, timeout=120)
        if rerender.returncode != 0:
            summary["related_image_render_error"] = rerender.stderr[-1000:] or rerender.stdout[-1000:]
    else:
        summary["related_images"] = {"error": img_proc.stderr[-1000:] or img_proc.stdout[-1000:]}
    return summary


def _limited_source(source: dict[str, object], limit: int) -> dict[str, object]:
    limited = dict(source)
    limited["max_items"] = min(int(limited.get("max_items", limit)), limit)
    return limited


def _summary(payload: dict[str, object], json_path: Path, html_path: Path, archive_path: Path) -> dict[str, object]:
    reports = payload.get("source_reports", [])
    status_counts: dict[str, int] = {}
    if isinstance(reports, list):
        for report in reports:
            if isinstance(report, dict):
                status = str(report.get("status", "unknown"))
                status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "candidate_count": payload.get("candidate_count", 0),
        "selected_count": payload.get("selected_count", 0),
        "source_status_counts": status_counts,
        "json": str(json_path),
        "html": str(html_path),
        "archive": str(archive_path),
    }


if __name__ == "__main__":
    raise SystemExit(main())
