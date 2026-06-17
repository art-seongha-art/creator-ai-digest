# 창작자를 위한 AI 다이제스트

예술·디자인·교육·콘텐츠 현장에서 바로 참고할 수 있는 AI 도구/사례/연구를 매일 모아 공개하는 다이제스트 파이프라인입니다. 발행일 기준 최근 10일 안의 주요 토픽을 쉽고 친근한 설명으로 정리합니다.

## 원칙

- 특정 개인을 대상으로 하지 않는 공개 다이제스트입니다.
- 창작자, 교육자, 디자이너, 콘텐츠 제작자, 연구자, 기획자가 바로 이해하고 써먹을 수 있는 항목을 우선합니다. 전문용어보다 ‘어디에 써볼 수 있는지’를 먼저 설명합니다.
- 일반 AI 업계 뉴스보다 이미지, 영상, 음악/오디오, 3D/공간, 로봇/모션, 창작 워크플로우, 저작권/정책, 수업·실습 가능한 도구를 우선합니다.
- 원문 링크와 날짜가 있는 항목만 사용합니다.
- 관련 이미지/영상이 있으면 먼저 사용하고, 없으면 생성 이미지를 보조로 사용할 수 있습니다.

## 공개 페이지

GitHub Pages를 켜면 다음 파일이 사이트의 첫 화면이 됩니다.

- `docs/index.html`
- `docs/latest.json`
- `docs/assets/`

## 실행

```bash
python3 scripts/weekly_ai_digest_v2.py \
  --period-days 10 \
  --output-json docs/latest_candidates.json \
  --output-html docs/index.html \
  --archive-dir docs/archive \
  --limit-per-source 4
```

LLM 큐레이션을 건너뛰고 규칙 기반 HTML만 만들려면:

```bash
python3 scripts/weekly_ai_digest_v2.py \
  --period-days 10 \
  --output-json docs/latest_candidates.json \
  --output-html docs/index.html \
  --archive-dir docs/archive \
  --limit-per-source 4 \
  --skip-llm-curation
```

## GitHub Actions

`.github/workflows/daily-digest.yml`가 매일 실행되어 `docs/`를 갱신하고 커밋합니다.

LLM 기반 한국어 큐레이션이나 이미지 생성을 붙이려면 별도 secret을 추가해 확장하세요. 기본 공개 버전은 외부 Python 패키지 없이 표준 라이브러리만 사용합니다.
