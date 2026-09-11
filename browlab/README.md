# BrowLab — 눈썹 문신 디자인 연습용 실물 크기(A4 1:1) 얼굴 시트 생성기

눈썹 모량이 부족하거나, 형태가 불확실하거나, 연한 눈썹을 가진 다양한 얼굴(10대~70대, 남녀, 여러 얼굴형)을
AI로 만들고, **A4 용지에 실제 사람 얼굴 크기(1:1)로 인쇄되는 시트**로 합성해 주는 도구입니다.
인쇄한 시트 위에 펜슬로 눈썹 디자인을 직접 그리며 연습할 수 있습니다.

더 나아가 실제 사진을 넣으면 **눈썹 부분만 검출**해서 얼굴은 그대로 두고 눈썹만 여러 스타일로 바꿔 보는
`restyle` 기능도 있습니다.

이미지 생성은 **Codex CLI 최신 버전(0.154+)의 내장 이미지 생성(`$imagegen`, ChatGPT Images 2.5)** 을
기본으로 사용하고, OpenAI Images API(`gpt-image-2.5-flare` / `gpt-image-2.5-sunburst`)를 직접 호출하는
백엔드와, 프롬프트만 뽑아 주는 수동 모드도 지원합니다.

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
codex --version                     # 0.154.0 이상 권장
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

## 3. 명령별 설명

### `generate` — 연습용 얼굴 생성 + A4 시트

| 옵션 | 값 | 설명 |
| --- | --- | --- |
| `-n, --count` | 정수 | 생성할 얼굴 수 |
| `--age` | `10s`~`70s`, 숫자, `random` | 나이대 (10대는 15~19세) |
| `--gender` | `female`, `male`, `random` | 성별 |
| `--face-shape` | `oval` 계란형, `round` 둥근형, `square` 각진형, `long` 긴형, `heart` 하트형, `diamond` 다이아몬드형, `triangle` 삼각형, `random` | 얼굴형 |
| `--brow-condition` | `sparse` 모량 부족, `faint` 연함, `patchy` 군데군데 빔, `missing_tail` 꼬리 없음, `asymmetric` 비대칭, `overplucked` 과도하게 뽑음, `undefined` 형태 불분명, `scar_gap` 흉터, `almost_none` 거의 없음, `random` | 눈썹 상태 |
| `--ethnicity` | `korean`, `japanese`, `chinese`, `southeast_asian`, `south_asian`, `middle_eastern`, `european`, `mediterranean`, `african`, `latino`, `mixed`, `random`, `any` | 외모. `random` 은 한국인 비중을 높게 둔 가중 무작위, `any` 는 균등 |
| `--seed` | 정수 | 같은 시드면 같은 조합(나이·성별·얼굴형…)이 나옵니다. 이미지 자체는 매번 달라집니다 |
| `--notes` | 문장 | 프롬프트 끝에 덧붙일 지시문 |
| `--backend` | `codex`(기본), `api`, `manual` | 이미지 생성 방법 |
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

1. 사진을 편집 가능한 크기로 정리(16의 배수, 긴 변 2048px 이하)합니다 → `00_original.png`
2. 얼굴 랜드마크로 **눈썹 영역 마스크**를 만듭니다 → `mask.png`, `mask_api.png`(알파), `mask_guide.png`(빨간 표시)
3. 스타일마다 편집 프롬프트로 생성합니다.
   - `api` 백엔드: 알파 마스크 인페인팅(마스크 밖은 모델이 건드리지 않음)
   - `codex` 백엔드: 원본 + 빨간 영역 가이드 이미지를 첨부해 "눈썹만 바꿔라"로 편집
4. 결과에서 **눈썹 영역만 원본 위에 합성**(`_composited.png`)해, 마스크 밖은 원본과 픽셀 단위로 동일하게 만듭니다.
5. 비교 시트(`sheet_compare`)와 눈썹 구역 1:1 시트(`sheet_browzone`)를 만듭니다.

| 옵션 | 설명 |
| --- | --- |
| `--styles` | `korean_natural` 자연, `straight` 일자, `soft_arch` 부드러운 아치, `angled_arch` 각진 아치, `rounded` 둥근, `high_arch` 하이 아치, `puppy` 처진(강아지상), `bold_thick` 볼드, `feathered` 결눈썹(엠보), `ombre_powder` 옴브레 파우더, `combo` 콤보, `s_curve` S자. `all`, `random:3` 가능 |
| `--color` | `match_hair`(기본), `natural_black`, `dark_brown`, `medium_brown`, `ash_brown`, `light_brown`, `gray_brown` |
| `--mask-up/--mask-side/--mask-down` | 마스크 여유(동공 간 거리 배수). 더 높거나 두꺼운 디자인을 허용하려면 `--mask-up` 을 키우세요 |
| `--no-composite` | 합성 단계 생략(모델 결과 그대로) |
| `--no-mask` | API 백엔드에서 마스크 없이 프롬프트만으로 편집 |
| `--sheet` | `grid`, `browzone`, `both`(기본), `none` |

### `calibrate` — 인쇄 배율 확인 시트

150 mm 가로·세로 눈금, 50 mm 정사각형, 성인 평균 동공 간격이 그려진 페이지입니다.
인쇄 후 자로 재서 정확하면 얼굴 시트도 1:1로 나옵니다.

---

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
| `mediapipe unavailable` | `pip install mediapipe` (리눅스는 `libgl1 libegl1 libgles2` 필요). 또는 `--landmarks codex` / `--pupils` |
| 얼굴이 A4에 다 안 들어감 | 정상입니다. 눈썹·눈·턱을 우선 살리고 머리 윗부분을 잘라냅니다. `--ipd-mm` 을 줄이면 전체가 작아집니다 |
| 시트 한글이 네모로 나옴 | `--font /경로/NanumGothic.ttf` 또는 `BROWLAB_FONT` 환경변수 |
| 생성된 얼굴 눈썹이 너무 또렷함 | `--brow-condition almost_none` / `faint`, 또는 `--notes "eyebrows must be barely visible"` |
| restyle 결과가 원본과 미묘하게 다름 | 기본 합성(`_composited.png`)을 쓰면 마스크 밖은 원본 그대로입니다. 마스크가 좁으면 `--mask-up 0.55` |

## 7. 주의

- 실제 고객 사진을 `restyle` 하면 사진이 OpenAI 서버(Codex/API)로 전송됩니다. 동의를 받고 사용하세요.
- AI 생성 얼굴은 실존 인물이 아니며, 나이·얼굴형 등은 프롬프트 지시일 뿐 항상 정확하지 않습니다.
- 1:1 배율은 평균 동공 간 거리에 맞춘 근사치입니다. 실제 시술 도안 용도가 아니라 연습용입니다.
