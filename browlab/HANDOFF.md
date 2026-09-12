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
| 9/12 03시 mini 전 기능 실측 (연구자 지시) | gpt-image-1-mini 로 웹 API 경유 전 기능 통과: 생성 2명(95초, 크기 1536x2304 요청 → 1024x1536 자동 맞춤) · photo_from 시트(5초) · photo_from 눈썹 3스타일(145초, **정렬 검사가 3장 모두 얼굴 이동 13~18% 검출 → 합성 생략**, 눈 두 겹 없음) · 같은 조건 한 장 더(50초) · 큐 취소 · 보정 · 갤러리 10항목 · 삭제 → 9항목 · 사용량 집계. 실제 청구 $0.48/10요청(대시보드) → mini 단가 표 보정. 산출물은 갤러리에 남김(연구자 확인용) |
| Free tier 원인 (9/12 06:50) | OpenAI 직원 답변(community.openai.com/t/usage-tier-not-upgrading-from-free-tier/1360606): 등급은 새 크레딧 구매 시점에만 재계산, 첫 결제 후 1주일 대기(사기 탐지). 첫 결제 9/12 → **9/19 이후 $5 이상 추가 구매**로 Tier 1 트리거. 지출액과 무관 |
| **9/12 07:54 gpt-image-2.5 열림 (Tier 1)** | 대시보드 "Usage tier 1"(첫 결제 후 약 6시간, 추가 구매 없이, 인증은 아직 in review). 감시기가 자동으로 2.5 전 기능 시험 통과: 생성 1536x2304 high 25초·2127 출력토큰·$0.067(눈썹이 프롬프트대로 듬성듬성함) · 시트 5초 · sunburst 편집 2스타일 70초·$0.11(타일 방식 정렬 오차 1%·합성 성공, 눈 두 겹 없음, 얼굴 동일) · 보정. 산출물 `jobs/20260912_0754*`(restyle 은 trash 로 이동됨). 연구자가 08:06 부터 실사용 중(생성·실사진 시트·restyle) |
| 랜드마크 검사 (9/12 07:10) | `tools/landmark_check.py` 로 생성 얼굴 전부 실측: 23/24 검출(미검출은 눈썹구역 시트=얼굴 아님), 동공·눈꼬리·코·턱 정확, 기울기 ≤1.1% IPD, 눈썹 폭 좌우 0.70~0.74 IPD(차 ≤0.03). A4 시트 위 IPD 731~757 px = 61.9~64.1 mm(목표 여62/남64) → 1:1 배율 확인. 눈썹 폴리곤은 MediaPipe 특성상 눈썹 능선만 따라가 실제보다 얇음(마스크 패딩이 보완). 마스크 위 여유 0.40 IPD 는 이마를 넓게 잡음 → 편집 톤 변화가 크면 0.30 검토 |
| 남은 일 | 2.5 개방 대기(9/19 이후 소액 추가 구매 또는 지원 티켓 · 감시기 자동 시험 · 조직 인증 심사 중) → 6.2 Codex(9/15 10:22 이후) → 6.4 실제 사진(본인 동의) → 6.5 → 눈썹 희소성 프롬프트 튜닝(2.5 결과 보고) → 인쇄 배율 실측 |

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

### 3.2.1 자동 백엔드 (2026-09-12 추가, 원격 세션)

- `--backend auto` 가 CLI·웹 기본값. `backends.FallbackBackend` 가 `[("codex", CodexBackend), ("api:gpt-image-2.5-flare|gpt-image-2.5-sunburst", OpenAIBackend), ("api:gpt-image-2", ...), ("api:gpt-image-1.5", ...), ("api:gpt-image-1", ...), ("api:gpt-image-1-mini", ...)]` 순으로 시도.
- `backends.UNAVAILABLE_PATTERNS` 에 맞는 실패(사용 한도, 로그인, 키 없음, `Limit 0`, 조직 인증, 모델 없음, 쿼터)는 그 실행 동안 sticky skip. 그 외(타임아웃, 5xx, 모더레이션)는 장마다 재시도.
- `make_auto_backend()` 는 codex 실행 파일이 없으면 Codex 단계를, `OPENAI_API_KEY` 가 없으면 API 단계를 빼고 `notes` 로 알림. `--model` 은 API 모델 고정, `--model-chain` / `--edit-model-chain` 으로 순서 변경.
- restyle 은 백엔드별 변형: Codex 에는 빨간 가이드 이미지 + 가이드 프롬프트, 마스크 없음, size auto; API 에는 알파 마스크 + 준비 이미지 크기. `FallbackBackend.edit(..., variants={"codex": {...}})`.
- 결과 기록: `GenResult.fallback`(실패한 시도 목록) → manifest 항목 `backend`/`model`/`fallback`; 웹 `summary()` 의 `used` 목록 → 작업 상세 "사용: api gpt-image-1-mini".
- 로컬 확인 필요: 실제 계정에서 Codex 한도 오류 문구가 `usage limit` 를 포함하는지(HANDOFF 0장 기록상 포함), API `Limit 0` 문구가 그대로인지. 다르면 `UNAVAILABLE_PATTERNS` 에 추가.

