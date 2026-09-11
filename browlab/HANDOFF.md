# BrowLab 핸드오프 문서 (로컬 작업용)

작성일: 2026-09-11 · 작성 세션: Claude Code 원격 세션 (`https://claude.ai/code/session_013K3VWBmC6gMtsyVn2maKrG`)

이 문서는 **로컬 환경(사용자 PC)에서 Claude Code / Codex 등 에이전트가 이어서 작업**할 수 있도록,
지금까지 한 일·확인한 사실·확인하지 못한 것·다음 할 일을 빠짐없이 적은 것입니다.
원격 세션은 OpenAI 서버 접근이 막혀 있어 **실제 이미지 생성은 한 번도 실행하지 못했습니다.**
로컬에서 가장 먼저 할 일은 그 검증입니다(6장).

---

## 0. 2026-09-12 로컬·4090 검증 결과 (최신 — 아래 2·6장보다 우선)

| 항목 | 결과 |
| --- | --- |
| 환경 | 맥 Python 3.11.15 / codex 0.153.2 · 4090 Python 3.12.3 / codex 0.153.3 (둘 다 ChatGPT 로그인). 테스트 59개 통과(웹 10개 포함) |
| 6.2 Codex 실제 생성 | **미완.** (a) `codex exec -C` 에 상대경로를 넘기던 버그로 즉시 실패(`No such file or directory (os error 2)`) → 절대경로로 수정, 회귀 테스트 추가. (b) 계정의 Codex 이미지 한도 소진: `ERROR: You've hit your usage limit ... try again at Sep 15th, 2026 10:22 AM` (맥·4090 동일 계정). 4090 systemd 서비스 안에서 codex 실행·인증·프롬프트 전달까지는 확인(같은 오류로 실패). **9/15 이후 또는 크레딧 구매 후 6.2 의 1~5 항목 재검증** |
| 6.3 API | 키 입력·$10 충전 완료(9/12 02시). **gpt-image-2.5-flare/sunburst·gpt-image-2 는 `429 ... (for limit gpt-image) ... Limit 0`** — OpenAI 조직 한도 0(결제 반영/조직 인증 확인 필요, platform.openai.com → Settings → Organization → Limits). `gpt-image-1-mini` 만 열려 있어 그 모델로 파이프라인 전 단계 실측 통과: 생성(50초, 1024x1536)→시트(mediapipe IPD 300px)→restyle 2스타일(마스크 편집·합성·비교/눈썹구역 시트, 110초)→보정→사용량 집계. 산출물 `~/browlab_data/_tests_20260912/jobs/`. 4090 감시기 `/tmp/watch_and_test.sh` 가 2.5 가 열리면 `/tmp/browlab_api_test_25.py` 로 자동 재시험(로그 `~/browlab_data/_tests_20260912/watch.log`). 기본 모델은 2.5 그대로(연구자 지시: 하위 모델 사용 금지) |
| 관찰(품질) | 생성 얼굴: 정면·흰 배경·안경 없음은 지켜지나 **눈썹이 프롬프트만큼 듬성듬성하지 않음**(mini 기준, 2.5 로 재확인 후 prompts.py Eyebrows/Constraints 강화). mini 편집은 마스크 영역 피부톤이 주황빛으로 변함(합성 뒤에도 보임) → 2.5 sunburst 결과로 판단, 필요 시 합성 전 색 맞춤 검토 |
| 6.4 restyle / 6.5 codex 랜드마크 | 실제 사진 없음 → 미검증(웹 테스트는 합성 이미지 + manual/none 랜드마크만) |
| 웹 UI | `python -m browlab web` + `browlab/index.html`(컴팩트 UI: 칩 4줄 + 고급 설정 접기, 작업 목록/PDF, 사진 업로드, 상단 `$지출/$충전` 사용량·설정 창, 외모 기본 한국인). 설계 캔버스: https://claude.ai/code/artifact/15a0bc8f-b5d1-4985-9c96-af6e28ee16de |
| 4090 상시 서비스 | `~/.config/systemd/user/browlab.service` (0.0.0.0:8177, `--base-path /browlab`), 데이터 `~/browlab_data`, 비밀번호 = 허브 비밀번호(`~/.config/browlab/password.txt`) |
| 외부 접근 | `https://seongha-art-4090.tailc4181c.ts.net/browlab/` (Funnel 443 `/browlab`, 공개 DNS 경로로 200 확인). **주의**: `tailscale serve` 로 경로를 추가하면 그 포트 Funnel 이 꺼짐 — 반드시 `sudo tailscale funnel --bg --https=443 --set-path ...` (README 8장) |
| 남은 일 | 6.2(9/15 이후) → 6.3(키) → 6.4 실제 사진(본인 동의) → 6.5 → 눈썹 희소성 프롬프트 튜닝 → 인쇄 배율 실측 |

