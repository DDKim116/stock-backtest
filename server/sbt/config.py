"""환경 변수 기반 설정."""
from __future__ import annotations

import os
from pathlib import Path

# 가격 데이터 루트. 시장별 하위 폴더(kr, us, demo)를 가진다.
DATA_DIR = Path(os.environ.get("SBT_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))

# KRX 정보데이터시스템 OpenAPI 인증키 (openapi.krx.co.kr 에서 발급)
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

# 웹앱 로그인 비밀번호 (단일 사용자). 비어 있으면 인증 없이 동작(로컬 개발용).
APP_PASSWORD = os.environ.get("SBT_PASSWORD", "")

# 기본으로 사용할 데이터셋 이름 (kr | demo)
DEFAULT_DATASET = os.environ.get("SBT_DATASET", "kr")


def dataset_dir(name: str) -> Path:
    return DATA_DIR / name