### 3.2.2 restyle 얼굴 타일 워크플로 (2026-09-12 추가, 원격 세션)

- 흐름: 원본(EXIF 보정, `downscale_to` 긴 변 ≤ `--max-edge`) → `lm_full` 검출 → `masks.face_tile_box`(눈 중심, 2:3, 머리 위 0.9 IPD·턱 아래 0.35 IPD, 사진 밖으로 나가면 축소·이동) → `crop_tile` → `00_face_tile.png`(기본 1024×1536) → 타일에서 랜드마크 재검출(manual 이면 `landmarks_to_tile` 변환) → 마스크/가이드는 타일 기준 → 편집 → `_align_edit`: 편집 결과 랜드마크 → `similarity_from_pupils`(동공 2점으로 이동·배율·회전) → 5% 이내 그대로 / `--align-max`(0.45) 이내 `warp_similarity` 보정 / 초과 시 합성 생략(`align.status = misaligned`) → `match_tone`(마스크 둘레 링의 평균색 차이만큼 보정, ±40) → `composite_brows` → `paste_back`(마스크 영역만 원본 사진에 되붙임) → `NN_..._composited.png` 는 원본 사진 크기.
- manifest 항목: `align: {status: aligned|warped|unchecked|misaligned, shift_pct, scale_pct, angle_deg}`, `aligned`(bool, 웹 호환), `composited`. manifest 최상위 `tile: {box, size}`, `prepared` 는 타일 경로.
- `--no-tile` 이면 이전 방식(사진 전체 `00_prepared.png`). 랜드마크가 없으면 타일·마스크 없이 프롬프트만.
- OpenAIBackend.edit 은 gpt-image-1 계열에도 `FIXED_SIZES` 에 있는 크기(1024x1536)는 그대로 전달.
- 로컬 확인 필요: 실제 사진으로 (1) 타일이 머리~턱을 잘 담는지, (2) mini 결과가 warped 로 잡혀 합성되는지, (3) 피부톤 보정이 과하지 않은지(과하면 `--no-tone-match`), (4) 눈썹 구역 1:1 시트가 원본 좌표계(lm_full)로 맞는지.

### 3.2.3 눈썹 마스크 모양 (2026-09-12 추가, 원격 세션)

- 기본 `--mask-shape brow`: mediapipe 눈썹 폴리곤(10점)을 `pad_side`(0.10 IPD) 만큼 둥글게 키우고, 위로 `pad_up`(0.14 IPD)·아래로 `pad_down`(0.06 IPD) 만큼 늘린 띠. 각 눈썹은 자기 쪽 윗눈꺼풀(159/386) 위 0.05 IPD 에서 잘림. 이전 방식(둥근 사각형, 위 0.40 IPD 로 이마까지 덮음)은 `--mask-shape box`.
- 디자인이 기존 눈썹보다 훨씬 높거나 두꺼우면 `--mask-up` 을 0.25~0.3 으로. 웹 UI 는 CLI 기본값을 그대로 씀(마스크 옵션 미노출).

### 3.2.4 합성 이음매 · 피부톤 (2026-09-12 추가, 원격 세션)