## 1. 요구사항 (사용자 원문 요약)

- A4 용지에 **실제 사람 얼굴 크기(1:1)** 로 인쇄되는 이미지를 만드는 프로그램.
- 목적: **눈썹 문신(반영구) 디자인 연습**. 눈썹 모량이 부족하거나, 형태가 불확실하거나, 연한 얼굴에
  얼굴형/스타일에 맞춰 디자인을 연습.
- 이미지 생성은 **Codex image CLI 최신 버전**으로. 흰 배경 포토리얼 스타일을 기본으로 삼고,
  **10대~70대, 다양한 얼굴 생김새의 남녀, 눈썹이 부족한** 얼굴. 얼굴형·나이·성별을 **직접 조절하거나 랜덤**.
- 확장: **사람 얼굴 사진을 넣으면 눈썹 부분만 검출**해서 얼굴은 유지하고 **눈썹만 여러 스타일로** 생성.
- 후속 질문: API 요금은 얼마인지, API를 먼저 연결할지 → 5장 참고.

---

## 2. 현재 상태 요약

| 항목 | 값 |
| --- | --- |
| 저장소 | `art-seongha-art/creator-ai-digest` |
| 브랜치 | `claude/eyebrow-tattoo-design-tool-q5twu7` (main에서 분기, 충돌 없음) |
| 커밋 | `679fcba` "Add BrowLab: life-size A4 practice sheets for eyebrow tattoo design" (+ 이 문서 커밋) |
| PR | https://github.com/art-seongha-art/creator-ai-digest/pull/1 (**draft**, 리뷰/CI 없음, mergeable clean) |
| 코드 위치 | `browlab/` 패키지, `tests/test_browlab.py`, `requirements-browlab.txt`, 루트 `README.md` 안내 문단 |
| 테스트 | `python -m unittest discover -s tests` → 48 tests OK (mediapipe 실사진 테스트 1개는 환경변수 없으면 skip) |
| 미검증 | 실제 이미지 생성(Codex `$imagegen`, OpenAI Images API), Codex 비전 랜드마크, API 마스크 편집 |

`portfolio` 저장소에는 아무것도 넣지 않았습니다(같은 이름의 브랜치만 원격에 존재). Python 파이프라인과
Codex 이미지 생성 코드가 이미 있는 creator-ai-digest 쪽을 택했습니다. 별도 저장소로 옮겨도 됩니다
(`browlab/`, `tests/test_browlab.py`, `requirements-browlab.txt` 만 복사하면 독립 동작).

---

## 3. 구현 내용

### 3.1 CLI (`python -m browlab ...`, 저장소 루트에서 실행)

| 명령 | 역할 |
| --- | --- |
| `generate` | 조건/무작위 조합으로 얼굴 프롬프트 생성 → 이미지 생성 → A4 1:1 시트(PNG+PDF) → `manifest.json` |
| `sheet` | 기존 얼굴 이미지(들)를 A4 1:1 시트로. `--layout face/browzone/both`, `--guides`, `--pupils` 수동 배율 |
| `restyle` | 사진 → 편집용 크기 정리 → 눈썹 마스크 → 스타일별 눈썹 편집 → 눈썹 영역만 원본에 합성 → 비교/1:1 시트 |
| `calibrate` | 프린터 배율 확인용 눈금 시트(150 mm 자, 50 mm 정사각형, 평균 동공 간격) |
| `presets` | 선택값 한국어 목록 |

