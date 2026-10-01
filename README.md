# 주식 검증기 (stock-backtest)

국내(향후 미국) 주식 **전 종목 일봉**으로 매매 조건을 검증하는 모바일 웹앱.

- 조건식이나 조립 화면으로 신호·매수·청산 규칙 입력 → 전 종목·전 기간 검증
- 성공률, 아무 날이나 샀을 때(기준)와의 차이, 연도·시장·가격대별 통계, 자동 점검
- 사례를 누르면 캔들차트에 신호·매수·매도 지점 표시
- 숫자 자리에 여러 값을 넣으면 한 번에 비교표 생성
- (선택) AI 질문: 말로 질문하면 조건식으로 변환해 확인받고 실행. AI 해설: 결과를 읽고 다음 검증할 변형까지 제안
  - AI 를 거치는 순간만 유료(Anthropic API), 실행·숫자 수정·차트는 무료. 월 한도 설정 가능

## 구조

```
server/   Python 분석 서버 (FastAPI + numpy/pandas/DuckDB)
  sbt/data/     KRX 수집, 수정주가 계산, 가상 데이터
  sbt/engine/   조건식 언어(dsl), 전략 명세(spec), 체결·청산 계산(event), 통계(stats)
  sbt/api/      HTTP API, 저장한 전략·AI 사용량(SQLite)
  sbt/ai/       Claude API 호출 (질문 → 조건, 결과 해설), 비용 계산
web/      Next.js 정적 웹앱 (서버가 같은 주소에서 함께 제공)
deploy/   오라클 서버 설치·자동 업데이트 스크립트 → deploy/README.md
```

## 계산 규칙 요약

- **신호일**: 조건식을 만족한 날 (수정주가 기준, 거래정지일 제외, '전일'은 직전 거래일)
- **매수**
  - 지정가: 신호일 값으로 계산한 가격으로 다음 날부터 유효기간 동안 주문. 저가 ≤ 지정가면 체결(시가가 더 낮으면 시가 체결)
  - 다음 날 시가 / 신호일 종가
- **청산**: 보유기간 안에 목표가(고가 또는 종가 기준) 도달 → 성공, 손절가(저가 기준) 도달 → 손절, 아니면 마지막 날 종가
  - 갭으로 시가가 목표·손절을 넘으면 시가 체결
- **불확실**: 일봉으로 순서를 알 수 없는 경우(같은 날 목표·손절 동시 도달, 장중 체결일의 고가). 기본 통계는 보수적, 낙관적 값을 함께 표시
- **기준 성공률**: 같은 대상·매수·청산 규칙으로 무작위 20만 개 종목·일자를 샀을 때의 성공률
- **비용**: 매수·매도 수수료, 매도 세금, 슬리피지 (기본 0.015% / 0.015% / 0.20% / 0%)

## 개발

```bash
python3 -m venv .venv && .venv/bin/pip install -e "server[dev]"
cd server && ../.venv/bin/python -m sbt.data.cli demo     # 가상 데이터
../.venv/bin/python -m pytest
SBT_DATASET=demo ../.venv/bin/uvicorn sbt.api.app:app --reload --port 8000

cd web && npm ci && npm run dev    # NEXT_PUBLIC_API_URL=http://localhost:8000
```
