#!/usr/bin/env bash
# GitHub 에 새 커밋이 있으면 받아서 다시 빌드·재시작한다. (sbt-deploy.timer 가 15분마다 실행)
#   수동 실행: bash deploy/update.sh --force
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
mark=data/.deployed          # 끝까지 적용을 마친 커밋 (중간에 실패하면 안 바뀜 → 다음 차례에 다시 시도)
status=data/deploy_status.txt  # 앱 '자동 매매' 상태 줄에 보여 준다
git fetch -q origin main
want=$(git rev-parse origin/main)
short=$(git rev-parse --short origin/main)
if [ "$(cat "$mark" 2>/dev/null || true)" = "$want" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi
# 같은 커밋이 방금 실패했으면 한 시간 뒤에 다시 (15분마다 빌드를 되풀이하지 않게)
if [ "${1:-}" != "--force" ] && grep -q "실패.*$short" "$status" 2>/dev/null && [ -n "$(find "$status" -mmin -55 2>/dev/null)" ]; then
  exit 0
fi
# 자동 매매 주문 시각(평일 08:40~09:05 매수, 15:05~15:35 기간만료 매도·마무리)에는 서버를 다시 켜지 않는다 → 다음 차례에
hm=$(TZ=Asia/Seoul date +%H%M); wd=$(TZ=Asia/Seoul date +%u)
if [ "${1:-}" != "--force" ] && [ "$wd" -le 5 ] && { { [ "$hm" -ge 0840 ] && [ "$hm" -lt 0905 ]; } || { [ "$hm" -ge 1505 ] && [ "$hm" -lt 1535 ]; }; }; then
  echo "주문 시각이라 다음에 적용 ($hm)"
  exit 0
fi
fail() {
  echo "$(TZ=Asia/Seoul date '+%F %T') 실패: $1 · $short" > "$status"
  echo "배포 실패: $1"
  exit 1
}
echo "새 버전 적용: $short"
git merge -q --ff-only origin/main || fail "새 코드 받기(git merge) — 서버에서 파일이 바뀌었을 수 있음: git status 확인"
.venv/bin/pip install -q -e "server[dev]" || fail "파이썬 패키지 설치(pip)"
(cd web && npm ci --silent && npx next build >/dev/null) || fail "앱 화면 만들기(npm build) — 메모리 부족일 수 있음"
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
  for t in deploy/systemd/*.timer; do
    sudo systemctl enable "$(basename "$t")" >/dev/null 2>&1 || true
    sudo systemctl restart "$(basename "$t")"
  done
fi
sudo systemctl restart sbt-api.service || fail "서버 다시 켜기"
echo "$want" > "$mark"
echo "$(TZ=Asia/Seoul date '+%F %T') 성공 · $short" > "$status"
echo "완료"