공통 옵션: `--backend codex|api|manual`, `--model`, `--quality`, `--size`(generate, api용), `--timeout`, `--codex-bin`,
`--codex-arg`(반복), `--dry-run`, `--dpi`, `--ipd-mm`, `--layout`, `--copies`, `--guides`, `--title`, `--font`,
`--no-pdf`, `--image-height-mm`, `--landmarks auto|mediapipe|codex|manual|none`, `--landmark-model`, `--no-download`.

기본 출력 폴더: `output/browlab/<faces|sheets|restyle_<파일명>|calibrate>_<YYYYmmdd_HHMMSS>/` (`output/` 는 .gitignore).

### 3.2 모듈

| 파일 | 내용 |
| --- | --- |
| `browlab/presets.py` | 나이대(10s=15~19 … 70s=70~79), 성별, 얼굴형 7종(oval/round/square/long/heart/diamond/triangle, 한글 라벨 + 프롬프트 문구 + 얼굴형별 눈썹 추천 문구), 눈썹 상태 9종(sparse/faint/patchy/missing_tail/asymmetric/overplucked/undefined/scar_gap/almost_none), 외모 11종(가중치: korean 8, japanese/chinese/southeast_asian/european 1.5, 나머지 1), 눈썹 스타일 12종, 눈썹 색 7종, 나이별 피부 묘사, IPD 기본값(여 62 / 남 64 / 10대 60 / 미상 63 mm) |
| `browlab/prompts.py` | `FaceSpec`(age, gender, face_shape, brow_condition, ethnicity, seed, notes), `make_specs(count, seed, **overrides)` 시드 재현, `build_face_prompt`(imagegen 스킬의 라벨 구조: Use case/Asset type/Primary request/Scene/Subject/Eyebrows/Style/Composition/Lighting/Color/Constraints/Avoid), `build_restyle_prompt`(identity-preserve, 눈썹 배치 규칙, 불변 조건) |
| `browlab/backends.py` | `CodexBackend`(`codex exec` + `$imagegen`), `OpenAIBackend`(openai SDK `images.generate/edit`), `ManualBackend`(프롬프트 `.prompt.txt` 저장), `make_backend()` |
| `browlab/landmarks.py` | `FaceLandmarks`(동공·눈꼬리/앞머리·윗눈꺼풀·콧방울·코끝·턱·이마·볼·눈썹 폴리곤; **right_* = 피사체 오른쪽 = 이미지 왼쪽**), `detect_mediapipe`, `detect_codex`(비전 JSON, `--output-schema`), `from_pupils`(동공 2점 + 평균 비율), `detect()` 디스패처(auto: mediapipe → codex) |
| `browlab/masks.py` | `brow_region_mask`(눈썹 bbox를 IPD 배수로 확장: 좌우 0.16, 위 0.40, 아래 0.12, 윗눈꺼풀 위 0.06 IPD에서 절단, 둥근 사각형), `api_mask_image`(RGBA, 알파 0 = 편집 영역), `guide_overlay`(빨간 반투명), `composite_brows`(가우시안 페더 합성), `prepare_for_edit`(16의 배수 중앙 크롭, 긴 변 ≤ 2048, 픽셀 655,360~8,294,400), `validate_gpt_image_size` |
| `browlab/sheet.py` | A4 = 2480×3508 @300dpi. `life_size_scale`(IPD_mm×px/mm ÷ IPD_px; 랜드마크 없으면 이미지 높이 320 mm 가정), `place_face`(머리 위 0.95 IPD·목 아래 0.35 IPD 포함해 중앙 배치, 안 들어가면 눈썹·눈·턱 우선), `_draw_guides`(콧방울→눈앞머리/홍채 바깥(동공 ± 0.095 IPD)/눈꼬리 점선 + 동공 수평선), 헤더(제목·캡션·우측 정보), 푸터(100 mm 자 1 mm 눈금, 20 mm 정사각형, 메모 최대 4줄), `compose_browzone_sheet`(눈썹 위 0.25 IPD ~ 동공 아래 0.25 IPD 구역, 페이지 자동 분할), `compose_grid_sheet`(2열, ≤4개면 2행, 아니면 3행), `calibration_sheet`, `save_pages`(PNG dpi 메타 + 다중 페이지 PDF `resolution=dpi`) |
| `browlab/fonts.py` | 한글 글꼴 후보 탐색(맥 AppleSDGothicNeo.ttc / AppleGothic.ttf, 윈도 malgun.ttf, 리눅스 NanumGothic / NotoSansCJK), `BROWLAB_FONT`, `--font`; 없으면 DejaVu/기본 글꼴 + 영어 라벨(`SheetFont.t(ko, en)`) |
| `browlab/cli.py` | argparse, 명령 구현, manifest 기록(얼굴마다 즉시 저장), 한국어 로그 `[browlab] ...` |
| `tests/test_browlab.py` | 48개: 프리셋/프롬프트 결정성/크기 검증/랜드마크 기하·JSON 변환/마스크·합성/시트 크기·dpi·PDF/가짜 `codex` 실행 파일/가짜 OpenAI 클라이언트/CLI(dry-run, sheet --pupils, manual generate, restyle manual, calibrate)/`BROWLAB_TEST_FACE` 실사진 mediapipe(옵션) |

