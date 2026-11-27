"""설치 점검 — API 호출 없이 파이썬·패키지·설정 파일 위치·전략 불러오기만 확인한다(키 값은 읽지 않음)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

import pandas  # noqa: E402
import requests  # noqa: E402,F401
import yaml  # noqa: E402,F401

from kis import config  # noqa: E402
from strategy.ported import load_strategy  # noqa: E402

print("python", sys.version.split()[0], "· pandas", pandas.__version__)
print("키 설정 파일 있음:", config.CONFIG_FILE.exists())
print("config.yaml 있음:", (ROOT / "config.yaml").exists())
print("전략:", load_strategy("supertrend"))
print("정지 파일 있음:", (ROOT / "state" / "stop").exists())
