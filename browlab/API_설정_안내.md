# BrowLab · OpenAI API 설정 안내 (2026-09-12)

## 0. 먼저 알아둘 것

- **Codex 모드**(기본)는 ChatGPT 구독 한도를 씁니다. 지금 이 계정은 한도가 차서 **9/15 10:22 까지** 이미지 생성이 막혀 있습니다(맥·4090 둘 다 확인). 한도 확인·크레딧 구매: https://chatgpt.com/codex/settings/usage
- **API 모드**는 ChatGPT 구독과 **별도 결제**(선불 크레딧)이고, 장당 과금됩니다. 크기·품질을 정확히 지정할 수 있어 인쇄용으로는 API 가 더 안정적입니다.

## 1. 결제 (platform.openai.com)

1. https://platform.openai.com 에 ChatGPT 와 같은 이메일로 로그인합니다.
2. 왼쪽 아래 톱니 **Settings → Billing → Add payment details** 에서 카드를 등록합니다.
3. **Add to credit balance** 에서 금액을 넣습니다. 최소 $5, 처음엔 **$10 이면 충분**합니다(아래 표).
   - 크레딧은 **1년 뒤 만료, 환불 불가**. Auto recharge(자동 충전)는 꺼 두어도 됩니다.

## 2. 키 발급

1. 왼쪽 메뉴 **API keys → Create new secret key**.
2. 이름 `browlab`, 권한은 기본(All) 그대로 → **Create**.
3. `sk-proj-…` 로 시작하는 키를 **그 자리에서 복사**합니다. 창을 닫으면 다시 볼 수 없습니다(그때는 새로 만들면 됨).

## 3. 4090 서버에 넣기 (맥 터미널에서 한 줄)

```bash
ssh -t 4090 '~/project/creator-ai-digest/browlab/tools/set_api_key.sh'
```

"OpenAI API 키를 붙여넣고 Enter" 가 나오면 키를 붙여넣습니다(화면에 안 보임). 스크립트가 `~/.config/browlab/env` 에 저장하고
서비스를 재시작한 뒤 "완료: 서비스가 API 키를 읽었습니다" 를 찍습니다. 키를 지우려면 같은 명령 끝에 `--remove` 를 붙입니다.

손으로 하려면: `ssh 4090` → `nano ~/.config/browlab/env` 에서 `#OPENAI_API_KEY=sk-...` 줄의 `#` 을 지우고 키를 넣고 저장(Ctrl+O, Enter, Ctrl+X) → `systemctl --user restart browlab`.

## 4. 확인

- 웹 화면 https://seongha-art-4090.tailc4181c.ts.net/browlab/ 을 새로고침 → 상단에 **"API 키 연결됨"** 이 보이면 됩니다.
- 첫 시험은 **고급 설정 → 엔진: OpenAI API, 품질 medium, 크기 1024x1536** 으로 1명 생성(약 $0.01).
- 사용량·잔액: https://platform.openai.com/usage

맥에서 명령줄로 쓰려면 `~/.zshrc` 에 `export OPENAI_API_KEY=sk-proj-...` 를 넣고 새 터미널을 엽니다.

## 5. 장당 비용 (OpenAI 공식 계산기, 2026-09-12 실측)

GPT Image 2.5 flare/sunburst. 이미지 출력 토큰만 계산한 값이고, 프롬프트 텍스트 몫은 장당 $0.01 미만입니다.

| 크기 | low | medium | high | xhigh | max |
| --- | --- | --- | --- | --- | --- |
| 1024×1024 | $0.006 | $0.013 | $0.053 | $0.094 | $0.211 |
| 1024×1536 (세로) | $0.005 | $0.010 | $0.041 | $0.074 | $0.165 |
| 1536×2304 (BrowLab 기본) | $0.007 | $0.016 | $0.064 | $0.114 | $0.255 |

- 연습 얼굴 100장: medium 1024×1536 ≈ $1, high 1536×2304 ≈ $6.4.
- `내 사진 눈썹`(편집)은 입력 사진 토큰($8/M)이 더해져 장당 조금 더 듭니다.
- 토큰 단가: 이미지 출력 $30/M · 이미지 입력 $8/M · 텍스트 입력 $5/M.
- 출처: https://developers.openai.com/api/docs/pricing , https://developers.openai.com/api/docs/guides/image-generation (Cost and latency)

## 6. 키를 바꾸거나 없앨 때

- 바꾸기: `~/.config/browlab/env` 의 값을 고치고 `systemctl --user restart browlab`.
- 없애기: 그 줄을 `#` 으로 다시 막고 재시작. 그리고 platform.openai.com 의 API keys 에서 키를 **Revoke** 합니다.
- 키는 절대 채팅·메일·깃에 붙여넣지 마세요. 이 파일(`~/.config/browlab/env`)은 본인만 읽을 수 있게(0600) 되어 있습니다.