### 3.3 Codex 백엔드가 실제로 실행하는 것

```bash
codex exec --skip-git-repo-check -s workspace-write -C <출력폴더> -o <출력폴더>/.browlab_last_message.txt --color never [-i 이미지 ...] [-m 모델] [--codex-arg들] -
```

- 프롬프트는 **표준 입력**으로 전달. 첫 줄 `$imagegen`, 이어서 "내장 image_gen 도구로 정확히 1장, 질문 금지,
  CLI fallback 금지, 세로 2:3 최고 해상도 PNG, 생성 후 `$CODEX_HOME/generated_images` 의 최신 파일을
  현재 작업 폴더에 `<파일명>` 으로 복사, 마지막에 `SAVED: <파일명>` 한 줄" 지시 + 이미지 스펙.
- 결과 수집 순서: (1) 출력 경로에 시작 시각 이후 생성된 비어 있지 않은 파일이 있으면 성공 →
  (2) 아니면 `$CODEX_HOME/generated_images/**` 에서 시작 이후 새로 생긴 png/jpg/webp 중 최신을 복사 →
  (3) 없으면 `GenerationError`(rc, 마지막 메시지, stderr 포함).
- `CODEX_HOME` 환경변수가 없으면 `~/.codex`. 타임아웃 기본 900초.
- 편집(`restyle`)은 `-i 00_original.png -i mask_guide.png`(빨간 영역 가이드, `--no-guide-image` 로 생략) 첨부.
- `--landmarks codex` 는 별도 호출: `codex exec --skip-git-repo-check --ephemeral -s read-only -C <임시폴더> -i <이미지> --output-schema schema.json -o out.txt --color never -` (JSON 스키마 `landmarks.LANDMARK_SCHEMA`).

### 3.4 OpenAI API 백엔드

- `openai` SDK. 생성 기본 모델 `gpt-image-2.5-flare`, 편집 기본 `gpt-image-2.5-sunburst`(`--model`, `--edit-model`).
- 생성: `images.generate(model, prompt, n=1, size="1536x2304", quality="high", output_format="png")` → `b64_json` 디코드(`url` 폴백).
- 편집: `images.edit(model, prompt, image=[...], mask=<RGBA PNG>, size="<준비된 이미지 크기>", quality, output_format="png")`.
  `gpt-image-1*` 모델일 때만 `input_fidelity="high"` 추가(2 이후 모델은 항상 고정밀, 옵션 자체가 거부됨).
