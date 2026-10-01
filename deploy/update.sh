#!/usr/bin/env bash
# GitHub 에 새 커밋이 있으면 받아서 다시 빌드·재시작한다. (sbt-deploy.timer 가 15분마다 실행)
#   수동 실행: bash deploy/update.sh --force
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin main
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi
echo "새 버전 적용: $(git rev-parse --short origin/main)"
git merge -q --ff-only origin/main
.venv/bin/pip install -q -e "server[dev]"
(cd web && npm ci --silent && npx next build >/dev/null)
sudo systemctl restart sbt-api.service
echo "완료"