- 증상(연구자 사진): 마스크 경계가 보이고 안쪽 피부색이 주변과 다름. 원인 두 가지 — (1) 페더가 `min(이미지 변)*0.006`(1024px 타일에서 6px)로 마스크 두께에 비해 너무 얇음, (2) `match_tone` 이 마스크 둘레 링의 **평균 하나**로 전체를 평행이동 → 링에 머리카락·속눈썹·눈이 섞이고, 얼굴 좌우로 색이 다르게 틀어진 경우를 못 잡음.
- 수정: `match_tone` 이 국소 보정 필드를 만듦. 마스크 밖 근접 피부(휘도 중앙값 ±40 으로 머리카락·배경 제외)에서 `원본−편집` 차이를 구하고, 박스 블러 3회로 정규화 확산(`num/den`)해 마스크 안쪽으로 외삽 → 경계에서는 원본과 일치, 안쪽은 완만히 변함(Poisson 블렌딩 근사). ±40 클리핑. `_box_blur` 는 numpy cumsum 기반(분리형, 3패스).
- 페더: `feather_for(mask)` = 마스크 bbox 높이의 10%(최소 3px). 1024×1536 타일 기준 6px → 18px.
- 실측(원격, 합성 편집): 균일 캐스트는 이전과 비슷(1.77 → 1.72), **좌우로 밝기가 다른 캐스트에서 8.26 → 0.93** (마스크 안 피부의 원본 대비 평균 채널 오차).
- 로컬 확인 필요: 실제 사진에서 경계가 사라졌는지, 보정이 과해 눈썹 색까지 바뀌지는 않는지(과하면 `--no-tone-match`).

### 3.2.5 모델 가용성 확인 (2026-09-12 추가, 원격 세션)

- `backends.check_models(models=None, probe=False)`: 모델마다 `models.retrieve`(무료)로 계정 가시성 확인 → `probe=True` 면 보이는 모델 중 위에서부터 하나만 실제 `images.generate`(1024x1024, low, 약 $0.01)로 시험, 실패하면 다음 모델로 내려감. 상태: ok / visible / limit0 / not_found / auth / quota / error (`PROBE_LABELS` 에 한국어 라벨).
- 노출: `POST /api/models/check {probe}` · 웹 설정 창 버튼 2개 · `python -m browlab models [--probe]`.
- 원격 세션은 OpenAI 접근이 막혀 있어 실제 확인 불가. 9/19 이후 추가 충전으로 Tier 1 이 되면 이 버튼으로 2.5 개방 여부를 바로 확인할 것.

### 3.2.6 눈썹 높이 고정 (2026-09-12 추가, 원격 세션)

- 증상(연구자 사진 4장): 새 눈썹이 원래보다 확연히 위에 그려져 눈-눈썹 간격이 벌어짐. 원인은 편집 프롬프트에 **세로 위치 지시가 전혀 없었던 것**(콧방울 기준선은 전부 가로 기준). 모델이 "예쁜" 높이로 올림.
- 수정: `build_restyle_prompt` 맨 앞에 "Eyebrow height (most important)" 블록 추가 — 기존 눈썹 아래선을 따라갈 것, 윗눈꺼풀-눈썹 간격 유지, 이마 쪽으로 올리지 말 것, 놀란 표정 금지. 기존 배치 문장은 "horizontal only" 로 명시. Avoid 에 "raising the brows higher on the forehead" 추가.
- 선택지: `P.BROW_HEIGHTS` (keep 기본 / slight_up / slight_down, 2~3mm). CLI `--height`, 웹 restyle 패널의 "높이" 칩 행, manifest `height`.
- 마스크 위 여유(`--mask-up` 0.14 IPD)는 그대로 둠. 줄이면 모델이 높게 그렸을 때 눈썹 윗부분이 잘려 더 나빠짐. 프롬프트로 잡는 것이 맞음.
- 로컬 확인 필요: 같은 사진으로 재실행해 눈-눈썹 간격이 원본과 같은지. 그래도 올라가면 `--mask-up 0.10` 을 함께 시험.

### 3.2.7 마스크를 눈썹에 붙이고, 남은 들림은 기하로 되돌림 (2026-09-12 추가)