- `OPENAI_API_KEY` 없으면 `GenerationError`.

---

## 4. 조사로 확인한 사실 (2026-09-11 기준)

### 4.1 Codex CLI 0.154.0 (npm `@openai/codex`)

- 내장 시스템 스킬이 첫 실행 때 `~/.codex/skills/.system/` 에 풀림. `imagegen/SKILL.md`, `references/{cli,image-api,prompting,sample-prompts,codex-network}.md`, `scripts/image_gen.py`, `scripts/remove_chroma_key.py`.
- 두 모드: **내장 `image_gen` 도구(기본, API 키 불필요, ChatGPT 로그인)** / **CLI fallback `scripts/image_gen.py`(`OPENAI_API_KEY` 필요, `generate|edit|generate-batch`, 기본 모델 gpt-image-2, quality medium, size auto, `--mask` 편집 전용, `--force`, `--dry-run`)**.
- 내장 도구는 `$CODEX_HOME/generated_images/...` 에 저장하며, 스킬 문서는 "목적지 인자를 쓰지 말고 생성 후 복사하라"고 지시 → BrowLab 프롬프트도 그렇게 함.
- 내장 편집은 "대화에 보이는 이미지"(첨부 `-i` 포함) 대상. 마스크 등 세부 제어는 CLI fallback 전용.
- `codex features list` 에서 `image_generation stable true`, `view_image stable true` 확인.
- `codex exec` 옵션: `-i/--image`, `-m`, `-s read-only|workspace-write|danger-full-access`, `-C`, `-o/--output-last-message`, `--output-schema`, `--json`, `--ephemeral`, `--skip-git-repo-check`, `--color never`, `-`(stdin 프롬프트).
- gpt-image-2 크기 규칙(스킬 문서): 최대 변 3840, 양변 16의 배수, 장변/단변 ≤ 3, 총 픽셀 655,360~8,294,400, 2560×1440 초과는 experimental. 인기 크기 1024x1024 / 1536x1024 / 1024x1536 / 2048x2048 / 2048x1152 / 3840x2160 / 2160x3840 / auto.
- gpt-image-2 는 `background=transparent` 미지원, `input_fidelity` 미지원(항상 high).

### 4.2 모델·SDK

- openai Python SDK 3.13.0 의 이미지 모델 리터럴: `gpt-image-1`, `gpt-image-1-mini`, `gpt-image-1.5`, `gpt-image-2`, `gpt-image-2-2026-04-21`, `gpt-image-2.5-sunburst(-2026-09-08)`, `gpt-image-2.5-flare(-2026-09-08)`, `chatgpt-image-latest`.
- 2.5 quality 값: `low, medium, high, xhigh, max, auto`. 마스크: 알파 0 인 곳이 편집 영역, PNG, 원본과 동일 크기, 4 MB 미만(문서마다 4 MB / 50 MB 표기가 달라 안전하게 작게).
- GPT Image 2.5 는 2026-09-08 출시. **flare = 빠름(2 대비 지연 50%↓, 일상 생성)**, **sunburst = 정밀 편집 우선(느림)**. ChatGPT/Codex 모든 유료 티어는 "ChatGPT Images 2.5"를 씀. 구독 경로에서는 flare/sunburst 를 고를 수 없음.

### 4.3 mediapipe

- 설치 버전 1.0.1. API: `from mediapipe.tasks.python import vision`, `vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=...), num_faces=1)`, `mp.Image(image_format=mp.ImageFormat.SRGB, data=np_rgb)`, 478점(홍채 468/473 포함).
- 모델 파일: `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task` (3.7 MB). 캐시 `~/.cache/browlab/face_landmarker.task`, 환경변수 `BROWLAB_FACE_MODEL`, `BROWLAB_CACHE_DIR`.
- 리눅스에서는 `libEGL.so.1`, `libGLESv2.so.2` 가 필요했음(`apt-get install libegl1 libgl1 libgles2 libglib2.0-0`). macOS/Windows 는 미확인.
- 사용 인덱스: 동공 468/473, 눈앞머리 133/362, 눈꼬리 33/263, 윗눈꺼풀 159/386, 콧방울 129/358, 코끝 1, 턱 152, 이마 10, 볼 234/454, 오른눈썹 `[70,63,105,66,107,55,65,52,53,46]`, 왼눈썹 `[300,293,334,296,336,285,295,282,283,276]`.
- 테스트 사진(NASA 공개 사진 512px)으로 검출 → A4 시트에서 동공 간 744 px(=63 mm @300dpi) 확인.

