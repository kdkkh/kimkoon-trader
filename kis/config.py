"""설정 읽기 — ~/KIS/config/kis_devlp.yaml 한 곳에서만 읽는다.

환경 변수 KIS_CONFIG_DIR 를 주면 그 폴더(kis_devlp.yaml · 토큰 캐시 · telegram.yaml)를 쓴다 — 시험 · 다른 PC 에서
  설정을 저장소 밖 다른 곳에 둘 때. 주지 않으면 예전과 같이 홈 폴더 KIS/config.
비밀값(앱키·시크릿·계좌·토큰)은 절대 화면·로그에 출력하지 않는다.
계좌는 mask_account() 로 끝 2자리만 보여 준다.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 윈도우 콘솔에서 한글이 깨지지 않게 출력 인코딩을 UTF-8 로 고정
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

CONFIG_DIR = Path(os.environ["KIS_CONFIG_DIR"]) if os.environ.get("KIS_CONFIG_DIR") else Path.home() / "KIS" / "config"
CONFIG_FILE = CONFIG_DIR / "kis_devlp.yaml"

# 모드별로 쓰는 설정 키 (기본값은 모의 "paper")
_KEYS = {
    "paper": dict(app="paper_app", sec="paper_sec", acct="my_paper_stock", rest="vps", ws="vops"),
    "real": dict(app="my_app", sec="my_sec", acct="my_acct_stock", rest="prod", ws="ops"),
}


def mask_account(acct: str) -> str:
    """계좌번호를 끝 2자리만 남기고 가린다. 예: ******12"""
    acct = str(acct or "")
    return "*" * max(len(acct) - 2, 0) + acct[-2:]


def _filled(v) -> bool:
    """값이 있고, 공식 예시 문구(한글)가 아니면 채워진 것으로 본다."""
    return v not in (None, "") and not re.search("[가-힣]", str(v))


@dataclass(frozen=True)
class KisConfig:
    mode: str                      # "paper"(모의) / "real"(실전)
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    account: str = field(repr=False)   # 계좌 앞 8자리
    product: str                   # 계좌 뒤 2자리 (01 = 종합)
    rest_url: str
    ws_url: str
    user_agent: str
    hts_id: str = field(repr=False, default="")

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def product_name(self) -> str:
        """계좌 뒤 2자리를 숫자 대신 이름으로 (화면 표시용)"""
        return {"01": "종합", "03": "선물옵션", "22": "연금저축", "29": "퇴직연금"}.get(self.product, "기타")

    @property
    def tr_prefix(self) -> str:
        """주문·잔고 tr_id 앞글자: 모의 V / 실전 T"""
        return "V" if self.is_paper else "T"

    @property
    def token_file(self) -> Path:
        """토큰 캐시 위치 (채널 폴더 밖)"""
        return CONFIG_DIR / ("tk_v.json" if self.is_paper else "tk_p.json")

    def summary(self) -> str:
        """비밀값 없이 화면에 보여 줄 요약"""
        return (f"모드={'모의' if self.is_paper else '실전'}  계좌={mask_account(self.account)}({self.product_name})  "
                f"REST={self.rest_url}  토큰캐시={self.token_file.name}")


def load_raw() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {CONFIG_FILE.name} (위치: ~/KIS/config/ 또는 KIS_CONFIG_DIR)")
    return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}


def load(mode: str = "paper") -> KisConfig:
    """모드에 맞는 설정을 읽는다. 빠진 값이 있으면 키 이름만 알려 준다."""
    if mode not in _KEYS:
        raise ValueError("mode 는 'paper'(모의) 또는 'real'(실전)")
    raw = load_raw()
    k = _KEYS[mode]
    missing = [raw_key for raw_key in (k["app"], k["sec"], k["acct"], k["rest"], k["ws"], "my_prod")
               if not _filled(raw.get(raw_key))]
    if missing:
        raise ValueError(f"[{mode}] 설정이 비어 있습니다: {', '.join(missing)}")
    hts = raw.get("my_htsid")
    return KisConfig(
        mode=mode,
        app_key=str(raw[k["app"]]),
        app_secret=str(raw[k["sec"]]),
        account=str(raw[k["acct"]]),
        product=str(raw["my_prod"]),
        rest_url=str(raw[k["rest"]]).rstrip("/"),
        ws_url=str(raw[k["ws"]]).rstrip("/"),
        user_agent=str(raw.get("my_agent") or "Mozilla/5.0"),
        hts_id=str(hts) if _filled(hts) else "",
    )


def status() -> list[tuple[str, bool]]:
    """설정 키별 채워짐 여부 (값은 돌려주지 않는다)"""
    return [(key, _filled(v)) for key, v in load_raw().items()]