- 3.2.6 의 "마스크 위 여유는 그대로 둔다"는 판단을 뒤집음. 프롬프트만으로는 안 잡혔고, **위로 열어둔 15mm 띠가 곧 모델에게 준 허가**였음.
- `brow_region_mask` 기본값 `pad_side=0.10/pad_up=0.14/pad_down=0.06` → **0.035 / 0.03 / 0.03**. 팽창이 방사형이라 눈썹 위 여백은 `pad_side+pad_up`. 실측(IPD 63mm 얼굴): 마스크 높이 31.5→20.6mm, 눈썹 위 15.0→**4.1mm**, 아래 1.9mm, 좌우 2.3mm.
- 그래도 올라간 만큼은 기하로 되돌림: `M.brow_baseline(lm)`(양쪽 눈썹 아랫변 평균 y) 를 원본과 편집 결과에서 각각 재고, 차이만큼 타일을 `M.shift_image` 로 내림. 0.02 IPD 미만은 무시, 0.35 IPD 초과는 클램프. `--no-brow-align`, `--brow-align-max` 로 조절. 60px 들린 눈썹이 60px 내려오는 것을 테스트로 고정.
- 갤러리에 모델이 실제로 받은 입력(원본 / 얼굴 타일 / 마스크 표시 / API 알파 마스크 / 모델 원본 출력)을 썸네일로 노출. 마스크가 잘못됐는지 추측하지 않고 눈으로 확인.

### 3.2.8 출력 배율 (2026-09-12 추가)

- 질문: "출력이 좀 작게 느껴진다, 상하좌우 1cm씩 컸으면 좋겠다. 일반인보다 작게 나오나?"
- 실측 결과 **축척 자체는 정확**. 연구자 사진 기준 광대 너비 142.4mm, 헤어라인~턱 180.3mm, 얼굴너비/IPD 2.26 — 성인 평균(女137·男146 / 女180·男190 / 2.20~2.30) 한가운데. 즉 1:1 은 맞고, 연습용으로 작게 느껴지는 것.
- `SheetOptions.print_scale`(배수) 과 `grow_mm`(상하좌우 각각 mm) 추가. `enlargement()` 가 둘을 곱해 `life_size_scale` 에 반영. `grow_mm=10` → 배율 114%, 너비 142→162mm, 세로 180→206mm, 턱은 256mm 로 A4(297mm) 안에 남음.
- CLI `--grow-mm` / `--print-scale`(%), 웹 3개 폼의 "출력 여유 mm". 헤더가 자로 잴 수 있는 실제 치수(동공간·너비·세로·배율)를 적고, 1:1 이 아니면 제목도 "실물의 114%" 로 바뀜.
- A4 세로 여유: 그림 영역 233mm 인데 머리+여유는 100%에서 이미 262mm — 머리카락 위쪽은 원래부터 잘림. `place_face` 가 눈썹·눈·턱을 우선 남기므로 배율을 올려도 작업 구역은 안전.

### 3.2.9 눈썹이 너무 진하고 부자연스러운 문제 (2026-09-12 추가)

- 증상(연구자 사진): 새 눈썹이 새까맣게 꽉 찬 덩어리로, 테두리가 칼같이 떨어지고 털이 아니라 그려 넣은 도형처럼 보임.
- 원인: 프롬프트에 **농도·불투명도 지시가 아예 없었음.** 스타일 프리셋은 전부 *모양*만(아치/일자/두께), 색 프리셋은 *색상*만 지정. 얼마나 진한지, 피부가 비쳐야 하는지, 테두리가 뭉개져야 하는지를 아무도 말해주지 않으니 모델이 기본값인 고대비 "살롱 시술 직후 사진"으로 감.
- `Asset type` 이 "brow tattoo / semi-permanent makeup consultation" 이었던 것도 그 룩을 불러옴 → "몇 달 뒤 완전히 아문 상태의 일상 사진, 살롱 비포애프터도 광고도 아님" 으로 교체.
- `P.BROW_INTENSITIES` 추가: soft(연하게) / natural(자연스럽게, 기본) / bold(진하게). CLI `--intensity`, 웹 restyle "농도" 칩 행. 칩 기본 선택이 첫 항목 고정이라 프리셋에 `default` 플래그를 주고 렌더러가 그걸 따르게 고침(natural 이 가운데).
- 모든 농도에 공통으로 붙는 블록: 털 사이로 피부가 보일 것, 앞머리가 가장 옅고 부드럽게 사라질 것, 테두리·스텐실 엣지·균일한 색 블록 금지, 본인 눈썹 털의 굵기/색/부드러움에 맞출 것.
- Avoid 확장: solid opaque block, hard painted outline, 본인 머리카락보다 진한 눈썹, 시술 직후의 번들거림, 광고/필터 룩, 기계로 그린 듯한 완벽 대칭.
- `bold_thick`("thick, full, dense ... well-defined edges") 와 `feathered`("slightly fuller than natural") 프리셋도 밀도를 부추기고 있어서 같이 순화.