### 4.4 원격 환경에서의 검증 이력

- Pillow 12.3.0, numpy 2.4.6, mediapipe 1.0.1, openai 3.13.0, Python 3.11.15, Node 22, codex-cli 0.154.0.
- `codex doctor`: 인증 없음 + OpenAI 엔드포인트 프록시 차단(403) → 실제 생성 불가.
- 가짜 `codex` 실행 파일(생성 폴더에만 파일을 쓰고 복사는 안 함)로 인자·프롬프트·폴백 수집 경로 검증.
- 시트 미리보기(얼굴 1:1, 눈썹 구역 1:1 3회 반복, 배율 확인)를 사람이 눈으로 확인함.

---

## 5. 요금 정리 (사용자 질문에 대한 답, 서드파티 요약 기준)

- API(gpt-image-2 = 2.5 flare/sunburst 동일 요율): 이미지 출력 $30/M 토큰, 이미지 입력 $8/M, 텍스트 입력 $5/M.
- 장당 대략: 1024² low ≈ $0.006, 1024×1536 medium ≈ $0.04~0.05, 1024² high ≈ $0.17~0.21, BrowLab 기본 1536×2304 high ≈ $0.3~0.5(추정). Batch API 50% 할인(BrowLab 미사용). 2.5 의 실제 토큰 소모는 외부 검증 자료 없음.
- 구독(Codex): 추가 요금 없이 ChatGPT 요금제 한도 사용. 이미지 생성은 한도를 3~5배 빨리 소모. Plus(5시간 창 10~100 메시지 수준)는 수십 장 연속 생성 시 한도 도달 가능. Pro 5x $100 / 20x $200.
- 권고: **먼저 Codex 백엔드로 파이프라인 검증 → 대량/정확한 크기/마스크 인페인팅이 필요하면 API 키 추가**(`--backend api --quality medium --size 1024x1536` 이 가성비). 원격 세션에서 공식 가격 페이지는 차단되어 있었으니 결제 전 platform.openai.com 에서 재확인.

---

## 6. 로컬에서 해야 할 일 (우선순위 순)

### 6.1 준비

```bash
git fetch origin && git checkout claude/eyebrow-tattoo-design-tool-q5twu7
python3 -m venv .venv && source .venv/bin/activate      # 윈도: .venv\Scripts\activate
pip install -r requirements-browlab.txt
python -m unittest discover -s tests                     # 48 OK 기대
npm install -g @openai/codex && codex login && codex --version   # 0.154+ 
python -m browlab presets
python -m browlab calibrate                              # 인쇄해 자로 확인
```

macOS 에서 mediapipe 설치가 실패하면 `pip install "mediapipe>=0.10"` 로 낮은 버전을 시도하고, 그래도 안 되면
`--landmarks codex` 또는 `--landmarks manual --pupils x1,y1,x2,y2` 로 우회. (0.10.x 도 `mediapipe.tasks.python.vision` API 동일.)

### 6.2 Codex 백엔드 실제 생성 검증 ← 가장 중요

```bash
python -m browlab generate -n 1 --seed 1 --age 30s --gender female --face-shape oval --brow-condition sparse --guides --layout both
```

확인할 것:

