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
# systemd 설정(갱신 시각 등)이 바뀌었으면 다시 설치
changed=0
for f in deploy/systemd/*.service deploy/systemd/*.timer; do
  dst="/etc/systemd/system/$(basename "$f")"
  new="$(sed -e "s|__REPO__|$PWD|g" -e "s|__USER__|$(id -un)|g" "$f")"
  if [ "$new" != "$(cat "$dst" 2>/dev/null)" ]; then
    printf '%s\n' "$new" | sudo tee "$dst" >/dev/null
    changed=1
  fi
done
if [ "$changed" = 1 ]; then
  sudo systemctl daemon-reload
  for t in deploy/systemd/*.timer; do sudo systemctl restart "$(basename "$t")"; done
fi
sudo systemctl restart sbt-api.service
echo "완료"