### 3.2.10 "새로 그리기"가 아니라 "기존 눈썹 보정" (2026-09-12 추가)

- 연구자 지적: "기본 눈썹을 유지하면서 조금 더 자연스럽게 추가되거나 수정되는 것이지, 막 진하게 깔끔하게 새롭게 생성하는 게 아니다."
- 원인 두 군데:
  1. 프롬프트가 `redraw ONLY the two eyebrows` — **"다시 그려라"** 라고 시키고 있었음. 모델은 시킨 대로 기존 털을 지우고 새 모양을 올림.
  2. `composite_brows` 가 마스크 안을 **통째로 교체**. 프롬프트가 아무리 잘 돼도 원래 털은 물리적으로 살아남을 수 없었음.
- 프롬프트: "KEEPS the eyebrows they already have. 지우거나 밀거나 덮지 말 것, 깨끗한 피부에서 새 모양을 시작하지 말 것. 기존 털은 위치·길이·방향·색 그대로 두고, 빈 곳과 모양에 필요한 가장자리에만 새 털을 추가. 원래 눈썹을 결과에서 빼면 미완성으로 보여야 한다." Avoid 에 지우기/밀기/덮기/원본과 겹치지 않는 눈썹 추가.
- 합성: `composite_brows(..., keep_hair=True)` 기본. 화소마다 **둘 중 어두운 쪽**(`ImageChops.darker`)을 취함 → 색소는 더해질 뿐 털은 절대 지워지지 않음. 실제 시술과 같은 연산.
- 연구자 사진으로 실측(마스크 안 44,813화소): 기존 방식은 **원본 털 3,355화소가 지워짐** / keep_hair 는 **0화소 지워짐**, 새로 추가된 19,101화소는 그대로 통과. `--blend 0.6` 이면 추가분만 11,056화소로 줄고 원본 털은 여전히 0 손실.
- CLI `--no-keep-hair`(끄기), `--blend 0~1`(추가 세기). 웹 restyle 고급 설정에 "추가 세기" + "기존 눈썹 털을 살리지 않고 통째로 교체".

### 3.2.11 눈썹이 두 개로 보이는 문제 (2026-09-12 추가)

- 증상: 3.2.10 의 keep_hair 적용 후 "기존 눈썹 위에 눈썹이 또 올라가서 두 개로 보임".
- 이건 keep_hair 가 만든 버그가 아니라 **원래 있던 버그가 드러난 것**. 전체 교체 방식은 원본 털을 지워서 이 어긋남을 가려주고 있었음.
- 실측(연구자 사진): 새 눈썹의 진한 부분 중심이 원본보다 **2.5mm 위**, 아래선은 **5mm 위**. 원본 눈썹 아랫부분 5mm 가 새 눈썹 밑으로 삐져나와 두 번째 눈썹으로 보임.
- **3.2.7 의 높이 보정이 사실상 작동하지 않고 있었음.** `M.brow_baseline(lm)` 은 MediaPipe 랜드마크 기반인데, 이건 얼굴 모델을 피팅한 해부학적 추정치라 **눈썹을 다른 위치에 다시 그려도 거의 움직이지 않음**. 랜드마크 기준 차이는 12px 로 나왔지만 실제 픽셀 기준 아래선 차이는 28px.
- 수정 1 — 측광 방식으로 교체: `M.hair_weight(image, mask)`(주변 피부보다 얼마나 어두운지 0~1), `M.hair_span(image, mask)`(실제 그려진 털의 위/아래/무게중심). `_brow_height_fix` 가 원본 타일과 편집 결과의 **아래선**을 맞춤(눈-눈썹 간격이 실제로 보이는 기준). 무게중심은 마스크가 아래로 갈수록 좁아져서 드리프트가 있어 안 씀.
- 수정 2 — `M.hair_proximity(base, mask, grow_px)`: 기존 털에서 `--near-mm`(기본 2.5mm) 안쪽에만 새 털 허용. 털이 거의 없는 사람(마스크 안 털 8% 미만)은 기준 삼을 게 없으니 제한을 걸지 않고 마스크 전체를 그대로 반환.
- 결과: 새 눈썹 진한 구간 y 541~608 → **556~625** (원본 547~628 안쪽). 웹 restyle 고급 설정에 "기존 털 근접 제한 mm".

