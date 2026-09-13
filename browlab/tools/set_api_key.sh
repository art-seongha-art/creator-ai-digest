#!/usr/bin/env bash
# BrowLab: OPENAI_API_KEY 를 터미널에서 안전하게 저장하고 서비스를 재시작한다.
#
#   맥에서:   ssh -t 4090 '~/project/creator-ai-digest/browlab/tools/set_api_key.sh'
#   4090에서: ~/project/creator-ai-digest/browlab/tools/set_api_key.sh
#   키 삭제:  ... set_api_key.sh --remove
#
# 키는 화면에 표시되지 않고, ~/.config/browlab/env (본인만 읽기 가능) 에만 저장된다.
set -euo pipefail

ENV_FILE="${BROWLAB_ENV_FILE:-$HOME/.config/browlab/env}"
SERVICE="browlab"
PORT="${BROWLAB_PORT:-8177}"

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

write_env() {  # $1 = 새 키 ('' 이면 제거)
  local tmp="$ENV_FILE.tmp"
  grep -v -E '^#?[[:space:]]*OPENAI_API_KEY=' "$ENV_FILE" > "$tmp" || true
  if [[ -n "$1" ]]; then
    printf 'OPENAI_API_KEY=%s\n' "$1" >> "$tmp"
  else
    printf '#OPENAI_API_KEY=sk-...\n' >> "$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

restart_and_check() {
  if [[ "${BROWLAB_SKIP_RESTART:-0}" == "1" ]]; then
    echo "(재시작 생략)"
    return 0
  fi
  systemctl --user restart "$SERVICE"
  sleep 2
  local me
  me="$(curl -s "http://127.0.0.1:${PORT}/browlab/api/me" || true)"
  if [[ "$me" == *'"api_key": true'* ]]; then
    echo "완료: 서비스가 API 키를 읽었습니다. 웹 화면 상단에 'API 키 연결됨' 이 보입니다."
  elif [[ "$me" == *'"api_key": false'* ]]; then
    echo "완료: 서비스 재시작됨, API 키 없음(제거됨)."
  else
    echo "저장은 됐지만 서비스 응답을 확인하지 못했습니다: journalctl --user -u $SERVICE -n 20"
    return 1
  fi
}

if [[ "${1:-}" == "--remove" ]]; then
  write_env ""
  echo "키를 지웠습니다 ($ENV_FILE). platform.openai.com 의 API keys 에서도 Revoke 하세요."
  restart_and_check
  exit 0
fi

if [[ -t 0 ]]; then
  printf 'OpenAI API 키를 붙여넣고 Enter (입력은 화면에 표시되지 않음): '
  read -rs KEY
  echo
else
  read -r KEY  # 파이프로 들어온 경우 (echo "sk-..." | set_api_key.sh)
fi
KEY="${KEY//[[:space:]]/}"
if [[ -z "$KEY" ]]; then
  echo "취소: 빈 입력"
  exit 1
fi
if [[ "$KEY" != sk-* ]]; then
  echo "경고: OpenAI 키는 보통 'sk-' 로 시작합니다. 그래도 저장하려면 y 를 입력: "
  read -r ans
  [[ "$ans" == "y" ]] || { echo "취소"; exit 1; }
fi

write_env "$KEY"
echo "저장: $ENV_FILE (끝 4자리 ...${KEY: -4})"
restart_and_check