1. `codex exec` 가 대화 없이 끝나고 `output/browlab/faces_*/face_01_*.png` 가 생기는지.
   - 안 생기면 `GenerationError` 메시지의 마지막 메시지/stderr 를 보고 원인 분류:
     a. 에이전트가 `image_gen` 도구를 못 씀(계정/환경) → `--backend api` 로 전환 안내.
     b. 생성은 됐는데 복사를 안 함 → 폴백 수집이 `~/.codex/generated_images` 에서 가져와야 함. 폴백도 실패하면 실제 저장 경로를 `ls -lt ~/.codex/generated_images` 로 확인하고 `CodexBackend.generated_dir` 수정.
     c. `cp` 가 승인 대기/거부로 막힘 → `--codex-arg=--approve-for-me` 또는 `-c approval_policy=never` 같은 옵션을 `--codex-arg` 로 넘겨 테스트하고, 통하는 조합을 `backends.py` 기본값에 반영.
     d. 프롬프트 첫 줄 `$imagegen` 이 exec 모드에서 스킬을 못 불러오면, 프롬프트 본문에 "Use the built-in image_gen tool" 지시가 이미 있으니 그래도 동작하는지 확인.
2. 생성된 얼굴의 품질: 정면·눈썹 노출·**눈썹이 정말 듬성듬성한지**(모델이 눈썹을 또렷하게 그리는 경향이 있음). 또렷하면 `prompts.py` 의 Eyebrows/Constraints 문구를 강화하거나 `--notes` 로 실험 후 기본 문구를 갱신.
3. 출력 크기·비율: 내장 도구가 세로 2:3 근처의 큰 해상도를 주는지. 1024 급이면 1:1 확대 시 흐릿하므로 프롬프트의 해상도 요청 문구를 조정하거나 API 백엔드 권장.
4. `sheets/face_01_*_A4.pdf` 를 인쇄(배율 100%)해서 100 mm 자와 20 mm 정사각형, 동공 간격(약 62 mm)을 자로 확인.
5. 소요 시간(장당 몇 분인지)을 README 에 기록.

### 6.3 API 백엔드 검증 (키가 있을 때)

```bash
export OPENAI_API_KEY=sk-...
python -m browlab generate -n 1 --seed 2 --backend api --quality medium --size 1024x1536
python -m browlab generate -n 1 --seed 2 --backend api --quality high --size 1536x2304
```

- 2.5 flare 가 `size=1536x2304`, `quality=high` 를 받는지(거부되면 오류 메시지를 보고 `--model gpt-image-2` 로 재시도).
- 응답이 `b64_json` 인지(`url` 이면 폴백 코드가 처리).
- 실제 청구 금액을 대시보드에서 확인해 5장 요금표를 갱신.

### 6.4 restyle 검증 (실제 사진, 본인 동의된 사진으로)

```bash
python -m browlab restyle photo.jpg --styles korean_natural,straight,feathered --color dark_brown            # codex
python -m browlab restyle photo.jpg --backend api --styles all --sheet both                                    # api + 알파 마스크
```

확인할 것:

1. `mask_guide.png` 의 빨간 영역이 눈썹 + 위쪽 여유를 덮고 눈은 안 덮는지(아니면 `--mask-up/--mask-side/--mask-down` 조정 후 기본값 변경).
2. Codex 경로: 결과가 원본 프레이밍을 유지하는지. 프레이밍이 바뀌면 합성(`composite_brows`)이 어긋남 → 프롬프트 불변 조건 강화 또는 `--no-composite` 안내. (합성은 결과를 원본 크기로 리사이즈 후 마스크 영역만 붙임.)
3. API 경로: `images.edit` 이 `mask` + `size=<원본 크기>` 를 받는지, 출력 크기가 입력과 같은지.
4. `sheet_browzone*.pdf` 로 스타일 비교가 실물 크기로 잘 보이는지.

### 6.5 Codex 비전 랜드마크 검증 (mediapipe 없을 때의 대안)

```bash
python -m browlab sheet photo.jpg --landmarks codex --save-landmarks
```

- `--output-schema` 로 JSON 이 나오는지, 좌표 오차가 IPD 5% 이내인지(mediapipe 결과와 비교).
- 실패하면 `landmarks.detect_codex` 의 프롬프트/스키마 수정.

### 6.6 마무리