### 3.2.12 더하기만이 아니라 "정리"도 (2026-09-12 추가)

- 연구자 정의: "기존 눈썹을 베이스로 채워주거나 없는 부분을 추가하고, **지저분한 곳은 정리**해서 자연스럽게. 지금은 진한 화장한 미국 아줌마 같다."
- 3.2.10 의 keep_hair(darker) 는 **털을 지울 수가 없음**. 퍼진 잔털이 전부 살아남고 그 위에 색소가 더해지니 두껍고 무거워짐. 그게 "진한 화장" 의 정체.
- `M.brow_core_mask(image, mask, spread_px)`: hair_weight 를 블러하면 촘촘한 눈썹 몸통은 높은 고원이 되고 **흩어진 잔털은 낮게 남음**. 그 밀도에 임계값(피크의 35%)을 걸어 "눈썹" 과 "눈썹 주변 털" 을 분리.
- 합성 규칙: **몸통 안 = 더하기(원본 털 보존), 몸통 밖 = 모델 결과 그대로(잔털 정리됨)**. `--tidy-mm` 기본 1.5mm, `--no-tidy` 로 끔.
- 연구자 사진 실측: 마스크 안 36%가 몸통, 64%가 정리 대상 구역. 마스크 안 평균밝기 104.6(정리 없음) → 107.4(정리) 로 밝아지면서 눈썹 색소량은 유지.
- 프롬프트도 "더하기 전용" 에서 **grooming** 으로: 몸통 털은 그대로, 빈 곳은 채우고, 진짜 없는 부분(주로 꼬리)만 연장하고, **눈썹선 위아래로 흩어진 잔털은 뽑아 맨살로**. Avoid 에 "사진보다 두껍거나 무거운 눈썹", "눈썹 화장/펜슬로 보이는 것".
- 프롬프트 내부 모순 두 개 제거: 높이 블록의 `redraw them in place`(→ groom them where they are), 농도 블록의 `Keep the ... stray hairs ... visible`(정리 지시와 정면 충돌).
- 주의: 합성 테스트의 잔털은 **선(width=2)** 으로 그려야 함. 8x6 블록은 실제 잔털보다 훨씬 굵어서 몸통으로 분류됨.

### 3.2.13 합성이 눈썹을 잘라먹는 문제 (2026-09-12 추가)

- 증상: "모델 원본 출력이 나은 것 같다. 합성하면서 눈썹이 잘린다. **특히 매번 오른쪽 눈썹이** 잘린다."
- **독립된 버그 두 개**였음.
- (1) **마스크 꼬리 여유가 2.2mm뿐.** 3.2.7 에서 세로 들림을 잡으려고 `pad_side` 를 2.3mm 로 조였는데, 그게 가로에도 그대로 적용됨. 그런데 3.2.12 프롬프트는 "진짜 없는 부분(주로 꼬리)은 연장하라" 고 시킴 — 늘릴 자리가 없어서 잘림. MediaPipe 가 재는 눈썹 길이는 45~47mm, 실제 성인은 50~55mm 라 **본인 꼬리조차** 마스크 밖일 수 있음.
  - `pad_tail`(기본 0.13 ≈ 8mm) / `pad_head`(0.04 ≈ 2.5mm) 추가. 눈썹 축 방향으로만 확장(`_dilate_x`)하므로 세로 들림과 무관. 실측 꼬리 여유 2.2 → **10.4mm**.
- (2) **`brow_core_mask` 가 옅은 쪽 눈썹을 통째로 지우고 있었음.** 밀도 임계값이 **양쪽 눈썹 공통 피크의 35%** 였음. 진한 쪽이 피크를 정하니 옅은 쪽은 전부 미달 → 잔털로 분류 → 정리 대상. 실측: 옅은 눈썹 앞머리 **0.4%**, 4/5 구간 4.5%, **꼬리 0%** 만 몸통으로 인정. 진한 쪽도 꼬리는 59%. 꼬리는 원래 얇고 성글어서 밀도 임계값에 가장 취약한 부위.
  - 밀도 임계값을 버리고 **연결성**으로 교체(scipy.ndimage). 털을 붙을 때까지 팽창시켜 blob 을 만들고, **자기 쪽 마스크 blob 안에서** 가장 큰 것 대비 22% 이상이면 몸통. 얇은 꼬리는 몸통에 매달려 있으니 살고, 떨어진 잔털은 죽음. 좌우 판정은 이미지 중심선이 아니라 **마스크 자신의 blob**(눈썹당 하나)으로 — 중심선을 쓰면 마스크가 하나로 이어진 경우 잔털이 "그쪽에서 제일 큰 것" 이 되어 살아남음(테스트로 잡힘).
  - 결과: 양쪽 눈썹 전 구간 **98~99.9%** 몸통 인정. 마스크 안 몸통 비율 36% → 71%.
