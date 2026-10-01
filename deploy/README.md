# 오라클 무료 서버 설치 안내

처음 한 번만 하면 됩니다. 이후 코드가 GitHub에 올라오면 서버가 15분 안에 알아서 업데이트하고,
국내 주가 데이터도 매일 자동으로 갱신됩니다.

---

## 1. 오라클 클라우드 가입과 서버 만들기

1. https://www.oracle.com/kr/cloud/free/ 에서 가입합니다(카드 등록 필요, 무료 범위에서는 청구 없음).
   - 홈 리전은 **나중에 바꿀 수 없습니다.** 춘천·서울은 ARM 서버 재고가 자주 부족하니, 생성이 계속 실패하면 일본(도쿄·오사카)도 괜찮습니다.
2. 콘솔 → **컴퓨트 → 인스턴스 → 인스턴스 생성**
   - 이미지: **Ubuntu 24.04** (또는 22.04)
   - Shape: **Ampere (VM.Standard.A1.Flex)**, OCPU **4**, 메모리 **24GB**
   - 부트 볼륨: **100GB** 이상(무료 한도 합계 200GB)
   - SSH 키: "키 쌍 생성"을 눌러 **개인 키를 내려받아 보관**하세요.
   - "Out of capacity" 오류가 나면 OCPU 2 / 메모리 12GB로 줄이거나 몇 시간 뒤 다시 시도합니다.
3. (권장) 계정을 **종량제(Pay As You Go)로 업그레이드**합니다. 무료 범위 안에서는 요금이 나오지 않고, 사용량이 적은 무료 계정의 서버가 회수되는 일을 막아 줍니다.

## 2. 서버 접속

휴대폰이나 태블릿만 있다면 **Termius** 앱(무료)으로 접속할 수 있습니다.
- Host: 인스턴스의 공용 IP, Username: `ubuntu`, Key: 1-2에서 받은 개인 키

## 3. 프로그램 설치

이 저장소는 비공개라서, 서버가 읽을 수 있게 **배포 키**를 등록해야 합니다.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```
출력된 한 줄을 GitHub 저장소 → Settings → Deploy keys → **Add deploy key**에 붙여넣습니다(쓰기 권한은 체크하지 않음).

```bash
git clone git@github.com:DDKim116/stock-backtest.git
cd stock-backtest
bash deploy/setup.sh
```
- 10분쯤 걸립니다. 끝나면 **웹 로그인 비밀번호**가 화면에 표시됩니다(`.env` 파일에도 저장됨).
- 설치 직후에는 화면 확인용 **데모(가상) 데이터**가 들어 있습니다.

## 4. 국내 데이터 첫 수집

1. https://openapi.krx.co.kr 가입 → **인증키 신청**
2. 서비스 이용 신청: **유가증권 일별매매정보**, **코스닥 일별매매정보** (승인까지 하루쯤 걸릴 수 있음)
3. 서버에서 키를 입력합니다.
   ```bash
   nano ~/stock-backtest/.env        # KRX_API_KEY= 뒤에 키 붙여넣기 → Ctrl+O, Enter, Ctrl+X
   ```
4. 2010년부터 수집을 시작합니다(약 8,000번 호출, 1~2시간). 접속을 끊어도 계속 돌아갑니다.
   ```bash
   cd ~/stock-backtest/server
   set -a; . ../.env; set +a
   nohup ../.venv/bin/python -m sbt.data.cli kr-collect --start 2010-01-01 > ~/collect.log 2>&1 &
   tail -f ~/collect.log             # 진행 상황 보기 (Ctrl+C 로 보기만 종료)
   ```
   - 하루 호출 한도에 걸리면 멈춥니다. 다음 날 같은 명령을 다시 실행하면 이어서 받습니다.
5. 다 받으면 분석용 파일을 만들고 서버를 재시작합니다.
   ```bash
   ../.venv/bin/python -m sbt.data.cli kr-build
   sudo systemctl restart sbt-api
   ```
6. **검증:** 아는 종목의 최근 가격을 증권사 앱과 비교해 보세요.
   ```bash
   ../.venv/bin/python -m sbt.data.cli check --code 005930
   ```
   이후로는 평일 아침과 저녁에 자동으로 갱신됩니다.

## 5. 휴대폰·태블릿에서 접속

서버의 포트를 인터넷에 열지 않고 안전하게 접속하는 방법 두 가지입니다.

### 방법 A — Tailscale (무료, 추천)
나와 내 기기만 접속할 수 있는 개인 네트워크를 만듭니다. 도메인이 필요 없습니다.
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # 표시되는 주소를 열어 로그인
sudo tailscale serve --bg 8000
tailscale serve status       # https://○○○.ts.net 주소 확인
```
휴대폰·태블릿에 **Tailscale 앱**을 설치하고 같은 계정으로 로그인한 뒤, 위 `https://…ts.net` 주소를 엽니다.
브라우저 메뉴에서 **홈 화면에 추가**를 하면 앱처럼 쓸 수 있습니다.

### 방법 B — Cloudflare Tunnel (도메인이 있을 때)
앱 설치 없이 어디서나 `https://주식.내도메인.com` 으로 접속하려면 Cloudflare에 연결된 도메인(연 1~2만 원)이 필요합니다.
Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → Create tunnel 안내에 따라 서버에 `cloudflared` 를 설치하고,
Public hostname 의 서비스 주소를 `http://localhost:8000` 으로 지정합니다.

---

## 자주 쓰는 명령

| 하고 싶은 일 | 명령 |
|---|---|
| 서버 상태 | `systemctl status sbt-api` |
| 서버 로그 | `journalctl -u sbt-api -n 100` |
| 데이터 갱신 기록 | `journalctl -u sbt-update -n 50` |
| 자동 업데이트 기록 | `journalctl -u sbt-deploy -n 50` |
| 지금 바로 업데이트 | `bash ~/stock-backtest/deploy/update.sh --force` |
| 비밀번호 변경 | `.env` 의 `SBT_PASSWORD` 수정 후 `sudo systemctl restart sbt-api` |
