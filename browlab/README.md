# BrowLab — 눈썹 문신 디자인 연습용 실물 크기(A4 1:1) 얼굴 시트 생성기

눈썹 모량이 부족하거나, 형태가 불확실하거나, 연한 눈썹을 가진 다양한 얼굴(10대~70대, 남녀, 여러 얼굴형)을
AI로 만들고, **A4 용지에 실제 사람 얼굴 크기(1:1)로 인쇄되는 시트**로 합성해 주는 도구입니다.
인쇄한 시트 위에 펜슬로 눈썹 디자인을 직접 그리며 연습할 수 있습니다.

더 나아가 실제 사진을 넣으면 **눈썹 부분만 검출**해서 얼굴은 그대로 두고 눈썹만 여러 스타일로 바꿔 보는
`restyle` 기능도 있습니다.

이미지 생성은 **Codex CLI 최신 버전(0.154+)의 내장 이미지 생성(`$imagegen`, ChatGPT Images 2.5)** 을
기본으로 사용하고, OpenAI Images API(`gpt-image-2.5-flare` / `gpt-image-2.5-sunburst`)를 직접 호출하는
백엔드와, 프롬프트만 뽑아 주는 수동 모드도 지원합니다.

명령줄 외에 **웹 UI**(`python -m browlab web`)가 있어 휴대폰/브라우저에서 조건을 고르고 결과 PDF 를 받을 수
있습니다. 4090 서버에 상시 서비스로 올라가 있습니다 → [8. 웹 UI · 4090 상시 서비스](#8-웹-ui--4090-상시-서비스).

---

## 1. 설치

```bash
# 저장소 루트에서
pip install -r requirements-browlab.txt      # pillow, numpy, mediapipe(권장), openai(선택)
```

- Python 3.10 이상.
- `mediapipe` 는 얼굴 랜드마크(동공·눈썹·콧방울 위치)를 찾는 데 씁니다. 1:1 배율 계산, 가이드선, 눈썹 마스크에
  필요합니다. 처음 실행할 때 구글 서버에서 랜드마크 모델(`face_landmarker.task`, 약 3.7 MB)을
  `~/.cache/browlab/` 에 자동 내려받습니다. (막으려면 `--no-download`, 직접 지정하려면 `BROWLAB_FACE_MODEL=경로`)
- mediapipe 설치가 어려우면 `--landmarks codex` (Codex의 비전 모델에게 좌표를 물어봄) 또는
  `--landmarks manual --pupils x1,y1,x2,y2` (동공 좌표 직접 입력)로 대신할 수 있습니다.

### Codex CLI 준비 (기본 백엔드)

```bash
npm install -g @openai/codex        # 또는 codex update
codex login                         # ChatGPT 계정 로그인
codex --version                     # 0.153.2 / 0.153.3 에서 exec 플래그 확인됨, 0.154+ 권장
```

`codex` 안에서 `$imagegen` 이 동작하면 준비 끝입니다. BrowLab은 `codex exec` 를 비대화식으로 실행하고,
`$imagegen` 으로 이미지를 만들게 한 뒤 결과 파일을 출력 폴더로 가져옵니다
(에이전트가 복사하지 않아도 `~/.codex/generated_images/` 에서 새 파일을 찾아 가져옵니다).

### OpenAI API 백엔드 (선택)

```bash
export OPENAI_API_KEY=sk-...
python -m browlab generate --backend api ...
```

API 백엔드는 출력 크기를 정확히 지정할 수 있고(`--size 1536x2304` 등), `restyle` 에서 **알파 마스크 인페인팅**을
씁니다. 기본 모델은 생성 `gpt-image-2.5-flare`(빠름), 편집 `gpt-image-2.5-sunburst`(정밀 편집)입니다.
`--model`, `--edit-model` 로 바꿀 수 있습니다(`gpt-image-2` 도 가능).

---

## 2. 빠른 시작

```bash
# 저장소 루트에서 실행합니다.

# (1) 프린터 배율 확인용 눈금 시트 먼저 출력해 보기
python -m browlab calibrate

# (2) 30대 여성, 얼굴형·눈썹 상태는 무작위, 4명 생성 → 각각 A4 1:1 시트(PNG+PDF)
python -m browlab generate -n 4 --age 30s --gender female --seed 7

# (3) 완전 무작위(10대~70대, 남녀, 얼굴형, 눈썹 상태, 다양한 외모) 10명
python -m browlab generate -n 10 --seed 2026

# (4) 얼굴형 지정 + 가이드선 + 눈썹 구역만 1:1로 3번 반복된 시트까지
python -m browlab generate -n 2 --face-shape round --brow-condition sparse --guides --layout both

# (5) 이미 있는 얼굴 이미지를 A4 1:1 시트로
python -m browlab sheet photo1.png photo2.png --guides --layout both

# (6) 실제 사진의 눈썹만 여러 스타일로 (얼굴은 그대로)
python -m browlab restyle client.jpg --styles korean_natural,straight,soft_arch,feathered --color dark_brown

# 선택 가능한 값 전체 보기
python -m browlab presets
```

출력은 기본적으로 `output/browlab/<종류>_<시각>/` 아래에 생깁니다 (`--out-dir` 로 변경).
`output/` 은 git에서 무시됩니다.

---

## 2.5 자동 백엔드 (기본값): Codex 먼저, 안 되면 API

`--backend auto`(기본값, 웹 UI 엔진 "자동")는 한 장마다 아래 순서로 시도하고, 처음 성공한 결과를 씁니다.
`--backend codex` 도 API 키가 있으면 같은 순서로 넘어갑니다(예전 페이지·설정 호환). Codex 만 쓰려면 `--backend codex-only`.
`--backend api` 는 Codex 없이 같은 모델 순서(2.5 → 2 → 1.5 → 1 → 1-mini)로 시도합니다. `--model` 을 주면 그 모델부터 아래로 내려갑니다.

1. **Codex** (`codex exec` + `$imagegen`, ChatGPT 구독 한도)
2. **API gpt-image-2.5** (생성 flare / 편집 sunburst)
3. **API gpt-image-2**
4. **API gpt-image-1.5**
5. **API gpt-image-1**
6. **API gpt-image-1-mini**

- 사용 한도 소진, 로그인 필요, 키 없음, `Limit 0`(조직 미개방), 조직 인증 필요, 모델 없음 같은 실패는 **그 실행 동안 해당 단계를 건너뜁니다**(매 장마다 다시 실패하지 않음). 시간 초과·일시 오류는 다음 장에서 다시 시도합니다.
- 실제로 쓰인 엔진·모델은 로그(`... 성공 (앞선 시도 실패: ...)`), `manifest.json` 의 각 항목 `backend` / `model` / `fallback`, 웹 작업 상세의 "사용:" 표시에 남습니다.
- 순서를 바꾸려면 `--model-chain gpt-image-2,gpt-image-1-mini` 처럼 지정하고, `--model X` 를 주면 API 단계는 그 모델 하나만 시도합니다. 편집 순서는 `--edit-model-chain`.
- 1-mini 까지 내려가는 것이 싫으면 `--model-chain gpt-image-2.5-flare,gpt-image-2` 로 끊으세요. `xhigh`/`max` 품질은 2.5 이외 모델에서 자동으로 `high` 로 낮춥니다.

## 3. 명령별 설명

### `generate` — 연습용 얼굴 생성 + A4 시트

| 옵션 | 값 | 설명 |
| --- | --- | --- |
| `-n, --count` | 정수 | 생성할 얼굴 수 |
| `--age` | `10s`~`70s`, 숫자, `random` | 나이대 (10대는 15~19세) |
| `--gender` | `female`, `male`, `random` | 성별 |
| `--face-shape` | `oval` 계란형, `round` 둥근형, `square` 각진형, `long` 긴형, `heart` 하트형, `diamond` 다이아몬드형, `triangle` 삼각형, `random` | 얼굴형 |
| `--brow-condition` | **모량**: `sparse` 모량 부족, `faint` 연함, `patchy` 군데군데 빔, `missing_tail` 꼬리 없음, `asymmetric` 비대칭, `overplucked` 과도하게 뽑음, `undefined` 형태 불분명, `scar_gap` 흉터, `almost_none` 거의 없음 · **형태**(기존 눈썹 위에 디자인 연습): `arch` 아치, `high_arch` 높은 아치, `straight_flat` 일자, `half` 1/2 반토막, `thin` 얇음, `thick` 두꺼움, `spread` 퍼짐 · `random` | 눈썹 상태·형태 |
| `--ethnicity` | `korean`(기본), `japanese`, `chinese`, `southeast_asian`, `south_asian`, `middle_eastern`, `european`, `mediterranean`, `african`, `latino`, `mixed`, `random`, `any` | 외모. 기본은 한국인 100%. `random` 은 한국인 비중을 높게 둔 가중 무작위, `any` 는 균등 |
| `--seed` | 정수 | 같은 시드면 같은 조합(나이·성별·얼굴형…)이 나옵니다. 이미지 자체는 매번 달라집니다 |
| `--notes` | 문장 | 프롬프트 끝에 덧붙일 지시문 |
| `--backend` | `auto`(기본), `codex`, `api`, `manual` | 이미지 생성 방법 (auto: 2.5절 참고) |
| `--size`, `--quality` | | API 백엔드용 (기본 `1536x2304`, `high`) |
| `--layout` | `face`(기본), `browzone`, `both` | 얼굴 전체 1:1 / 눈썹·눈 구역만 1:1로 여러 번 / 둘 다 |
| `--guides` | | 눈썹 황금비 가이드선 표시 |
| `--ipd-mm` | mm | 동공 간 거리 기준값. 생략 시 여 62 / 남 64 / 10대 60 mm |
| `--dry-run` | | 프롬프트만 출력 |

생성된 얼굴은 `face_01_<성별나이>_<얼굴형>_<눈썹상태>_<외모>_s<시드>.png`, 시트는 `sheets/` 아래에
`..._A4.png/.pdf`, `..._browzone.png/.pdf` 로 저장됩니다. `manifest.json` 에 조합·프롬프트·경로가 남습니다.

### `sheet` — 기존 이미지를 A4 1:1 시트로

```bash
python -m browlab sheet face.png --guides --layout both --face-shape oval --gender female
python -m browlab sheet face.png --landmarks manual --pupils 412,590,688,592   # 동공 좌표 직접 지정
```

### `restyle` — 사진의 눈썹만 다시 그리기

1. 사진(EXIF 회전 보정, 긴 변 2048px 이하로 축소)에서 얼굴 랜드마크를 찾습니다 → `00_original.png`
2. 얼굴만 잘라 **얼굴 타일**(기본 1024×1536, 머리 위~턱 아래)을 만듭니다 → `00_face_tile.png`. 모델은 사진 전체가 아니라 이 타일만 봅니다.
3. 타일 위에 **눈썹 윤곽을 따라가는 마스크**(둘레 약 6 mm, 위로 약 9 mm 여유, 윗눈꺼풀 위에서 잘림)를 만듭니다 → `mask.png`, `mask_api.png`(알파), `mask_guide.png`(빨간 표시)
4. 스타일마다 편집 프롬프트로 생성합니다.
   - `api` 백엔드: 알파 마스크 인페인팅(마스크 밖은 모델이 건드리지 않음)
   - `codex` 백엔드: 타일 + 빨간 영역 가이드 이미지를 첨부해 "눈썹만 바꿔라"로 편집
5. 결과에서 다시 동공을 찾아 원본 타일과 **정렬**합니다. 5% 이내면 그대로, 45% 이내면 이동·크기·회전을 보정(유사 변환), 그 이상이면 다른 그림으로 보고 합성을 생략합니다.
6. 마스크 주변 피부에서 **국소 색 보정 필드**를 만들어(경계에서는 원본과 정확히 일치, 안쪽으로는 부드럽게 이어짐 · `--no-tone-match` 로 생략) 색을 맞춘 뒤, 마스크 두께의 10% 만큼 가장자리를 흐려 **눈썹 영역만 원본 타일에 합성**하고, 그 타일을 **원본 사진의 제자리에 되붙입니다** → `NN_스타일_색_composited.png`. 마스크 밖은 원본 사진과 픽셀 단위로 같습니다.
7. 비교 시트(`sheet_compare`)와 눈썹 구역 1:1 시트(`sheet_browzone`)를 만듭니다.

작은 모델(gpt-image-1-mini 등)은 마스크를 무시하고 그림 전체를 다시 그리는 경우가 많은데, 5~6단계 덕분에 최종 결과는 항상 "원본 사진 + 새 눈썹" 형태를 유지합니다.

| 옵션 | 설명 |
| --- | --- |
| `--styles` | `korean_natural` 자연, `straight` 일자, `soft_arch` 부드러운 아치, `angled_arch` 각진 아치, `rounded` 둥근, `high_arch` 하이 아치, `puppy` 처진(강아지상), `bold_thick` 볼드, `feathered` 결눈썹(엠보), `ombre_powder` 옴브레 파우더, `combo` 콤보, `s_curve` S자. `all`, `random:3` 가능 |
| `--color` | `match_hair`(기본), `natural_black`, `dark_brown`, `medium_brown`, `ash_brown`, `light_brown`, `gray_brown` |
| `--height` | `keep`(기본) 원래 눈썹 높이 그대로 · `slight_up` / `slight_down` 2~3mm 만 올리거나 내림. 지정하지 않으면 모델이 눈썹을 이마 쪽으로 올려 눈과 눈썹 사이가 벌어집니다 |
| `--mask-shape` | `brow`(기본): 검출된 눈썹 윤곽을 따라가는 마스크 / `box`: 눈썹을 감싸는 둥근 사각형 |
| `--mask-side/--mask-up/--mask-down` | 마스크 여유(동공 간 거리 배수, 기본 0.10 / 0.14 / 0.06 ≈ 6 / 9 / 4 mm). 높은 아치나 두꺼운 디자인이면 `--mask-up 0.25` |
| `--tile-size` | 얼굴 타일 크기(기본 `1024x1536`, 모든 gpt-image 모델 호환). 2.x 전용이면 `1536x2304` 가능 |
| `--tile-margin` | 타일 여유 배수(기본 1.0) |
| `--no-tile` | 얼굴을 잘라내지 않고 사진 전체를 편집(이전 방식) |
| `--align-max` | 정렬 보정 허용 이동량(동공 간 거리 대비, 기본 0.45) |
| `--no-tone-match` | 합성 전 피부톤 맞춤 생략. 보정이 과하다고 느껴질 때만 |
| `--no-composite` | 합성 단계 생략(모델 결과 그대로) |
| `--no-mask` | API 백엔드에서 마스크 없이 프롬프트만으로 편집 |
| `--sheet` | `grid`, `browzone`, `both`(기본), `none` |

### `calibrate` — 인쇄 배율 확인 시트

150 mm 가로·세로 눈금, 50 mm 정사각형, 성인 평균 동공 간격이 그려진 페이지입니다.
인쇄 후 자로 재서 정확하면 얼굴 시트도 1:1로 나옵니다.

---

## 3.4 쓸 수 있는 모델 확인

API 키로 어떤 이미지 모델이 열려 있는지 확인합니다.

```bash
python -m browlab models            # 무료 확인 (계정에 모델이 보이는지)
python -m browlab models --probe    # 실제로 1장(1024x1024, low) 생성해 한도 0 여부까지 확인, 약 $0.01
```

웹에서는 설정 창의 **"모델 확인 (무료)"** / **"1장 생성해 확인"** 버튼을 쓰면 됩니다. 표시 색은 초록(사용 가능),
노랑(한도 0 — 조직 인증·티어 대기), 빨강(계정에 안 보임)입니다. 무료 확인은 모델이 보이는지만 알려 주고,
`Limit 0` 은 실제 요청을 해봐야 드러나므로 확실히 하려면 1장 생성해 확인하세요.

## 3.5 웹 UI에서 서버 업데이트

설정(사용량) 창의 **"서버 업데이트 (git pull + 재시작)"** 버튼은 서버 저장소를 `git pull --ff-only` 한 뒤 같은 인자로 서버를 다시 실행합니다(`POST /api/update`).
진행 중인 작업이 있으면 미룹니다. 같은 창에 지금 서버가 돌고 있는 코드의 커밋이 표시됩니다.

## 4. 인쇄 방법 (중요)

- 용지 **A4**, 배율 **100% / 실제 크기**, **"페이지에 맞춤" 해제**, 여백 자동.
- PDF 파일로 인쇄하는 것이 가장 정확합니다(PNG는 300 dpi 메타데이터가 들어 있지만 일부 앱이 무시합니다).
- 시트 아래쪽 **100 mm 눈금자**와 **20 mm 정사각형**을 자로 확인하세요.
- 디자인 연습에는 무광(매트) 용지나 일반 복사지가 펜슬이 잘 먹습니다. 시트 위에 트레이싱지를 올려 여러 번 연습해도 됩니다.

### 1:1 배율의 원리

AI가 만든 얼굴에는 "실제 크기"가 없으므로, **동공 간 거리(IPD)** 를 기준으로 배율을 맞춥니다.
성인 여성 평균 약 62 mm, 남성 약 64 mm, 10대 후반 약 60 mm 를 기본값으로 쓰고 `--ipd-mm` 로 바꿀 수 있습니다.
랜드마크를 못 찾으면 이미지 전체 높이를 320 mm 로 가정한 근사 배율을 씁니다(`--image-height-mm`).

### 가이드선(`--guides`)

반영구 디자인에서 쓰는 기본 비율선을 연한 점선으로 넣습니다.

- 콧방울 → 눈 앞머리: 눈썹 **앞머리** 시작선
- 콧방울 → 홍채 바깥쪽: 눈썹 **산(아치)** 위치
- 콧방울 → 눈꼬리: 눈썹 **꼬리** 끝선
- 동공을 지나는 수평선

얼굴형별 추천 문구(예: 둥근형 → 각이 있는 아치)도 시트 하단에 표시됩니다.

---

## 5. 동작 방식 / 파일 구조

```
browlab/
  cli.py        명령줄 인터페이스 (generate / sheet / restyle / calibrate / presets)
  presets.py    나이대·성별·얼굴형·눈썹 상태·외모·눈썹 스타일·색 프리셋 (한국어 라벨 + 프롬프트 문구)
  prompts.py    FaceSpec, 무작위 조합(시드 재현), 생성/편집 프롬프트 빌더
  backends.py   codex(Codex CLI $imagegen) / api(OpenAI Images API) / manual 백엔드
  landmarks.py  mediapipe / codex(비전) / manual 랜드마크 검출, 좌표 변환
  masks.py      눈썹 영역 마스크, API용 알파 마스크, 가이드 오버레이, 눈썹만 합성, 편집용 크기 정리
  sheet.py      A4 300dpi 시트 합성(얼굴 1:1, 눈썹 구역, 비교 그리드, 배율 확인), PNG/PDF 저장
  fonts.py      한글 글꼴 자동 탐색(맥 AppleSDGothicNeo, 윈도 malgun, 리눅스 Nanum/Noto CJK)
tests/test_browlab.py   단위 테스트 (python -m unittest discover -s tests)
```

Codex 백엔드가 실제로 실행하는 명령은 다음과 같습니다(프롬프트는 표준 입력으로 전달).

```bash
codex exec --skip-git-repo-check -s workspace-write -C <출력폴더> -o <마지막메시지파일> --color never -
```

프롬프트는 Codex `imagegen` 스킬의 구조(Use case / Subject / Composition / Constraints / Avoid …)를 따르며,
`$imagegen` 으로 시작해 내장 도구를 쓰도록 하고, 생성 후 파일을 출력 폴더로 복사하라고 지시합니다.
필요하면 `--codex-arg` 로 `codex exec` 인자를 더 넘길 수 있습니다.

---

## 6. 문제 해결

| 증상 | 해결 |
| --- | --- |
| `Codex CLI not found` | `npm install -g @openai/codex` 후 `codex login`. 다른 경로면 `--codex-bin` |
| `codex exec finished but no image was produced` | `codex` 를 직접 열어 `$imagegen` 이 되는지 확인. 이미지 도구가 막힌 계정/환경이면 `--backend api` |
| `stderr: Error: No such file or directory (os error 2)` 로 즉시 실패 | 2026-09-12 이전 판의 버그(`-C` 에 상대경로 전달). 현재 판은 절대경로로 고쳤음. 재발하면 `--out-dir` 을 절대경로로 |
| `ERROR: You've hit your usage limit ... try again at ...` | Codex(ChatGPT 구독) 이미지 한도 소진. 표시된 시각까지 기다리거나 크레딧 구매, 또는 `--backend api` |
| 웹 UI 작업이 `실패 · 종료 코드 1` | 작업 상세의 로그 마지막 줄을 볼 것. 위 두 메시지 중 하나인 경우가 대부분 |
| API: `429 rate_limit_exceeded ... Limit 0, Requested N` | 이 OpenAI 조직에서 gpt-image-1/1.5/2/2.5 가 아직 안 열림(분당 한도 0). platform.openai.com → Settings → Organization → Limits 에서 결제 반영·조직 인증(Verify organization)을 확인하고, 키를 만든 조직과 충전한 조직이 같은지 확인. 2026-09-12 이 계정에서 실측: 2.5-flare/sunburst/2 모두 한도 0, gpt-image-1-mini 만 열려 있었음 |
| 랜드마크가 제대로 잡히는지 보고 싶다 | `python browlab/tools/landmark_check.py <이미지 또는 작업폴더> --out <폴더>` — 랜드마크·눈썹 마스크 오버레이(`*_lm.png`)와 눈썹 구역 2배 확대(`*_zoom.png`), 검출률·IPD·기울기·눈썹 폭 지표를 출력. 2026-09-12 생성 얼굴 23/24 검출(미검출 1건은 얼굴이 아닌 눈썹구역 시트), 160~200 ms/장 |
| `mediapipe unavailable` | `pip install mediapipe` (리눅스는 `libgl1 libegl1 libgles2` 필요). 또는 `--landmarks codex` / `--pupils` |
| 얼굴이 A4에 다 안 들어감 | 정상입니다. 눈썹·눈·턱을 우선 살리고 머리 윗부분을 잘라냅니다. `--ipd-mm` 을 줄이면 전체가 작아집니다 |
| 시트 한글이 네모로 나옴 | `--font /경로/NanumGothic.ttf` 또는 `BROWLAB_FONT` 환경변수 |
| 생성된 얼굴 눈썹이 너무 또렷함 | `--brow-condition almost_none` / `faint`, 또는 `--notes "eyebrows must be barely visible"` |
| restyle 결과가 원본과 미묘하게 다름 | 기본 합성(`_composited.png`)을 쓰면 마스크 밖은 원본 그대로입니다. 마스크가 좁으면 `--mask-up 0.55` |

## 7. 주의

- 실제 고객 사진을 `restyle` 하면 사진이 OpenAI 서버(Codex/API)로 전송됩니다. 동의를 받고 사용하세요.
- AI 생성 얼굴은 실존 인물이 아니며, 나이·얼굴형 등은 프롬프트 지시일 뿐 항상 정확하지 않습니다.
- 1:1 배율은 평균 동공 간 거리에 맞춘 근사치입니다. 실제 시술 도안 용도가 아니라 연습용입니다.

---

## 8. 웹 UI · 4090 상시 서비스

### 웹 UI 실행

```bash
python -m browlab web --host 127.0.0.1 --port 8177 \
    --data-dir ~/browlab_data --password-file ~/.config/browlab/password.txt \
    --base-path /browlab --codex-bin /절대경로/codex
```

| 옵션 | 설명 |
| --- | --- |
| `--password-file` / `BROWLAB_PASSWORD` | 로그인 비밀번호(필수). 로컬 전용이면 `--no-auth` |
| `--data-dir` | 작업 폴더. `jobs/<작업id>/` 아래에 `job.json`, `job.log`, 결과 PNG/PDF, `manifest.json` |
| `--base-path` | 프록시 경로 접두어. `https://호스트/browlab/` 로 노출할 때 `/browlab` |
| `--codex-bin` | codex 실행 파일. systemd 처럼 PATH 가 없는 환경에서는 절대경로 |
| `--default-backend` | 화면 기본 백엔드 (`codex`/`api`/`manual`) |

- 화면: 왼쪽 **상담 시뮬레이션 / 연습용 얼굴 생성 / 눈썹 생성기 / 출력 시트** 폼, 오른쪽 **갤러리**(결과 이미지 격자 → 눌러서 크게 보기 · PDF · 원본 · 삭제 · 로그).
  생성한 얼굴은 라이트박스의 "이 얼굴로 눈썹 편집"으로 바로 restyle 에 넣을 수 있고(`photo_from`), "같은 조건으로 한 장 더"도 됩니다.
- 삭제는 작업 폴더를 `<data-dir>/trash/<작업id>/` 로 옮깁니다(복구는 폴더를 `jobs/` 로 되돌리고 서비스 재시작).
- 작업은 `python -m browlab <명령> ... --out-dir <작업폴더>` 서브프로세스로 **한 번에 하나씩** 실행되고,
  화면은 4초마다 상태·로그·결과(PDF 링크, 이미지 썸네일)를 갱신합니다.
- 업로드 사진은 EXIF 회전을 바로잡아 `input.jpg/png` 로 저장합니다. 25 MB 이하, PNG/JPEG/WebP.
- 로그인 실패 8회면 15분 잠금. 세션 쿠키 30일.
- 서버 재시작 시 진행 중이던 작업은 `실패 · 서버가 재시작되어 중단됨` 으로 표시됩니다.

### 상담 시뮬레이션 (생성 없이, 도안 오버레이)

상담자 사진에 **직접 그린 눈썹 도안(투명 PNG)** 을 올려 시술 후 모습을 보여주는 메뉴입니다. 인공지능 생성이 없어 즉시 되고, 요금도 없습니다.

1. **도안 올리기**: 배경이 투명한 PNG 한 장(`오른쪽/왼쪽 눈썹` 선택), 또는 흰 종이에 그린 **도안 시트**(여러 쌍) 그대로. 시트는 자동으로 한 쌍씩 잘려 들어갑니다(`browlab brows` 와 같은 처리). 도안은 `<data-dir>/templates/` 에 보관됩니다.
2. **사진 분석하고 시작**: 얼굴을 찾아 눈썹 머리·꼬리에 도안을 그대로 맞춰 놓습니다. 못 찾으면 평균 위치에 놓이고 직접 끌어 맞추면 됩니다.
3. **모양**: 눈썹마다 조절점 5개 — 머리 윗선/아랫선, 아치 윗선/아랫선, 꼬리. 윗선 점은 윗선을, 아랫선 점은 아랫선을 움직이고, 꼬리 점은 길이와 각도, 몸통을 끌면 통째로 이동. 두 손가락으로 확대, 빈 곳을 끌면 화면 이동. `대칭` 이 켜져 있으면 반대쪽이 거울처럼 따라옵니다. 미세 조정 버튼은 0.5 mm 단위.
4. **색·농도**: 강도(색소 농도), 부드러움(피부 속에 자리 잡은 느낌), 선 진하기·굵기, 색소 6종 + **머리색 자동**, 밝기·따뜻함. 경과 미리보기 `시술 직후 / 치유 후(4주) / 1년 후` 는 강도·부드러움 프리셋입니다(치유 후 30~50% 연해지는 일반적 경과 기준).
5. **저장**: 치수(길이·두께·꼬리 높이·아치 높이·미간, 동공 간 거리 기준 mm), 상담 메모, `갤러리에 저장`(회원명, 비우면 회원_N), `내려받기`, `1:1 출력 시트`(저장본으로 A4 시트 작업). 조절 상태는 자동 저장되어 갤러리의 `상담 이어하기` 로 그대로 열립니다. `원본` 버튼(또는 스페이스)을 누르고 있는 동안 원본 사진.

### 4090 배포 상태 (2026-09-12)

| 항목 | 값 |
| --- | --- |
| 주소 (외부·휴대폰) | `https://seongha-art-4090.tailc4181c.ts.net/browlab/` (Tailscale Funnel, 443 의 `/browlab` 경로) |
| 주소 (테일넷 직접) | `http://100.74.241.125:8177/browlab/` |
| 비밀번호 | 미디어아트 허브·강의 덱과 같은 비밀번호. 파일 `~/.config/browlab/password.txt` (0600) |
| 코드 | `~/project/creator-ai-digest` (브랜치 `claude/eyebrow-tattoo-design-tool-q5twu7`), venv `.venv` (Python 3.12) |
| 서비스 | `~/.config/systemd/user/browlab.service` (`systemctl --user status/restart browlab`, 로그 `journalctl --user -u browlab -f`) |
| 작업 폴더 | `~/browlab_data/jobs/` |
| API 키 | 맥에서 `ssh -t 4090 '~/project/creator-ai-digest/browlab/tools/set_api_key.sh'` (숨김 입력 → `~/.config/browlab/env` 저장 → 서비스 재시작 → 확인). 삭제는 `--remove` |
| codex | `~/.nvm/versions/node/v22.22.3/bin/codex` (ChatGPT 로그인 상태). node 를 올리면 유닛의 경로 두 곳을 바꿀 것 |

코드 갱신: `cd ~/project/creator-ai-digest && git pull && systemctl --user restart browlab`.

**Tailscale 함정**: 경로를 추가할 때 `tailscale serve --https=443 --set-path ...` 를 쓰면 **그 포트의 Funnel 이 꺼집니다**
(2026-09-12 실제로 꺼져 1~2분간 강의 허브 외부 접속이 끊겼음). 공개 포트에는 반드시
`sudo tailscale funnel --bg --https=443 --set-path /browlab http://127.0.0.1:8177/browlab` 처럼 **`funnel` 명령**으로 추가한다.
서브 설정에 파일 경로 핸들러(10003, 10005)가 있어서 변경은 `sudo` 가 필요하다(4090 은 비밀번호 없는 sudo 가능).

---

## 9. 요금과 API 키 (2026-09-12 확인)

### Codex 모드 (기본)

ChatGPT 구독의 Codex 한도를 씁니다. 추가 요금은 없지만 이미지 생성은 한도를 빨리 소모하고,
한도가 차면 `ERROR: You've hit your usage limit ... try again at <시각>` 으로 실패합니다
(이 계정은 2026-09-12 기준 9/15 10:22 까지 막혀 있었음). 한도·크레딧 구매: https://chatgpt.com/codex/settings/usage

### API 모드 — 키 발급과 결제

1. https://platform.openai.com 에 ChatGPT 와 같은 계정으로 로그인 (API 계정은 ChatGPT 구독과 **별도 결제**).
2. **Settings → Billing → Add payment method** 에서 카드 등록 → **Add to credit balance** 로 선결제 크레딧 구매.
   최소 $5, 기본 $10, 크레딧은 1년 뒤 만료·환불 불가. 자동 충전(Auto recharge)은 켜지 않아도 됨.
3. **Dashboard → API keys → Create new secret key** 로 키 생성. 키는 만들 때 한 번만 보이므로 바로 복사.
4. 맥 터미널에서 `ssh -t 4090 '~/project/creator-ai-digest/browlab/tools/set_api_key.sh'` 를 실행해 키를 붙여넣습니다(숨김 입력, 자동 재시작·확인).
   (로컬 CLI 는 `export OPENAI_API_KEY=sk-...`)
5. 사용량·청구는 https://platform.openai.com/usage 에서 확인.

### API 장당 비용 (OpenAI 공식 계산기, GPT Image 2.5 flare/sunburst, 이미지 출력 토큰만)

토큰 단가: 이미지 출력 $30/M, 이미지 입력 $8/M(캐시 $2/M), 텍스트 입력 $5/M(캐시 $1.25/M). 프롬프트 텍스트 몫은 장당 $0.01 미만.

| 크기 | low | medium | high | xhigh | max |
| --- | --- | --- | --- | --- | --- |
| 1024×1024 | $0.006 | $0.013 | $0.053 | $0.094 | $0.211 |
| 1024×1536 (세로) | $0.005 | $0.010 | $0.041 | $0.074 | $0.165 |
| 1536×2304 (BrowLab 기본) | $0.007 | $0.016 | $0.064 | $0.114 | $0.255 |

- 연습용 얼굴 100장: `medium 1024x1536` ≈ $1, `high 1536x2304` ≈ $6.4 → **$10 크레딧이면 충분**.
- `restyle` 편집은 입력 사진 토큰(이미지 입력 $8/M)이 더해져 장당 조금 더 듭니다.
- 화면 상단 `$지출 / $충전` 버튼 → 설정 · API 사용량: 각 API 응답의 토큰 × 공식 단가로 누적 지출을 추정하고, 충전 누계를 적어 두면 남은 금액을 보여 줍니다(`~/browlab_data/settings.json`).
- 계산기: https://developers.openai.com/api/docs/guides/image-generation (Cost and latency 절), 단가표: https://developers.openai.com/api/docs/pricing