- (3) `hair_proximity` 도 등방성이라 꼬리 연장을 막고 있었음 → **비등방**으로: 세로는 `grow_px`(2.5mm, 두 번째 눈썹 방지), 가로는 그 4배(꼬리 연장 허용).
- 검증: 합성 결과의 눈썹 길이가 모델 출력과 좌 0.1mm / 우 1.2mm 차이로 일치.
- scipy 없으면 `brow_core_mask` 는 마스크를 그대로 반환(정리 안 함). 정리는 선택이지만 눈썹을 망가뜨리는 건 선택이 아님.

### 3.2.14 메뉴 이름 (2026-09-12)

- 얼굴 생성 → **연습용 얼굴 생성**, 내 사진 눈썹 바꾸기 → **눈썹 생성기**, 사진 시트 → **출력 시트 만들기**. `index.html` 의 h1·`VIEWS`·엔진 힌트 문구와 `web.KIND_KO` 네 군데.

### 3.2.15 겹치기를 끄고 모델 출력을 그대로 씀 (2026-09-12) ← 3.2.10 뒤집음

- 연구자: "여전히 눈썹이 2개가 되고 있어. **이게 오버레이 되는건가?** ... 모델이 그린 그림을 그대로 쓰면 되는거 아냐?" — 맞음.
- 3.2.10 의 `keep_hair`(darker 합성)는 **원본 눈썹을 새 눈썹 위에 겹쳐 찍는 연산**. 모델 눈썹이 원본을 완전히 덮을 때만 자연스럽고, **더 얇게 그린 쪽에서는 원본 아랫부분이 그대로 남음**.
- 실측(같은 사진, 겹치기가 모델 출력 위에 덧칠한 화소 2,651개 = 마스크의 4.2%):
  - 사진 왼쪽 눈썹(모델이 y 537~636, 99px 로 두껍게 그림): 덧칠 1,447화소 **전부 눈썹 안** → 문제 없음.
  - 사진 오른쪽 눈썹(모델이 y 558~609, **51px 로 얇게**): **눈썹 아래 835화소, 최대 7.1mm 삐져나감** → 두 번째 눈썹.
  - 한쪽에서만 생기는 이유가 이것. 3.2.11 의 높이 보정(아래선 정렬)으로도 못 고침 — 높이가 아니라 **두께** 차이라서.
- `composite_brows(keep_hair=...)` **기본값 True → False**. 합성의 본래 임무는 "마스크 **바깥**을 원본과 픽셀 동일하게 유지"(얼굴/신원 보존) 하나뿐이고, 마스크 안은 모델이 그린 그대로 쓰는 게 맞음. "기존 눈썹을 베이스로" 는 **프롬프트가 할 일**이지 픽셀 합성이 할 일이 아니었음.
- CLI `--no-keep-hair` → `--keep-hair`(옵트인). 웹 체크박스도 "기존 눈썹 털을 결과 위에 겹쳐 보존 (눈썹이 두 줄로 보일 수 있음)". `near_px`/`tidy_px` 는 keep_hair 를 켤 때만 의미가 있으므로 그때만 전달.
- 남아 있는 것: 마스크·높이 보정·톤 매칭·되붙이기는 그대로 유효. 3.2.13 의 꼬리 여유/연결성 core 도 유효(keep_hair 를 켰을 때 쓰임).
- 색: `match_hair` 가 "머리색과 자연스럽게 맞는 색" 이라 흑발이면 새까맣게 나옴. 실제 눈썹은 머리보다 1~2톤 밝음 → "머리보다 한두 톤 부드럽게, 검은 머리만큼 검지 않게, 이미 있는 눈썹 털보다 진하지 않게" 로 교체. `natural_black` 도 "검은 머리보다는 밝게" 추가.

