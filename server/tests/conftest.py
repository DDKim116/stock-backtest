import os

os.environ.setdefault("SBT_PUSH_LOOP", "0")  # 시험 중에는 알림 확인 스레드를 띄우지 않는다
os.environ.setdefault("SBT_AUTO_LOOP", "0")