- 검증 결과를 `browlab/README.md`(문제 해결 표)와 PR 본문에 반영, 필요 시 기본값 수정.
- 같은 브랜치에 커밋·푸시하면 PR #1 이 갱신됨. **force-push 금지**(원격 세션이 PR 을 구독 중).
- 준비되면 draft 해제 후 머지.

---

## 7. 알려진 제약·설계 결정

- **1:1 배율의 정의**: AI 얼굴에는 실제 크기가 없으므로 동공 간 거리(IPD)를 평균값으로 맞춤. 가이드선의 홍채 바깥 지점은 동공 ± 0.095 IPD 로 근사.
- **얼굴이 A4 에 다 안 들어가면** 머리 윗부분/목을 잘라내고 눈썹·눈·턱을 우선 배치(`place_face`). 여성 성인 얼굴 기준 대개 다 들어감.
- **`prepare_for_edit`** 는 16의 배수를 위해 최대 15 px 중앙 크롭 + 긴 변 2048 제한. `restyle` 결과물은 모두 이 "준비된 이미지" 기준.
- **외모 무작위 가중치**는 한국인 비중을 높게 둠(`--ethnicity any` 로 균등). 10대는 15~19세로 제한.
- **Codex 백엔드는 크기를 지정할 수 없음**(프롬프트 요청만). 정확한 해상도가 필요하면 API.
- **테스트는 네트워크를 쓰지 않음**. Pillow 없으면 모듈 전체 skip(CI 워크플로가 의존성을 설치하지 않기 때문).
- 시트의 한글은 시스템 글꼴 탐색에 의존. 맥은 AppleSDGothicNeo.ttc(인덱스 0)로 동작할 것으로 예상하나 미확인.
- `codex exec` 를 `workspace-write` 로 실행하므로 에이전트가 출력 폴더 안에 파일을 만들 수 있음. 출력 폴더를 프로젝트 외부(예: `~/BrowLab/out`)로 두면 안전.
- 실제 고객 사진은 OpenAI 로 전송됨(동의 필요). README 에 명시.

---

## 8. 백로그 (검증 후 고려)

1. 생성 품질 튜닝: 눈썹 희소성 강화 문구, 나이/얼굴형 반영도 평가, 얼굴형별 프롬프트 세부화.
2. `generate --backend api` 에 Batch API(50% 할인) 옵션, `--n` 변형 생성.
3. 인쇄 보정: 인쇄 후 잰 100 mm 자 길이를 입력하면 배율을 자동 보정하는 `--print-correction 0.98` 옵션.
4. 여러 얼굴의 눈썹 구역만 모아 한 장에 인쇄하는 모드(`sheet --layout browzone` 다중 파일 조합).
5. 간단한 GUI(Tkinter 또는 로컬 웹 UI) — 사용자가 "어플"이라 표현함.
6. 눈썹 디자인 결과를 시트 위에 반투명 겹쳐 보는 "정답 비교" 모드(restyle 결과를 1:1 시트에 연하게 인쇄).
7. Codex 비전 랜드마크 정확도가 충분하면 mediapipe 의존 제거 검토.

---

## 9. 로컬 세션 시작용 프롬프트 (복사해서 붙여넣기)

```
저장소 art-seongha-art/creator-ai-digest 의 브랜치 claude/eyebrow-tattoo-design-tool-q5twu7 에 있는
browlab/HANDOFF.md 를 먼저 끝까지 읽어. 그 문서의 6장 순서대로(6.1 준비 → 6.2 Codex 백엔드 실제 생성 검증 →
6.3 API(키 있으면) → 6.4 restyle → 6.5 codex 랜드마크 → 6.6 마무리) 로컬에서 검증하고, 실패하는 부분은 원인을 찾아
코드를 고친 뒤 테스트(python -m unittest discover -s tests)를 통과시키고 같은 브랜치에 커밋·푸시해. force-push 는 하지 마.
바뀐 기본값·발견한 문제·소요 시간·실제 요금은 browlab/README.md 와 HANDOFF.md 에 기록해.
```