### 3.2.16 검은 얼룩과 "합성하지 말고 원본 출력" (2026-09-12)

- 연구자: "합성하면서 모델 원본 출력엔 없는 **검정색**이 왼쪽 눈썹 아래에 생겼어. 굳이 합성하지 말고 원본 출력으로 그쳐도 될거 같아."
- 범인은 `match_tone` 이 아니었음(실측: 최대 -18, 순검정 0). **`shift_image` 와 `warp_similarity` 가 비운 자리를 PIL 기본값인 검정으로 채우고 있었음.** 같은 타일에서 순검정 화소 20,760개. 그게 마스크 안에 걸리면 얼굴에 검은 얼룩으로 합성됨.
  - 두 함수에 `background=` 추가. 비운 자리를 **원본 타일**로 채움(RGBA 알파로 실제로 그려진 영역을 구해 `Image.composite`). `_align_edit` 과 `_brow_height_fix` 가 원본 타일을 넘김.
- 합성 기본값을 뒤집음: `--no-composite` → **`--composite`(옵트인)**. 기본은 모델 출력 그대로 — 정렬·톤보정·되붙이기를 전부 건너뜀. 웹 체크박스 "눈썹만 원본 사진에 합성 (기본은 모델 출력 그대로)".
  - 대가: 합성을 끄면 결과가 **얼굴 타일 전체**라 눈썹 밖의 피부·이목구비도 모델이 다시 그린 것. 신원 보존이 필요하면 체크박스를 켜야 함.
- 테스트 두 개가 합성 기본값에 의존하고 있어 `--composite` 를 붙임.

### 3.2.17 라이트박스 A/B 비교 (2026-09-12)

- 결과를 원본과 겹쳐 놓고 **손잡이를 끌어 좌우로 와이프**, **이미지를 누르고 있으면 원본**, 떼면 결과.
- "before" 는 화면에 뜬 것과 픽셀이 맞는 것으로 고름: `_composited.png` 면 `00_original.png`, 모델 원본 출력이면 `00_face_tile.png` → `00_prepared.png` → `00_original.png` 순. 못 불러오면 단일 이미지 뷰로 폴백.
- 손잡이는 세로 32% 위치 — 50% 에 두면 이전/다음 화살표와 겹침.
- 칩 기본 선택: 스타일은 **자연 눈썹 하나만**(웹 기본 4개 → 1개), 색은 **머리색에 맞춤**.

### 3.2.18 라이트박스 보정 · 회원명 저장 (2026-09-12)

- 라이트박스가 캔버스로 바뀜(이미지 2장 겹치기 → `<canvas>`). 조정이 **합성 결과 전체**에 걸려야 해서.
- 두 모드: **좌우 비교**(와이프, 손잡이 드래그) / **겹치기**(알파 블렌드, 0~100%). 어느 모드든 이미지를 누르고 있으면 원본.
- 슬라이더: 겹치기 · 밝기 · 대비 · 채도 · 색온도. 밝기/대비/채도는 `ctx.filter`, 색온도는 `soft-light` 로 따뜻/차가운 색을 덮음(칠하지 않고 물들임).
- 미리보기는 화면 크기에 맞춘 축소 캔버스(dpr 최대 2), **저장할 때만 원본 해상도**로 다시 그림(`fullCanvas()`).
- 검증(브라우저 실측 픽셀): 겹치기 0% = 순수 원본(120,105,95) · 100% = 순수 생성(30,25,20) · 70% = (57,48,42) = 0.7×30+0.3×120 정확히 일치. 밝기 +50 → 45. 색온도 +60 → R 66 / B 25 (따뜻). JS 에러 0.
- 저장: `POST api/saves {job, name, image(base64 png), settings}` → `<job_dir>/saves/{NNN}.png` + `{NNN}.json`(회원명·설정·시각). 회원명 비우면 **회원_N**, N 은 `settings.json` 의 `save_seq` 전역 카운터(잠금 안에서 read-modify-write).
- 갤러리에 `saved: true`, `kind_ko: "저장한 보정"` 카드로 뜸. 저장본은 다시 열어도 편집 패널이 안 뜸(원본 쌍이 없음).
- 주의: 저장 후 `refreshGallery()` 가 `items` 를 통째로 갈아끼움. 테스트에서 가짜 아이템을 넣어 열어뒀다면 저장 한 번 뒤에 사라짐.

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
