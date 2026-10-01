#!/usr/bin/env bash
# 오라클 클라우드 Ubuntu(ARM) 서버 첫 설치 스크립트.
# 사용: git clone 후 저장소 폴더에서  bash deploy/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
cd "$REPO_DIR"

echo "== 1/6 시스템 패키지"
sudo timedatectl set-timezone Asia/Seoul
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl
if ! command -v node >/dev/null || [ "$(node -v | cut -c2- | cut -d. -f1)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

echo "== 2/6 파이썬 환경"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e "server[dev]"

echo "== 3/6 웹 화면 빌드"
(cd web && npm ci --silent && npx next build)

echo "== 4/6 설정 파일 (.env)"
if [ ! -f .env ]; then
  PW="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 12)"
  cat > .env <<ENV
# KRX OpenAPI 인증키 (openapi.krx.co.kr 에서 발급)
KRX_API_KEY=
# (선택) AI 질문·해설용 Anthropic API 키 (console.anthropic.com). 비우면 AI 기능만 꺼짐
ANTHROPIC_API_KEY=
# 웹 로그인 비밀번호
SBT_PASSWORD=$PW
SBT_DATASET=kr
SBT_DATA_DIR=$REPO_DIR/data
ENV
  chmod 600 .env
  echo "  .env 를 만들었습니다. 웹 로그인 비밀번호: $PW"
  echo "  KRX_API_KEY 를 채우세요: nano $REPO_DIR/.env"
fi

echo "== 5/6 서비스 등록 (systemd)"
for f in deploy/systemd/*.service deploy/systemd/*.timer; do
  sed -e "s|__REPO__|$REPO_DIR|g" -e "s|__USER__|$USER_NAME|g" "$f" \
    | sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done
# 자동 업데이트 스크립트가 비밀번호 없이 API 를 재시작할 수 있게 허용
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart sbt-api.service" \
  | sudo tee /etc/sudoers.d/sbt >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now sbt-api.service sbt-update.timer sbt-update-us.timer sbt-deploy.timer

echo "== 6/6 데모 데이터 (실제 데이터 수집 전 화면 확인용)"
set -a; . ./.env; set +a
(cd server && ../.venv/bin/python -m sbt.data.cli demo >/dev/null)
sudo systemctl restart sbt-api.service

echo
echo "설치 완료. 확인: curl -s localhost:8000/api/health"
echo "다음: deploy/README.md 의 4~5단계를 진행하세요."
